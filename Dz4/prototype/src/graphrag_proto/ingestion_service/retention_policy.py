"""Политика версий данных: режим развёртывания и офлайновая уборка.

Политика — свойство **развёртывания**, а не профиля: границы профиля заданы закрытым
списком в `docs/01` §4, и политика данных туда сознательно не входит. Решение владельца
и обоснование — `CONCEPT.md` §5.1, `docs/02` §4.3–4.5, `docs/06` §2.4.

Модуль существует ради одной вещи: **решение о политике принимается в одном месте**. Иначе
каждый новый вызов уборки и каждая будущая настройка решают его заново, и рано или поздно
разъезжаются. Всё, что касается признаков и отбора, живёт в `delete_orphans` адаптера;
здесь только режим и сам порядок работы.
"""

from __future__ import annotations

import os
from typing import Any, Literal

# Ровно два значения. Алгебра режимов не нужна и вредна: режим, который нельзя выразить
# одним словом, невозможно и отладить.
RetentionMode = Literal["current_only", "keep_history"]

#: Режим по умолчанию. Молчание должно означать безопасное поведение, а не «гигиену нужно
#: включить руками»: безопасный режим, который надо явно не включить, на практике
#: выключают один раз и забывают. `docs/02` §4.4.
DEFAULT_RETENTION_MODE: RetentionMode = "current_only"

#: Имя переменной окружения. Env — это дефолтный источник значения; хранилищем остаётся
#: Config Service, а конфигуратор (M5+) будет его клиентом. Логика уборки не знает, откуда
#: пришло значение, и не должна узнавать.
RETENTION_MODE_ENV = "DATA_RETENTION_MODE"

#: Что предупредить, если режим хранит историю, а развёртывание её не обеспечивает.
#: Предупреждение, а не ошибка: развёртывание, которое ничего не трогает, ломать нельзя.
ARCHIVE_REQUIRED_MODES = frozenset({"keep_history"})


class RetentionPolicy:
    """Разрешает режим политики и однажды предупреждает о небезопасной комбинации.

    Предупреждение выдаётся один раз на инстанс: иначе фоновая задача, проходящая
    регулярно, засоряет лог на каждом проходе, и предупреждение перестаёт быть заметным.
    """

    def __init__(self, mode: str | None = None) -> None:
        self._requested = mode if mode is not None else os.environ.get(RETENTION_MODE_ENV)
        self.mode: RetentionMode = self._resolve(self._requested)
        self._warned = False

    @staticmethod
    def _resolve(value: str | None) -> RetentionMode:
        """Неизвестное значение — дефолт, а не исключение.

        Опечатка в переменной окружения не должна поднимать сервис: безопасный дефолт
        означает, что худший случай при разборе — лишняя уборка мусора, а не его накопление.
        """
        if value is None:
            return DEFAULT_RETENTION_MODE
        normalized = str(value).strip().lower()
        if normalized in {"current_only", "current-only", "currentonly"}:
            return "current_only"
        if normalized in {"keep_history", "keep-history", "keephistory"}:
            return "keep_history"
        return DEFAULT_RETENTION_MODE

    @property
    def drops_orphans(self) -> bool:
        """Удалять ли осиротевшие связи и узлы в этом режиме."""
        return self.mode == "current_only"

    def check_archive(self) -> str | None:
        """Предупреждение о режиме без архива, один раз за инстанс.

        Система не имеет права считать, что внешний VCS или иной архив существует: у
        развёртывания без него «хранить историю» означает копить то, что никто не прочитает.
        """
        if self.mode in ARCHIVE_REQUIRED_MODES and not self._warned:
            self._warned = True
            return (
                f"{RETENTION_MODE_ENV}={self.mode}, но архив версий не подтверждён: "
                "история будет накапливаться без возможности её предъявить. "
                "Если архива нет - используйте current_only."
            )
        return None


def run_orphan_cleanup(
    graph_store: Any,
    fact_store: Any,
    *,
    job_id: str,
    domain: str,
    policy: RetentionPolicy | None = None,
) -> dict[str, Any]:
    """Один проход уборки: подсчёт, затем удаление, затем факт в реестре.

    `fact_store` - именно `JobStore` (`record_orphan_cleanup` живёт там, а не в
    `DocumentRegistry`). Параметр называется `fact_store`, а не `registry` сознательно:
    под старым именем в маршруте был передан `DocumentRegistry`, у которого такого метода
    нет, и вызов падал бы на записи факта - то есть уборка прошла бы, а след остался бы
    нет. Дыру не нашёл ни один тест, потому что вызывающего кода не существовало.

    Порядок зафиксирован в `docs/02` §4.5 и не переставляется:

    1. **`dry_run` сначала.** Цена ошибки предиката — молчаливая потеря данных в графе, и
       ни одна транзакция её не откатит, потому что команда исполнилась честно. Неверный
       предикат должен быть виден на этом прогоне.
    2. **Повторный подсчёт перед удалением.** Между проходами может прийти другой ingest и
       изменить граф, после чего удалится не то, что было посчитано. Уборка фоновая и
       транзакцию ingest'а не держит, поэтому дешевле пересчитать, чем блокировать запись.
    3. **Факт пишется после удаления, в том же вызове.** Сбой между коммитом удаления и
       записью факта дал бы правдоподобный ноль, который на следующем проходе читается как
       «удалять нечего» — то есть правдоподобно и неверно.

    Возвращает словарь с подсчётами; при выключенном режиме ничего не удаляется и
    `skipped` равен `True`.
    """
    active = policy or RetentionPolicy()
    warning = active.check_archive()
    if warning is not None:
        import logging

        logging.getLogger(__name__).warning("%s", warning)

    if not active.drops_orphans:
        return {
            "mode": active.mode,
            "skipped": True,
            "planned_relations": 0,
            "removed_relations": 0,
            "planned_nodes": 0,
            "removed_nodes": 0,
        }

    planned = int(graph_store.delete_orphans(domain, dry_run=True))
    removed = int(graph_store.delete_orphans(domain, dry_run=False))
    fact = {
        "mode": active.mode,
        "skipped": False,
        "planned_relations": planned,
        "removed_relations": removed,
        "planned_nodes": 0,
        "removed_nodes": 0,
    }
    if fact_store is not None:
        fact_store.record_orphan_cleanup(
            job_id,
            domain,
            planned_relations=planned,
            removed_relations=removed,
            planned_nodes=fact["planned_nodes"],
            removed_nodes=fact["removed_nodes"],
            mode=active.mode,
        )
    return fact
