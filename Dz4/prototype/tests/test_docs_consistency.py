"""Согласованность живых документов с текущей архитектурой.

Vector-first рерайт (CONCEPT.md, docs/00-06, data_model, glossary, invariants) обновил
не все документы: часть осталась с graph-first формулировками, и eval это видел — eval
играет по корпусу `docs/`, поэтому векторный поиск доставал противоречивые утверждения,
а вопросы опирались на старые. Здесь документы не «подчищаются вручную» — этот тест не
даёт старой лексике вернуться.

Исключения оговорены явно:
  * `docs/history.md` — журнал вех, он фиксирует состояние на момент коммита, и
    переписывать его значило бы подделать историю;
  * `docs/05_adr_log.md` — старые формулировки законны внутри помеченных Superseded
    ADR, в этом их смысл.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Паттерн — причина, по которой он недопустим в живых документах.
SUPERSEDED_PATTERNS: dict[str, str] = {
    "9 этапов": "primitive-конвейер описан в CONCEPT.md §4.1 (6 этапов)",
    "9 фиксированных": "обязательные document/chunk/embed/vector-commit, L3-01",
    "7 шагов": "цикл запроса vector-first, см. docs/03_retriever.md",
    "7 фаз": "цикл запроса vector-first, см. docs/03_retriever.md",
    "скелет": "отдельного skeleton-блока в контексте больше нет, docs/03_retriever.md §3",
    "Констрейнт в Neo4j": "ADR-031 вывел runtime constraints из ingest path",
    "констрейнт в Neo4j": "ADR-031 вывел runtime constraints из ingest path",
    "соединяются только на этапе Context Assembly": "оси сходятся в bounded context, graph expansion после vector search",
    "Graph (Cypher-шаблон)": "ритейвер vector-first, docs/03_retriever.md §1",
    "граф∥вектор": "graph — опциональная projection, а не параллельная ось",
    "Graph ∥ Vector": "graph — опциональная projection, а не параллельная ось",
    "node_labels": "типизированные метки выведены из ingest path, ADR-031",
    "CanonicalName": "identity контекстного узла — tag_id, инвариант L2-01",
}


def _live_docs(repo_root: Path) -> list[Path]:
    docs = sorted((repo_root / "docs").glob("*.md"))
    docs = [p for p in docs if p.name != "history.md"]
    return [*docs, repo_root / "CONCEPT.md", repo_root / "prototype" / "README.md"]


@pytest.mark.parametrize("pattern,reason", sorted(SUPERSEDED_PATTERNS.items()))
def test_live_docs_have_no_superseded_vocabulary(
    repo_root: Path, pattern: str, reason: str
) -> None:
    """Снятая формулировка не должна возвращаться в живые документы."""
    adr_log = repo_root / "docs" / "05_adr_log.md"
    offenders: list[str] = []
    for path in _live_docs(repo_root):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern not in line:
                continue
            # В ADR-логе старая формулировка законна, если строка сама помечена Superseded.
            if path == adr_log and "Superseded" in line:
                continue
            offenders.append(f"{path.relative_to(repo_root)}:{number}")
    assert not offenders, (
        f"устаревшая формулировка {pattern!r} ({reason}) осталась в: {', '.join(offenders)}"
    )


def test_concept_and_adr_log_agree_on_superseded_status(repo_root: Path) -> None:
    """CONCEPT.md и ADR-лог — источники статуса, они не должны расходиться.

    Найдено при ревизии eval-датасетов: CONCEPT.md помечал ADR-006 и ADR-008 как
    Superseded, а канонический лог держал их Accepted.
    """
    concept = (repo_root / "CONCEPT.md").read_text(encoding="utf-8")
    log = (repo_root / "docs" / "05_adr_log.md").read_text(encoding="utf-8")

    concept_superseded = set(re.findall(r"^### (ADR-\d+):.*\(Superseded\)", concept, re.MULTILINE))
    assert concept_superseded, "CONCEPT.md не содержит помеченных Superseded ADR — проверка ослабла"

    for adr in sorted(concept_superseded):
        block = re.search(rf"^## {adr}:.*?(?=^## ADR-|\Z)", log, re.MULTILINE | re.DOTALL)
        assert block is not None, f"{adr} есть в CONCEPT.md, но нет в ADR-логе"
        assert "Superseded" in block.group(0), (
            f"{adr}: CONCEPT.md помечает Superseded, а ADR-лог — нет. Лог здесь SSOT."
        )
