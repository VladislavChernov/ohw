"""Гард исключения каталогов из корпуса ингеста (`.ingest-ignore`).

Два утверждения, и второе важнее первого:

1. **Помеченное не попадает в сборку** — простая проверка механики.
2. **Ни один `golden_source` эталонов не лежит в исключённом каталоге, и все `golden_source`
   существуют на диске.** Это то, что делает исключение безопасным по построению: если кто-то
   архивирует файл, от которого зависит вопрос, гейт падает с указанием вопроса и файла.
   Без этого утверждения исключение архива тихо ломает приёмку, и узнать об этом можно
   только на финальном прогоне.
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "infra" / "eval"))

from run_eval import _is_ingest_excluded, select_corpus_files

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCS = REPO_ROOT / "docs"
DATASETS = ("it", "library", "cinema")


def _selected() -> set[str]:
    return {rel for _path, rel in select_corpus_files(DOCS)}


def test_marker_excludes_declared_directories() -> None:
    selected = _selected()
    assert not any(rel.startswith("archive/") for rel in selected), "архив попал в корпус"
    assert not any(rel.startswith("plans/") for rel in selected), "планы попали в корпус"


def test_marker_detects_nested_exclusion(tmp_path: pathlib.Path) -> None:
    """Вложенный исключённый каталог внутри живого тоже не должен попадать в корпус:
    проверяются все сегменты пути, а не только первый."""
    (tmp_path / "live").mkdir()
    (tmp_path / "live" / "nested").mkdir()
    (tmp_path / "live" / "keep.md").write_text("keep", encoding="utf-8")
    (tmp_path / "live" / "nested" / "skip.md").write_text("skip", encoding="utf-8")
    (tmp_path / "live" / "nested" / ".ingest-ignore").write_text("исключить", encoding="utf-8")

    selected = {rel for _path, rel in select_corpus_files(tmp_path)}

    assert selected == {"live/keep.md"}


def test_corpus_has_exactly_the_live_documents() -> None:
    """Явное число, чтобы «потерялось шесть файлов» не выглядело как успех. При добавлении
    нормативного документа число меняется — и это должно быть сделано руками, а не молча."""
    assert len(_selected()) == 30, sorted(_selected())[:5]


def _questions(domain: str) -> list[dict]:
    path = REPO_ROOT / f"prototype/infra/eval/{domain}/questions.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_every_golden_source_exists_and_is_ingestible() -> None:
    """`golden_sources` записаны от корня репозитория (`docs/invariants.md`), тогда как
    `select_corpus_files` возвращает путь относительно корпуса (`invariants.md`), а
    `source_url` собирается как `<source_prefix>/<relpath>`. Поэтому сравниваем с корнем."""
    offenders_missing: list[str] = []
    offenders_excluded: list[str] = []
    for domain in DATASETS:
        for question in _questions(domain):
            qid = question.get("id", "?")
            for source in question.get("golden_sources") or []:
                absolute = REPO_ROOT / source
                if not absolute.is_file():
                    offenders_missing.append(f"{qid}: {source} не существует")
                elif _is_ingest_excluded(absolute, DOCS):
                    offenders_excluded.append(f"{qid}: {source} лежит в исключённом каталоге")
    assert not offenders_missing, "источник эталона отсутствует:\n" + "\n".join(offenders_missing)
    assert not offenders_excluded, (
        "источник эталона исключён из корпуса — приёмка на нём невозможна:\n"
        + "\n".join(offenders_excluded)
    )