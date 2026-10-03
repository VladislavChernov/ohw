"""Гейт приёмки золотого набора: что должно быть верно перед прогоном, который считает числа.

Задача прибора — не «сверить даты», а **поймать эталон, который утверждает снятую сущность**.
Такой эталон найден 2026-10-03 вручную, через историю коммитов, и это неприемлемый путь:
цена ошибки — смещённое число в отчёте, а находка случайна.

Проверки делятся на два класса, и это деление существенно:

* **блокирующие** — объективны, детерминированы, дешёвы; их нарушение означает «прогон
  считает не то»;
* **докладываемые** — полезны человеку, но шумны: `as_of` против даты изменения файла даёт
  срабатывание почти на каждом вопросе, потому что документ правили позже и после написания
  эталона. Если сделать это блокирующим, гейт станет красным всегда и через неделю его
  начнут игнорировать — это хуже, чем гейта нет.

Символы схемы, снятые решениями, хранятся в `suspended_symbols.py`, а не размазаны по тестам:
иначе проверка и список разойдутся, и проверка станет фиктивной. Там же живёт правило для
живых документов — список один, а контекст у двух потребителей разный (см. модуль).
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime

if __package__ in (None, ""):
    # Прибор запускают и как `python infra/eval/review_gate.py` — так его описан в
    # REVIEW-GATE.md, — и как пакетом из тестов. В первом случае корень репозитория в
    # `sys.path` не попадает, и импорт падал с ModuleNotFoundError: pytest был зелёным,
    # а документированный запуск для человека не работал. Оба способа обязаны работать,
    # иначе прибор есть только у гейта.
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from infra.eval.suspended_symbols import SUSPENDED_SYMBOLS

EVAL_ROOT = pathlib.Path(__file__).resolve().parent
REPO_ROOT = EVAL_ROOT.parents[2]
DOCS = REPO_ROOT / "docs"
DOMAINS = ("it", "library", "cinema")
INGEST_IGNORE_MARKER = ".ingest-ignore"


@dataclass(frozen=True)
class Finding:
    question_id: str
    domain: str
    symbol: str
    where: str
    snippet: str

    def as_line(self) -> str:
        return f"{self.question_id} [{self.symbol}] в {self.where}: {self.snippet}"


@dataclass(frozen=True)
class GateReport:
    suspended_hits: list[Finding] = field(default_factory=list)
    stale_after: list[str] = field(default_factory=list)
    missing_sources: list[str] = field(default_factory=list)
    excluded_sources: list[str] = field(default_factory=list)
    questions_per_domain: dict[str, int] = field(default_factory=dict)
    graph_slice: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> list[str]:
        lines = [f.as_line() for f in self.suspended_hits]
        lines += [f"источник эталона отсутствует: {s}" for s in self.missing_sources]
        lines += [f"источник эталона исключён из корпуса: {s}" for s in self.excluded_sources]
        return lines

    @property
    def informational(self) -> list[str]:
        return [f"{q}: as_of старше даты изменения источника — нужна проверка человеком" for q in self.stale_after]


def as_texts(value: object) -> list[str]:
    return [value] if isinstance(value, str) else list(value or [])


SUSPENDED_PATTERNS = [
    (name, re.compile(pattern))
    for name, group in SUSPENDED_SYMBOLS.items()
    for pattern in group
]


def load_questions(domain: str) -> list[dict]:
    path = EVAL_ROOT / domain / "questions.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _excluded(rel_path: pathlib.Path) -> bool:
    current = DOCS
    for segment in rel_path.parts[:-1]:
        current = current / segment
        if (current / INGEST_IGNORE_MARKER).is_file():
            return True
    return False


def _mtime(source: str) -> float:
    """`golden_sources` записаны от корня репозитория (`docs/invariants.md`)."""
    target = REPO_ROOT / source.replace("\\", "/")
    return target.stat().st_mtime if target.is_file() else 0.0


def _iso_date(value: str) -> str:
    return value[:10]


def suspended_hits(question_id: str, domain: str, where: str, text: str) -> list[Finding]:
    """Единая точка применения правил: и `check()`, и тесты зовут её, чтобы проверка и набор
    правил не разошлись."""
    found = []
    for name, pattern in SUSPENDED_PATTERNS:
        match = pattern.search(text)
        if match:
            snippet = text[max(0, match.start() - 40): match.end() + 40]
            found.append(Finding(question_id, domain, name, where, snippet.strip()))
    return found


def check() -> GateReport:
    report = GateReport()
    for domain in DOMAINS:
        questions = load_questions(domain)
        report.questions_per_domain[domain] = len(questions)
        for question in questions:
            qid = str(question.get("id", "?"))
            fields = {"query": str(question.get("query") or ""),
                      "golden_facts": " ".join(as_texts(question.get("golden_facts"))),
                      "rubric": " ".join(as_texts(question.get("rubric")))}
            for where, text in fields.items():
                report.suspended_hits.extend(suspended_hits(qid, domain, where, text))
            sources = [str(s) for s in as_texts(question.get("golden_sources"))]
            for source in sources:
                absolute = REPO_ROOT / source
                if not absolute.is_file():
                    report.missing_sources.append(f"{qid}: {source}")
                    continue
                rel = absolute.relative_to(DOCS) if str(absolute).startswith(str(DOCS)) else None
                if rel is not None and _excluded(rel):
                    report.excluded_sources.append(f"{qid}: {source}")
            if question.get("golden_graph_evidence") is True:
                report.graph_slice.append(qid)
            as_of = _iso_date(str(question.get("as_of") or ""))
            if as_of:
                newest = max((_mtime(s) for s in sources), default=0.0)
                if newest > 0 and newest > _epoch(as_of):
                    report.stale_after.append(qid)
    return report


def _epoch(day: str) -> float:
    return datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()


def main() -> int:
    report = check()
    print(f"вопросов по доменам: {report.questions_per_domain}")
    print(f"срез на ревью перед замером ({len(report.graph_slice)}): {', '.join(report.graph_slice)}")
    print(f"блокирующих замечаний: {len(report.blocking)}")
    for line in report.blocking:
        print(f"  БЛОК: {line}")
    print(f"требующих проверки человеком: {len(report.informational)}")
    for line in report.informational[:10]:
        print(f"  инфо: {line}")
    if len(report.informational) > 10:
        print(f"  … ещё {len(report.informational) - 10}")
    print("ИТОГ:", "проходим" if not report.blocking else "не проходим")
    return 0 if not report.blocking else 1


if __name__ == "__main__":
    raise SystemExit(main())