"""Гард видимого вклада графа в Demo UI (ADR-072, ADR-075).

Проверяется смысловая часть, которую видит человек: graph-only чанки, их попадание в
контекст и различие «не измерено» / «измеренный ноль» / «граф выключен». Разметка
Streamlit тестами не покрыта, поэтому сводка вынесена в чистые функции.
"""

from __future__ import annotations

from typing import Any

from graphrag_proto.demo_ui.app import (
    graph_contribution,
    graph_contribution_lines,
    graph_only_chunk_rows,
    source_rows,
)


def _measured_done() -> dict[str, Any]:
    return {
        "effective_retrieval": {
            "graph_search_enabled": True,
            "graph_only_chunk_ids": ["c-adr", "c-other", "c-lost"],
            "graph_materialized_chunk_ids": ["c-adr", "c-other"],
            "graph_only_chunk_sources": {
                "c-adr": "docs/05_adr_log.md",
                "c-other": "docs/03_retriever.md",
            },
        },
        "context": {"body_chunk_ids": ["c-adr"]},
    }


def test_cache_hit_is_unknown_not_zero() -> None:
    """Кэш не отдаёт effective_retrieval: это отсутствие данных, а не нулевой вклад."""
    summary = graph_contribution({})

    assert summary["status"] == "unknown"
    assert graph_only_chunk_rows(summary) == []
    text = " ".join(graph_contribution_lines(summary))
    assert "не измерен" in text.lower()
    assert "0" not in text, "неизмеренное не имеет права выглядеть измеренным нулём"


def test_disabled_graph_is_not_zero() -> None:
    summary = graph_contribution(
        {"effective_retrieval": {"graph_search_enabled": False, "graph_only_chunk_ids": None}}
    )

    assert summary["status"] == "disabled"
    text = " ".join(graph_contribution_lines(summary))
    assert "выключен" in text.lower()
    assert "0" not in text


def test_measured_zero_is_named_as_zero() -> None:
    """Граф отработал и ничего не добавил — это измеренный ноль, его надо назвать."""
    summary = graph_contribution(
        {
            "effective_retrieval": {
                "graph_search_enabled": True,
                "graph_only_chunk_ids": [],
                "graph_materialized_chunk_ids": [],
                "graph_only_chunk_sources": {},
            },
            "context": {"body_chunk_ids": []},
        }
    )

    assert summary["status"] == "measured"
    assert summary["graph_only"] == 0
    assert graph_contribution_lines(summary) == [
        "Граф не добавил ни одного чанка сверх векторной оси."
    ]


def test_graph_only_counts_source_and_context_separately() -> None:
    summary = graph_contribution(_measured_done())

    assert summary["graph_only"] == 3
    assert summary["materialized"] == 2
    assert summary["in_context"] == 1
    assert summary["adr_graph_only"] == 1
    assert summary["adr_in_context"] == 1

    text = " ".join(graph_contribution_lines(summary))
    assert "Граф добавил чанков сверх векторной оси: 3." in text
    assert "Материализовано в текст: 2." in text
    assert "Доехало в контекст: 1." in text
    assert "Из 05_adr_log.md: 1; доехало в контекст: 1." in text


def test_graph_only_chunks_name_their_fate() -> None:
    rows = graph_only_chunk_rows(graph_contribution(_measured_done()))
    fate = {row["chunk_id"]: row["в контексте"] for row in rows}

    assert fate == {
        "c-adr": "да",
        "c-other": "нет",
        "c-lost": "не материализован",
    }


def test_missing_context_does_not_claim_zero_reached() -> None:
    done = _measured_done()
    done.pop("context")

    summary = graph_contribution(done)

    assert summary["in_context"] is None
    assert summary["adr_in_context"] is None
    text = " ".join(graph_contribution_lines(summary))
    assert "Попадание в контекст: не измерено." in text
    assert "Доехало в контекст: 0." not in text


def test_source_rows_keep_axis_and_graph_knowledge() -> None:
    rows = source_rows(
        [
            {
                "source_url": "docs/05_adr_log.md",
                "axis": "graph",
                "graph_knowledge": "contributed",
                "relevance": None,
                "graph_knowledge_reason": "bridge",
            },
            {
                "source_url": "docs/03_retriever.md",
                "axis": "vector",
                "graph_knowledge": "reachable_unused",
                "relevance": 0.75,
            },
        ]
    )

    assert rows[0]["axis"] == "граф"
    assert rows[0]["graph_knowledge"] == "граф дал текст"
    assert rows[0]["relevance"] == "—"
    assert rows[1]["axis"] == "вектор"
    assert rows[1]["graph_knowledge"] == "граф дошёл, текст не взят"


def test_missing_graph_knowledge_is_unknown_not_blind() -> None:
    rows = source_rows([{"source_url": "docs/03_retriever.md", "axis": "vector"}])

    assert rows[0]["graph_knowledge"] == "не измерено"
