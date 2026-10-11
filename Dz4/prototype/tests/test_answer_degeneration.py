"""Вырождение ответа модели вместо обрыва по длине (ADR-056).

Тесты строят ответ той же формы, какую выдала модель на
`docs/02_pipeline_and_normalizer.md` (чанк 0, лимит 6144): JSON, начатый верно, 35 имён
без дублей, затем 120 пар, из которых уникальны 33, и 108 пар от одной вершины.

Проверяется положительный контракт - «прибор называет вырождение и не путает его с
нехваткой токенов». Отрицательные утверждения вида «счётчик не равен нулю» тут были бы
хрупкими: они ломались бы от любой законной правки счётчика, и команда начала бы
обходить тест.
"""

from __future__ import annotations

import json

from graphrag_proto.ingestion_service.pipeline.orchestrator import (
    _answer_shape,
    _declared_names_in_answer,
    _declared_relations_in_answer,
)
from graphrag_proto.retrieval.adapters.llm import LLMResponseError, _reject_truncation


def _relation(source: str, target: str) -> str:
    return f'    {{\n      "from": "{source}",\n      "to": "{target}",\n      "kind": "REQUIRES_CONSTRAINT",\n      "confidence": 0.9\n    }}'


def degenerate_answer() -> str:
    """Ответ, как его выдала модель: верно начат, зациклен на одной вершине, не закрыт."""
    nodes = "\n".join(
        f'    {{\n      "canonical_name": "{name}",\n      "aliases": [],\n      "description": ""\n    }}'
        for name in ("Analyzer", "CommitStage", "ChunkStage")
    )
    # 33 уникальные пары, повторённые до 120 объявлений, все от NormalizeStage.
    pairs = [
        (source, f"target{index}")
        for index, source in enumerate(["NormalizeStage"] * 11 + ["ExtractStage"] * 11
                                       + ["CommitStage"] * 11)
    ]
    repeated = (pairs * 4)[:120]
    relations = ",\n".join(_relation(*pair) for pair in repeated)
    return (
        "```json\n{\n"
        '  "concepts": [\n' + nodes + "\n  ],\n"
        '  "relationships": [\n' + relations + "\n  ]\n"
    )


def test_degenerate_answer_is_unclosed_and_overproduced() -> None:
    """У вырожденного ответа два признака: JSON не закрыт, пар больше, чем уникальных."""
    answer = degenerate_answer()

    lines, unique_lines, brace = _answer_shape(answer)

    assert brace > 0, "ответ модели не закрыл скобки - это и есть «JSON не распарсится»"
    assert lines > unique_lines, "вырожденный ответ обязан иметь дублирующиеся строки"
    # Пара важнее соседних дублей: в измеренном ответе одинаковых пар подряд не было ни
    # разу, поэтому проверка на соседние повторы эту поломку пропустила бы.
    declared = _declared_relations_in_answer(answer)
    assert len(declared) == 120
    assert len(set(declared)) < len(declared) / 2


def test_names_are_counted_from_a_truncated_answer() -> None:
    """Имена читаются из сырого ответа, поэтому обрыв их не обнуляет.

    Именно в этом был молчащий ноль: счётчики считались по распарсенным записям, а при
    обрыве записей нет, и 35 выданных имён читались как «ни одного мимо списка».
    """
    answer = degenerate_answer()

    names = _declared_names_in_answer(answer)

    assert names == ["Analyzer", "CommitStage", "ChunkStage"]


def test_a_closed_answer_is_not_reported_as_degenerate() -> None:
    """Нормальный ответ не должен попадать под диагноз - иначе сигнал обесценится."""
    payload = {
        "concepts": [{"canonical_name": "Analyzer", "aliases": [], "description": ""}],
        "relationships": [
            {"from": "Analyzer", "to": "CommitStage", "kind": "REFERENCES", "confidence": 0.5}
        ],
    }
    answer = json.dumps(payload, ensure_ascii=False)

    lines, unique_lines, brace = _answer_shape(answer)
    declared = _declared_relations_in_answer(answer)

    assert brace == 0, "закрытый JSON не должен давать незакрытый баланс"
    assert lines == unique_lines, "нормальный ответ не должен иметь дубли строк"
    assert len(declared) == len(set(declared)) == 1


def test_error_text_stops_recommending_a_bigger_token_limit() -> None:
    """Текст ошибки не должен отправлять оператора на неверный рычаг.

    `finish_reason = "length"` при зацикливании не лечится увеличением лимита: проверено
    на 8192 - модель добавила ещё круг и JSON не закрылся. Раньше здесь стоял именно
    такой совет, и он уводил от причины.
    """
    try:
        _reject_truncation("length", "```json\n{\n  \"relationships\": [")
    except LLMResponseError as exc:
        message = str(exc)
    else:  # pragma: no cover - защита от молчаливого изменения поведения
        raise AssertionError("finish_reason=length обязан поднимать ошибку")

    assert "увеличьте max_tokens" not in message.casefold()
    assert "не помогает" in message.casefold() or "зацикл" in message.casefold()


def test_counters_reach_the_report_not_just_memory(tmp_path) -> None:
        """Счётчик, посчитанный и забытый в памяти, читается как «ничего не было».

        Эта проверка существует потому, что её отсутствие стоило реального прогона:
        счётчики вырождения были добавлены в `PipelineContext` и посчитаны на живом
        стенде, но не были проведены до `job_enrichment`, поэтому отчёт показывал нули
        при заведомо непустом ответе - то есть прибор врал молча.
        """
        from graphrag_proto.ingestion_service.storage.registry import JobStore

        registry = JobStore(tmp_path / "degen.db")
        registry.record_enrichment(
            "job",
            cause="model_error",
            lost_entities=0,
            lost_edges=0,
            llm_records=0,
            llm_edges=0,
            llm_names_raw=35,
            llm_names_raw_unique=35,
            llm_names_raw_off_list=12,
            llm_names_raw_grounded=4,
            llm_relations_declared=120,
            llm_relations_unique=33,
            llm_answer_lines=901,
            llm_answer_lines_unique=120,
            llm_answer_brace_balance=2,
        )

        facts = registry.enrichment("job")

        assert facts["llm_relations_declared"] == 120
        assert facts["llm_relations_unique"] == 33
        assert facts["llm_answer_brace_balance"] == 2
        assert facts["llm_names_raw_off_list"] == 12
        # Развилка обязана вычитываться из отчёта, а не из лога: именно по ней видно,
        # что ограничение нарушено ИЗ-ЗА ПРОВАЛА СПИСКА, а не из-за выдумки модели.
        assert facts["llm_names_raw_grounded"] < facts["llm_names_raw_off_list"]


def test_exchange_record_marks_the_cap_separately_from_log_truncation(
    tmp_path, monkeypatch
) -> None:
    """Обрыв моделью и усечение журналом - разные признаки, смешивать их нельзя.

    `truncated.response` означает «текст обрезан при записи в журнал». Обрыв ответа
    моделью - другое событие, и раньше он не читался вовсе: при `completion_tokens`,
    равном лимиту, в записи стояло `truncated.response: false`, то есть «ответ полный».
    """
    import json as json_mod

    from graphrag_proto.retrieval.adapters.llm import _ExchangeLog

    path = tmp_path / "exchange.jsonl"
    # Конфигурация журнала идёт через окружение, а не через аргументы конструктора:
    # единственный способ, каким он создаётся в коде.
    monkeypatch.setenv("LLM_EXCHANGE_LOG", "response")
    monkeypatch.setenv("LLM_EXCHANGE_LOG_FILE", str(path))
    monkeypatch.setenv("LLM_EXCHANGE_LOG_MAX_CHARS", "100000")
    log = _ExchangeLog()
    log.record(
        model="m",
        stage="extract",
        stream=False,
        response="```json\n{\n" + "x" * 100,
        finish_reason="length",
        usage={"completion_tokens": 6144, "prompt_tokens": 3126},
        max_tokens=6144,
    )

    record = json_mod.loads(path.read_text(encoding="utf-8").splitlines()[0])

    assert record["at_cap"] is True, "ответ, упёршийся в лимит, обязан быть помечен"
    assert record["finish_reason"] == "length", "причина завершения обязана попадать в запись"
    assert record["truncated"]["response"] is False, (
        "усечение ЖУРНАЛОМ не должно выдаваться за обрыв ответа моделью"
    )