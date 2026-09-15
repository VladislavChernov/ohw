"""Semantic Cache (бандл 3/3 вехи M3; docs/api_reference.md, ADR-016 конверт).

Хранилище готовых ответов, ключованных эмбеддингом запроса: повторный запрос, чей
эмбеддинг косинусно близок (>= порога) к закэшированному, получает `done` из кэша
без graph/vector/rerank/context/LLM и без `token`-событий.

Свежесть (зафиксированное допущение, решение 2026-09-13): только TTL + `clear()`;
COMMIT не инвалидирует кэш (нет catalog-revision в контуре запроса). Planned upgrade
path — epoch-bump ключа `query:sc:<rev>:<domain>` — зафиксирован в spec бандла, не
реализован. Моменты пересмотра допущения: M4 (Eval), M5 (конфигуратор профилей),
M6 (коннекторы).

«Плохие» ответы не кэшируются: пустой `text`, явный отказ LLM («контекста
недостаточно»), сбой пайплайна -> `store()` no-op (решение по свежести).

Безопасность при сбое Valkey (2026-09-14, замечания команд ревью R6-4/SC-3/S3):
- `lookup` при ConnectionError/TimeoutError → miss (fail-open, запрос идёт полным циклом);
- `store` при ConnectionError/TimeoutError → no-op;
- `clear()` — SCAN + DEL (не зависит от in-process счётчиков);
- `stats()` — HLEN (реальное состояние Redis, а не in-process кэш);
- соединение: `socket_timeout=2s` (fail-fast, не блокирует поток надолго);
- поле: `sc:<sha256(repr(embedding))>` (полный 64-символьный hex, нет коллизий 48-бит);
- EXPIRE на HASH = TTL при каждом store (S4: брошенный домен не держит ключ вечно);
- shared-счётчики hit/miss (ревью №7 §2.1): живут в Valkey — HASH ``query:sc:<domain>:meta``
  с полями `hits`/`misses` (HINCRBY), НЕ в памяти процесса -> stats() агрегируют HGETALL
  по доменам и переживают рестарт/несколько воркеров (этап B — sink в /metrics).
  Сбой-пути (lookup при недоступной Valkey) счётчик не увеличивают: агрегировать негде,
  фиксируется как допущение.
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_DEFAULT_THRESHOLD = 0.85
_DEFAULT_TTL_S = 3600.0
_REDIS_DEFAULT_URL = "redis://valkey:6379/0"
_KEY_PREFIX = "query:sc"
_META_SUFFIX = ":meta"

# Явный отказ LLM по DEFAULT_SYSTEM_PROMPT («Если контекста недостаточно — так и скажи»).
_REFUSAL_MARKERS = (
    "контекста недостаточно",
    "недостаточно контекста",
    "контекста не хватает",
)


@dataclass
class CachedAnswer:
    """То, что возвращает hit: текст ответа и источники из закэшированного done."""

    text: str
    sources: list[dict[str, Any]]


def should_cache_text(text: str) -> bool:
    """«Плохие» ответы не складируются (решение 2026-09-13): пустой text или отказ."""
    body = text.strip()
    if not body:
        return False
    lowered = body.lower()
    return not any(marker in lowered for marker in _REFUSAL_MARKERS)


def _is_blank_embedding(embedding: list[float]) -> bool:
    return not embedding or all(value == 0.0 for value in embedding)


def _cosine(a: list[float], b: list[float]) -> float:
    """Косинус между векторами; нулевые/несовместимые -> 0.0 (мимо порога)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / math.sqrt(norm_a * norm_b)


class SemanticCache(ABC):
    """Контракт кэша (spec бандла add-semantic-cache)."""

    mode: str
    threshold: float
    ttl_s: float

    def store(self, embedding: list[float], answer: CachedAnswer, domain: str = "") -> None:
        """Запись ответа. No-op для «плохих» ответов (should_cache_text)."""
        if not should_cache_text(answer.text):
            return
        self._store(embedding, answer, domain)

    @abstractmethod
    def lookup(
        self, embedding: list[float], threshold: float, domain: str = ""
    ) -> CachedAnswer | None:
        """Ответ с максимальным косинусом среди живых записей >= threshold; иначе None."""

    @abstractmethod
    def _store(self, embedding: list[float], answer: CachedAnswer, domain: str) -> None:
        """Реализация записи (после проверки should_cache_text)."""

    @abstractmethod
    def stats(self) -> dict[str, int]:
        """Счётчики {entries, hits, misses}."""

    @abstractmethod
    def clear(self) -> None:
        """Ручной сброс кэша (единственный механизм инвалидации помимо TTL)."""


class _InMemoryEntry:
    __slots__ = ("answer", "embedding", "ts")

    def __init__(self, embedding: list[float], answer: CachedAnswer, ts: float) -> None:
        self.embedding = embedding
        self.answer = answer
        self.ts = ts


class InMemorySemanticCache(SemanticCache):
    """Тесты/демо без Valkey (паттерн InMemoryTaskQueue): потоко-безопасный бакет."""

    mode = "inmemory"

    def __init__(self, threshold: float = _DEFAULT_THRESHOLD, ttl_s: float = _DEFAULT_TTL_S) -> None:
        self.threshold = float(threshold)
        self.ttl_s = float(ttl_s)
        self._lock = threading.Lock()
        self._bucket: dict[str, list[_InMemoryEntry]] = {}
        self._hits = 0
        self._misses = 0

    def lookup(self, embedding: list[float], threshold: float, domain: str = "") -> CachedAnswer | None:
        if _is_blank_embedding(embedding):
            with self._lock:
                self._misses += 1
            return None
        with self._lock:
            alive = _trim_expired(self._bucket.get(domain, []), self.ttl_s)
            self._bucket[domain] = alive
            best = _best_alive(alive, embedding, threshold)
            if best is None:
                self._misses += 1
                return None
            self._hits += 1
            return best.answer

    def _store(self, embedding: list[float], answer: CachedAnswer, domain: str) -> None:
        with self._lock:
            self._bucket.setdefault(domain, []).append(
                _InMemoryEntry(list(embedding), answer, time.time())
            )

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "entries": sum(len(items) for items in self._bucket.values()),
                "hits": self._hits,
                "misses": self._misses,
            }

    def clear(self) -> None:
        with self._lock:
            self._bucket.clear()
            self._hits = 0
            self._misses = 0


def _trim_expired(entries: list[_InMemoryEntry], ttl_s: float) -> list[_InMemoryEntry]:
    if ttl_s <= 0:
        return entries
    now = time.time()
    return [entry for entry in entries if now - entry.ts <= ttl_s]


def _best_alive(
    entries: list[_InMemoryEntry],
    embedding: list[float],
    threshold: float,
) -> _InMemoryEntry | None:
    best: _InMemoryEntry | None = None
    best_cos = -1.0
    for entry in entries:
        cos = _cosine(embedding, entry.embedding)
        if cos >= threshold and cos > best_cos:
            best = entry
            best_cos = cos
    return best


class RedisSemanticCache(SemanticCache):
    """Valkey/Redis (ADR-023): HASH ``query:sc:<domain>``, поле ``sc:<sha256>``.

    Ленивое подключение к ``redis`` с ``socket_timeout=2s`` (fail-fast);
    ``client`` можно подменить фейком в тестах.  При сбое подключения /
    команды — fail-open: ``lookup`` → ``None``, ``store`` → no-op
    (сбой кэша не должен ломать пользовательский запрос).

    Сканирование — HGETALL + линейный косинус (объём прототипа мал,
    зафиксировано в spec); просроченные записи — lazy HDEL.
    ``clear()`` использует SCAN + DEL (покрывает и записи, и meta-счётчики);
    ``stats()`` читает HLEN/HGETALL из Valkey — никаких in-process счётчиков.
    """

    mode = "redis"

    def __init__(
        self,
        url: str = _REDIS_DEFAULT_URL,
        threshold: float = _DEFAULT_THRESHOLD,
        ttl_s: float = _DEFAULT_TTL_S,
        client: Any = None,
    ) -> None:
        self.threshold = float(threshold)
        self.ttl_s = float(ttl_s)
        self._url = url
        self._client = client

    def _redis(self) -> Any:
        if self._client is None:
            from redis import Redis  # ленивый импорт — redis не обязателен для тестов/демо

            self._client = Redis.from_url(
                self._url, decode_responses=True, socket_timeout=2
            )
        return self._client

    @staticmethod
    def _key(domain: str) -> str:
        return f"{_KEY_PREFIX}:{domain}"

    @staticmethod
    def _meta_key(domain: str) -> str:
        return f"{_KEY_PREFIX}:{domain}{_META_SUFFIX}"

    def _bump(self, domain: str, metric: str) -> None:
        """Shared-счётчик в Valkey (HINCRBY); сбой — молчаливый пропуск (fail-open)."""
        try:
            self._redis().hincrby(self._meta_key(domain), metric, 1)
        except (ConnectionError, TimeoutError, OSError):
            pass

    @staticmethod
    def _field(embedding: list[float]) -> str:
        digest = hashlib.sha256(repr(embedding).encode("utf-8")).hexdigest()
        return f"sc:{digest}"

    @staticmethod
    def _payload(
        embedding: list[float], answer: CachedAnswer, ts: float
    ) -> str:
        return json.dumps(
            {
                "embedding": embedding,
                "text": answer.text,
                "sources": answer.sources,
                "ts": ts,
            },
            ensure_ascii=False,
        )

    def lookup(self, embedding: list[float], threshold: float, domain: str = "") -> CachedAnswer | None:
        if _is_blank_embedding(embedding):
            self._bump(domain, "misses")
            return None
        try:
            client = self._redis()
            key = self._key(domain)
            raw = client.hgetall(key)
        except (ConnectionError, TimeoutError, OSError):
            self._bump(domain, "misses")
            return None
        if not isinstance(raw, Mapping) or not raw:
            self._bump(domain, "misses")
            return None

        now = time.time()
        best: CachedAnswer | None = None
        best_cos = -1.0
        stale: list[str] = []
        for field, value in raw.items():
            try:
                record: dict[str, Any] = json.loads(value)
            except (TypeError, ValueError):
                stale.append(field)
                continue
            try:
                ts = float(record.get("ts") or 0)
            except (TypeError, ValueError):
                stale.append(field)
                continue
            if self.ttl_s > 0 and now - ts > self.ttl_s:
                stale.append(field)
                continue
            cos = _cosine(embedding, record.get("embedding") or [])
            if cos >= threshold and cos > best_cos:
                sources = [dict(item) for item in (record.get("sources") or [])]
                best = CachedAnswer(text=str(record.get("text") or ""), sources=sources)
                best_cos = cos

        if stale:
            try:
                client.hdel(key, *stale)
            except (ConnectionError, TimeoutError, OSError):
                pass
        if best is None:
            self._bump(domain, "misses")
            return None
        self._bump(domain, "hits")
        return best

    def _store(self, embedding: list[float], answer: CachedAnswer, domain: str) -> None:
        try:
            client = self._redis()
            field = self._field(embedding)
            key = self._key(domain)
            client.hset(key, field, self._payload(embedding, answer, time.time()))
            if self.ttl_s > 0:
                client.expire(key, math.ceil(self.ttl_s))
        except (ConnectionError, TimeoutError, OSError):
            pass

    def stats(self) -> dict[str, int]:
        entries = 0
        hits = 0
        misses = 0
        try:
            client = self._redis()
            cursor = 0
            while True:
                cursor, keys = client.scan(cursor=cursor, match=f"{_KEY_PREFIX}:*", count=100)
                for key in keys:
                    if key.endswith(_META_SUFFIX):
                        meta = client.hgetall(key)
                        hits += int(meta.get("hits") or 0)
                        misses += int(meta.get("misses") or 0)
                        continue
                    try:
                        entries += client.hlen(key)
                    except (ConnectionError, TimeoutError, OSError):
                        pass
                if cursor == 0:
                    break
        except (ConnectionError, TimeoutError, OSError):
            pass
        return {"entries": entries, "hits": hits, "misses": misses}

    def clear(self) -> None:
        # SCAN ``query:sc:*`` покрывает и HASH-записи, и meta-ключи счётчиков.
        try:
            client = self._redis()
            cursor = 0
            while True:
                cursor, keys = client.scan(cursor=cursor, match=f"{_KEY_PREFIX}:*", count=100)
                if keys:
                    client.delete(*keys)
                if cursor == 0:
                    break
        except (ConnectionError, TimeoutError, OSError):
            pass