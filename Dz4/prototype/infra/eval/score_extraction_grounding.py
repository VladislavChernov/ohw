"""Вердикты по извлечению: что модель выдала и чем это кончилось.

Замер сделан потому, что предыдущие критерии не видели главного: ответ можно признать
годным по форме и при этом не иметь ни одного общего имени с документом. Проверка «оба конца
среди объявленных» такую выдумку не ловит — `CACHE_LIMIT` из примера в инструкции был
объявлен и потому разрешился, хотя в документе его нет. Поэтому здесь две шкалы, и их
смешивать нельзя:

  * **grounding** — встречается ли имя в тексте документа. Это качество извлечения.
  * **разрешимость** — объявлено ли имя среди имён того же ответа. Это то, что умеет
    проверять `_validate_edges`, и единственное, что может снести LLM-слой документа.

Прибор **офлайновый**: он читает уже записанные `exchange_*.json` и переигрывает их без
стенда, без LLM и без БД. Это сделано умышленно — пересчёт должен быть воспроизводим на
любой машине и не должен стоить прогона, иначе решение «пересчитать, прежде чем спорить»
нереализуемо.

**Что прибор не делает.** Не решает, хороши ли сущности по смыслу, не отбирает кандидатов в
уборку и не вводит порогов: `loops`, `mutual_pairs` и `hub` — измеренный факт, а не условие
приёмки. Причина в том, что порог по входящим выбрать после того, как увидели 0.889 против
0.5 против 0.143, — это критерий, написанный по итогу.

Каждая константа в `CONSTANTS` проверена руками по сырому ответу и ссылается на прогон, где
видно, откуда взялось число. Это не украшение: счётчик на тихом дефекте выглядит ровно так же,
как настоящий, пока он неверен, — за два дня один такой счётчик вернул уверенный ноль, и
второй то же самое. Поэтому константы обязательны и обязаны быть видимыми.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from graphrag_proto.ingestion_service.pipeline.orchestrator import (
    ENTITY_CANONICAL_FIELD,
    ENTITY_FIELD_ROLES,
    ENTITY_NAME_FIELDS,
    NON_NAME_FIELDS,
    _entity_names_from_item,
    _identity_key,
)

#: Корень `Dz4`: файл лежит в `prototype/infra/eval/`.
_DZ4 = Path(__file__).resolve().parents[3]
#: Куда ищем документы, чьи имена встречаются в `source_url`.
_CORPUS = _DZ4 / "docs"
#: Журналы обмена и вердикты.
_PROBE = _DZ4 / "test_artifacts" / "llm-probe"

INSTRUMENT = "offline-grounding-v3"

#: Разделители, которые выбрасываются на ступени SEP. Отображать их в пробел нельзя:
#: `underscore -> space` даёт `dedupstage` против `dedup stage`, то есть не лечит camel/snake.
#: Неработающий вариант предлагался дважды, поэтому причина здесь.
_SEPARATORS = " \t\r\n-_./:*()[]"

#: Ступени лестницы по убыванию строгости. Индекс в этом кортеже — «ранг»: меньше значит
#: лучше, и при слиянии повторов побеждает минимальный ранг.
_RUNGS = ("EXACT", "FOLDED", "SEP", "PROMPT-ONLY", "UNGROUNDED")

#: Отдельный вердикт, а не ступень лестницы. Счётчик, который не может измерить, обязан
#: сказать «не знаю», а не выдать правдоподобное число: отсутствие данных и отсутствие
#: дефекта — разные результаты. Без этого отсутствующий документ дал бы `UNGROUNDED`, то
#: есть ложное обвинение против модели, и такой случай уже случался в этом же приборе.
UNKNOWN = "UNKNOWN"

#: Ступени, которые не являются дефектом извлечения и потому не могут быть условием приёмки.
#: FOLDED поглощается существующим `_identity_key` по построению; SEP — задача склейки, и
#: единственный разрешённый путь для неё — алиас глоссария.
NOT_DEFECTS = ("FOLDED", "SEP")

#: Проверено руками по сырым ответам. Значения обязаны совпадать с пересчётом, иначе
#: расходится либо прибор, либо человек, который смотрел в ответ.
#: `jobs.degraded` — независимый источник для `loses_layer`.
CONSTANTS: dict[str, dict[str, Any]] = {
    "llm-probe-full-20260930-174640": {
        "names_distinct": 8,
        "names_SEP": 1,
        "ends_unresolvable": 0,
        "relations": 7,
        "self_loops": 7,
        "mutual_pairs": 0,
        "loses_layer": False,
        "seen_in_raw": "7 relations, each with from == to: DEDUP_STAGE, DEDUP_AUTO, DEDUP_LLM, SIMILAR_TO, *_THRESHOLD x3",
    },
    "expB-rule-only": {
        "names_distinct": 10,
        "names_SEP": 0,
        "ends_unresolvable": 0,
        "relations": 9,
        "self_loops": 0,
        "mutual_pairs": 0,
        "hub_fan_in": 8,
        "hub_name": "dedupstage",
        "loses_layer": False,
        "seen_in_raw": "8 of 9 relations end at DEDUPStage, the class name; the 9th is DEDUPStage -> SIMILAR_TO",
    },
    "expC-rule-plus-example": {
        "names_distinct": 8,
        "names_PROMPT-ONLY": 2,
        "ends_total": 12,
        "ends_unresolvable": 0,
        "relations": 6,
        "self_loops": 0,
        "mutual_pairs": 1,
        "loses_layer": False,
        "seen_in_raw": "SIMILAR_TO -> SIMILAR_TO_THRESHOLD and the reverse; CACHE_LIMIT and CACHE_EVICTION are declared but absent from the document",
    },
    "expD-rule-plus-example-big-doc": {
        "names_distinct": 5,
        "ends_total": 10,
        "ends_unresolvable": 4,
        "unresolvable_matching_non_name_field": 4,
        "relations": 5,
        "self_loops": 1,
        "mutual_pairs": 0,
        "loses_layer": True,
        "seen_in_raw": "each 'to' is the category value the model wrote for that very entity: chunker parameters, entity extraction and links, node types, graph search enabled; plus glossary -> glossary",
    },
    "expA-req-n-example-failed": {
        "ends_total": 12,
        "ends_unresolvable": 12,
        "unresolvable_matching_non_name_field": 7,
        "relations": 6,
        "self_loops": 0,
        "mutual_pairs": 0,
        "loses_layer": True,
        "seen_in_raw": "canonical_name holds the real identifiers (DEDUP_AUTO, DEDUP_AUTO_THRESHOLD, ...) and `id` holds the invented REQ-1..3 / CON-1..4: 7 distinct names, 12 endpoint slots. All 6 relations link by `id`, which _validate_edges does not read",
    },
    "llm-probe-20260930-172056": {
        "ends_total": 42,
        "ends_unresolvable": 2,
        "unresolvable_matching_non_name_field": 1,
        "relations_total": 21,
        "self_loops": 7,
        "mutual_pairs": 0,
        "loses_layer": True,
        "seen_in_raw": "record 4 links to `document`, which appears only in `id`; the file is a five-element array of five separate chunks, one of which answers with empty lists",
    },
}

CRITERION = {
    "instrument": INSTRUMENT,
    "written_before_run": True,
    "counts_over": "distinct names, not declarations: the model repeats one name across buckets (34 declarations = 8 distinct in the baseline)",
    "ladder": [
        "EXACT: the name occurs verbatim in the document",
        "FOLDED: NFKC + Yo/yo->Ye/e + casefold + whitespace collapse; found in the document",
        "SEP: additionally drops separators; found in the document",
        "PROMPT-ONLY: absent from the document at every rung, present in the prompt",
        "UNGROUNDED: not found in the document nor in the prompt",
    ],
    "ladder_merge": "best rung wins, so one name declared in several buckets is not counted as MIXED",
    "two_questions_never_conflated": "GROUNDING asks whether a name occurs in the DOCUMENT. RESOLVABILITY asks whether it is among the names declared in THIS response, which is all _validate_edges can see: the stage has no access to the document. The two come apart - CACHE_LIMIT in expC is absent from the document yet declared in the response, so it is ungrounded and resolvable at once, and that run did not degrade. Only unresolvable slots can cost the LLM layer; a ground verdict never can.",
    "not_defects": "FOLDED and SEP are not extraction defects. FOLDED is absorbed by the existing _identity_key by design; SEP is a merge problem whose only sanctioned repair is a glossary alias. Neither may appear in an acceptance criterion.",
    "name_source_contract": "The fields a name may come from are ENTITY_NAME_FIELDS in the orchestrator, and the instrument imports them rather than keeping its own list: it once read more fields than validation did and reported 0 unresolvable where there were 12. ENTITY_FIELD_ROLES states the same contract positively - the field the ontology calls canonical must resolve endpoints - so that editing the list is not a test failure. `id` is a domain identifier and is never a name source; it is the field expA's 12 invented endpoints came from, which is why widening the list would hide the defect instead of fixing it.",
    "denominator": "ends_unresolvable is counted over endpoint SLOTS, and ends_total is that denominator. Counting over relations doubled every ratio once: expA read as 12 of 5 relations instead of 12 of 12 endpoints, expD as 4 of 5 instead of 4 of 10. Each relation has two endpoints, and in expA all 6 lost both.",
    "name_classes_not_gated_here": "PROMPT-ONLY and UNGROUNDED are different classes with different owners. Pass/fail for them belongs in the run manifest written before the run.",
    "structure_is_measured_not_gated": "loops, mutual_pairs and hub are a measured fact, not a gate. No hub threshold is introduced: choosing one after seeing 0.889 against 0.5 against 0.143 would be a criterion written after the result.",
    "known_method_limits": "PROMPT-ONLY means 'absent from the document but present in the prompt'. The prompt is NOT split into instruction and chunk, so chunk_tail_in_doc reports whether the chunk is still consistent with the current document file; false means the document changed after the run and every name verdict for that record is suspect.",
    "rejected_normalization": "casefold alone and mapping a separator to a space both FAIL on camel<->snake (dedupstage vs dedup stage). Only DROPPING separators works. Recorded because the non-working version was proposed twice.",
    "constants": "See CONSTANTS in the tool source. Every value is hand-checked against a raw response and carries seen_in_raw.",
}


def _strip(value: str) -> str:
    """Ранг SEP: то же, что `_identity_key`, плюс выброшенные разделители."""
    return "".join(ch for ch in _identity_key(value) if ch not in _SEPARATORS)


def _document_index() -> dict[str, Path]:
    """Имя файла → путь. `source_url` в журнале приходит то именем, то путём в корпусе."""
    index: dict[str, Path] = {}
    for path in _CORPUS.rglob("*"):
        if path.is_file():
            index.setdefault(path.name, path)
    return index


def _rung(name: str, document: str, document_fold: str, document_strip: str, prompt_strip: str) -> int | None:
    """Ранг имени по лестнице; 0 — точное, 4 — не найдено нигде, `None` — измерить нельзя."""
    if not document:
        return None
    if name in document:
        return 0
    if _identity_key(name) in document_fold:
        return 1
    if _strip(name) in document_strip:
        return 2
    if _strip(name) in prompt_strip:
        return 3
    return 4


def _entity_buckets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Все объекты сущностей из ответа: корзины сущностей, без `relationships`/`links`."""
    out: list[dict[str, Any]] = []
    for bucket, items in payload.items():
        if bucket in {"relationships", "links"} or not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                out.append(item)
    return out


def _relations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    relations = payload.get("relationships")
    if relations is None:
        relations = payload.get("links")
    if not isinstance(relations, list):
        return []
    return [r for r in relations if isinstance(r, dict)]


def _names_of(item: dict[str, Any]) -> list[str]:
    """Имена в том виде, в каком их записала бы `_entity_record`.

    Список полей и порядок их выбора берутся **из production-кода**, а не пишутся здесь
    второй раз. Расхождение двух реализаций уже стоило неверного вывода: прибор, взявший
    больше полей, чем валидатор, показал «0 неразрешённых концов» там, где их было 12, и
    выглядел при этом правдоподобно. `ENTITY_NAME_FIELDS` — контракт, и `id` в него не
    входит: `id` это идентификатор домена, а не имя.
    """
    canonical, name = _entity_names_from_item(item)
    out: list[str] = []
    for key in ENTITY_NAME_FIELDS:
        # Из ответа модели запись получает `canonical` и `name`; `tag_id` и `canonical_name`
        # в записи не выставляются, так что читать их тут нечего.
        value = {"canonical": canonical, "name": name}.get(key)
        if isinstance(value, str) and value.strip():
            out.append(value.strip())
    return out


def _non_name_values(item: dict[str, Any]) -> list[tuple[str, str]]:
    """`(ключ значения, имя поля)` для полей, которые именем не являются.

    Поле возвращается вместе со значением, иначе распределение «какое поле подставило конец»
    не восстановить: по одному лишь значению `id` и `category` неразличимы.
    """
    out: list[tuple[str, str]] = []
    for field in NON_NAME_FIELDS:
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            out.append((_identity_key(value.strip()), field))
    return out


def _ends_of(relation: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for side in ("from", "to"):
        value = relation.get(side)
        if isinstance(value, str) and value.strip():
            out.append(value.strip())
    return out


def _structure(relations: list[dict[str, Any]]) -> dict[str, Any]:
    """Петли, взаимные пары и хаб. Измерение, не условие приёмки."""
    pairs = [(_identity_key(a), _identity_key(b)) for a, b in ((_ends_of(r) + ["", ""])[:2] for r in relations) if a and b]
    loops = sorted({a for a, b in pairs if a == b})
    mutual: set[str] = set()
    for index, (a, b) in enumerate(pairs):
        if a == b:
            continue
        for other in pairs[index + 1 :]:
            if a == other[1] and b == other[0]:
                mutual.add("|".join(sorted((a, b))))
    fan_in: Counter[str] = Counter(b for _, b in pairs)
    hub_name, hub_in = ("", 0)
    if fan_in:
        hub_name, hub_in = max(fan_in.items(), key=lambda kv: (kv[1], kv[0]))
    return {
        "is_gate": False,
        "relations": len(pairs),
        "self_loops": len(loops),
        "self_loop_names": loops,
        "mutual_pairs": len(mutual),
        "mutual_pair_names": sorted(mutual),
        "hub_fan_in": hub_in,
        "hub_share": round(hub_in / len(pairs), 3) if pairs else 0,
        "hub_name": hub_name,
        "note": "measured fact, not a gate; no hub threshold is defined",
    }


def _verdict_for(exchange: Path, documents: dict[str, Path]) -> list[dict[str, Any]]:
    """Вердикт по одному набору. Пустой список — журнал есть, а записей в нём нет."""
    records = json.loads(exchange.read_text(encoding="utf-8"))
    if isinstance(records, dict):
        records = [records]
    out: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        labels = record.get("labels")
        if not isinstance(labels, dict) or not labels.get("chunk_id"):
            # Раньше молча схлопывало несколько записей в одну и давало правдоподобный
            # неверный вердикт. Форма файла обязана быть известна, иначе считать нельзя.
            raise ValueError(f"{exchange.name}: запись {index} без labels — форма файла не та")
        raw = record.get("response")
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue

        prompt = str(record.get("prompt") or "")
        source_url = str(labels.get("source_url") or "")
        document = documents.get(Path(source_url).name)
        text = document.read_text(encoding="utf-8") if document else ""
        text_fold, text_strip = _identity_key(text), _strip(text)
        prompt_strip = _strip(prompt)
        tail = prompt[-200:]
        entities = _entity_buckets(payload)
        relations = _relations(payload)

        best_names: dict[str, int | None] = {}
        declarations = 0
        declared: set[str] = set()
        # Ключ значения -> поля, в которых оно встречалось. Нужен именно словарь, а не счётчик
        # попаданий: попадание уже измерено, а распределение по полям отвечает на другой вопрос —
        # систематический ли это уклон в «поле как узел» (тогда кандидат на промт) или разнобой
        # (тогда дефект формы ответа). Одно число эти два случая не различает.
        non_name_fields: dict[str, set[str]] = {}
        for entity in entities:
            for name in _names_of(entity):
                declarations += 1
                declared.add(_identity_key(name))
                rank = _rung(name, text, text_fold, text_strip, prompt_strip)
                key = _identity_key(name)
                if key not in best_names or _better(rank, best_names[key]):
                    best_names[key] = rank
            for key, field in _non_name_values(entity):
                non_name_fields.setdefault(key, set()).add(field)
        non_name_values = set(non_name_fields)

        name_tally = _tally_rungs(best_names)
        name_detail = _detail_rungs(best_names, _RUNGS[2:])

        best_ends: dict[str, int | None] = {}
        end_slots = 0
        unresolvable: list[str] = []
        unresolvable_slots = 0
        for relation in relations:
            for name in _ends_of(relation):
                end_slots += 1
                rank = _rung(name, text, text_fold, text_strip, prompt_strip)
                key = _identity_key(name)
                if key not in best_ends or _better(rank, best_ends[key]):
                    best_ends[key] = rank
                if key not in declared:
                    unresolvable_slots += 1
                    if key not in unresolvable:
                        unresolvable.append(key)
        end_tally = _tally_rungs(best_ends)
        end_detail = _detail_rungs(best_ends, ("PROMPT-ONLY", "UNGROUNDED"))

        out.append(
            {
                "record_index": index,
                "source_url": source_url,
                "chunk_id": labels.get("chunk_id"),
                "doc_matched": document is not None,
                "doc_path": str(document) if document else None,
                "chunk_tail_in_doc": bool(document) and tail in text,
                "names": {
                    "distinct": len(best_names),
                    "declarations": declarations,
                    UNKNOWN: name_tally.get(UNKNOWN, 0),
                    **{rung: name_tally.get(rung, 0) for rung in _RUNGS},
                },
                "names_detail": {rung: sorted(values) for rung, values in name_detail.items()},
                "ends": {
                    "total": end_slots,
                    "distinct": len(best_ends),
                    UNKNOWN: end_tally.get(UNKNOWN, 0),
                    **{rung: end_tally.get(rung, 0) for rung in _RUNGS},
                },
                "ends_detail": {rung: sorted(values) for rung, values in end_detail.items()},
                "ends_resolution": {
                    "question": "is the endpoint among the names declared in THIS response? this is what _validate_edges checks, and the only thing that can cost the LLM layer",
                    "key": "orchestrator._identity_key, imported, not reimplemented; name fields are ENTITY_NAME_FIELDS from the same module, so this cannot drift from the code",
                    "resolvable_slots": end_slots - unresolvable_slots,
                    "unresolvable_slots": unresolvable_slots,
                    "unresolvable_names": sorted(unresolvable),
                    "unresolvable_matching_non_name_field": sorted(set(unresolvable) & non_name_values),
                    "unresolvable_by_field": _by_field(unresolvable, non_name_fields),
                    "non_name_fields_watched": list(NON_NAME_FIELDS),
                    "non_name_field_note": "non-empty means the model named the endpoint by a field that is not a name (`id`, `category`). That is a measurable fact, not a guess: expA is `id`, expD is `category`. `unresolvable_by_field` separates a systematic lean into one field (a prompt candidate) from a scatter across fields (a response-shape defect).",
                },
                "current_code_loses_llm_layer": unresolvable_slots > 0,
                "structure": _structure(relations),
            }
        )
    return out


def _by_field(unresolvable: list[str], non_name_fields: dict[str, set[str]]) -> dict[str, list[str]]:
    """Распределение неразрешённых концов по полям, из которых модель их взяла.

    Пустые поля не выводятся. Иначе каждая запись тащила бы ключи всех просматриваемых полей с
    нулём, и «уклон в `category`» не отличалось бы от «поля не смотрели вовсе» — то есть прибор
    повторил бы ровно ту ошибку, которую здесь и чинят.
    """
    keys = set(unresolvable)
    out: dict[str, list[str]] = {}
    for field in sorted({f for key in keys for f in non_name_fields.get(key, ())}):
        matched = sorted(key for key in keys if field in non_name_fields.get(key, ()))
        if matched:
            out[field] = matched
    return out


def _better(rank: int | None, current: int | None) -> bool:
    """Побеждает ли `rank`. `None` — «измерить нельзя» и проигрывает всему измеримому."""
    if rank is None:
        return False
    if current is None:
        return True
    return rank < current


def _tally_rungs(best: dict[str, int | None]) -> Counter[str]:
    """Разложение по ступеням. Неизмеримое не превращается в `UNGROUNDED`."""
    return Counter(UNKNOWN if rank is None else _RUNGS[rank] for rank in best.values())


def _detail_rungs(best: dict[str, int | None], wanted: Sequence[str]) -> dict[str, list[str]]:
    """Подробности только по запрошенным ступеням, чтобы JSON не пухнул."""
    out: dict[str, list[str]] = {rung: [] for rung in wanted}
    for key, rank in best.items():
        if rank is None:
            continue
        rung = _RUNGS[rank]
        if rung in out:
            out[rung].append(key)
    return out


def _sum(records: list[dict[str, Any]], *path: str) -> int:
    """Сумма одного числового поля по всем записям набора: `_sum(records, "names", "EXACT")`."""
    total = 0
    for record in records:
        node: Any = record
        for step in path:
            node = node.get(step, {}) if isinstance(node, dict) else {}
        total += int(node or 0)
    return total


def _hub(records: list[dict[str, Any]]) -> tuple[int, str]:
    best = max((record["structure"]["hub_fan_in"] for record in records), default=0)
    names = sorted(
        {
            record["structure"]["hub_name"]
            for record in records
            if record["structure"]["hub_fan_in"] == best
        }
    )
    return best, (names[0] if len(names) == 1 else "|".join(names))


#: Имя поля в CONSTANTS -> как его достать из записей. Таблица вместо цепочки условий:
#: цепочка выросла до восьми ветвей, и одна из них считала словари вместо чисел.
_FIELDS = {
    "names_distinct": lambda r: _sum(r, "names", "distinct"),
    "names_EXACT": lambda r: _sum(r, "names", "EXACT"),
    "names_FOLDED": lambda r: _sum(r, "names", "FOLDED"),
    "names_SEP": lambda r: _sum(r, "names", "SEP"),
    "names_PROMPT-ONLY": lambda r: _sum(r, "names", "PROMPT-ONLY"),
    "names_UNGROUNDED": lambda r: _sum(r, "names", "UNGROUNDED"),
    # Знаменатель для `ends_unresolvable`. Без него «4 из 10» и «4 из 5» неразличимы, и
    # отношение легко удваивает, приняв число связей за число концов.
    "ends_total": lambda r: _sum(r, "ends", "total"),
    "ends_unresolvable": lambda r: _sum(r, "ends_resolution", "unresolvable_slots"),
    # Здесь в JSON лежит список, а не число, поэтому длины, а не сумма.
    "unresolvable_matching_non_name_field": lambda r: sum(
        len(record["ends_resolution"]["unresolvable_matching_non_name_field"]) for record in r
    ),
    "names_UNKNOWN": lambda r: _sum(r, "names", UNKNOWN),
    "ends_UNKNOWN": lambda r: _sum(r, "ends", UNKNOWN),
    "relations": lambda r: _sum(r, "structure", "relations"),
    "relations_total": lambda r: _sum(r, "structure", "relations"),
    "self_loops": lambda r: _sum(r, "structure", "self_loops"),
    "mutual_pairs": lambda r: _sum(r, "structure", "mutual_pairs"),
    "hub_fan_in": lambda r: _hub(r)[0],
    "hub_name": lambda r: _hub(r)[1],
    "loses_layer": lambda r: any(record["current_code_loses_llm_layer"] for record in r),
}

#: Поля, которые нельзя вывести из записей и которые прибор не проверяет сам.
_UNCHECKED = ("seen_in_raw",)


def check_constants(verdicts: dict[str, list[dict[str, Any]]]) -> list[str]:
    """Сверка с ручными константами. Несовпадение — это ошибка прибора или человека."""
    problems: list[str] = []
    for run, expected in CONSTANTS.items():
        records = verdicts.get(run)
        if records is None:
            problems.append(f"{run}: прогон не найден, а для него есть константы")
            continue
        for field, value in expected.items():
            if field in _UNCHECKED:
                continue
            extract = _FIELDS.get(field)
            if extract is None:
                problems.append(f"{run}: поле {field!r} есть в константах, но не извлекается")
                continue
            got = extract(records)
            if got != value:
                problems.append(f"{run}: {field}={got!r} != {value!r}")
    return problems


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    check = "--check" in args
    documents = _document_index()
    verdicts: dict[str, list[dict[str, Any]]] = {}
    written = 0
    for directory in sorted(p for p in _PROBE.iterdir() if p.is_dir()):
        exchange = directory / "exchange_fresh.json"
        if not exchange.exists():
            continue
        try:
            records = _verdict_for(exchange, documents)
        except ValueError as exc:
            print(f"[{directory.name}] {exc}", file=sys.stderr)
            return 2
        if records is None:
            # Журнал есть, а разбирать нечего: ноль записей. Молчать здесь нельзя — прогон
            # выпал бы из вердиктов, и по набору файлов нельзя было бы понять, что он был.
            # На практике это деградация без причины: `165440` деградировал, а журнал обмена
            # пуст, и восстановить причину по этому набору нельзя.
            records = []
        verdicts[directory.name] = records
        target = directory / "grounding_verdict.json"
        payload = {
            "instrument": INSTRUMENT,
            "criterion": CRITERION,
            "name_source_fields": list(ENTITY_NAME_FIELDS),
            "name_field_roles": dict(ENTITY_FIELD_ROLES),
            "canonical_name_field": ENTITY_CANONICAL_FIELD,
            "run": directory.name,
            "attributable": bool(records),
            "attribution_note": (
                ""
                if records
                else "exchange log holds zero records, so the cause of this run cannot be "
                "determined from artifacts; if jobs.json says degraded, treat the reason as unknown"
            ),
            "summary": records,
        }
        if not check:
            target.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
            )
            written += 1
        else:
            existing = json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
            if existing != payload:
                print(f"[{directory.name}] вердикт на диске расходится с пересчётом", file=sys.stderr)
                return 1

    problems = check_constants(verdicts)
    for problem in problems:
        print(f"[constants] {problem}", file=sys.stderr)
    if problems:
        return 1
    if check:
        print(f"проверено наборов: {len(verdicts)}")
    else:
        print(f"записано вердиктов: {written}, наборов: {len(verdicts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
