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

Отдельно проверяется адресность ссылок на ADR из кода: после развода двух серий
`CONCEPT.md` и `docs/05_adr_log.md` номер «ADR-014» стал неоднозначным, и docstring,
ссылавшийся на «ADR-014 п. 1», молча сменил бы смысл, нигде не упав.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from infra.eval.suspended_symbols import (
    SUSPENDED_SYMBOLS,
    doc_claim_violations,
    marked_suspended,
    paragraphs_of,
)
from tests.gitignore_filter import is_ignored

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
    "соединяя результаты только на этапе Context Assembly": "оси сходятся в bounded context, graph expansion после vector search",
    "время обоих осей (параллельно)": "оси последовательны: vector search, затем bounded expansion",
    "Graph (Cypher-шаблон)": "ритейвер vector-first, docs/03_retriever.md §1",
    "граф∥вектор": "graph — опциональная projection, а не параллельная ось",
    "Graph ∥ Vector": "graph — опциональная projection, а не параллельная ось",
    "node_labels": "типизированные метки выведены из ingest path, ADR-031",
    "CanonicalName": "identity контекстного узла — tag_id, инвариант L2-01",
}


def _live_docs(repo_root: Path) -> list[Path]:
    docs = sorted((repo_root / "docs").glob("*.md"))
    docs = [p for p in docs if p.name != "history.md"]
    return [
        *docs,
        repo_root / "CONCEPT.md",
        repo_root / "README-acceptance.md",
        repo_root / "prototype" / "README.md",
    ]


def _tracked_docs(repo_root: Path) -> list[Path]:
    """Только документы, которые попадают в репозиторий.

    Личные материалы владельца лежат в `docs/` под `.gitignore`. Проверять их словарь —
    значит ловить чужую рабочую работу и ронять гейт чужой сессии, что и случилось с
    `docs/defense_notes_simplifications.md`.
    """
    return [path for path in _live_docs(repo_root) if not is_ignored(repo_root, path)]


def _corrective_noted_lines(path: Path) -> frozenset[int]:
    """Номера строк ADR-блоков, к которым добавлена явная корректирующая пометка.

    Статус Accepted не отменяет решение, поэтому старая формулировка в его тексте
    остаётся историей. Но она перестаётся быть нормой только когда рядом сказано, что
    часть не реализована, — этот разряд и отмечает такой блок.
    """
    if path.name != "05_adr_log.md":
        return frozenset()
    noted: set[int] = set()
    block_start: int | None = None
    block_noted = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.startswith("## ADR-"):
            if block_start is not None and block_noted:
                noted.update(range(block_start, number))
            block_start = number
            block_noted = False
        if "Примечание (" in line:
            block_noted = True
    if block_start is not None and block_noted:
        noted.update(range(block_start, number + 1))
    return frozenset(noted)


def _doc_suspension_violations(path: Path) -> list[tuple[int, str, str]]:
    """Абзацы, которые называют снятую сущность, не сказав в том же абзаце, что она снята.

    Правило и список живут в `infra/eval/suspended_symbols.py` — том же, который читает
    гейт приёмки набора. Свой список здесь означал бы, что документы проверяются на
    другое множество, чем эталон, и расхождение придёт вместе с первым новым снятием.
    """
    violations: list[tuple[int, str, str]] = []
    for start, paragraph in paragraphs_of(path.read_text(encoding="utf-8")):
        for symbol in doc_claim_violations(paragraph):
            violations.append((start, symbol, " ".join(paragraph.split())[:160]))
    return violations


@pytest.mark.parametrize("pattern,reason", sorted(SUPERSEDED_PATTERNS.items()))
def test_live_docs_have_no_superseded_vocabulary(
    repo_root: Path, pattern: str, reason: str
) -> None:
    """Снятая формулировка не должна возвращаться в живые документы."""
    adr_log = repo_root / "docs" / "05_adr_log.md"
    offenders: list[str] = []
    for path in _tracked_docs(repo_root):
        noted = _corrective_noted_lines(path)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if pattern not in line:
                continue
            # В ADR-логе старая формулировка законна, если строка сама помечена Superseded.
            if path == adr_log and "Superseded" in line:
                continue
            # ...или если весь ADR-блок несёт явную корректирующую пометку.
            if number in noted:
                continue
            offenders.append(f"{path.relative_to(repo_root)}:{number}")
    assert not offenders, (
        f"устаревшая формулировка {pattern!r} ({reason}) осталась в: {', '.join(offenders)}"
    )


def test_live_docs_do_not_name_suspended_entities(repo_root: Path) -> None:
    """Живой документ не должен называть снятую сущность как существующую.

    Найдено 2026-10-03 при ревью среза: ADR-046 п.9 снял ноду `Source` и ребро `CONTAINS`,
    а восемь живых мест продолжали их описывать — включая два ожидания в `docs/test_plan.md`,
    где проверка сравнивала число `CONTAINS` с нулём и потому не могла упасть. Перечень
    снятого читается из `infra/eval/suspended_symbols.py`, тем же, что и гейт приёмки.

    **`docs/05_adr_log.md` из проверки исключён намеренно.** Журнал хранит текст решений на
    момент их принятия: там «3 `Source` — как было» и «`Source` 0 (было 3)» — это замеры
    прошлого, а не утверждение о схеме. Побочный эффект исключения назван: если в ADR
    напишут «нода `Source` существует» как живое правило, эта проверка его не поймает —
    журнал читается руками, и его собственная дисциплина здесь заменяет автоматику.
    """
    offenders: list[str] = []
    for path in _tracked_docs(repo_root):
        if path.name == "05_adr_log.md":
            continue
        for start, symbol, snippet in _doc_suspension_violations(path):
            offenders.append(f"{path.relative_to(repo_root)}:{start} [{symbol}] {snippet}")
    assert not offenders, "живые документы называют снятое как существующее:\n" + "\n".join(offenders)


#: Абзацы, которые обязаны быть пойманы. Тексты скопированы из HEAD-версий тех мест, где
#: правка ещё не внесена: пересказ абзаца проверку не доказывает — это выяснилось адверсarial-
#: ревью, когда три «пересказанных» теста остались зелёными на реальном тексте.
STALE_PARAGRAPHS = [
    (
        "Части старой версии — чанки, узлы чанков, векторы, структурные связи `CONTAINS` и "
        "`MENTIONS` — удаляются сразу."
    ),
    (
        "- **структура старой версии удаляется полностью** — узел чанка, его вектор, связи "
        "`CONTAINS` и `MENTIONS` (последние снимаются каскадно при `DETACH DELETE` узла чанка);"
    ),
    (
        "**Чего не делать.** не удалять все связи, потерявшие хоть один источник: связь, которую "
        "держат другие документы, обязана выжить (правило — «нет ни одного поддерживающего "
        "`chunk_id`», а не «нет одного источника»); не удалять связи без дискриминатора "
        "`chunk_ids IS NOT NULL` — снесёт `CONTAINS`/`MENTIONS`, у которых этого свойства нет "
        "вовсе; не удалять узлы раньше связей; не добавлять ключ политики в профиль"
    ),
]

#: Известные дырки правила, измеренные на HEAD-копиях: абзац ловится, когда маркер «снято» стоит
#: в соседней фразе. Фиксируются тестом, чтобы дырка не расширилась молча и чтобы решение о ней
#: было бы видно в коде, а не в переписке.
KNOWN_ESCAPES = [
    (
        "- **Архива версий нет.** Старая версия документа не остаётся читаемой: её чанки, узлы "
        "чанков, векторы и структурные связи `CONTAINS`/`MENTIONS` удаляются при re-ingest, "
        "вместе с ней. Параметров «N версий» и «T дней» не существует — держать нечего."
    ),
    (
        "`GraphStoreProvider.list_chunk_ids_of_source(source_url, domain)` — чанки документа по "
        "полю владельца на самом чанке (ADR-046 п. 9; раньше — по ребру `CONTAINS` от ноды-якоря "
        "`Source`). Перечисление обязано идти союзом на обеих осях: ось, чьи записи уже пропали "
        "при частичном сбое, не перечислит остаток в другой; `delete_node(chunk_id)` удаляет узел "
        "и инцидентные рёбра (в том числе CONTAINS); `VectorStoreProvider.delete_vectors(chunk_ids)` "
        "снимает те же чанки с поиска. Узлы ContextNode и Source при soft-delete **сохраняются**, "
        "но `source_ids`/`chunk_ids` удалённого источника очищаются, поэтому его evidence не "
        "попадает в graph-контекст."
    ),
]


@pytest.mark.parametrize("paragraph", STALE_PARAGRAPHS)
def test_doc_rule_still_catches_a_stale_paragraph(paragraph: str) -> None:
    """Правило обязано ловить нарушение само. Иначе зелёный корпус доказывал бы только то,
    что проверку выключили, — ровно тот случай, который описан в ADR-048 про тест на
    «гейт что-то находит»."""
    assert doc_claim_violations(paragraph), f"правило промолчало на {paragraph!r}"


@pytest.mark.parametrize("paragraph", KNOWN_ESCAPES)
def test_known_escapes_are_pinned_not_accidentally_fixed(paragraph: str) -> None:
    """Дырка зафиксирована тестом, а не только проговоркой.

    Если этот тест начнёт падать, значит дырка закрыта — и это хорошо: тогда её надо убрать
    из списка и записать, чем закрыта (предложение вместо абзаца даёт 2 ложных срабатывания,
    см. докстринг `infra/eval/suspended_symbols.py`). Если тест молчит, а правило стало
    строже по другой причине — список врёт и его надо пересчитать.
    """
    assert not doc_claim_violations(paragraph), (
        "абзац больше не проходит: дырка закрыта, пересчитать список известных пропусков"
    )


#: Абзацы, которые обязаны остаться незамеченными: отрицание и помеченная история.
LEGITIMATE_PARAGRAPHS = [
    (
        "отдельной ноды источника и связи `CONTAINS` в схеме нет — решение владельца 2026-10-02, "
        "ADR-046 пункт 9."
    ),
    (
        "`list_chunk_ids_of_source(source_url, domain)` — чанки документа по полю владельца "
        "(ADR-046 п. 9; раньше — по ребру `CONTAINS` от ноды-якоря `Source`)."
    ),
    (
        "Ожидается: `MENTIONS` на месте (единственное структурное ребро; `CONTAINS` снят "
        "ADR-046 п. 9)."
    ),
    "не удалять узлы раньше связей; не добавлять ключ политики в профиль.",
]


@pytest.mark.parametrize("paragraph", LEGITIMATE_PARAGRAPHS)
def test_doc_rule_accepts_negation_and_marked_history(paragraph: str) -> None:
    """Отрицание и история — не нарушение. Проверка, которая их ловит, станет красной
    с первого дня, а такой гейт через неделю начинают игнорировать (ADR-048 п.3)."""
    assert not doc_claim_violations(paragraph), f"ложное срабатывание на {paragraph!r}"


def test_ordering_word_is_not_a_suspension_marker() -> None:
    """«раньше» в смысле порядка — не «раньше = снято».

    Найдено ручной сверкой 2026-10-03: `docs/06_operations_and_risks.md` («не удалять узлы
    раньше связей») был помечен как история, и живая ложь в том же абзаце прошла бы молча.
    """
    paragraph = (
        "Чего не делать: не удалять связи без дискриминатора `chunk_ids IS NOT NULL` — "
        "снесёт `MENTIONS`, у которых этого свойства нет вовсе; не удалять узлы раньше связей."
    )
    assert not marked_suspended(paragraph), "однословный омогним «раньше» попал в маркеры"
    assert "edge CONTAINS" not in doc_claim_violations(paragraph)


def test_suspension_markers_are_unambiguous() -> None:
    """Маркер «снят» без «ADR-046» не должен прощать абзац, который идёт в записи как
    перечисление существующих рёбер, — иначе омогним повторится на другой глагол."""
    paragraph = "структурные связи `CONTAINS` и `MENTIONS` — удаляются сразу"
    assert "edge CONTAINS" in doc_claim_violations(paragraph)
    assert set(SUSPENDED_SYMBOLS) >= {"edge CONTAINS", "node Source"}


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


def test_code_adr_references_resolve_to_adr_log(repo_root: Path) -> None:
    """Номер ADR, процитированный в коде, должен существовать в журнале.

    Найдено при разводе серий: `CONCEPT.md` и `docs/05_adr_log.md` имели по своей серии
    с 014-го номера, поэтому «ADR-014» без указания файла означал разные решения. Точки
    роста уехали в журнал как ADR-033/034, и ссылка в docstring на «ADR-014 п. 1»
    (эмиссия метрик там, где значение вычислено) молча сменила бы смысл. Проверка
    ловит именно класс «перенумеровали ADR, а код остался с прежним номером».
    """
    log = (repo_root / "docs" / "05_adr_log.md").read_text(encoding="utf-8")
    known = set(re.findall(r"^## (ADR-\d+):", log, re.MULTILINE))
    assert len(known) >= 30, "в ADR-логе подозрительно мало записей — проверка ослабла"

    cited: dict[str, list[str]] = {}
    for path in sorted((repo_root / "prototype" / "src").rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for adr in re.findall(r"ADR-\d+", line):
                cited.setdefault(adr, []).append(f"{path.relative_to(repo_root)}:{number}")

    assert cited, "в prototype/src нет ссылок на ADR — проверка ослабла"
    unknown = {adr: refs for adr, refs in cited.items() if adr not in known}
    assert not unknown, (
        "ссылки на номера, которых нет в ADR-логе: "
        + "; ".join(f"{adr} → {', '.join(refs)}" for adr, refs in sorted(unknown.items()))
    )
