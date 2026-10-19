"""Гард-тесты пораздельной готовности проекции (ADR-046 п. 3 и п. 6).

Формулировки контрактов здесь положительные и проверяют **измеримость**, а не списки
значений: главный риск этого прибора — не «посчитал не то», а «посчитал ноль там, где
измерять было нечем».
"""

from __future__ import annotations

from graphrag_proto.retrieval.pipeline import evaluate_projection_readiness


def test_unknown_observed_is_not_a_confident_zero() -> None:
    """Адаптер не умеет отдавать ревизии → `None` → счётчики `None`, а не нули.

    Это главный гард: молчаливый ноль здесь означал бы «проекция актуальна» там, где её
    никто не смотрел, и дал бы `necessity = 0` вместо результата.
    """
    readiness = evaluate_projection_readiness({"src://a": "rev-a"}, None)

    assert readiness.ready is False
    assert readiness.reason == "projection_not_measurable"
    assert readiness.sources_without_graph is None
    assert readiness.revision_mismatches is None
    assert readiness.chunks_without_owner is None
    assert readiness.chunks_total is None


def test_empty_journal_is_unknown_not_ready() -> None:
    """Нет опубликованных ревизий — сравнивать нечего, и это не «всё хорошо»."""
    readiness = evaluate_projection_readiness({}, {"src://a": {"rev-a": 3}})

    assert readiness.ready is False
    assert readiness.reason == "no_tracked_sources"
    assert readiness.revision_mismatches is None


def test_matching_revisions_make_domain_ready() -> None:
    readiness = evaluate_projection_readiness(
        {"src://a": "rev-a", "src://b": "rev-b"},
        {"src://a": {"rev-a": 10}, "src://b": {"rev-b": 2}},
    )

    assert readiness.ready is True
    assert readiness.reason == "checked"
    assert readiness.sources_tracked == 2
    assert readiness.sources_without_graph == 0
    assert readiness.revision_mismatches == 0
    assert readiness.chunks_without_owner == 0
    assert readiness.chunks_total == 12


def test_stale_chunk_revision_blocks_and_is_counted() -> None:
    readiness = evaluate_projection_readiness(
        {"src://a": "rev-new"},
        {"src://a": {"rev-new": 4, "rev-old": 3}},
    )

    assert readiness.ready is False
    assert readiness.revision_mismatches == 3
    assert readiness.chunks_total == 7


def test_source_without_chunks_is_counted_but_does_not_block() -> None:
    """Нет `Chunk`-нод у документа — это частичная проекция, а не рассинхронизация.

    До поправки владельца 2026-10-16 это утверждалось как `ready = false`, и гард выключал ось
    целиком: при 13 таких документах из 30 на демо-наборе графическая рукава не открылась ни для
    одного вопроса, то есть вклад графа стал неизмерим на любом неполном корпусе. Теперь счётчик
    остаётся (масштаб слепоты читатель должен видеть), но условием готовности не является.
    """
    readiness = evaluate_projection_readiness(
        {"src://a": "rev-a", "src://b": "rev-b"},
        {"src://a": {"rev-a": 4}},
    )

    assert readiness.sources_without_graph == 1
    assert readiness.revision_mismatches == 0
    assert readiness.ready is True


def test_ready_still_requires_revision_agreement_and_owner() -> None:
    """Снятие одного условия не превращает гард в формальность.

    Формулировка положительная и переживает правки списка: «`ready` ⇒ ревизии сошлись и у чанков
    есть владелец», а не «в готовности нет такого-то счётчика».
    """
    mismatched = evaluate_projection_readiness(
        {"src://a": "rev-new"},
        {"src://a": {"rev-new": 4, "rev-old": 3}},
    )
    ownerless = evaluate_projection_readiness(
        {"src://a": "rev-a"},
        {"src://a": {"rev-a": 1}, "<ownerless>": {"rev-x": 2}},
    )

    assert mismatched.revision_mismatches == 3
    assert mismatched.ready is False
    assert ownerless.chunks_without_owner == 2
    assert ownerless.ready is False


def test_ready_requires_at_least_one_tracked_source() -> None:
    """Пустой журнал — это «нечего проверять», а не «всё в порядке»."""
    readiness = evaluate_projection_readiness({}, {"src://a": {"rev-a": 3}})

    assert readiness.ready is False
    assert readiness.reason == "no_tracked_sources"


def test_chunk_without_owner_and_untracked_source_are_counted_separately() -> None:
    readiness = evaluate_projection_readiness(
        {"src://a": "rev-a"},
        {
            "<ownerless>": {"rev-x": 2},
            "src://surprise": {"rev-y": 5},
            "src://a": {"rev-a": 1},
        },
    )

    assert readiness.ready is False
    # Сироты и неучтённые документы — разные вещи, и обе попадают в один счётчик; в
    # `revision_mismatches` они не попадают, иначе дефект учитывался бы дважды.
    assert readiness.chunks_without_owner == 7
    assert readiness.revision_mismatches == 0