"""Проверка ссылок на секции и идентификаторы в живых документах.

Отдельный класс дрейфа, который лексический линтер (`test_docs_consistency.py`) не видит:
Vector-first рерайт переехал по секциям, а ссылки остались на старых номерах. Пример,
подтверждённый при ревизии eval-датасетов: `invariants.md` L3-04 ссылается на
`docs/03_retriever.md §2`, а §2 теперь «Fallback и атрибуция» - бюджет контекста в §3.

**Что проверяется и почему только это.** Первая версия линтера брала «ближайший упомянутый
файл в строке» и дала 15 срабатываний, из которых почти все оказались ложными: строка может
перечислять несколько документов подряд, файл может быть назван строкой выше, а короткие
ключи вроде `adapters` или `06` давали коллизии. Линтер, который кричит волком, опаснее
отсутствия линтера - его отключат.

Поэтому проверяется только однозначный случай: **`<файл> §<N>` где файл стоит непосредственно
перед §-ссылкой** (`docs/03_retriever.md §2`, `docs/02 §1`, `prototype_requirements.md §6`).
Голые `§N` без названного файла не проверяются: они могут относиться к своему файлу, к
упомянутому выше или к только что названному, и без разбора контекста строки решение
неоднозначно. Такие ссылки считаются и выводятся в `test_link_linter_coverage` - если
проверенная доля падает, значит линтер деградировал, и это тоже падение.

Неоднозначные ключи (имя, совпадающее у двух документов, например `README`) не резолвятся
вообще, а считаются непроверенными.

Исключения оговорены явно:
  * `docs/history.md` - журнал вех, он фиксирует состояние на момент коммита;
  * `docs/05_adr_log.md` - журнал ADR, в нём ссылки на секции других документов исторические.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCS_DIR_NAME = "docs"
EXEMPT_FILES = {"history.md", "05_adr_log.md"}

_SECTION_HEADING = re.compile(r"^#{1,6}\s+(?:L)?([0-9]+(?:\.[0-9]+)*)\.?\s")
# Ссылка вида `<файл> §<N>`: файл и секция разделяет только пробел/ backtick / скобка.
_EXPLICIT_REF = re.compile(
    r"(?P<file>`?[A-Za-z0-9_][A-Za-z0-9_./-]*`?)\s*§\s?(?P<num>[0-9]+(?:\.[0-9]+)*)"
)
_ANY_REF = re.compile(r"§\s?[0-9]+(?:\.[0-9]+)*")
_ADR_REF = re.compile(r"\bADR-(\d{3})\b")
_INVARIANT_REF = re.compile(r"\bL(\d)-(\d{2})\b")
_MIN_VALIDATED_SHARE = 0.25


def _live_docs(repo_root: Path) -> list[Path]:
    docs = [p for p in sorted((repo_root / DOCS_DIR_NAME).glob("*.md")) if p.name not in EXEMPT_FILES]
    return [*docs, repo_root / "CONCEPT.md", repo_root / "prototype" / "README.md"]


def _section_index(path: Path) -> set[str]:
    """Номера секций из заголовков. `## L2. Данные` индексируется и как `2`."""
    sections: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _SECTION_HEADING.match(line)
        if match:
            sections.add(match.group(1))
    return sections


def _build_file_map(docs: list[Path]) -> dict[str, Path | None]:
    """Ключи ссылок -> файл. None означает неоднозначность: такой ключ не резолвим.

    Ключи: полное имя, имя без .md, `docs/<полное>`, `docs/<stem>`, `docs/<префикс до _>`.
    Голые номера (`06`, `01`) ключами не являются - они совпадают с массой обычного текста.
    """
    mapping: dict[str, Path | None] = {}
    ambiguous: set[str] = set()

    def put(key: str, path: Path) -> None:
        if key in mapping and mapping[key] != path:
            ambiguous.add(key)
        else:
            mapping[key] = path

    for path in docs:
        stem = path.stem
        put(path.name, path)
        put(stem, path)
        put(f"{DOCS_DIR_NAME}/{path.name}", path)
        put(f"{DOCS_DIR_NAME}/{stem}", path)
        prefix = stem.split("_", 1)[0]
        if prefix != stem:
            put(f"{DOCS_DIR_NAME}/{prefix}", path)
    for key in ambiguous:
        mapping[key] = None
    return mapping


def _resolve(ref: str, file_map: dict[str, Path | None]) -> Path | None:
    """Файл, на который указывает ссылка. None - неоднозначно или не найдено."""
    key = ref.strip("`")
    for candidate in (key, f"{DOCS_DIR_NAME}/{key}"):
        if candidate in file_map:
            return file_map[candidate]
    return None


def _scan(repo_root: Path) -> tuple[list[str], int, int]:
    """Возвращает (нарушения, проверено ссылок, всего ссылок)."""
    docs = _live_docs(repo_root)
    file_map = _build_file_map(docs)
    index = {path: _section_index(path) for path in docs}
    by_name = {path.name: path for path in docs}
    violations: list[str] = []
    validated = 0
    total = 0
    for path in docs:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for match in _ANY_REF.finditer(line):
                total += 1
            for match in _EXPLICIT_REF.finditer(line):
                ref = match.group("file")
                wanted = match.group("num")
                # Ссылка на сам файл, а не на документ из реестра.
                if ref.strip("`") in by_name and by_name[ref.strip("`")] == path:
                    target = path
                else:
                    target = _resolve(ref, file_map)
                if target is None or target not in index:
                    continue
                validated += 1
                if wanted not in index[target]:
                    violations.append(
                        f"{path.relative_to(repo_root)}:{number}: "
                        f"§{wanted} -> нет такого заголовка в {target.relative_to(repo_root)}"
                    )
    return violations, validated, total


def test_no_dangling_explicit_section_references(repo_root: Path) -> None:
    """Явная ссылка `<файл> §N` должна вести в существующий заголовок."""
    violations, _, _ = _scan(repo_root)
    assert not violations, "битые ссылки на секции:\n  " + "\n  ".join(violations)


def test_adr_references_exist(repo_root: Path) -> None:
    """Ссылка на ADR должна существовать в каноническом логе."""
    adr_log = (repo_root / DOCS_DIR_NAME / "05_adr_log.md").read_text(encoding="utf-8")
    known = set(_ADR_REF.findall(adr_log))
    assert known, "не удалось извлечь ни одного ADR из лога - проверка ослабла"
    offenders: list[str] = []
    for path in _live_docs(repo_root):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for found in _ADR_REF.findall(line):
                if found not in known:
                    offenders.append(f"{path.relative_to(repo_root)}:{number}: ADR-{found}")
    assert not offenders, "ссылки на несуществующие ADR:\n  " + "\n  ".join(offenders)


def test_invariant_references_exist(repo_root: Path) -> None:
    """Ссылка на инвариант должна существовать в docs/invariants.md."""
    invariants = (repo_root / DOCS_DIR_NAME / "invariants.md").read_text(encoding="utf-8")
    known = {f"{a}-{b}" for a, b in _INVARIANT_REF.findall(invariants)}
    assert known, "не удалось извлечь инварианты - проверка ослабла"
    offenders: list[str] = []
    for path in _live_docs(repo_root):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for major, minor in _INVARIANT_REF.findall(line):
                if f"{major}-{minor}" not in known:
                    offenders.append(f"{path.relative_to(repo_root)}:{number}: L{major}-{minor}")
    assert not offenders, "ссылки на несуществующие инварианты:\n  " + "\n  ".join(offenders)


def test_link_linter_coverage(repo_root: Path) -> None:
    """Линтер не должен деградировать в ничто.

    Если доля проверенных ссылок падает ниже порога, значит резолвер перестал работать
    (например, файлы переехали) и «зелёный» результат больше ничего не значит.
    """
    _, validated, total = _scan(repo_root)
    assert total > 40, f"найдено подозрительно мало §-ссылок: {total} - индексация сломалась"
    share = validated / total
    assert share >= _MIN_VALIDATED_SHARE, (
        f"проверено только {validated} из {total} ссылок ({share:.0%}) - "
        f"линтер деградировал, порог {_MIN_VALIDATED_SHARE:.0%}"
    )


@pytest.mark.parametrize("name", sorted(EXEMPT_FILES))
def test_exempt_files_still_exist(repo_root: Path, name: str) -> None:
    """Исключения линтера должны существовать: иначе исключение устарело молча."""
    assert (repo_root / DOCS_DIR_NAME / name).is_file()
