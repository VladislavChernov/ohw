"""Гоняет боевой гейт проекции против живого Neo4j и живой SQLite (ADR-046 п. 3).

Собирает те же зависимости, которые получает `QueryPipeline` в сервисе, и печатает решение
гейта вместе со счётчиками. Цель — проверить, что ось графа на живых данных действительно
открывается, а не только то, что счётчики сходятся.

**Две заглушки здесь были не «заглушками безобидными», а двумя поломками прибора** (2026-10-03,
найдено на стенде). Обе молчали, и обе выглядели как вывод о системе:

* эмбеддер был `deterministic_embedding` — **8 измерений против 1024** в индексе bge-m3, поэтому
  векторный поиск возвращал **0 хитов**, и прибор печатал `no_context_ids` и `graph_degraded:
  true`. На живых данных это ложь: обогащены 17 чанков из 21;
* реранкер возвращал словари чанков вместо чисел, поэтому при любом найденном хите конвейер
  падал в `retrieval/pipeline.py` на `float(score)`.

Поэтому эмбеддер теперь боевой (`BgeM3ServiceAdapter.from_env()`), а реранкер — тот, что вернул
бы векторные score без изменения порядка. Второй честно назван в выводе: **проверка ранжирования
этим прибором не выполняется**, проверяется ось графа и счётчики. Ранжирование на стенде этого
контура нет (сервиса reranker в минимальном стенде не существует), и молча подставлять его
имитацию с проверкой, что «реранкер работает», было бы тем же-class дефектом.
"""

from __future__ import annotations

import json
import os
import pathlib

from graphrag_proto.ingestion_service.projection import SQLiteProjectionStateStore
from graphrag_proto.retrieval.adapters.base import LLMInference, Reranker
from graphrag_proto.retrieval.adapters.bge import BgeM3ServiceAdapter
from graphrag_proto.retrieval.adapters.neo4j import Neo4jGraphStore, Neo4jVectorStore
from graphrag_proto.retrieval.pipeline import QueryPipeline

DOMAIN = os.environ.get("DOMAIN", "it")


class _VectorScoreReranker(Reranker):
    """Возвращает векторные score как есть: порядок не меняется, порядок и не проверяется.

    Ранжирование — не предмет этого прибора. Прежний вариант возвращал сами чанки, и конвейер
    падал на `float(score)`; «рабочий» имитатор реранкера здесь был бы ложью того же класса,
    что и заглушка эмбеддера.
    """

    def rerank(
        self, query: str, hits: list[dict[str, object]], top_k: int | None = None
    ) -> list[float]:
        chosen = hits if top_k is None else hits[:top_k]
        return [float(hit.get("score") or 0.0) for hit in chosen]


class _LLM(LLMInference):
    def generate(self, prompt: str, *, max_tokens: int = 64) -> str:
        return "ok"


def main() -> int:
    graph = Neo4jGraphStore(
        uri=os.environ["NEO4J_BOLT"],
        user=os.environ.get("NEO4J_USER", "neo4j"),
        password=os.environ["NEO4J_PASSWORD"],
    )
    vector = Neo4jVectorStore(
        uri=os.environ["NEO4J_BOLT"],
        user=os.environ.get("NEO4J_USER", "neo4j"),
        password=os.environ["NEO4J_PASSWORD"],
    )
    state = SQLiteProjectionStateStore(pathlib.Path(os.environ["PROJECTION_STATE_DB"]))
    embedder = BgeM3ServiceAdapter.from_env()
    pipeline = QueryPipeline(
        embedder=embedder,
        graph_store=graph,
        vector_store=vector,
        reranker=_VectorScoreReranker(),
        llm=_LLM(),
        projection_state_store=state,
        projection_config_fingerprint=os.environ.get("PROJECTION_CONFIG_FINGERPRINT", "default"),
        projection_state_required=True,
    )
    stored = state.get(DOMAIN)
    print(json.dumps({
        "state_status": None if stored is None else stored.status,
        "state_data_revision": None if stored is None else stored.data_revision[:20],
        "state_projection_revision": None if stored is None else stored.projection_revision[:20],
        "journal_sources": len(state.source_revisions(DOMAIN)),
        "embedder": os.environ.get("EMBEDDING_MODEL", "bge-m3"),
        "embedder_dimensions": int(os.environ.get("EMBEDDING_DIMENSIONS", "1024")),
        "reranker": "векторные score без перестановки — ранжирование не проверяется",
    }, ensure_ascii=False, indent=2))

    answer = pipeline.run(
        "дедупликация кэширование",
        domain=DOMAIN,
        revision=stored.data_revision if stored else None,
        generate=False,
        trace=True,
    )
    events = answer.get("trace") or answer.get("events") or []
    readiness = [
        {"stage": event.get("stage"), "degraded": event.get("degraded"), "status": event.get("status"), "reason": event.get("reason")}
        for event in events
        if isinstance(event, dict)
    ]
    expansion = next((e for e in events if isinstance(e, dict) and e.get("stage") == "graph_expansion"), {})
    vector_stage = next((e for e in events if isinstance(e, dict) and e.get("stage") == "vector"), {})
    candidates = vector_stage.get("candidates") or []
    sources = sorted(
        {
            str(source.get("source_url"))
            for source in (answer.get("sources") or [])
            if isinstance(source, dict) and source.get("source_url")
        }
    )
    print(json.dumps({
        "graph_degraded": answer.get("graph_degraded"),
        "degraded_reasons": {k: v for k, v in answer.items() if "degrad" in k or "reason" in k},
        "projection_status": answer.get("projection_status"),
        "projection_revision": (answer.get("projection_revision") or "")[:20],
        # Только то, что реально несёт трасса. Счётчика «кандидатов с context_ids» здесь
        # нет намеренно: событие `vector` отдаёт только chunk_id/rank/score, и подсчёт по
        # отсутствующему полю давал бы ноль, который читается как «обогащения нет».
        "vector_candidates": len(candidates),
        "expansion_paths": len(expansion.get("paths") or []),
        "expansion_reason": expansion.get("reason"),
        "sources": sources,
        "graph_readiness_events": readiness,
    }, ensure_ascii=False, indent=2))

    # Нулевая выдача вектора и пустой обход — это «прибор ничего не проверил», а не «граф не
    # нужен». Молчаливый ноль здесь уже выдавал себя за вывод о системе, поэтому прибор обязан
    # сказать о нуле голосом и кодом возврата.
    if not candidates:
        print("FAIL векторный поиск не вернул ни одного кандидата — мерить нечего")
        return 1
    if answer.get("graph_degraded"):
        print(f"FAIL контур деградировал: {expansion.get('reason') or answer.get('projection_status')}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())