"""Гард прибора ревью среза (`infra/eval/slice_review_anchors.py`).

Прибор нужен, чтобы человек сверял факт с документом, а не с памятью автора. Но прибор,
который печатает «найдено» без места нахождения, опаснее отсутствия прибора: он даёт
уверенный ноль. Такая поломка в этом файле уже была — словарь всех источников проверялся
на пустоту вместо списка вхождений, и якорь `L2-09` печатался как `[OK]` с пустым местом,
а итог сходился к «0 якорей вне источников» вместо двух. Поэтому проверка здесь не «прибор
что-то находит», а «прибор не врёт о том, что нашёл».
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import re
from pathlib import Path
from typing import Any

import pytest

_INFRA_EVAL = Path(__file__).resolve().parents[1] / "infra" / "eval"
_PACKET = _INFRA_EVAL / "slice-review-61050ae.md"
_LOCATION = re.compile(r"[\w/]+\.md:\d+")


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def anchors() -> Any:
    return _load("slice_review_anchors", _INFRA_EVAL / "slice_review_anchors.py")


@pytest.fixture(scope="module")
def report(anchors: Any) -> str:
    """Полный вывод прибора на текущем дереве.

    `redirect_stdout`, а не `capsys`: прибор печатает один раз на модуль, а `capsys` жёстко
    привязан к функции, и общий фикстур с ним не поднимается.
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        assert anchors.main() == 0
    return buffer.getvalue()


def _sections(report: str) -> dict[str, list[str]]:
    """Вывод прибора, разложенный по вопросам: заголовок `id  as_of=…` → строки ниже."""
    blocks: dict[str, list[str]] = {}
    current: str | None = None
    for line in report.split("\n"):
        header = re.match(r"^(\S+)\s+as_of=", line)
        if header:
            current = header.group(1)
            blocks[current] = []
        elif current is not None:
            blocks[current].append(line)
    return blocks


def test_ok_line_always_carries_a_location(report: str) -> None:
    """`[OK]` без места — это не находка, а тишина прибора. Такой строке быть не должно."""
    offenders = [line for line in report.split("\n") if "[OK]" in line and not _LOCATION.search(line)]
    assert not offenders, "прибор нашёл якорь и не сказал где:\n" + "\n".join(offenders)


def test_every_question_of_the_slice_is_covered(anchors: Any, report: str) -> None:
    """Пропущенный вопрос выглядит как «проверили и расхождений нет», поэтому граница среза
    сверяется с полем, а не с числом в заголовке пакета."""
    questions = anchors.load_questions(anchors.find_repo_root() / anchors.DATASET_RELATIVE)
    expected = {q["id"] for q in questions if q.get(anchors.SLICE_FIELD) is True}
    assert set(_sections(report)) == expected


def _found_by_instrument_semantics(anchor: str, text: str) -> bool:
    """«Якорь найден» — ровно та семантика, которой отвечает прибор: целое слово.

    Реализация здесь своя, а не вызов `anchors.anchor_regex`: тест, который импортирует
    функцию проверяемого кода, проверяет сам себя. Но **семантика обязана совпадать** — иначе
    тест отвечает на другой вопрос, чем прибор. Правило проекта то же: прибор, читающий другой
    набор входов, чем проверяемый код, — это тот же дефект. До правки здесь была подстрока, и
    расхождение было латентным: якорь `L2-09` при тексте `L2-09x` даёт у прибора `MISS`
    (сосед — слово), а подстрока нашла бы его и объявила проверку неверной.
    """
    return re.search(r"(?<!\w)" + re.escape(anchor) + r"(?!\w)", text) is not None


def test_miss_anchor_is_really_absent_from_declared_sources(anchors: Any, report: str) -> None:
    """`MISS` перепроверяется независимо: якоря нет ни в одном объявленном источнике.

    Пересчёт здесь намеренно не через функции прибора — иначе тест проверял бы сам себя, —
    но с той же семантикой «найдено», что и у прибора (см. `_found_by_instrument_semantics`).
    """
    repo = anchors.find_repo_root()
    assert repo is not None
    questions = {q["id"]: q for q in anchors.load_questions(repo / anchors.DATASET_RELATIVE)}
    checked = 0
    for qid, lines in _sections(report).items():
        sources = [str(src) for src in questions[qid]["golden_sources"]]
        texts = [(repo / src).read_text(encoding="utf-8") for src in sources]
        for line in lines:
            if "[MISS]" not in line:
                continue
            anchor = line.split("[MISS]", 1)[1].split("||")[0].strip()
            assert not any(_found_by_instrument_semantics(anchor, text) for text in texts), (
                f"{qid}: якорь {anchor!r} помечен MISS, но найден в объявленных источниках"
            )
            checked += 1
    assert checked > 0, "в срезе не осталось ни одного якоря вне источников — проверка молчит"


def test_word_boundary_semantics_is_the_agreed_one(anchors: Any) -> None:
    """Расхождение, которое третья сессия вынесла как открытое решение, закрыто в пользу
    семантики прибора: якорь ищется как целое слово.

    Проверяется на том же примере, который был назван: `L2-09` против текста `L2-09x`.
    Возврат к подстроке означал бы `[OK]` там, где якоря нет, — то есть класс «уверенный
    ноль вместо «не измерено»», который в этом проекте уже ловили дважды.
    """
    assert anchors.anchor_regex("L2-09").search("L2-09x") is None
    assert anchors.anchor_regex("L2-09").search("L2-09") is not None
    assert not _found_by_instrument_semantics("L2-09", "инвариант L2-09x в тексте")
    assert _found_by_instrument_semantics("L2-09", "инвариант L2-09 в тексте")
    # границы не должны ломаться на якорях, начинающихся и кончающихся не буквой
    assert anchors.anchor_regex("/revision").search("`GET /revision?domain=`") is not None
    assert _found_by_instrument_semantics("/revision", "поллер `GET /revision?domain=`")


def test_packet_states_which_questions_it_covers(anchors: Any, report: str) -> None:
    """Пакет ревью и прибор читают одну границу: разойтись им нельзя, иначе человек сверяет
    не тот список, который считает `necessity`."""
    packet = _PACKET.read_text(encoding="utf-8")
    for qid in _sections(report):
        assert f"### {qid} " in packet, f"{qid} разобран прибором, но не попал в пакет ревью"