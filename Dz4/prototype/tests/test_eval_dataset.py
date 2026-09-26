"""Валидация eval-датасетов (ADR-015 формат + v2 delta): questions.jsonl парсится,
поля непустые, v2-поля валидируются (типы, evidence_policy=graph_required ⇒
golden_graph_evidence), старые наборы остаются парсибельными."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

EVAL_ROOT = Path(__file__).resolve().parent.parent / "infra" / "eval"
DOMAINS = ["it", "library", "cinema"]

_RUN_EVAL_PY = EVAL_ROOT / "run_eval.py"




def _load_run_eval() -> Any:
    spec = importlib.util.spec_from_file_location("run_eval", _RUN_EVAL_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_run_eval = _load_run_eval()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [dict(json.loads(line)) for line in lines if line.strip()]


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


# ---------------------------------------------------------------------------
# v2 (ADR-015 delta, design.md §4): validation unit-тесты + наборы
# ---------------------------------------------------------------------------

def _v2_question(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "q1",
        "query": "Как соединяются оси?",
        "golden_sources": ["docs/03_retriever.md"],
        "golden_facts": ["Оси соединяются в Context Assembly."],
        "reasoning_type": "single-hop",
        "answerability": "answerable",
        "as_of": "2026-09-17",
        "evidence_sections": ["Context Assembly §2"],
        "evidence_policy": "joint",
        "rubric": "Назвать этап сборки.",
        "golden_graph_evidence": False,
    }
    base.update(overrides)
    return base


def test_validate_question_legacy_without_v2_passes() -> None:
    _run_eval.validate_question(
        {"id": "legacy_1", "query": "Вопрос", "golden_sources": ["s://a"], "golden_facts": ["факт"]}
    )


def test_validate_question_v2_full_passes() -> None:
    _run_eval.validate_question(_v2_question())


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"id": ""}, "без обязательного поля"),
        ({"query": ""}, "без обязательного поля"),
        ({"golden_sources": []}, "golden_sources"),
        ({"golden_facts": "не список"}, "golden_facts"),
        ({"reasoning_type": "не-exist"}, "reasoning_type"),
        ({"answerability": "не-exist"}, "answerability"),
        ({"as_of": "2026/17/09"}, "as_of"),
        ({"evidence_sections": "не список"}, "evidence_sections"),
        ({"evidence_policy": "не-exist"}, "evidence_policy"),
        ({"rubric": 7}, "rubric"),
        ({"golden_graph_evidence": "yes"}, "golden_graph_evidence"),
    ],
)
def test_validate_question_rejects_bad_v2(overrides: dict[str, Any], fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment):
        _run_eval.validate_question(_v2_question(**overrides))


def test_validate_question_graph_required_implies_evidence() -> None:
    q = _v2_question(evidence_policy="graph_required", golden_graph_evidence=True)
    _run_eval.validate_question(q)
    with pytest.raises(ValueError, match="graph_required"):
        _run_eval.validate_question(_v2_question(evidence_policy="graph_required"))


def test_load_dataset_legacy_files_parseable() -> None:
    """library/cinema (без v2-полей) проходят validate_question через load_dataset."""
    for domain in ("library", "cinema"):
        jsonl = EVAL_ROOT / domain / "questions.jsonl"
        if not jsonl.exists():
            continue
        _run_eval.load_dataset(jsonl)


def test_merge_datasets_preserves_ids_and_rejects_duplicates() -> None:
    a = [{"id": "a1", "query": "q", "golden_sources": ["s"], "golden_facts": ["f"]}]
    b = [{"id": "b1", "query": "q", "golden_sources": ["s"], "golden_facts": ["f"]}]
    merged = _run_eval.merge_datasets([a, b])
    assert [q["id"] for q in merged] == ["a1", "b1"]
    with pytest.raises(ValueError, match="дублирующийся id"):
        _run_eval.merge_datasets([a, [{"id": "a1"}]])


def test_it_dataset_is_v2() -> None:
    jsonl = EVAL_ROOT / "it" / "questions.jsonl"
    if not jsonl.exists():
        pytest.skip()
    for q in _load_jsonl(jsonl):
        for key in ("reasoning_type", "answerability", "as_of", "evidence_sections", "evidence_policy", "rubric"):
            assert key in q, f"it/{q.get('id')} без v2-поля {key}"
        assert all(f.strip() for f in q["golden_facts"]), f"пустой факт в it/{q.get('id')}"
    assert len(_load_jsonl(jsonl)) == 50


def test_repo_docs_are_mounted(repo_root: Path) -> None:
    """Документы проекта должны быть доступны тестам.

    Резолвер живёт в `conftest.require_repo_root` и падает, а не отдаёт `None`:
    иначе все проверки ниже выключаются молча, и следующий ревайт документов
    снова пройдёт зелёным.
    """
    assert (repo_root / "docs").is_dir()
    assert (repo_root / "prototype").is_dir()


def test_it_graph_goldens_match_runtime_contract(repo_root: Path) -> None:
    jsonl = EVAL_ROOT / "it" / "questions_graph.jsonl"
    questions = _load_jsonl(jsonl)
    for question in questions:
        assert "HAS_ENTITY" not in " ".join(question["golden_facts"])
        assert "unique_key id" not in " ".join(question["golden_facts"])
        assert "Chunk -> Source" not in " ".join(question["golden_facts"])
        assert "Graph Retriever расширяет обход" not in " ".join(question["golden_facts"])
        assert "invariants v10" not in " ".join(question["golden_facts"])
        assert "sorted(content_hash" not in " ".join(question["golden_facts"])
    # Existence of every cited document is a hard check, not a silent no-op.
    for question in questions:
        for source in question["golden_sources"]:
            assert (repo_root / source).is_file(), f"{question['id']}: нет {source}"


def test_runtime_docs_exclude_mandatory_ddl(repo_root: Path) -> None:
    """ADR-031: runtime ingest не создаёт constraints (ensure_schema выведен из ingest path).

    Проверка намеренно не привязана к одной формулировке: `docs/01` говорит
    «не создаются Neo4j constraints», `docs/data_model.md` — «не создаёт constraints».
    Раньше здесь была точная фраза «не создаёт constraints» для обоих файлов, и
    Vector-first ревайт её сломал — а тест при этом молча пропускался из-за
    недоступного монтирования, так что расхождение не всплыло.
    """
    for relative in ("docs/01_ontology_and_domain_profile.md", "docs/data_model.md"):
        text = (repo_root / relative).read_text(encoding="utf-8")
        assert "ensure_schema" in text, f"{relative}: ensure_schema должен быть назван"
        assert "CREATE CONSTRAINT" not in text, f"{relative}: runtime-DDL инструкция недопустима"
        assert "constraints" in text, f"{relative}: нужно явное упоминание constraints"
    data_model = (repo_root / "docs/data_model.md").read_text(encoding="utf-8")
    assert "не создаёт constraints" in data_model, "docs/data_model.md потерял формулировку ADR-031"


def test_it_042_matches_structure_aware_chunker_contract() -> None:
    questions = {q["id"]: q for q in _load_jsonl(EVAL_ROOT / "it" / "questions.jsonl")}
    facts = " ".join(questions["it_042"]["golden_facts"])
    assert "отдельным чанком" in facts
    assert "объединяются" not in facts


def test_it_questions_graph_dataset() -> None:
    jsonl = EVAL_ROOT / "it" / "questions_graph.jsonl"
    if not jsonl.exists():
        pytest.skip()
    questions = _load_jsonl(jsonl)
    _run_eval.load_dataset(jsonl)
    assert len(questions) >= 15, f"questions_graph: n={len(questions)}, требуется ≥ 15"
    required = [q for q in questions if q.get("evidence_policy") == "graph_required"]
    assert len(required) >= 8, (
        f"questions_graph: graph_required={len(required)}, требуется ≥ 8"
    )
    for q in required:
        assert q.get("golden_graph_evidence") is True
        assert q.get("reasoning_type") in {"multi-hop", "cross-document", "contradiction"}