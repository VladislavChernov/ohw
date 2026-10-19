"""Ссылки на строки в as-built снимках обязаны быть проверяемы.

Снимок прототипа отвечает на вопрос «от чего отталкиваться, решая задачу», и его ценность
равна ценности его точности. Проверить точность prose нельзя, но можно проверить, что
диапазон строк вообще существует: как только файл растёт или сокращается, старые ссылки
уезжают, и читатель получает уверенное неверное описание.

Наблюдалось 2026-09-30: из четырёх проверенных ссылок `Ingest/ingest_as_built.md` три
указывали на неверный диапазон, а центральное утверждение о состоянии модуля (LLM-извлечение
как план M3) оказалось ложным. Тест ловит именно класс «диапазон уехал»; смысловой дрейф
остаётся на пересверке, о чём сказано в самом файле.

Правило 8 `analitic/ANALYTICAL_NOTES_RULES.md` — источник самого требования.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_DZ4 = Path(__file__).resolve().parents[2]
_SRC = _DZ4 / "prototype" / "src"

#: Снимки прототипа: у них есть шапка с отметкой о сверке, в отличие от аналитических записок.
#: Путь от корня `Dz4`, а не от репозитория: контейнер монтирует именно `Dz4` в `/repo`.
_AS_BUILT = ("Ingest/ingest_as_built.md",)

#: Ссылка на диапазон строк пишется двумя способами, и оба приходится ловить:
#: в прозе как `` `файл.py:123-456` `` и в код-блоке как комментарий `# файл.py:123-456`.
#: Первый вариант перекрывался только обратными кавычками, и из-за этого уехавшая ссылка
#: в §3.4 снимка прошла гейт молча. Расширение файла обязательно, чтобы не ловить ссылки
#: вида `docs/02:4`, где после двоеточия номер раздела, а не строки.
_REF_PROSE = re.compile(
    r"`(?P<path>[A-Za-z0-9_./-]+\.(?:py|yaml|yml|json|md)):(?P<start>\d+)(?:-(?P<end>\d+))?`"
)
_REF_CODE = re.compile(
    r"^\s*#\s*(?P<path>[A-Za-z0-9_./-]+\.(?:py|yaml|yml|json|md)):(?P<start>\d+)(?:-(?P<end>\d+))?\s*$",
    re.MULTILINE,
)

#: Куда искать файл, если в ссылке путь короткий. Порядок важен: сначала `src`, потом `Dz4`.
_SEARCH_ROOTS = (_SRC, _DZ4)


def _source_index() -> dict[str, list[Path]]:
    """Имя файла → все пути под `prototype/src`.

    Индекс строится один раз: снимок ссылается на одни и те же файлы десятки раз, и `rglob`
    на каждый ссылку превратил бы тест в обход дерева. Имена уникальны, но путей может быть
    несколько (например `neo4j.py` в адаптере и в конфигурации), поэтому значение — список.
    """
    index: dict[str, list[Path]] = {}
    for path in _SRC.rglob("*"):
        if path.is_file():
            index.setdefault(path.name, []).append(path)
    return index


_SOURCE_INDEX: dict[str, list[Path]] | None = None


def _resolve(path: str) -> Path | None:
    """Найти файл, на который указывает ссылка.

    Короткое имя (типа `orchestrator.py`) ищется в индексе исходников, длинный путь — от
    корня `Dz4`. Возвращается `None`, если файла нет: это отдельный случай, и тест ниже его
    ловит своим сообщением, а не молча пропускает ссылку.
    """
    global _SOURCE_INDEX
    direct = _DZ4 / path
    if direct.is_file():
        return direct
    name = Path(path).name
    if _SOURCE_INDEX is None:
        _SOURCE_INDEX = _source_index()
    found = _SOURCE_INDEX.get(name)
    if not found:
        for root in _SEARCH_ROOTS:
            candidate = root / name
            if candidate.is_file():
                return candidate
        return None
    return found[0]


def _references(markdown: str) -> list[tuple[str, int, int]]:
    out: list[tuple[str, int, int]] = []
    for pattern in (_REF_PROSE, _REF_CODE):
        for match in pattern.finditer(markdown):
            start = int(match.group("start"))
            end = int(match.group("end") or match.group("start"))
            out.append((match.group("path"), start, end))
    return out


def _as_built_cases() -> list[tuple[str, str, int, int]]:
    cases: list[tuple[str, str, int, int]] = []
    for rel in _AS_BUILT:
        markdown = (_DZ4 / rel).read_text(encoding="utf-8")
        cases.extend((rel, p, s, e) for p, s, e in _references(markdown))
    return cases


def test_as_built_file_exists() -> None:
    for rel in _AS_BUILT:
        assert (_DZ4 / rel).is_file(), f"{rel}: снимок as-built, на который ссылаются правила, не найден"


def test_as_built_declares_verification_stamp() -> None:
    """Снимок без отметки о сверке нельзя читать как описание (правило 7)."""
    for rel in _AS_BUILT:
        text = (_DZ4 / rel).read_text(encoding="utf-8")
        assert "**Вид документа:**" in text, f"{rel}: нет обязательной шапки «Вид документа»"
        assert "**Сверено с кодом:**" in text, f"{rel}: нет обязательной отметки о сверке с кодом"


def test_as_built_has_line_references() -> None:
    """Страховка от вырожденного случая: файл без ссылок проходит проверку молча."""
    assert _as_built_cases(), "в as-built снимке нет ни одной ссылки вида `файл.py:N-M`"


@pytest.mark.parametrize(
    "rel,path,start,end",
    _as_built_cases(),
    ids=[f"{Path(r).name}:{s}" if p == r else f"{Path(p).name}:{s}-{e}" for r, p, s, e in _as_built_cases()],
)
def test_as_built_line_reference_fits_file(rel: str, path: str, start: int, end: int) -> None:
    resolved = _resolve(path)
    assert resolved is not None, (
        f"{rel}: ссылка на несуществующий файл `{path}`. Правило 8 требует, чтобы файл "
        f"существовал: иначе читатель не может проверить, что по этим строкам видно"
    )
    total = len(resolved.read_text(encoding="utf-8").splitlines())
    assert start <= end, f"{rel}: `{path}:{start}-{end}` — начало диапазона позже конца"
    assert start >= 1, f"{rel}: `{path}:{start}` — строки нумеруются с 1"
    assert end <= total, (
        f"{rel}: `{path}:{start}-{end}` — в файле всего {total} строк. Ссылка уехала за кодом; "
        f"либо поправь диапазон, либо пересверь раздел и обнови отметку в шапке"
    )
