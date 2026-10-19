"""Правила извлечения кандидатов: что они обязаны ловить и чего не должны.

Сторож на конец правила, а не на его наличие: до правки класс «пара заглавных слов»
пропускался целиком, и гейт был зелёным, потому что пропуск никто не утверждал.
"""

from __future__ import annotations

from graphrag_proto.pre_ingest.generate import (
    CANDIDATE_PATTERNS,
    is_well_formed,
    rule_candidates,
)

# Реальные имена из docs/04_services_config.md, которые до правки не попадали ни в один
# шаблон. Список зафиксирован по замеру, а не придуман: каждое имя присутствует в
# документе и было объявлено моделью как дословно встречающееся в своём чанке.
MULTIWORD_ENTITIES = (
    "Glossary Service",
    "Config Service",
    "Embeddings Service",
    "Reranker Service",
    "Domain Profile",
    "Topology Orchestrator",
    "Topology Configurator",
    "Runtime Config Management",
)

# Начало обычного предложения. Попадание такого в список означает, что правило начало
# ловить прозу, и список перестал сужать выбор модели.
PROSE_STARTS = (
    "Это описание сервиса",
    "Обработка идёт по шагам",
    "Такой подход работает",
)


def test_multiword_entity_names_are_candidates() -> None:
    """Пара заглавных слов - настоящая форма имени, а не пропуск правила."""
    text = "Сервисы: Glossary Service, Config Service, Embeddings Service."
    found = rule_candidates(text)

    missing = [name for name in MULTIWORD_ENTITIES[:3] if name not in found]
    assert not missing, f"правило пропустило: {missing}"


def test_multiword_matched_regardless_of_position_in_list() -> None:
    """То же имя в середине и в конце перечисления ловится одинаково."""
    text = "Topology Orchestrator, Glossary Service и Reranker Service."
    found = rule_candidates(text)

    for name in ("Topology Orchestrator", "Glossary Service", "Reranker Service"):
        assert name in found, f"пропущено: {name}"


def test_prose_starts_are_not_candidates() -> None:
    """Правило не должно начинать ловить начало предложения."""
    found = rule_candidates(
        "Это описание сервиса. Обработка идёт по шагам. Такой подход работает."
    )

    leaked = [name for name in PROSE_STARTS if name in found]
    assert not leaked, f"правило начало ловить прозу: {leaked}"


def test_cyrillic_is_not_captured_by_surface_rules() -> None:
    """Кириллица регуляркой НЕ берётся, и это должно остаться зафиксированным.

    Тест на отсутствие поведения, а не на его наличие: он удерживает от тихого
    возврата шаблона, который давал 88 совпадений на 5 настоящих имён. Если кириллица
    когда-нибудь закроется честно, тест придётся переписать вместе с решением, а не
    удалить потому что «стало зелено».
    """
    found = rule_candidates("Без неё ничего. Дом стоит. Внимание прочитайте.")

    assert "Без неё" not in found
    assert "Дом" not in found


def test_pattern_count_matches_declared_rules() -> None:
    """Каждое правило объявлено и не потеряно при правке."""
    assert len(CANDIDATE_PATTERNS) == 6


def test_backticked_rule_still_extracts_group() -> None:
    """Правило обратных кавычек достаёт СОДЕРЖИМОЕ, а не слово с ковычками."""
    assert "`app.py`" not in rule_candidates("см. `app.py`")
    assert "app.py" in rule_candidates("см. `app.py`")


def test_is_well_formed_still_rejects_code_fragments() -> None:
    """Структурный фильтр не должен ослабнуть вместе с новым правилом."""
    assert not is_well_formed("(domain, canonical_name)")
    assert not is_well_formed('"atomic"')
    assert is_well_formed("Ingestion API")