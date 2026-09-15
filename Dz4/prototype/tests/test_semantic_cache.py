"""Semantic Cache: hit/miss/порог/TTL/stats/clear, плохие ответы, Redis-формат."""
from __future__ import annotations

import fnmatch
import json
import time

from graphrag_proto.retrieval.semantic_cache import (
    CachedAnswer,
    InMemorySemanticCache,
    RedisSemanticCache,
    should_cache_text,
)

# --- helpers -----------------------------------------------------------

_EMB_SIMILAR: list[float] = [1.0, 0.0, 0.0, 0.0, 0.0]
_EMB_PERP: list[float] = [0.0, 1.0, 0.0, 0.0, 0.0]
_EMB_EMPTY: list[float] = [0.0, 0.0, 0.0, 0.0, 0.0]


def _answer(text: str = "ответ") -> CachedAnswer:
    return CachedAnswer(text=text, sources=[{"source_url": "s://a", "relevance": 0.9}])


# --- FakeRedis (хитрый тонкий мок) ------------------------------------

class FakeRedis:
    """Имитация redis с ключом query:sc:<domain>, HASH: dict[str, dict[str, str]]."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, str]] = {}
        self._deletes: list[str] = []
        self._fail_next: bool = False  # имитация ConnectionError
        self._ttl: dict[str, int] = {}  # EXPIRE-таймауты ключей

    def hgetall(self, key: str) -> dict[str, str]:
        if self._fail_next:
            self._fail_next = False
            raise ConnectionError("fake connection lost")
        return dict(self._store.get(key, {}))

    def hset(self, key: str, field: str, value: str) -> int:
        if self._fail_next:
            self._fail_next = False
            raise ConnectionError("fake connection lost")
        bucket = self._store.setdefault(key, {})
        is_new = field not in bucket
        bucket[field] = value
        return int(is_new)

    def expire(self, key: str, ttl: int) -> None:
        self._ttl[key] = ttl

    def hdel(self, key: str, *fields: str) -> int:
        bucket = self._store.get(key, {})
        removed = sum(1 for f in fields if bucket.pop(f, None) is not None)
        self._deletes.append(key)
        return removed

    def hlen(self, key: str) -> int:
        return len(self._store.get(key, {}))

    def delete(self, *keys: str) -> None:
        for key in keys:
            self._store.pop(key, None)
            self._deletes.append(key)

    def scan(self, cursor: int = 0, match: str = "*", count: int = 100) -> tuple[int, list[str]]:
        all_keys = list(self._store.keys())
        matched = [k for k in all_keys if fnmatch.fnmatch(k, match)]
        return 0, matched


# --- tests: inmemory --------------------------------------------------

def test_hit_above_threshold() -> None:
    cache = InMemorySemanticCache(threshold=0.85, ttl_s=0)
    cache.store(_EMB_SIMILAR, _answer("результат"))
    hit = cache.lookup(_EMB_SIMILAR, threshold=0.85)
    assert hit is not None
    assert hit.text == "результат"
    assert cache.stats()["hits"] == 1


def test_miss_below_threshold() -> None:
    cache = InMemorySemanticCache(threshold=0.99, ttl_s=0)
    cache.store(_EMB_SIMILAR, _answer())
    assert cache.lookup(_EMB_PERP, threshold=0.99) is None
    assert cache.stats()["misses"] == 1


def test_empty_embedding_miss() -> None:
    cache = InMemorySemanticCache()
    cache.store(_EMB_SIMILAR, _answer())
    assert cache.lookup(_EMB_EMPTY, threshold=0.50) is None
    assert cache.stats()["entries"] == 1


def test_ttl_istechenie() -> None:
    cache = InMemorySemanticCache(threshold=0.80, ttl_s=0.05)
    cache.store(_EMB_SIMILAR, _answer("сначала"))
    assert cache.lookup(_EMB_SIMILAR, threshold=0.80) is not None
    time.sleep(0.06)
    assert cache.lookup(_EMB_SIMILAR, threshold=0.80) is None


def test_stats_and_clear() -> None:
    cache = InMemorySemanticCache(threshold=0.80, ttl_s=0)
    cache.store(_EMB_SIMILAR, _answer())
    cache.lookup(_EMB_SIMILAR, threshold=0.80)
    cache.lookup(_EMB_PERP, threshold=0.80)
    stats = cache.stats()
    assert stats["entries"] == 1
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    cache.clear()
    assert cache.stats() == {"entries": 0, "hits": 0, "misses": 0}


# --- tests: redis (fake) ----------------------------------------------

def test_redis_format_key_field_json_hdel() -> None:
    fake = FakeRedis()
    cache = RedisSemanticCache(url="redis://fake:0", threshold=0.80, ttl_s=0, client=fake)
    cache.store(_EMB_SIMILAR, _answer("redis-ok"), domain="it")
    key = "query:sc:it"
    assert key in fake._store
    fields = fake.hgetall(key)
    assert len(fields) == 1
    field = next(iter(fields))
    assert field.startswith("sc:")
    record = json.loads(fields[field])
    assert record["text"] == "redis-ok"
    assert record["ts"] > 0
    assert isinstance(record["embedding"], list)
    assert isinstance(record["sources"], list)


def test_redis_ttl_purge() -> None:
    fake = FakeRedis()
    cache = RedisSemanticCache(url="redis://fake:0", threshold=0.80, ttl_s=0.05, client=fake)
    cache.store(_EMB_SIMILAR, _answer(), domain="dom")
    assert cache.lookup(_EMB_SIMILAR, threshold=0.80, domain="dom") is not None
    time.sleep(0.06)
    assert cache.lookup(_EMB_SIMILAR, threshold=0.80, domain="dom") is None
    # record HDEL'ed
    assert fake._store.get("query:sc:dom", {}) == {}


def test_redis_poor_answer_no_store() -> None:
    fake = FakeRedis()
    cache = RedisSemanticCache(url="redis://fake:0", threshold=0.80, ttl_s=0, client=fake)
    cache.store(_EMB_SIMILAR, CachedAnswer(text="", sources=[]), domain="x")
    assert fake._store.get("query:sc:x", {}) == {}
    cache.store(
        _EMB_SIMILAR,
        CachedAnswer(text="контекста недостаточно", sources=[]),
        domain="x",
    )
    assert fake._store.get("query:sc:x", {}) == {}


def test_redis_clear_removes_all_keys() -> None:
    fake = FakeRedis()
    cache = RedisSemanticCache(url="redis://fake:0", threshold=0.80, ttl_s=0, client=fake)
    cache.store(_EMB_SIMILAR, _answer("a"), domain="d1")
    cache.store(_EMB_PERP, _answer("b"), domain="d2")
    assert len(fake._store) == 2
    cache.clear()
    assert len(fake._store) == 0
    assert cache.stats() == {"entries": 0, "hits": 0, "misses": 0}


def test_redis_stats_uses_hlen() -> None:
    fake = FakeRedis()
    cache = RedisSemanticCache(url="redis://fake:0", threshold=0.80, ttl_s=0, client=fake)
    cache.store(_EMB_SIMILAR, _answer("a"), domain="d1")
    cache.store(_EMB_PERP, _answer("b"), domain="d1")
    cache.store(_EMB_SIMILAR, _answer("c"), domain="d2")
    stats = cache.stats()
    assert stats["entries"] == 3


def test_redis_expire_set_on_store() -> None:
    fake = FakeRedis()
    cache = RedisSemanticCache(url="redis://fake:0", threshold=0.80, ttl_s=45, client=fake)
    cache.store(_EMB_SIMILAR, _answer("a"), domain="d1")
    assert fake._ttl.get("query:sc:d1") == 45


def test_redis_no_expire_when_ttl_zero() -> None:
    fake = FakeRedis()
    cache = RedisSemanticCache(url="redis://fake:0", threshold=0.80, ttl_s=0, client=fake)
    cache.store(_EMB_SIMILAR, _answer("a"), domain="d1")
    assert fake._ttl == {}


def test_redis_fail_open_on_lookup() -> None:
    fake = FakeRedis()
    cache = RedisSemanticCache(url="redis://fake:0", threshold=0.80, ttl_s=0, client=fake)
    cache.store(_EMB_SIMILAR, _answer("ok"), domain="x")
    fake._fail_next = True
    result = cache.lookup(_EMB_SIMILAR, threshold=0.80, domain="x")
    assert result is None
    assert cache.stats()["misses"] == 1


def test_redis_fail_open_on_store() -> None:
    fake = FakeRedis()
    cache = RedisSemanticCache(url="redis://fake:0", threshold=0.80, ttl_s=0, client=fake)
    fake._fail_next = True
    cache.store(_EMB_SIMILAR, _answer("should not crash"), domain="x")
    assert fake._store == {}


# --- tests: should_cache_text (3.4) -----------------------------------

def test_should_cache_text_rejects_empty() -> None:
    assert not should_cache_text("   ")
    assert not should_cache_text("")


def test_should_cache_text_rejects_refusal() -> None:
    assert not should_cache_text("Если контекста недостаточно — скажи об этом.")


def test_should_cache_text_accepts_normal() -> None:
    assert should_cache_text("Вот ответ по вашему вопросу.")
