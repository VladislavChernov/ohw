"""Валидация eval-датасетов (ADR-015 формат): questions.jsonl парсится, поля непустые."""

from __future__ import annotations

from pathlib import Path

import pytest

EVAL_ROOT = Path(__file__).resolve().parent.parent / "infra" / "eval"
DOMAINS = ["it", "library", "cinema"]


def _load_jsonl(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [dict(__import__("json").loads(line)) for line in lines if line.strip()]


@pytest.mark.parametrize("domain", DOMAINS)
def test_dataset_parses(domain: str) -> None:
    jsonl = EVAL_ROOT / domain / "questions.jsonl"
    if not jsonl.exists():
        pytest.skip(f"questions.jsonl не найден: {jsonl}")
    questions = _load_jsonl(jsonl)
    assert len(questions) > 0, f"Пустой датасет {domain}"


@pytest.mark.parametrize("domain", DOMAINS)
def test_dataset_ids_unique(domain: str) -> None:
    jsonl = EVAL_ROOT / domain / "questions.jsonl"
    if not jsonl.exists():
        pytest.skip()
    questions = _load_jsonl(jsonl)
    ids = [q["id"] for q in questions]
    assert len(ids) == len(set(ids)), f"Дублирующиеся id в {domain}: {[i for i in ids if ids.count(i) > 1][:5]}"


@pytest.mark.parametrize("domain", DOMAINS)
def test_dataset_required_fields(domain: str) -> None:
    jsonl = EVAL_ROOT / domain / "questions.jsonl"
    if not jsonl.exists():
        pytest.skip()
    questions = _load_jsonl(jsonl)
    for q in questions:
        assert "id" in q, f"Нет 'id' в вопросе: {q}"
        assert "query" in q and q["query"].strip(), f"Пустой query: {q.get('id')}"
        assert "golden_sources" in q and len(q["golden_sources"]) > 0, (
            f"Пустые golden_sources: {q.get('id')}"
        )
        assert "category" in q and q["category"].strip(), f"Нет category: {q.get('id')}"


@pytest.mark.parametrize("domain", DOMAINS)
def test_dataset_category_valid(domain: str) -> None:
    VALID_CATEGORIES = {
        "architecture", "semantics", "invariants", "security",
        "operations", "retrieval", "caching", "data_model", "api", "general",
    }
    jsonl = EVAL_ROOT / domain / "questions.jsonl"
    if not jsonl.exists():
        pytest.skip()
    for q in _load_jsonl(jsonl):
        assert q["category"] in VALID_CATEGORIES, (
            f"Неизвестная категория '{q['category']}' в {q.get('id')}: допустимы {VALID_CATEGORIES}"
        )