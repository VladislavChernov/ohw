"""Гард вкладки «Обслуживание» (LP-06 бандла add-operator-maintenance-controls).

Пункт был помечен выполненным, хотя разметка `demo_ui/app.py` не импортировалась ни одним
тестом: 0 совпадений по `demo_ui.app` в `prototype/tests/`. Поэтому проверяемая часть
вынесена из Streamlit в чистые функции `maintenance_preview` и `maintenance_result_lines`,
а тест бьёт по ним — это и есть требование «до нажатия видно, что удаляется; после —
подсчёты из ответа».
"""

from __future__ import annotations

from graphrag_proto.demo_ui.app import maintenance_preview, maintenance_result_lines


def test_preview_names_domain_and_says_chunks_untouched() -> None:
    text = maintenance_preview("it")

    assert "it" in text
    assert "будет удалено" in text.lower()
    # Оператор должен знать границу предмета: уборка не трогает чанки.
    assert "чанк" in text.lower()
    assert "не трогает" in text.lower()


def test_preview_never_leaves_domain_blank_without_mark() -> None:
    assert "—" in maintenance_preview("")


def test_result_lines_show_actual_counts_not_just_success() -> None:
    response = {
        "job_id": "maintenance:abc",
        "mode": "dry_run",
        "skipped": False,
        "planned_relations": 2,
        "removed_relations": 2,
        "planned_nodes": 1,
        "removed_nodes": 1,
    }

    lines = maintenance_result_lines(response)
    joined = "\n".join(lines)

    assert "maintenance:abc" in joined
    for value in ("2", "1"):
        assert value in joined
    # Счётчики раздельные (ADR-047): узлы не должны попадать в счётчик связей.
    assert "Связи" in joined and "Узлы" in joined


def test_skipped_is_not_reported_as_nothing_to_delete() -> None:
    """Пропуск политики — «не разрешено», а не «удалять нечего». Смешивать нельзя,
    и это ровно тот случай, где молчание читалось бы как успех."""

    lines = maintenance_result_lines(
        {"job_id": "j", "mode": "never", "skipped": True, "planned_relations": 0}
    )
    joined = "\n".join(lines)

    assert "пропущен" in joined.lower()
    assert "не разрешено" in joined.lower()
    assert "Связи" not in joined, "при пропуске счётчики показывать нечего и нельзя"