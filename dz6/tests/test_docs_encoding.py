"""Guard-тест кодировки документации: в `.md` не должно быть битых символов.

Проверка отвечает на конкретный класс дефектов: в текст попадают иероглифы
(следствие сбоя перевода/кодировки), символ замены U+FFFD и управляющие
символы (следствие неэкранированного `\\b` или `\\t` в скрипте записи).
Такой файл открывается и выглядит почти нормально, поэтому проверять нужно
диапазоны кодов, а не глазами.
"""

from __future__ import annotations

import pathlib

import pytest

DOCS_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _broken_characters(text: str) -> list[tuple[int, int, int]]:
    broken: list[tuple[int, int, int]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for column, char in enumerate(line, start=1):
            code = ord(char)
            if (
                char == "\ufffd"
                # CJK, кана, хангыль, полноширинные формы
                or 0x2E80 <= code <= 0x9FFF
                or 0x3040 <= code <= 0x30FF
                or 0xAC00 <= code <= 0xD7AF
                or 0xF900 <= code <= 0xFAFF
                or 0xFF00 <= code <= 0xFFEF
                # управляющие символы, кроме табуляции
                or (code < 0x20 and char != "\t")
                or code == 0x7F
            ):
                broken.append((lineno, column, code))
    return broken


SKIP_DIRS = {".git", "node_modules", ".pytest_cache", "__pycache__", ".venv"}


def _markdown_files() -> list[pathlib.Path]:
    return sorted(
        path
        for path in DOCS_ROOT.rglob("*.md")
        if not SKIP_DIRS.intersection(path.relative_to(DOCS_ROOT).parts)
    )


def test_project_docs_are_scanned() -> None:
    """Тест не должен зеленеть на пустом или урезанном множестве файлов."""
    docs = _markdown_files()
    if not docs:
        pytest.skip("документация не копируется в образ; проверка идёт на хосте")
    if not (DOCS_ROOT / "TZ.md").exists():
        pytest.skip("в образ копируются только prompts/*.md, не весь набор документации")
    names = {path.name for path in docs}
    assert {"README.md", "TZ.md", "LOCAL_VS_SUBAGENT_REPORT.md"} <= names


@pytest.mark.parametrize("path", _markdown_files(), ids=lambda p: p.name)
def test_markdown_has_no_broken_characters(path: pathlib.Path) -> None:
    text = path.read_text(encoding="utf-8")
    broken = _broken_characters(text)
    assert not broken, (
        f"{path.relative_to(DOCS_ROOT)}: битые символы "
        + ", ".join(f"строка {line}, колонка {col}: U+{code:04X}" for line, col, code in broken)
    )
