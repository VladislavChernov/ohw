"""Гард: остатки тяжёлого графа не возвращаются в профиль.

Формулировка контракта положительная и проверяет **связность**, а не запрещённый список:
для каждого ключа должно выполняться «его нет в профилях **или** его читает код». Пока ключ
не читается никем, он мёртв и является обузой; как только появится читатель — условие
выполнится, и тест начнёт требовать, чтобы читатель был не пустой.

Это защита от повторения ADR-031 в обратную сторону: тот раз типизация снималась, а ключи от
неё остались; они молчали и выглядели настройками.
"""

from __future__ import annotations

import pathlib

import pytest

PROFILES = ("it", "library", "cinema")
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Ключ профиля -> подстроки, по которым ищем читателя в `prototype/src`.
DEAD_KEYS: dict[str, tuple[str, ...]] = {
    "cypher_template": ("cypher_template",),
    "validation.rules": ("validation", "rules"),
    "canonicalization.nodes": ("canonicalization", "nodes"),
    "unique_key": ("unique_key",),
}

#: Ключи, у которых есть осмысленный читатель в production-коде, и мы их НЕ считаем мёртвыми.
ALIVE_READERS: dict[str, tuple[str, ...]] = {
    "canonicalization.nodes": ("_identity_key",),
    "unique_key": ("ensure_schema",),
}


def _profile_text(domain: str) -> str:
    return (REPO_ROOT / f"prototype/domain_profiles/domain_profile.{domain}.yaml").read_text(
        encoding="utf-8"
    )


def _source_text() -> str:
    parts = [
        path.read_text(encoding="utf-8")
        for path in (REPO_ROOT / "prototype/src").rglob("*.py")
    ]
    return "\n".join(parts)


@pytest.mark.parametrize("domain", PROFILES)
@pytest.mark.parametrize("key", sorted(DEAD_KEYS))
def test_profile_key_either_absent_or_read(domain: str, key: str) -> None:
    present = key in _profile_text(domain)
    source = _source_text()
    readers = [token for token in DEAD_KEYS[key] if token in source]
    if key in ALIVE_READERS:
        readers += [token for token in ALIVE_READERS[key] if token in source]
    if present and not readers:
        pytest.fail(
            f"ключ `{key}` есть в профиле домена `{domain}` и не читается ничем в "
            "`prototype/src` — это остаток снятой типизированной схемы (ADR-031), "
            "настройка без действия"
        )


def test_no_typed_ontology_cypher_rules() -> None:
    """`validation.rules[].cypher` искали метки `:Requirement/:Concept/:Contract`. Таких
    меток в графе нет с ADR-031, поэтому правило не могло сработать никогда."""
    source = _source_text()
    assert "validation" not in source or "rules" not in source, (
        "появилось чтение `validation` — если это новый смысл, перепишите правило под "
        "актуальные метки и снимите эту проверку"
    )


def test_graph_retriever_has_no_cypher_attribute() -> None:
    """`GraphRetriever` не держит сырой Cypher: ось работает через `expand()` адаптера,
    а шаблон был остатком типизированного запроса с нулём вызывающих."""
    source = _source_text()
    assert "self.cypher" not in source, (
        "в коде снова появился сырой Cypher в GraphRetriever — это откат к схеме, "
        "снятой ADR-031"
    )