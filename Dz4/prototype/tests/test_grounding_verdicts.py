"""Вердикты по извлечению пересчитываются из журналов и не должны разъезжаться.

Артефакт без проверки — это лог. Здесь ровно две вещи, которые и должны быть зафиксированы:

1. **Пересчёт совпадает с тем, что лежит на диске.** Сверяется не байт в байт, а значения:
   форма JSON меняется вместе с прибором, и сравнение байтов тарило бы шумом. Смысл,
   который тут охраняется, — «прибор и артефакт не разошлись в числах», и для него сравнение
   значений строже, а не слабее.

2. **Константы из прибора сходятся с ручной сверкой.** Они лежат в исходнике прибора, у
   каждой есть `seen_in_raw`. Это единственная защита от молчаливого нуля: за два дня один
   счётчик тихого дефекта вернул уверенный ноль, и он пережил два отчёта.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

_EVAL = Path(__file__).resolve().parents[1] / "infra" / "eval"


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _EVAL / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tool() -> Any:
    return _load("score_extraction_grounding")


@pytest.fixture(scope="module")
def verdicts(tool: Any) -> dict[str, list[dict[str, Any]]]:
    documents = tool._document_index()
    out: dict[str, list[dict[str, Any]]] = {}
    for directory in sorted(p for p in tool._PROBE.iterdir() if p.is_dir()):
        exchange = directory / "exchange_fresh.json"
        if not exchange.exists():
            continue
        records = tool._verdict_for(exchange, documents)
        if records:
            out[directory.name] = records
    return out


def test_every_probe_run_has_a_verdict(tool: Any) -> None:
    """Каждый набор с журналом обязан иметь вердикт — даже с пустым.

    Пустой вердикт информативнее отсутствующего: он говорит, что прибор смотрел и не нашёл
    ничего, тогда как отсутствие файла выглядит как «просто не считали». На практике именно
    так и выяснилось, что `165440` деградировал при пустом журнале, то есть причина этой
    деградации по артефактам не восстанавливается вовсе.
    """
    import json

    root = tool_probe_root()
    for directory in sorted(p for p in root.iterdir() if p.is_dir()):
        if not (directory / "exchange_fresh.json").exists():
            continue
        target = directory / "grounding_verdict.json"
        assert target.exists(), f"{directory.name}: журнал есть, вердикта нет"
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["instrument"] == tool.INSTRUMENT
        if not payload["summary"]:
            assert payload["attributable"] is False
            assert payload["attribution_note"]


def test_unattributable_run_is_declared_not_hidden(tool: Any) -> None:
    """Прогон с пустым журналом и деградацией обязан быть помечен, а не выпасть из счёта."""
    import json

    empty = [
        name
        for name in (p.name for p in tool_probe_root().iterdir() if p.is_dir())
        if (tool_probe_root() / name / "exchange_fresh.json").exists()
        and not json.loads((tool_probe_root() / name / "exchange_fresh.json").read_text(encoding="utf-8"))
    ]
    for name in empty:
        payload = json.loads((tool_probe_root() / name / "grounding_verdict.json").read_text(encoding="utf-8"))
        jobs = json.loads((tool_probe_root() / name / "jobs.json").read_text(encoding="utf-8"))
        assert any(job.get("degraded") for job in jobs), f"{name}: пустой журнал, но и деградации нет"
        assert "cannot be determined" in payload["attribution_note"]


def tool_probe_root() -> Path:
    return Path(__file__).resolve().parents[2] / "test_artifacts" / "llm-probe"


def test_manual_constants_hold(verdicts: dict[str, list[dict[str, Any]]], tool: Any) -> None:
    """Ручные константы — эталон, и прибор обязан с ними сходиться."""
    assert tool.check_constants(verdicts) == []


def test_verdict_files_match_recomputation(tool: Any, verdicts: dict[str, list[dict[str, Any]]]) -> None:
    for run, records in verdicts.items():
        target = tool._PROBE / run / "grounding_verdict.json"
        assert target.exists(), f"{run}: вердикт не записан, пересчёт не с чем сверить"
        import json

        on_disk = json.loads(target.read_text(encoding="utf-8"))
        assert on_disk["instrument"] == tool.INSTRUMENT, f"{run}: вердикт сделан другим прибором"
        assert on_disk["summary"] == records, f"{run}: значения на диске разошлись с пересчётом"


def test_id_is_not_a_name_source(tool: Any) -> None:
    """`id` не признаётся именем. Это контракт, а не наблюдение.

    Проверяется **поведением валидатора**, а не видом списка полей: взятый в ADR перечень —
    это утверждение о коде, и оно должно падать вместе с кодом. Связь, концы которой взяты
    из `id`, обязана быть отвергнута.

    Оговорка: если `id` когда-нибудь станет допустимым источником имени, тест и контракт в
    ADR меняются вместе, а тест не отключается. Иначе он перестанет охранять утверждение и
    станет защитой от его исправления — то есть останется зелёным на лжи.
    """
    from graphrag_proto.ingestion_service.pipeline.orchestrator import (
        ExtractionModelError,
        ExtractStage,
    )

    records = [{"canonical_name": "REAL", "name": "REAL", "id": "INVENTED"}]
    with pytest.raises(ExtractionModelError):
        ExtractStage._validate_edges([{"from": "REAL", "to": "INVENTED"}], records)
    # Объявленное имя, взятое из `id` же, разрешается: `id` не имя, но и не помеха.
    ExtractStage._validate_edges([{"from": "REAL", "to": "REAL"}], records)


def test_the_field_the_ontology_calls_canonical_is_always_a_name_source(tool: Any) -> None:
    """Контракт формулируется положительно: каноническое поле обязано быть источником имени.

    Проверка «`id` не входит» замораживала бы сегодняшний перечень: первая же правка онтологии
    упёрлась бы в тест как в ошибку, хотя решать её надо перечнем, а не откатом. Поэтому
    утверждение здесь одно — то поле, которое онтология называет каноническим, разрешает концы,
    — и оно переживает любую правку состава списка.
    """
    from graphrag_proto.ingestion_service.pipeline.orchestrator import ExtractStage

    canonical_field = tool.ENTITY_CANONICAL_FIELD
    assert canonical_field in tool.ENTITY_NAME_FIELDS
    records = [{canonical_field: "REAL", "name": "REAL", "id": "INVENTED"}]
    ExtractStage._validate_edges([{"from": "REAL", "to": "REAL"}], records)


def test_expA_signature_is_declared_ids(tool: Any, verdicts: dict[str, list[dict[str, Any]]]) -> None:
    """Отпечаток expA: все неразрешённые концы — это объявленные `id`."""
    record = verdicts["expA-req-n-example-failed"][0]
    resolution = record["ends_resolution"]
    assert resolution["unresolvable_slots"] == 12
    assert resolution["unresolvable_matching_non_name_field"] == resolution["unresolvable_names"]
    assert record["code_behaviour"]["loses_llm_layer"] is True


def test_expD_signature_is_category(tool: Any, verdicts: dict[str, list[dict[str, Any]]]) -> None:
    """Отпечаток expD: концы взяты из значений `category` — «поле как узел»."""
    resolution = verdicts["expD-rule-plus-example-big-doc"][0]["ends_resolution"]
    assert resolution["unresolvable_slots"] == 4
    assert resolution["unresolvable_matching_non_name_field"] == resolution["unresolvable_names"]


def test_code_prediction_is_not_measured_as_measurement(tool: Any, verdicts: dict[str, list[dict[str, Any]]]) -> None:
    """Предсказание поведения кода обязано называть версию, а не выдавать себя за измерение.

    Прибор переигрывает журнал через текущий код, поэтому ровно одно поле из всего вердикта
    зависит от версии кода: `code_behaviour.loses_llm_layer`. Как только неразрешённый конец
    начнёт записываться фактом (ADR-037, случай 3), поле станет `false` — и это не будет
    ошибкой расчёта, это будет другой вопрос. Пока версия не названа, поле просто тихо
    перестанет соответствовать действительности, а выглядеть будет так же.
    """
    assert tool.CODE_BEHAVIOUR in tool.CRITERION.values() or any(
        "unresolvable" in str(value) for value in tool.CRITERION.values()
    ), "предсказываемое поведение кода не описано в CRITERION"
    for run, records in verdicts.items():
        for record in records:
            assert "code_behaviour" in record, f"{run}: поле предсказания отсутствует"
            assert record["code_behaviour"]["predicts"] == tool.CODE_BEHAVIOUR
            assert isinstance(record["code_behaviour"]["would_write_facts"], int)
            # Старое имя поля означало «слой потерян» как свойство ответа. Его больше нет:
            # ответ не теряет слой, теряет его код, и это разные утверждения.
            assert "current_code_loses_llm_layer" not in record


def test_measurement_does_not_move_with_the_code(tool: Any, verdicts: dict[str, list[dict[str, Any]]]) -> None:
    """Всё, кроме `code_behaviour`, измеряет журнал и от версии кода не зависит.

    Это различение держит `--check` честным: после пункта 3 рассинхронизируется только
    предсказание, а измерения останутся прежними, и снимок будет отличаться ровно одним
    предсказанием. Если бы от кода зависело что-то ещё, «устарело» и «сломано» стало бы
    неразличимыми.
    """
    record = verdicts["expA-req-n-example-failed"][0]
    measured = {key: value for key, value in record.items() if key != "code_behaviour"}
    assert {"ends", "ends_resolution", "names", "structure", "doc_matched"} <= set(measured)
    assert record["code_behaviour"]["loses_llm_layer"] is True
    assert record["code_behaviour"]["would_write_facts"] == len(record["ends_resolution"]["unresolvable_names"])
    # Счётчик фактов совпадает с числом неразрешённых имён, а не со слотами: один факт на имя.
    assert record["ends_resolution"]["unresolvable_slots"] > record["code_behaviour"]["would_write_facts"]


def test_end_keys_match_the_validator(tool: Any) -> None:
    """Прибор читает те же ключи концов, что и валидация, и в том же порядке.

    Дыра была в обе стороны и обе молчали. Прибор брал больше полей, чем валидация, и показывал
    «0 неразрешённых» там, где их было 12. Потом брал меньше: `_validate_edges` принимает
    `from_id`/`to_id` через `relation.get("from") or relation.get("from_id")`, а прибор знал
    только `from`/`to` — и по такой связи показывал ноль концов вместо двух. Отсутствие данных
    и отсутствие связи неразличимы по такому молчанию, поэтому оба случая закрыты здесь явно.
    """
    assert tool.END_KEYS == (("from", "from_id"), ("to", "to_id"))
    # Порядок обязателен: при обоих полях валидация берёт первое, и прибор должен считать тот же конец.
    assert tool._ends_of({"from": "A", "from_id": "B", "to": "C", "to_id": "D"}) == ["A", "C"]
    # Алиас без основного поля — тоже конец, иначе связь выпала бы из счёта целиком.
    assert tool._ends_of({"from_id": "A", "to_id": "C"}) == ["A", "C"]


def test_denominator_is_endpoint_slots_not_relations(tool: Any, verdicts: dict[str, list[dict[str, Any]]]) -> None:
    """Знаменатель — число концов, а не число связей.

    На этом уже удвоилась доля: expA посчитан как «12 из 5 связей» вместо «12 из 12 концов»,
    expD — как «4 из 5». Разница видна сразу, потому что у каждой связи два конца, и в expA все
    6 связей потеряли оба. Само число попаданий от знаменателя не зависит, поэтому и нужен
    отдельный счётчик концов, а не только число связей.
    """
    records = verdicts["expA-req-n-example-failed"]
    for record in records:
        assert record["ends"]["total"] == record["structure"]["relations"] * 2
        assert record["ends"]["total"] == record["ends_resolution"]["resolvable_slots"] + record[
            "ends_resolution"
        ]["unresolvable_slots"]


def test_unresolvable_is_measured_per_field(tool: Any, verdicts: dict[str, list[dict[str, Any]]]) -> None:
    """Распределение по полям отличает уклон в одно поле от разнобоя.

    Одно число «попало в не-имя» эти два случая не различает, а решения у них разные: уклон в
    `category` — кандидат на промт, разнобой по разным полям — дефект формы ответа.
    """
    lean = verdicts["expD-rule-plus-example-big-doc"][0]["ends_resolution"]["unresolvable_by_field"]
    assert set(lean) == {"category"}, f"expD должен уходить только в category, а не в {lean}"
    exp_a = verdicts["expA-req-n-example-failed"][0]["ends_resolution"]["unresolvable_by_field"]
    assert set(exp_a) == {"id"}, f"expA должен уходить только в id, а не в {exp_a}"
    # Поля с нулём не выводятся: иначе «уклон в поле» и «поле не смотрели» неразличимы.
    for records in verdicts.values():
        for record in records:
            assert all(value for value in record["ends_resolution"]["unresolvable_by_field"].values())
            assert "id" in record["ends_resolution"]["non_name_fields_watched"]


def test_name_fields_are_shared_with_the_validator(tool: Any) -> None:
    """Прибор и валидация обязаны брать имена из одного места.

    Расхождение уже случалось: прибор смотрел на большее число полей, чем валидатор, и
    показал «0 неразрешённых» там, где их было 12. Теперь список импортирован, но контракт
    надо охранять — иначе следующая правка снова разъедется.
    """
    from graphrag_proto.ingestion_service.pipeline.orchestrator import (
        ENTITY_NAME_FIELDS,
        _declared_names,
        _entity_names_from_item,
    )

    item = {"canonical_name": "Canonical", "canonical": "Other", "name": "Other"}
    canonical, name = _entity_names_from_item(item)
    assert (canonical, name) == ("Canonical", "Other")
    # Прибор повторяет ровно этот выбор, а не свой.
    assert tool._names_of(item) == [canonical, name]
    assert tool.ENTITY_NAME_FIELDS is ENTITY_NAME_FIELDS
    assert _declared_names([{"canonical": canonical, "name": name}]) == {canonical.lower(), name.lower()}


def test_missing_document_is_unknown_not_ungrounded(tool: Any) -> None:
    """Нет документа — это «не знаю», а не «не grounded».

    Иначе прибор обвиняет модель в выдумке на данных, которых у него просто нет. Ровно это
    он и делал: отсутствующий документ превращался в `UNGROUNDED`, то есть в ложное
    обвинение. Отсутствие данных и отсутствие дефекта — разные результаты.
    """
    assert tool._rung("ANY", "", "", "", "") is None
    tally = tool._tally_rungs({"a": None, "b": 0, "c": 4})
    assert tally[tool.UNKNOWN] == 1
    assert tally["EXACT"] == 1
    assert tally["UNGROUNDED"] == 1
    assert tool._better(None, 2) is False
    assert tool._better(1, None) is True


def test_name_verdicts_are_not_silently_unknown(verdicts: dict[str, list[dict[str, Any]]]) -> None:
    """`UNKNOWN` допустим, но обязан быть виден, а не спрятан среди нулей."""
    for run, records in verdicts.items():
        for record in records:
            if record["doc_matched"]:
                assert record["names"]["UNKNOWN"] == 0, f"{run}: документ есть, мерить можно"


def test_loses_layer_agrees_with_jobs(verdicts: dict[str, list[dict[str, Any]]]) -> None:
    """`jobs.degraded` — независимый источник правды о том, что было гейт-падение.

    Сверка обязательна: прибор, считающий по документу, однажды показал «0 неразрешённых»
    при `degraded = true`, то есть правдоподобную неправду.
    """
    import json

    root = tool_probe_root()
    for run, records in verdicts.items():
        jobs_path = root / run / "jobs.json"
        if not jobs_path.exists():
            continue
        for job in json.loads(jobs_path.read_text(encoding="utf-8")):
            if not job.get("degraded"):
                continue
            assert any(
                record["code_behaviour"]["loses_llm_layer"]
                for record in records
                if record["source_url"].endswith(job["source_url"].split("/")[-1])
            ), f"{run}: degraded у {job['source_url']}, но ни одна запись не теряет слой"


def test_structure_is_not_a_gate(tool: Any, verdicts: dict[str, list[dict[str, Any]]]) -> None:
    """Петли, взаимные пары и хаб — измерение. Гейтом они стали бы критерием по итогу."""
    for records in verdicts.values():
        for record in records:
            assert record["structure"]["is_gate"] is False
    assert tool.NOT_DEFECTS == ("FOLDED", "SEP")
