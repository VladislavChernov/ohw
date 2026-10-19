"""Ожидание терминального статуса асинхронной джобы — общий для всех тестов.

Ingestion отвечает `202` («поставлено в очередь»), поэтому результат джобы нужно дождаться.
Класс дефекта, который этот модуль закрывает: ожидание было привязано к короткому бюджету
времени (5 с и 10 с в четырёх местах), и на прогоне под нагрузкой тесты падали не из-за
системы, а из-за того, что не успели. Проверять следует инвариант «джоба дошла до
терминального статуса», а не «уложилась в N секунд».

Потолок остаётся: тест, который завис, обязан падать. Но 120 с — это потолок для
диагностики, а не рабочий бюджет, и при его исчерпании сообщение обязано называть последний
наблюдённый статус и прошедшее время, иначе падение невозможно отличить от «джоба не пошла».
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled"})

JOB_WAIT_CEILING_S = 120.0


def wait_for_terminal(
    fetch: Callable[[], dict[str, Any]],
    *,
    timeout_s: float = JOB_WAIT_CEILING_S,
    interval_s: float = 0.05,
) -> tuple[dict[str, Any], float]:
    """Дождаться терминального статуса; вернуть последнее состояние и прошедшее время.

    Потолок не «съедает» тест: по его исчерпании возвращается текущее состояние, и вызывающий
    код падает на своём утверждении, где в сообщении видно и состояние, и прошедшее время.
    """
    state = fetch()
    started = time.monotonic()
    while str(state.get("status")) not in TERMINAL_STATUSES:
        if time.monotonic() - started >= timeout_s:
            return state, time.monotonic() - started
        time.sleep(interval_s)
        state = fetch()
    return state, time.monotonic() - started


def wait_until(
    condition: Callable[[], bool],
    *,
    timeout_s: float = JOB_WAIT_CEILING_S,
    interval_s: float = 0.1,
) -> bool:
    """Общий опрос произвольного условия с тем же потолком, что и у джоб.

    Отдельная функция, а не обёртка над `wait_for_terminal`: условия здесь бывают не про
    статус (например, появление строки в отчёте), и приводить их к словарю нельзя.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval_s)
    return False