"""Гард видимости потери LLM-слоя в Demo-UI (ADR-066).

Тот же приём, что в `test_demo_ui_maintenance.py`: разметка `app.py` не импортируется
ни одним тестом, поэтому проверяемая часть вынесена в чистую функцию
`enrichment_loss_lines`, а тест бьёт по ней.

Проверяется не «функция возвращает текст», а содержательные требования: потеря названа
числами, названа причина, сказано про no-op повторной загрузки, и — главное — **признак
потери не путается с успехом**.
"""

from __future__ import annotations

from typing import Any

import pytest

from graphrag_proto.demo_ui.app import (
    ENRICHMENT_LOST_CAUSE_NOTE,
    enrichment_loss_lines,
    model_response_fragment,
)
from graphrag_proto.retrieval.adapters.llm import RESPONSE_EXCERPT_MARKER

CLEAN_JOB: dict[str, Any] = {
    "status": "succeeded",
    "enrichment": {"cause": None, "lost_entities": 0, "lost_edges": 0},
}

LOOPED_JOB: dict[str, Any] = {
    "status": "succeeded",
    "enrichment": {
        "cause": "model_error",
        "lost_entities": 112,
        "lost_edges": 133,
        "llm_relations_declared": 133,
        "llm_relations_unique": 40,
        "llm_answer_lines": 1398,
        "llm_answer_lines_unique": 456,
    },
}


def test_no_loss_produces_no_lines() -> None:
    """Успешная джоба без потерь не обязана пугать человека."""
    assert enrichment_loss_lines(CLEAN_JOB) == []


def test_missing_enrichment_is_treated_as_no_loss_not_as_unknown() -> None:
    """Отсутствие блока - это «слой не теряли», а не «не знаем».

    Обратное толкование заставило бы UI показывать предупреждение у каждой джобы без LLM,
    и предупреждение быстро перестали бы читать.
    """
    assert enrichment_loss_lines({"status": "succeeded"}) == []
    assert enrichment_loss_lines({"enrichment": None}) == []


def test_loss_is_named_with_counts_and_cause() -> None:
    """Потеря обязана называться числами и причиной, иначе это просто слово «деградация»."""
    text = " ".join(enrichment_loss_lines(LOOPED_JOB))
    assert "112" in text, "число потерянных сущностей обязано быть видно"
    assert "133" in text, "число потерянных связей обязано быть видно"
    assert "model_error" in text, "причина потери обязана называться"


def test_loss_says_reload_of_same_file_will_not_help() -> None:
    """Без этого человек перезапустит загрузку и решит, что починил."""
    assert any(ENRICHMENT_LOST_CAUSE_NOTE in line for line in enrichment_loss_lines(LOOPED_JOB))


def test_loss_points_at_repetition_when_declared_differ_from_unique() -> None:
    """Причина зацикливания видна по числам, и её надо назвать, а не оставить загадкой.

    Объявлено 133 связи, уникальных 40 - модель повторялась. Это ровно тот случай, где
    «поднимите max_tokens» не помогает, и сказать об этом заранее дешевле повторной загрузки.
    """
    text = " ".join(enrichment_loss_lines(LOOPED_JOB))
    assert "133" in text and "40" in text
    assert "повторение" in text.lower()


def test_equal_declared_and_unique_do_not_claim_repetition() -> None:
    """Нет повторения - нет и утверждения о нём.

    Объявлять причину, которой нет, хуже, чем не объявлять никакой: человек начнёт искать
    несуществующее зацикливание.
    """
    job = {
        "status": "succeeded",
        "enrichment": {
            "cause": "model_http_error",
            "lost_entities": 5,
            "lost_edges": 7,
            "llm_relations_declared": 50,
            "llm_relations_unique": 50,
        },
    }
    text = " ".join(enrichment_loss_lines(job))
    assert "повторение" not in text.lower()


def test_succeeded_with_loss_is_still_a_loss() -> None:
    """Статус `succeeded` не отменяет потерю.

    Ровно тот случай, из-за которого потеря молчала: джоба числится успешной, и любая
    проверка по статусу её пропустит.
    """
    assert LOOPED_JOB["status"] == "succeeded"
    assert enrichment_loss_lines(LOOPED_JOB)


@pytest.mark.parametrize("bad", [None, "text", 42])
def test_non_dict_enrichment_is_not_treated_as_loss(bad: Any) -> None:
    """Мусор в блоке не превращается в потерю: придумывать её нельзя."""
    assert enrichment_loss_lines({"enrichment": bad}) == []


# Фрагмент берётся из реального ответа модели на `05_adr_log` от 18.10: она зациклилась на
# `RECORDING_*` и ответ не закрылся. Формат ответа - контракт теста, а не выдумка.
TRUNCATED_RESPONSE = (
    "```json\n{\n  \"requirements\": [\n"
    '    {"canonical_name": "RECORDING_ACTIVITY", "id": "RECORDING_ACTIVITY"},\n'
    '    {"canonical_name": "RECORDING_ALERTS", "id": "RECORDING_ALERTS"},\n'
    '    {"canonical_name": "RECORDING_ACTIVITY", "id": "RECORDING_ACTIVITY"},\n'
    '    {"canonical_name": "RECORDING_ACTIVITY", "id": "RECORDING_ACTIVITY"\n'
)


def _job_with_response(message: str) -> dict[str, Any]:
    return {
        "status": "succeeded",
        "enrichment": {"cause": "model_error", "lost_entities": 190, "lost_edges": 127},
        "stages": [
            {"stage": "INGEST", "status": "succeeded", "message": ""},
            {
                "stage": "CONTRACT",
                "status": "succeeded",
                "message": f"enrichment_degraded: ответ LLM неполон\n{RESPONSE_EXCERPT_MARKER}\n{message}",
            },
        ],
    }


def test_response_fragment_is_shown_when_the_response_is_truncated() -> None:
    """Человек должен видеть, что модель наговорила, а не только что счётчики испорчены.

    Без ответа вопрос «почему» упирается в «нужно ещё раз прогнать», а прогон стоит
    минут тринадцать.
    """
    fragment = model_response_fragment(_job_with_response(TRUNCATED_RESPONSE))
    assert "RECORDING_ACTIVITY" in fragment
    assert RESPONSE_EXCERPT_MARKER not in fragment, "маркер не должен попадать в вывод"


def test_response_fragment_keeps_the_cut_json_as_is() -> None:
    """Обрезанный JSON показывается обрезанным.

    Приводить его в порядок нельзя: человек должен увидеть, что скобка не закрыта, - это и
    есть доказательство обрыва. «Починенный» фрагмент врал бы.
    """
    fragment = model_response_fragment(_job_with_response(TRUNCATED_RESPONSE))
    assert fragment.count("{") > fragment.count("}")
    assert fragment.rstrip().endswith('"RECORDING_ACTIVITY"')


def test_no_marker_means_no_fragment() -> None:
    """Ответ есть не всегда: транспортный отказ не несёт фрагмента, и придумывать его нельзя."""
    job = {
        "status": "succeeded",
        "enrichment": {"cause": "model_http_error", "lost_entities": 5, "lost_edges": 7},
        "stages": [{"stage": "CONTRACT", "status": "succeeded", "message": "enrichment_degraded: таймаут"}],
    }
    assert model_response_fragment(job) == ""


def test_missing_stages_is_not_an_error() -> None:
    """Джоба без стадий - обычное состояние до старта, а не повод показывать пустоту."""
    assert model_response_fragment({}) == ""
    assert model_response_fragment({"stages": None}) == ""
    assert model_response_fragment({"stages": ["текст вместо словаря"]}) == ""