"""Механическая половина ревью среза: якоря фактов против текущих документов.

Правило проверки объявлено заранее и записано в `slice-review-61050ae.md`:
факт подтверждён механически, если каждый буквальный якорь из его текста найден
регистрозависимым поиском хотя бы в одном файле из `golden_sources` вопроса.

Якорь найден не означает «факт верен». Этот скрипт не решает ничего: он печатает
материал для чтения человеком и `MISS` там, где якорь в объявленных источниках
не найден. Ничего в наборе не пишет.

Восемь регулярных выражений ниже — это определение слова «якорь». Менять их молча
нельзя: иначе меняется смысл «дословно» и результаты перестают быть сравнимыми
между прогонами. Изменение правил — повод переписать раздел 2 пакета ревью.

Запуск (из dev-контейнера, без стенда и без сети):

    docker exec ohw-dz4-dev python3 /repo/prototype/infra/eval/slice_review_anchors.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SLICE_FIELD = "golden_graph_evidence"
DATASET_RELATIVE = Path("prototype/infra/eval/it/questions.jsonl")

#: Порядок и состав выражений — часть определения. Новый якорь добавляется сюда
#: явно, а не «по входу в текст».
ANCHOR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"`([^`]+)`"),
    re.compile(r"\bADR-\d+"),
    re.compile(r"\bL\d-\d+\b"),
    re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\b"),
    re.compile(r"\b[a-z_][a-z0-9_]*\(\)"),
    re.compile(r"\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b"),
    re.compile(r"/[a-z][a-z0-9_/]*"),
    re.compile(r"\bdocs/[a-z0-9_]+"),
)

MIN_ANCHOR_LEN = 3
CODE_FENCE = re.compile(r"^```")
FACTS_FIELD = "golden_facts"
SOURCES_FIELD = "golden_sources"


def find_repo_root() -> Path | None:
    """Корень репозитория: env, затем путь скрипта, затем текущий каталог вверх.

    Поиск вверх нужен для запуска через stdin (`python3 -`), где `__file__` не задан:
    иначе прибор работает только по одному из двух способов и молча падает на другом.
    """
    override = os.environ.get("DZ4_REPO_ROOT")
    if override:
        candidate = Path(override)
        return candidate if (candidate / DATASET_RELATIVE).is_file() else None
    starts: list[Path] = []
    module_file = globals().get("__file__")
    if module_file:
        starts.append(Path(module_file).resolve().parent)
    starts.append(Path.cwd().resolve())
    for start in starts:
        for candidate in (start, *start.parents):
            if (candidate / DATASET_RELATIVE).is_file():
                return candidate
    return None


def anchors_of(text: str) -> list[str]:
    """Буквальные якоря текста факта, в порядке первого появления."""
    found: list[str] = []
    for pattern in ANCHOR_PATTERNS:
        for match in pattern.finditer(text):
            token = (match.group(1) if match.lastindex else match.group(0)).strip().rstrip(".,;:")
            if len(token) >= MIN_ANCHOR_LEN and token not in found:
                found.append(token)
    return found


def source_lines(path: Path) -> list[tuple[int, str, bool]]:
    """Строки файла как `(номер, текст, внутри_кодового_блока)`.

    Кодовые блоки помечены, потому что совпадение внутри примера в документе и
    совпадение в прозе — разные утверждения, и путать их нельзя.
    """
    if not path.is_file():
        return []
    rows: list[tuple[int, str, bool]] = []
    in_fence = False
    for number, line in enumerate(path.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n"), 1):
        if CODE_FENCE.match(line):
            in_fence = not in_fence
            continue
        rows.append((number, line.strip(), in_fence))
    return rows


def anchor_regex(anchor: str) -> re.Pattern[str]:
    """Поиск якоря как целого слова, а не подстроки.

    Извлечение якоря использует границы слов, а поиск был подстрокой — асимметрия в одну
    сторону: `ensure_schema` находился внутри `ensure_schema_legacy`, и прибор печатал `[OK]`
    там, где якоря нет. Вокруг якоря ставится проверка «сосед не слово», а не `\\b`, потому
    что якорь может начинаться и кончиться не буквой (`docs/…`, `ADR-046`).
    """
    return re.compile(r"(?<!\w)" + re.escape(anchor) + r"(?!\w)")


def locate(anchor: str, lines: list[tuple[int, str, bool]]) -> list[str]:
    """Первые три вхождения якоря как `документ:строка(проза|в блоке кода)`."""
    pattern = anchor_regex(anchor)
    hits = [f"{number}({'блок' if fenced else 'проза'})" for number, text, fenced in lines if pattern.search(text)]
    return hits[:3]


def elsewhere_corpus(repo: Path) -> list[tuple[str, list[tuple[int, str, bool]]]]:
    """Документы для поиска «где ещё встречается».

    Раньше это был `docs/*.md` верхнего уровня, поэтому якорь, лежащий в подкаталоге `docs/`
    или в бандле, выглядел как «не встречается нигде». Это делало `MISS` слабее, чем он есть:
    отсутствие находки выдавалось за отсутствие якоря.
    """
    rows: list[tuple[str, list[tuple[int, str, bool]]]] = []
    for path in sorted(repo.rglob("*.md")):
        parts = path.relative_to(repo).as_posix()
        if parts.startswith(("docs/", "openspec/")) and "/archive/" not in parts:
            rows.append((parts, source_lines(path)))
    return rows


def load_questions(dataset: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in dataset.read_text(encoding="utf-8").split("\n"):
        if line.strip():
            rows.append(json.loads(line))
    return rows


def main() -> int:
    repo = find_repo_root()
    if repo is None:
        print(
            "корень репозитория не найден: не виден "
            f"{DATASET_RELATIVE.as_posix()}. Задайте DZ4_REPO_ROOT.",
            file=sys.stderr,
        )
        return 2
    dataset = repo / DATASET_RELATIVE

    questions = load_questions(dataset)
    slice_rows = [row for row in questions if row.get(SLICE_FIELD) is True]
    print(f"репозиторий: {repo}")
    print(f"вопросов всего: {len(questions)}; в срезе по полю {SLICE_FIELD}: {len(slice_rows)}")
    print(f"срез: {', '.join(str(row['id']) for row in slice_rows)}")
    print()

    missing_total = 0
    facts_total = facts_unmeasured = facts_with_anchor = facts_confirmed = 0
    facts_all_miss: list[str] = []
    unmeasured: list[str] = []
    corpus = elsewhere_corpus(repo)
    for row in slice_rows:
        sources = [str(src) for src in row.get(SOURCES_FIELD, [])]
        absent = [src for src in sources if not (repo / src).is_file()]
        print("=" * 100)
        print(f"{row['id']}  as_of={row.get('as_of')}  policy={row.get('evidence_policy')}")
        print(f"  вопрос: {row.get('query')}")
        print(f"  источники: {', '.join(sources)}")
        if absent:
            print(f"  ОТСУТСТВУЮТ НА ДИСКЕ: {', '.join(absent)}")
        print(f"  evidence_sections: {'; '.join(str(s) for s in row.get('evidence_sections', []))}")

        cache = {src: source_lines(repo / src) for src in sources}
        for index, fact in enumerate(row.get(FACTS_FIELD, []), 1):
            print("-" * 100)
            print(f"  Ф{index}: {fact}")
            facts_total += 1
            fact_anchors = anchors_of(str(fact))
            if not fact_anchors:
                # Факт без буквальных якорей не проверяется ничем: ни `[OK]`, ни `MISS` ему
                # не соответствуют. Раньше такой факт просто не печатался, и на глаз был
                # неотличим от «проверен, расхождений нет». Это тот же класс, что уверенный
                # ноль вместо «не измерено», только по другому полю.
                unmeasured.append(f"{row['id']} Ф{index}")
                facts_unmeasured += 1
                print("    [НЕ ИЗМЕРЕНО] нет буквальных якорей — подтвердить нечем, читать глазами")
                continue
            facts_with_anchor += 1
            confirmed_here = False
            for anchor in fact_anchors:
                # Только источники с непустым списком вхождений. Словарь всех источников
                # здесь дал бы `[OK]` с пустым местом: первая версия прибора печатала
                # «найдено» для якоря, которого нет ни в одном файле, и итог был уверенный
                # ноль вместо «не измерено». Пустое место вхождений — это MISS.
                found = {src: hits for src in sources if (hits := locate(anchor, cache[src]))}
                if not found:
                    pattern = anchor_regex(anchor)
                    elsewhere = [
                        f"{parts}:{number}"
                        for parts, lines in corpus
                        for number, text, _ in lines
                        if pattern.search(text)
                    ]
                    missing_total += 1
                    tail = (
                        f"  || встречается вне источников: {', '.join(elsewhere[:3])}" if elsewhere else ""
                    )
                    print(f"    [MISS] {anchor}{tail}")
                    continue
                where = "; ".join(f"{src}:{','.join(hits)}" for src, hits in found.items())
                confirmed_here = True
                print(f"    [OK]   {anchor:<44} {where}")
            if confirmed_here:
                facts_confirmed += 1
            else:
                facts_all_miss.append(f"{row['id']} Ф{index}")
        print()

    print(f"фактов в срезе: {facts_total} · без якорей (не измерено): {facts_unmeasured} · "
          f"с якорями: {facts_with_anchor} · из них с подтверждённым: {facts_confirmed}")
    print(f"якорей вне объявленных источников: {missing_total}")
    if unmeasured:
        print("не измеренные факты (читать глазами): " + ", ".join(unmeasured))
    if facts_all_miss:
        print("факты, у которых ни один якорь не подтверждён (читать глазами): " + ", ".join(facts_all_miss))
    print("MISS — это «не подтверждено объявленными источниками», а не «факт неверен».")
    print("НЕ ИЗМЕРЕНО — это «прибор здесь бессилен», а не «факт верен».")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())