"""Связь, собранная моделью, обязана дойти до графа (регрессия COMMIT).

Почему этот тест понадобился. Модель отдаёт концы связи **именами** (`network`), и так
же их отдаёт вся документация промта. Но `_validate_edges` проверяет конец против
объявленных **имён** и признаёт его разрешённым, а COMMIT на строке 1814 проверяет
тот же конец против **идентификаторов** `_context_node_id` → `tag:it:<ключ>`. Имя и
идентификатор — разные строки всегда, поэтому первая же связь роняет джобу с
`COMMIT: link references unknown context node`.

Почему это жило так долго. Ни один тест проекта не коммитил связь, полученную от
модели: `test_commit_stage.py` не передаёт `links=` ни разу, поэтому его связи —
детерминированные, а те дают ноль; `test_unresolved_endpoint_fact.py` доводит
концы до EXTRACT и на этом кончается, то есть до COMMIT не доходит никто. Обе
проверки по отдельности зелёные, а между ними — дыра, в которую провалился весь
граф: на прогоне `docs/api_reference.md` из 47 сущностей и 32 связей в граф не
записалось ни одной, `necessity` был 0, и это читалось как «граф не нужен».

Тест строит связь ровно так, как её строит модель, — именами, — и доводит её до
записи. Второй тест фиксирует решение владельца: конец, который не переводится в
идентификатор, это факт и пропуск, а не гибель всего документа.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from graphrag_proto.ingestion_service.pipeline.orchestrator import (
    Analyzer,
    ChunkStage,
    CommitStage,
    ContractStage,
    DedupStage,
    EmbedStage,
    ExtractStage,
    IngestStage,
    NormalizeStage,
    PipelineContext,
    ValidateStage,
)
from graphrag_proto.ingestion_service.readers.registry import TxtReader
from graphrag_proto.ingestion_service.storage.registry import DocumentRegistry
from graphrag_proto.retrieval.adapters.inmemory import (
    InMemoryGraphStore,
    InMemoryVectorStore,
)

CONTENT = "сеть вектор риск и данные\n"


class _OneShotLLM:
    """Адаптер с боевым контрактом: `generate` отдаёт кусками, итератором.

    `is_fake` намеренно не задаётся: с ним `ExtractStage` ушёл бы в
    детерминированный путь и до LLM-ветки не дошёл бы вовсе.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        self._text = json.dumps(payload, ensure_ascii=False)
        self.last_usage: dict[str, int] = {"prompt_tokens": 1, "completion_tokens": 1}

    def generate(self, *_args: Any, **_kwargs: Any) -> Any:
        return iter([self._text])


def _profile() -> dict[str, Any]:
    return {
        "profile": {"name": "it", "version": "1"},
        "ontology": {"node_types": [{"type": "Requirement"}]},
        "extraction": {
            "llm_enabled": True,
            "prompt_template": {"id": "t", "system": "s", "user": "u"},
        },
    }


def _payload() -> dict[str, Any]:
    """Ответ модели: две сущности и одна связь, оба конца - именами."""
    return {
        "tags": [
            {"canonical_name": "NETWORK", "name": "NETWORK"},
            {"canonical_name": "VECTOR", "name": "VECTOR"},
        ],
        "relationships": [
            {"from": "NETWORK", "to": "VECTOR", "kind": "REQUIRES_CONSTRAINT", "confidence": 0.8}
        ],
    }


def _run(
    tmp_path: Path,
    payload: dict[str, Any],
    graph: InMemoryGraphStore,
    vector: InMemoryVectorStore,
) -> PipelineContext:
    """Полный конвейер, а не отдельные стадии.

    NORMALIZE участвует в отказе так же, как и в бою: он переводит концы в
    канонические имена, и если пропустить его, тест проверит не тот путь, который
    падал на прогоне.
    """
    src = tmp_path / "d.txt"
    src.write_text(CONTENT, encoding="utf-8")
    registry = DocumentRegistry(tmp_path / "commit.db")
    # Фетчер профиля один и достаётся Analyzer'у. Раньше его получали три стадии
    # (CHUNK, EXTRACT, COMMIT), и `_load_profile` выходил по `ctx.profile_loaded`, то
    # есть авторитетом становился кто позвал первым: стадия без фетчера замораживала
    # `profile={}`, LLM-экстракция молча уходила в детерминированный путь с
    # `cause=None`, и выглядело это как «зелёный тест, а модель не звали». Именно на
    # этом спотыкался сам тест, пока фетчер не начали подавать в одну точку.
    fetcher: Any = lambda _domain: _profile()
    stages = [
        IngestStage({"txt": TxtReader()}),
        ChunkStage(None),
        EmbedStage(None),
        ExtractStage(llm=_OneShotLLM(payload), optional_failure=True),
        NormalizeStage(""),
        DedupStage(),
        ContractStage(),
        ValidateStage(),
        CommitStage(registry, graph_store=graph, vector_store=vector, graph_optional=True),
    ]
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://d.txt",
        source_path=str(src),
    )
    Analyzer(stages, profile_fetcher=fetcher).run(ctx)
    return ctx


def _written(graph: InMemoryGraphStore) -> list[tuple[str, str, str]]:
    """Рёбра, записанные модельной экстракцией, без технических MENTIONS/CONTAINS."""
    return [
        (edge[0], edge[1], edge[2])
        for edge in graph._edges
        if edge[2] not in {"MENTIONS", "CONTAINS"}
    ]


def test_relation_with_name_endpoints_reaches_the_graph(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Связь с именными концами записывается. До правки - `ValueError` на COMMIT."""
    monkeypatch.setenv("EXTRACT_LLM", "true")
    graph, vector = InMemoryGraphStore(), InMemoryVectorStore()

    ctx = _run(tmp_path, _payload(), graph, vector)

    assert ctx.enrichment_degraded is False
    assert ctx.llm_layer_dropped is False
    assert ctx.llm_edges == 1
    # Узлы записаны.
    assert any(
        node.get("properties", {}).get("tag_id") == "tag:it:network"
        for node in graph._nodes.values()
    )
    # И связь между ними, с концами-идентификаторами, а не именами.
    assert _written(graph) == [("tag:it:network", "tag:it:vector", "REQUIRES_CONSTRAINT")]


def test_mixed_name_and_id_endpoints_reach_the_graph(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Смешанная форма: `from` именем, `to` именем, плюс `to_id` идентификатором.

    Именно на этом упал боевой прогон `docs/api_reference.md`: в сообщении COMMIT было
    `from_id='Query API Contract'`, `to_id='tag:it:query api'`, 38 узлов в документе.

    Форма неоднозначна, и в этом отдельная беда. VALIDATE требует ЛИБО пару
    `from_id`+`to_id`, ЛИБО пару `from`+`to`, поэтому форму `from` + `to_id` он отбивает
    как `link без endpoints` ещё до COMMIT. Боевая связь до COMMIT дошла, а значит была
    тремя полями сразу: `from`, `to` и `to_id`, и пару `from`+`to` она удовлетворяла.
    NORMALIZE при этом переводит концы, ТОЛЬКО если ни один не несёт `*_id`: увидев
    непустой `to_id`, стадия делает `continue` и не переводит ни один конец. Дальше
    COMMIT сравнивает сырое имя с множеством `tag:it:...` и валит документ.

    То есть VALIDATE и NORMALIZE расходятся в том, что считают формой связи: первую
    такая форма устраивает, вторую нет. И обе считают её законной, потому что промт
    разрешает `from`/`to` и `from_id`/`to_id` одновременно.

    Хвостовой перевод строки в боевом ответе - побочный шум, а не причина: ключ
    почистил бы его, но его не вызвали. Поэтому конец здесь взят без перевода: даже
    `NETWORK` в чистом виде не равен `tag:it:network`.
    """
    monkeypatch.setenv("EXTRACT_LLM", "true")
    graph, vector = InMemoryGraphStore(), InMemoryVectorStore()
    payload = _payload()
    payload["relationships"] = [
        {
            "from": "NETWORK",
            "to": "VECTOR",
            "to_id": "tag:it:vector",
            "kind": "REFERENCES",
            "confidence": 0.7,
        }
    ]

    ctx = _run(tmp_path, payload, graph, vector)

    assert ctx.enrichment_degraded is False
    assert _written(graph) == [("tag:it:network", "tag:it:vector", "REFERENCES")]


def test_untranslatable_endpoint_is_fact_not_document_loss(
    tmp_path: Path, monkeypatch: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """Решение владельца: конец вне идентификаторов - факт и пропуск, документ цел.

    Проверяется и содержимое факта, и то, что в лог уходит ошибка: молчаливый пропуск
    вернул бы то молчание, из-за которого баг прожил неделю - связи не пишутся,
    `necessity` равен нулю, и по артефактам не видно, что что-то потеряно.
    """
    monkeypatch.setenv("EXTRACT_LLM", "true")
    graph, vector = InMemoryGraphStore(), InMemoryVectorStore()
    payload = _payload()
    payload["relationships"].append(
        {"from": "NETWORK", "to": "MADE_UP", "kind": "REFERENCES", "confidence": 0.5}
    )

    ctx = _run(tmp_path, payload, graph, vector)

    assert ctx.enrichment_degraded is False
    facts = ctx.unresolved_endpoints
    assert [fact["endpoint_name"] for fact in facts] == ["MADE_UP"]
    assert facts[0]["relation_kind"] == "REFERENCES"
    # Одна связь записана, вторая пропущена - документ не потерян.
    assert _written(graph) == [("tag:it:network", "tag:it:vector", "REQUIRES_CONSTRAINT")]
    assert "MADE_UP" in caplog.text
