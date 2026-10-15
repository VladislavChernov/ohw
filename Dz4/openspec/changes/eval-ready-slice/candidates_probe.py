"""Диагностика кандидатов по документам набора `ready` (2026-10-15).

Зачем: шесть загруженных документов имеют чанки, но ноль `ContextNode` — то есть extraction
по ним не шёл. По договорённости ADR-053 без списка кандидатов документ грузится только в
вектор. Здесь меряется, что дают ПРАВИЛА (`rule_candidates`) на этих документах в сравнении
с теми, где узлы есть. NER (natasha) не запускается: правила — отдельная функция, а вопрос
про верхнюю границу «есть из чего выбирать модели».

Запуск (в dev-контейнере):  uv run --no-sync python candidates_probe.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/repo/prototype/src")

from graphrag_proto.pre_ingest.generate import rule_candidates

DOCS = Path("/repo/docs")

# Документы, у которых в наборе `ready` НЕТ узлов (только чанки).
VECTOR_ONLY = [
    "01_ontology_and_domain_profile.md",
    "02_pipeline_and_normalizer.md",
    "05_adr_log.md",
    "06_operations_and_risks.md",
    "connectors_guide.md",
    "defense_notes_simplifications.md",
]

# Контроль: у этих узлов много (148 / 117 / 109 по замерам разведки).
WITH_NODES = [
    "04_services_config.md",
    "api_reference.md",
    "adapters_specification.md",
]


def probe(name: str) -> tuple[int, int, list[str]]:
    text = (DOCS / name).read_text(encoding="utf-8")
    found = rule_candidates(text)
    # Второй замер по тому же тексту: сколько «имён» вытягивают только правила с парами
    # заглавных слов — класс, который добавляли последним.
    pairs = {
        value
        for value in found
        if " " in value and value[:1].isupper()
    }
    return len(found), len(pairs), sorted(found)


def main() -> int:
    for group, names in (("VECTOR-ONLY (узлов нет)", VECTOR_ONLY), ("CONTROL (узлы есть)", WITH_NODES)):
        print(f"=== {group} ===")
        for name in names:
            total, pairs, sample = probe(name)
            print(f"{name}: rules={total} (из них парных заглавных={pairs})")
            print("   ", ", ".join(sample[:12]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
