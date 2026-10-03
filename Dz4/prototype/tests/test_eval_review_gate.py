"""Гард гейта приёмки золотого набора.

Проверяет сам прибор, а не только набор. Набор стал чистым 2026-10-03 (ADR-046 п.9 снял якорь,
эталоны переписаны), поэтому «гейт находит нарушение» больше нельзя проверять на реальных
данных — иначе тест зеленеет ровно тогда, когда правило перестало работать. Вместо этого
нарушение **внедряется** тем же текстом, каким его писал бы человек.
"""

from __future__ import annotations

import re

import pytest

from infra.eval.review_gate import SUSPENDED_SYMBOLS, check, load_questions, suspended_hits

#: Формулировки, которые набор содержал до переноса на текущую схему.
STALE = [
    "Технические anchors - Source и Chunk; они не являются бизнес-типами.",
    "Рёбра технические: CONTAINS (Source → Chunk) и MENTIONS (Chunk → ContextNode).",
    "Назови CONTAINS техническим ребром Source → Chunk.",
    "Объясни cypher_template из профиля.",
]


def test_dataset_is_clean_after_retarget() -> None:
    """Позитивный инвариант: в эталоне нет снятых символов. Один вход — одна причина падения."""
    report = check()
    assert not report.blocking, "\n".join(report.blocking)


@pytest.mark.parametrize("stale", STALE)
def test_rule_still_catches_a_stale_wording(stale: str) -> None:
    """Регрессия правила. Без этого теста зелёный набор доказывал бы только то, что проверку
    выключили, а не то, что она работает."""
    assert suspended_hits("synthetic", "it", "golden_facts", stale), f"правило промолчало на {stale!r}"


def test_cinema_document_named_source_is_not_a_false_positive() -> None:
    """В домене `cinema` есть документ, названный «Source», и вопрос спрашивает, чем он
    отличается от источника документа. Наивное правило «`Source` есть — значит снятая нода»
    даёт здесь ложное срабатывание, и сигнал перестаёт быть сигналом."""
    cinema = load_questions("cinema")
    assert any("Source" in q["query"] for q in cinema), "фикстура изменилась: в cinema нет вопроса про Source"
    assert not [f for f in check().suspended_hits if f.question_id == "cin_010"]


def test_informational_check_never_blocks() -> None:
    """`as_of` против даты изменения файла даёт ~71 срабатывание из 74 и блокировать не должен:
    гейт, красный всегда, через неделю игнорируют."""
    report = check()
    assert all("as_of" not in line for line in report.blocking)
    assert len(report.informational) > 0, "информационная проверка молчит вовсе"


@pytest.mark.parametrize("decoy", ["sources", "source_url", "DocumentRegistry", "источника документа"])
def test_suspended_rules_ignore_decoys(decoy: str) -> None:
    """Требование одно — ни одно правило снятого символа не ловит обычный текст. Проверять
    «в правиле есть заглавная буква» нельзя: ключ `cypher_template` строчный по природе."""
    for name, group in SUSPENDED_SYMBOLS.items():
        for pattern in group:
            assert not re.search(pattern, decoy), f"{name}: правило {pattern} ловит {decoy!r}"


def test_shape_of_the_set_is_intact() -> None:
    questions = load_questions("it")
    assert len(questions) == 74
    assert sum(1 for q in questions if q["id"].startswith("it_graph_")) == 24
    assert sum(1 for q in questions if q.get("golden_graph_evidence")) == 8