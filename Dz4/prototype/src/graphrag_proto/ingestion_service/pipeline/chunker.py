"""Чанкеры: фрагментация текста блока в чанки (CHUNK, ADR-021).

Единицей входа является блок (text|code) канонического Document — блоки уже
разделены ридером и обрабатываются независимо (code-блоки не рвутся).

ABC `Chunker` повторяет паттерн `Embedder`/`Reranker` (docs/adapters_specification.md §2):
хранилища и модели подменяются без правки этапов. `ChunkStage` получает chunker
через DI; дефолт — `SlidingWindowChunker(512, 64)` (историческое поведение M1).
"""

from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from typing import Any, ClassVar, cast

from graphrag_proto.plugin_registry import (
    PluginLoadError,
    PluginMissingError,
    list_plugins,
    load_plugin,
)

DEFAULT_CHUNK_SIZE = 512
DEFAULT_CHUNK_OVERLAP = 64

MARKDOWN_HEADER = re.compile(r"^\s{0,3}#{1,6}\s+.*$")

ALLOWED_CHUNKERS = ("sliding_window", "structure_aware", "langchain", "llamaindex")

CHUNKER_ENTRY_POINT_GROUP = "graphrag.chunkers"


def list_chunkers() -> tuple[str, ...]:
    """Все резолвимые имена стратегий: встроенные + плагины (без конфликтующих).

    Имя плагина, совпадающее со встроенной стратегией, не резолвится
    (встроенные имеют приоритет — детерминированное поведение ядра).
    """
    builtins = tuple(ALLOWED_CHUNKERS)
    plugins = tuple(name for name in list_plugins(CHUNKER_ENTRY_POINT_GROUP) if name not in builtins)
    return builtins + plugins


class Chunker(ABC):
    """Фрагментация текста одного блока в список чанков."""

    @abstractmethod
    def chunk(self, text: str) -> list[str]:
        """Чанки текста блока; пустая строка → пустой список."""


class SlidingWindowChunker(Chunker):
    """Скользящее окно по словам (историческое поведение M1: 512/64)."""

    def __init__(self, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size должен быть > 0")
        if overlap < 0 or overlap >= chunk_size:
            raise ValueError("overlap должен быть в [0, chunk_size)")
        self._chunk_size = chunk_size
        self._overlap = overlap

    def chunk(self, text: str) -> list[str]:
        if not text.strip():
            return []
        words = text.split(" ")
        if len(words) <= self._chunk_size:
            return [text]
        chunks: list[str] = []
        step = max(self._chunk_size - self._overlap, 1)
        for i in range(0, len(words), step):
            chunk = " ".join(words[i : i + self._chunk_size])
            if chunk.strip():
                chunks.append(chunk)
        return chunks


class StructureAwareChunker(Chunker):
    """Пер-секционный чанкинг по заголовкам Markdown.

    Сначала текст блока нарезается на секции по заголовкам (см. `MARKDOWN_HEADER`).
    Каждая секция чанкится отдельно: короткие секции → один чанк целиком,
    длинные — скользящим окном в пределах секции (заголовок главы не уезжает
    от своего текста). Для текста без заголовков — вырождается в sliding window
    (fallback руками: StructureAwareChunker не требует структуры).
    """

    def __init__(self, chunk_size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_CHUNK_OVERLAP) -> None:
        self._sliding = SlidingWindowChunker(chunk_size=chunk_size, overlap=overlap)
        self._chunk_size = chunk_size

    def _split_sections(self, text: str) -> list[str]:
        """Нарезка на секции по заголовкам Markdown (^\\#{1,6}\\s)."""
        lines = text.splitlines()
        sections: list[str] = []
        current: list[str] = []
        for line in lines:
            if MARKDOWN_HEADER.match(line):
                if current:
                    sections.append("\n".join(current))
                    current = []
                current.append(line)
            else:
                current.append(line)
        if current:
            sections.append("\n".join(current))
        return [s for s in sections if s.strip()]

    def chunk(self, text: str) -> list[str]:
        if not text.strip():
            return []
        if len(text) <= self._chunk_size:
            return [text]
        sections = self._split_sections(text)
        if len(sections) <= 1:
            return self._sliding.chunk(text)
        chunks: list[str] = []
        for section in sections:
            if len(section) <= self._chunk_size:
                if section.strip():
                    chunks.append(section)
            else:
                chunks.extend(self._sliding.chunk(section))
        return chunks


class LangChainChunker(Chunker):
    """Адаптер LangChain text splitters (optional dependency).

    Выбранный класс подгружается лениво: `from langchain.text_splitter import <name>`.
    Если langchain не установлен — fail-fast с понятной ошибкой при построении.
    """

    SPLITTERS: ClassVar[dict[str, str]] = {
        "recursive": "RecursiveCharacterTextSplitter",
        "character": "CharacterTextSplitter",
        "token": "TokenTextSplitter",
    }

    def __init__(self, splitter: str = "recursive", chunk_size: int = DEFAULT_CHUNK_SIZE, chunk_overlap: int = DEFAULT_CHUNK_OVERLAP) -> None:
        if splitter not in self.SPLITTERS:
            raise ValueError(f"langchain splitter={splitter!r}: допустимо {', '.join(sorted(self.SPLITTERS))}")
        self._splitter = splitter
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._instance: Any = None

    @property
    def _client(self) -> Any:
        if self._instance is None:
            try:
                from langchain.text_splitter import (  # noqa: F401
                    CharacterTextSplitter,
                    RecursiveCharacterTextSplitter,
                    TokenTextSplitter,
                )
            except ImportError as exc:  # optional dependency, fail-fast
                raise RuntimeError("langchain не установлен (optional dep; установите `[chunking]`)") from exc
            cls = getattr(__import__("langchain.text_splitter", fromlist=[self.SPLITTERS[self._splitter]]), self.SPLITTERS[self._splitter])
            self._instance = cls(chunk_size=self._chunk_size, chunk_overlap=self._chunk_overlap)
        return self._instance

    def chunk(self, text: str) -> list[str]:
        if not text.strip():
            return []
        return cast(list[str], self._client.split_text(text))


class LlamaIndexChunker(Chunker):
    """Адаптер LlamaIndex node parsers (optional dependency, lazy import)."""

    PARSERS: ClassVar[dict[str, tuple[str, str]]] = {
        "sentence": ("llama_index.core.node_parser", "SentenceSplitter"),
        "semantic": ("llama_index.core.node_parser", "SemanticSplitterNodeParser"),
    }

    def __init__(self, parser: str = "sentence", chunk_size: int = DEFAULT_CHUNK_SIZE) -> None:
        if parser not in self.PARSERS:
            raise ValueError(f"llamaindex parser={parser!r}: допустимо {', '.join(sorted(self.PARSERS))}")
        self._parser = parser
        self._chunk_size = chunk_size
        self._instance: Any = None

    @property
    def _client(self) -> Any:
        if self._instance is None:
            module_name, class_name = self.PARSERS[self._parser]
            try:
                node_parser = __import__(module_name, fromlist=[class_name])
            except ImportError as exc:  # optional dependency, fail-fast
                raise RuntimeError("llama-index не установлен (optional dep; установите `[chunking]`)") from exc
            cls = getattr(node_parser, class_name)
            self._instance = cls(chunk_size=self._chunk_size)
        return self._instance

    def chunk(self, text: str) -> list[str]:
        if not text.strip():
            return []
        return cast(
            list[str],
            [node.get_content() for node in self._client.get_nodes_from_documents([_text_doc(text)])],
        )


def _text_doc(text: str) -> Any:
    """Обёртка текста в Document/SQLAlchemy-нейтральном виде для llama-index parsers."""
    try:
        from llama_index.core import Document
    except ImportError as exc:  # pragma: no cover - только при lazy-вызове без пакета
        raise RuntimeError("llama-index не установлен (optional dep; установите `[chunking]`)") from exc
    return Document(text=text)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def _namespaces_chunking() -> dict[str, object]:
    """Секция `chunking` из namespaces.yaml (SSOT дефолтов, docs/04 §5)."""
    import yaml

    path = os.environ.get("NAMESPACES_PATH", "infra/config/namespaces.yaml")
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return {}
    section = data.get("chunking") if isinstance(data, dict) else None
    return section if isinstance(section, dict) else {}


def _fetch_profile(domain: str, config_url: str) -> dict[str, object]:
    """Профиль домена из Config Service (`GET /api/v1/config/domain/profile/{domain}`).

    Недоступность Config Service не валит ingest (чанкинг — некритичная настройка):
    пустой результат → fallback на namespaces/дефолт (паттерн Glossary fallback).
    """
    import json
    import os
    import urllib.request

    headers = {}
    api_key = os.environ.get("AUTH_API_KEY") or os.environ.get("GRAPH_AUTH_API_KEY", "")
    if api_key:
        headers["X-API-Key"] = api_key
    url = urllib.request.Request(
        f"{config_url.rstrip('/')}/api/v1/config/domain/profile/{domain}",
        headers=headers,
        method="GET",
    )
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - Config недоступен -> дефолтные настройки
        return {}
    return payload if isinstance(payload, dict) else {}


def _strategy_values(domain: str = "", config_url: str = "") -> dict[str, str | int]:
    """Настройки чанкинга с per-field precedence: env > профиль домена > namespaces > дефолты.

    Каждое поле берётся из самого специфичного источника, где оно задано:
    - env: `INGEST_CHUNKER`, `INGEST_CHUNK_SIZE`, `INGEST_CHUNK_OVERLAP`;
    - профиль домена: секция `chunking` (Config Service, если config_url задан);
    - namespaces.yaml: секция `chunking`;
    - иначе — исторические значения M1 (512/64, sliding_window).
    """
    ns = _namespaces_chunking()
    profile = _fetch_profile(domain, config_url) if config_url else {}
    if isinstance(profile, dict):
        profile_section = profile.get("chunking")
    else:
        profile_section = {}
    # источники от более специфичного к менее: env(значения выше) > профиль > namespaces.
    # поле заполняется из первого источника, где оно задано (fill-if-empty).
    strategy = _env("INGEST_CHUNKER", "").strip().lower()
    chunk_size = _env_int("INGEST_CHUNK_SIZE", 0)
    overlap = _env_int("INGEST_CHUNK_OVERLAP", -1)
    for section in (profile_section if isinstance(profile_section, dict) else {}, ns):
        if not strategy and section.get("strategy"):
            strategy = str(section["strategy"]).strip().lower()
        if not chunk_size and section.get("chunk_size"):
            chunk_size = int(str(section["chunk_size"]))
        if overlap < 0 and section.get("overlap") is not None:
            overlap = int(str(section["overlap"]))

    if not strategy:
        strategy = "sliding_window"
    if not chunk_size:
        chunk_size = DEFAULT_CHUNK_SIZE
    if overlap < 0:
        overlap = DEFAULT_CHUNK_OVERLAP
    return {"strategy": strategy, "chunk_size": chunk_size, "overlap": overlap}


def _build_chunker(
    strategy: str,
    chunk_size: int,
    overlap: int,
) -> Chunker:
    strategy = strategy.strip().lower()
    if strategy == "sliding_window":
        return SlidingWindowChunker(chunk_size=chunk_size, overlap=overlap)
    if strategy == "structure_aware":
        return StructureAwareChunker(chunk_size=chunk_size, overlap=overlap)
    if strategy == "langchain":
        return LangChainChunker(
            splitter=_env("INGEST_LANGCHAIN_SPLITTER", "recursive"),
            chunk_size=chunk_size,
            chunk_overlap=overlap,
        )
    if strategy == "llamaindex":
        return LlamaIndexChunker(
            parser=_env("INGEST_LLAMAINDEX_PARSER", "sentence"),
            chunk_size=chunk_size,
        )
    chunker = _build_chunker_from_plugin(strategy, chunk_size=chunk_size, overlap=overlap)
    if chunker is not None:
        return chunker
    raise ValueError(f"strategy={strategy!r}: допустимо {', '.join(list_chunkers())}")


def _build_chunker_from_plugin(
    strategy: str,
    *,
    chunk_size: int,
    overlap: int,
) -> Chunker | None:
    """Сборка стратегии из entry-point плагина (группа `graphrag.chunkers`).

    Возвращает `None`, если имя не принадлежит ни одному установленному плагину
    (тогда вызывающий завершает ошибкой). Сбой самого плагина — `PluginLoadError`
    без тихого fallback на sliding_window (иначе маскируется ошибка конфигурации).
    """
    try:
        factory = load_plugin(CHUNKER_ENTRY_POINT_GROUP, strategy)
    except PluginMissingError:
        return None
    try:
        chunker = factory(chunk_size=chunk_size, overlap=overlap)
    except Exception as exc:
        raise PluginLoadError(
            f"плагин {strategy!r}: фабрика не собрала Chunker при вызове "
            f"factory(chunk_size=..., overlap=...): {exc}"
        ) from exc
    if not isinstance(chunker, Chunker):
        raise PluginLoadError(
            f"плагин {strategy!r}: фабрика вернула не Chunker, а {type(chunker).__name__}"
        )
    return chunker


def build_chunker() -> Chunker:
    """Сборка chunker'а текущего процесса (env/namespaces-дефолты; профиль не тянется).

    Используется на старте (без доменного контекста) — историческая точка входа;
    per-job резолвер с профилем — `build_chunker_for`.
    """
    settings = _strategy_values()
    return _build_chunker(
        str(settings["strategy"]),
        int(settings["chunk_size"]),
        int(settings["overlap"]),
    )


def build_chunker_for(domain: str, config_url: str | None = None) -> Chunker:
    """Per-job резолвер: env > профиль домена > namespaces > дефолты (вариант 3).

    `domain` — целевой домен джобы; `config_url` — Config Service (base URL).
    При `None` (вызов из ChunkStage) берётся из env `CONFIG_URL`; при пустом
    значении (тесты) профиль не опрашивается.
    """
    if config_url is None:
        config_url = os.environ.get("CONFIG_URL", "")
    settings = _strategy_values(domain=domain, config_url=config_url)
    return _build_chunker(
        str(settings["strategy"]),
        int(settings["chunk_size"]),
        int(settings["overlap"]),
    )