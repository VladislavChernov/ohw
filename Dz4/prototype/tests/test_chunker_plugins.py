"""Entry-point плагины чанкер-стратегий (CONCEPT §2.6, docs/chunkers_guide.md §4).

Плагины не устанавливаются в тестах как пакеты: подменяется discovery
(`plugin_registry.map_plugins`) + регистрируется реальный модуль в `sys.modules`,
чтобы `importlib.metadata.EntryPoint.load()` сработал без установки.
"""

from __future__ import annotations

import sys
import types
from importlib.metadata import EntryPoint

import pytest

from graphrag_proto import plugin_registry
from graphrag_proto.ingestion_service.pipeline.chunker import (
    SlidingWindowChunker,
    build_chunker,
    build_chunker_for,
    list_chunkers,
)
from graphrag_proto.plugin_registry import PluginLoadError

GROUP = "graphrag.chunkers"


def _install_entry_point(monkeypatch, name: str, value: str) -> None:
    """Фейковый entry-point в каталоге группы без установки модуля."""
    ep = EntryPoint(name=name, value=value, group=GROUP)
    monkeypatch.setattr(
        plugin_registry,
        "map_plugins",
        lambda group: {name: ep} if group == GROUP else {},
    )


def _install_plugin(monkeypatch, name: str, module_name: str, factory) -> None:
    """Фейковый entry-point: `name` -> `module_name:build` + рабочий модуль."""
    mod = types.ModuleType(module_name)
    mod.build = factory
    sys.modules[module_name] = mod
    _install_entry_point(monkeypatch, name, f"{module_name}:build")


def _build_plugin(*, chunk_size: int = 10, overlap: int = 2) -> SlidingWindowChunker:
    return SlidingWindowChunker(chunk_size=chunk_size, overlap=overlap)


def _clear_env(monkeypatch) -> None:
    for key in (
        "INGEST_CHUNKER",
        "INGEST_CHUNK_SIZE",
        "INGEST_CHUNK_OVERLAP",
        "CONFIG_URL",
        "NAMESPACES_PATH",
    ):
        monkeypatch.delenv(key, raising=False)


def test_plugin_resolved_via_env(monkeypatch) -> None:
    _clear_env(monkeypatch)
    _install_plugin(monkeypatch, "my_splitter", "fake_plugin_mod1", _build_plugin)
    monkeypatch.setenv("INGEST_CHUNKER", "my_splitter")
    monkeypatch.setenv("INGEST_CHUNK_SIZE", "10")
    monkeypatch.setenv("INGEST_CHUNK_OVERLAP", "2")
    chunker = build_chunker()
    assert isinstance(chunker, SlidingWindowChunker)
    assert chunker.chunk("a b c d e") == ["a b c d e"]


def test_plugin_resolved_via_profile_per_job(monkeypatch) -> None:
    _clear_env(monkeypatch)
    _install_plugin(monkeypatch, "my_splitter", "fake_plugin_mod2", _build_plugin)
    monkeypatch.setattr(
        "graphrag_proto.ingestion_service.pipeline.chunker._fetch_profile",
        lambda domain, config_url: {
            "chunking": {"strategy": "my_splitter", "chunk_size": 16, "overlap": 4}
        },
    )
    chunker = build_chunker_for("it", config_url="http://config-service:8001")
    assert isinstance(chunker, SlidingWindowChunker)
    assert chunker._chunk_size == 16
    assert chunker._overlap == 4


def test_builtin_beats_plugin_with_same_name(monkeypatch) -> None:
    _clear_env(monkeypatch)
    # "вредный" плагин с именем встроенной стратегии — ядро его игнорирует
    _install_plugin(monkeypatch, "sliding_window", "fake_plugin_mod3", lambda **kw: "not-a-chunker")
    chunker = build_chunker()
    assert isinstance(chunker, SlidingWindowChunker)


def test_plugin_broken_import_fails_fast(monkeypatch) -> None:
    _clear_env(monkeypatch)
    # модуль НЕ регистрируется в sys.modules — импорт обязан упасть
    _install_entry_point(monkeypatch, "broken", "no_such_module_xyz:build")
    monkeypatch.setenv("INGEST_CHUNKER", "broken")
    with pytest.raises(PluginLoadError):
        build_chunker()


def test_plugin_factory_wrong_type_fails_fast(monkeypatch) -> None:
    _clear_env(monkeypatch)
    _install_plugin(monkeypatch, "bad_type", "fake_plugin_mod4", lambda **kw: "not-a-chunker")
    monkeypatch.setenv("INGEST_CHUNKER", "bad_type")
    with pytest.raises(PluginLoadError):
        build_chunker()


def test_plugin_factory_error_wrapped_as_plugin_error(monkeypatch) -> None:
    """P-2: фабрика с неверным контрактом → PluginLoadError, не TypeError."""
    def _bad_factory(**kwargs):
        raise RuntimeError("boom")

    _clear_env(monkeypatch)
    _install_plugin(monkeypatch, "bad_factory", "fake_plugin_mod7", _bad_factory)
    monkeypatch.setenv("INGEST_CHUNKER", "bad_factory")
    with pytest.raises(PluginLoadError, match="boom"):
        build_chunker()


def test_unknown_strategy_message_lists_plugins(monkeypatch) -> None:
    _clear_env(monkeypatch)
    _install_plugin(monkeypatch, "my_splitter", "fake_plugin_mod5", _build_plugin)
    monkeypatch.setenv("INGEST_CHUNKER", "magic")
    with pytest.raises(ValueError) as exc_info:
        build_chunker()
    assert "magic" in str(exc_info.value)
    assert "my_splitter" in str(exc_info.value)
    assert "sliding_window" in str(exc_info.value)


def test_list_chunkers_combines_builtins_and_plugins(monkeypatch) -> None:
    _install_plugin(monkeypatch, "my_splitter", "fake_plugin_mod6", _build_plugin)
    names = list_chunkers()
    assert "sliding_window" in names
    assert "structure_aware" in names
    assert "my_splitter" in names
    # конфликтующее имя встроенной стратегии НЕ попадает в список/резолюцию
    assert names.count("sliding_window") == 1