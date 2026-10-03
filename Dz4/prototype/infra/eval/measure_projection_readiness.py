"""Считает пораздельную готовность по выгруженным входам (ADR-046 п. 3 и п. 6).

Входы — ровно те, что читает гейт: журнал `projection_state_sources` и группировка
ревизий чанков. Файл собирается в два шага, потому что на стенде приёмный контейнер
не имеет всех зависимостей: `cypher-shell` даёт граф, `sqlite3` — журнал, а считает
здесь тот же код, что и в пиплайне (импорт, а не копия логики).
"""

from __future__ import annotations

import json
import pathlib
import sys

from graphrag_proto.retrieval.adapters.base import OWNERLESS_SOURCE
from graphrag_proto.retrieval.pipeline import evaluate_projection_readiness

journal_path, graph_path = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
expected = {str(k): str(v) for k, v in json.loads(journal_path.read_text(encoding="utf-8")).items()}
observed = {
    str(owner): {str(rev): int(count) for rev, count in revisions.items()}
    for owner, revisions in json.loads(graph_path.read_text(encoding="utf-8")).items()
}

readiness = evaluate_projection_readiness(expected, observed)
print(json.dumps({
    "sources_in_journal": len(expected),
    "sources_in_graph": len([o for o in observed if o != OWNERLESS_SOURCE]),
    "readiness": {
        "sources_tracked": readiness.sources_tracked,
        "sources_without_graph": readiness.sources_without_graph,
        "revision_mismatches": readiness.revision_mismatches,
        "chunks_without_owner": readiness.chunks_without_owner,
        "chunks_total": readiness.chunks_total,
        "reason": readiness.reason,
        "ready": readiness.ready,
    },
    "per_source_revisions": observed,
    "journal": expected,
}, ensure_ascii=False, indent=2))
sys.exit(0 if readiness.ready else 2)