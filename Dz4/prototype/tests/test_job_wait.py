"""Гард на ожидатель терминального статуса: потолок длинный, но молчание невозможно.

Класс дефекта, который закрывается этими тестами: либо ожидание короче реального времени
задачи и тест падает без причины, либо ожидание «на всякий случай» без потолка и тест
зависает. Оба варианта неверны, и оба проверяются здесь явно.
"""

from __future__ import annotations

import time

from tests.job_wait import JOB_WAIT_CEILING_S, TERMINAL_STATUSES, wait_for_terminal


def test_returns_immediately_when_already_terminal() -> None:
    calls = 0

    def fetch() -> dict[str, str]:
        nonlocal calls
        calls += 1
        return {"status": "succeeded"}

    state, elapsed = wait_for_terminal(fetch)
    assert state["status"] == "succeeded"
    assert calls == 1
    assert elapsed < 1.0


def test_polls_until_status_becomes_terminal() -> None:
    statuses = iter(["queued", "running", "running", "succeeded"])

    def fetch() -> dict[str, str]:
        return {"status": next(statuses)}

    state, _ = wait_for_terminal(fetch, interval_s=0.001)
    assert state["status"] == "succeeded"


def test_failed_status_is_terminal_and_returned_not_swallowed() -> None:
    """`failed` — терминальный статус: ждать дальше бессмысленно, но и «успеха» не будет.

    Именно этот случай отличает корректный ожидатель от «проглоть ошибку и ждать»: результат
    возвращается вызывающему, и падение происходит на утверждении о статусе.
    """
    state, _ = wait_for_terminal(lambda: {"status": "failed", "stage": "EXTRACT"})
    assert state["status"] == "failed"
    assert state["stage"] == "EXTRACT"


def test_timeout_returns_last_state_and_elapsed_without_raising() -> None:
    """Потолок исчерпан — возвращаем наблюдённое состояние, а не исключение и не «успех»."""
    state, elapsed = wait_for_terminal(
        lambda: {"status": "running", "stage": "EMBED"},
        timeout_s=0.05,
        interval_s=0.01,
    )
    assert state["status"] == "running"
    assert elapsed >= 0.05


def test_ceiling_is_generous_but_finite() -> None:
    """Потолок остаётся конечным: тест, который завис, обязан падать."""
    assert JOB_WAIT_CEILING_S >= 60.0
    assert JOB_WAIT_CEILING_S < float("inf")


def test_terminal_set_covers_cancelled() -> None:
    assert TERMINAL_STATUSES == {"succeeded", "failed", "cancelled"}


def test_polling_is_not_a_busy_loop() -> None:
    """Опрос обязан иметь интервал: без него ожидание съедает ядро и ускоряет падение."""
    started = time.monotonic()
    wait_for_terminal(lambda: {"status": "running"}, timeout_s=0.12, interval_s=0.05)
    assert time.monotonic() - started >= 0.1