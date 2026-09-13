"""Pluggable registry поверх Python entry points (CONCEPT §2.6).

Единый механизм discovery подключаемых компонентов ядра: группа entry-point'ов
(например ``graphrag.chunkers``) отображается в карту имя → фабрика-загрузчик.
Используется чанкерами; в перспективе — слоты адаптеров (``graphrag.adapters.*``).
"""

from __future__ import annotations

import importlib.metadata
from collections.abc import Callable
from functools import cache
from typing import Any, cast

__all__ = (
    "PluginLoadError",
    "PluginMissingError",
    "clear_cache",
    "list_plugins",
    "load_plugin",
    "map_plugins",
)


class PluginMissingError(RuntimeError):
    """Entry point с указанным именем не найден среди установленных пакетов."""


class PluginLoadError(RuntimeError):
    """Entry point найден, но модуль-фабрика недоступен, не callable
    или возвращает объект неожиданного типа."""


@cache
def map_plugins(group: str) -> dict[str, importlib.metadata.EntryPoint]:
    """Карта ``{имя: entry-point}`` для установленных пакетов группы ``group``.

    Результат кэшируется на процесс. Для тестов / горячей перезагрузки
    используйте :func:`clear_cache`.
    """
    return {ep.name: ep for ep in importlib.metadata.entry_points(group=group)}


def list_plugins(group: str) -> tuple[str, ...]:
    """Имена доступных плагинов группы (без загрузки модулей)."""
    return tuple(sorted(map_plugins(group)))


def load_plugin(group: str, name: str) -> Callable[..., Any]:
    """Загрузить фабрику из entry-point ``name`` группы ``group``.

    Raises:
        PluginMissingError: entry point не найден.
        PluginLoadError: модуль недоступен или не является callable.
    """
    eps = map_plugins(group)
    ep = eps.get(name)
    if ep is None:
        raise PluginMissingError(
            f"entry-point {name!r} группы {group!r} не зарегистрирован "
            f"(доступно: {', '.join(sorted(eps)) or 'нет'})"
        )
    try:
        obj = ep.load()
    except Exception as exc:
        raise PluginLoadError(
            f"плагин {name!r} группы {group!r}: "
            f"не удалось импортировать {ep.value!r}"
        ) from exc
    if not callable(obj):
        raise PluginLoadError(
            f"плагин {name!r}: entry point {ep.value!r} "
            f"не является фабрикой (callable), тип: {type(obj).__name__}"
        )
    return cast(Callable[..., Any], obj)


def clear_cache(group: str | None = None) -> None:
    """Очистить кэш discovery (для тестов / горячей перезагрузки).

    Если ``group`` указан — очищается весь кэш (lru_cache не поддерживает
    частичную инвалидацию по аргументу).
    """
    map_plugins.cache_clear()