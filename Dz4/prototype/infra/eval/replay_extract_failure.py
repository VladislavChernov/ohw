"""Воспроизведение отказа ExtractStage на записанном ответе.

Вопрос, который нельзя решить чтением: чем именно был вызван `model_error` в каждом из
наборов. Первая версия прибора `score_extraction_grounding` считала неразрешённые концы по
объявленным именам и для expA давала ноль — при том что джоба деградировала. Значит причина
в другом, и называть её «неразрешёнными концами» без проверки нельзя.

Скрипт собирает сущности ровно тем способом, которым это делает `_entity_record`
(ключи `canonical` и `name`, без `canonical_name`), и зовёт настоящий
`ExtractStage._validate_edges`. Путь до `domain_profile.it.yaml` задан явно, потому что
`allowed_fields` зависит от профиля.

**Запуск требует `PYTHONPATH`, и это не формальность.** В окружении пакет установлен из
`/app/src`, поэтому `uv run python infra/eval/...` без `PYTHONPATH` импортирует **ту** копию и
молча измеряет не тот код:

    PYTHONPATH=/repo/prototype/src uv run --no-sync python infra/eval/replay_extract_failure.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from graphrag_proto.ingestion_service.pipeline.orchestrator import (
    CONTEXT_NODE_LABEL,
    ExtractionModelError,
    ExtractStage,
    _identity_key,
    _plural_entity_key,
)

PROBE = Path("/repo/test_artifacts/llm-probe")
PROFILE = Path("/repo/prototype/domain_profiles/domain_profile.it.yaml")


def allowed_fields() -> set[str]:
    """Ровно то, что собирает `_extract_llm`: профиль -> плюрали -> плюс связи."""
    import yaml

    data = yaml.safe_load(PROFILE.read_text(encoding="utf-8")) or {}
    node_types = ((data.get("ontology") or {}).get("node_types")) or []
    payloads = {"tags", "entities"}
    for node in node_types:
        if isinstance(node, dict) and node.get("type"):
            payloads.add(_plural_entity_key(str(node["type"])))
    return {"relationships", "links"} | payloads


def records_of(payload: dict[str, Any], allowed: set[str]) -> list[dict[str, Any]]:
    """Записи в том же виде, что даёт `_entity_record`: `canonical` и `name`."""
    records: list[dict[str, Any]] = []
    for key, items in payload.items():
        if key in {"relationships", "links"} or key not in allowed or not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            canonical = item.get("canonical_name") or item.get("canonical") or item.get("name")
            if not isinstance(canonical, str) or not canonical.strip():
                raise ValueError(f"no canonical in {key}")
            name = item.get("name")
            records.append(
                {
                    "type": CONTEXT_NODE_LABEL,
                    "name": name if isinstance(name, str) and name.strip() else canonical,
                    "canonical": canonical,
                }
            )
    return records


def main() -> int:
    allowed = allowed_fields()
    print(f"allowed_fields: {sorted(allowed)}")
    for directory in sorted(p for p in PROBE.iterdir() if p.is_dir()):
        exchange = directory / "exchange_fresh.json"
        if not exchange.exists():
            continue
        raw_records = json.loads(exchange.read_text(encoding="utf-8"))
        records_in = raw_records if isinstance(raw_records, list) else [raw_records]
        for index, record in enumerate(records_in, start=1):
            response = record.get("response")
            if not isinstance(response, str) or not response.strip():
                continue
            label = f"{directory.name}[{index}]"
            try:
                payload = json.loads(response)
            except json.JSONDecodeError as exc:
                print(f"{label}: 1 not-json ({exc})")
                continue
            if not isinstance(payload, dict):
                print(f"{label}: 1 not-object")
                continue
            unknown = sorted(set(payload) - allowed)
            if unknown:
                print(f"{label}: 2 unknown-fields {unknown}")
                continue
            try:
                records = records_of(payload, allowed)
            except ValueError as exc:
                print(f"{label}: 3/4 {exc}")
                continue
            relations = payload.get("relationships", payload.get("links", []))
            if not isinstance(relations, list):
                print(f"{label}: 5 relationships not a list")
                continue
            try:
                ExtractStage._validate_edges(relations, records)
            except ExtractionModelError as exc:
                known = set()
                for record_item in records:
                    for key in ("tag_id", "canonical", "canonical_name", "name"):
                        value = record_item.get(key)
                        if isinstance(value, str) and value:
                            known.add(_identity_key(value))
                missing: list[str] = []
                for relation in relations:
                    source = relation.get("from") or relation.get("from_id")
                    target = relation.get("to") or relation.get("to_id")
                    for end in (source, target):
                        if _identity_key(end) not in known:
                            missing.append(f"{end!r}")
                print(f"{label}: RAISED {exc} | unresolvable={len(missing)} {sorted(set(missing))[:4]}")
                continue
            print(f"{label}: passes validation ({len(records)} records, {len(relations)} relations)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
