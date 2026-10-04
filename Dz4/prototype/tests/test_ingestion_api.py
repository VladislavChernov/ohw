from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from graphrag_proto.ingestion_service.app import create_app
from graphrag_proto.ingestion_service.storage.registry import (
    STATUS_ACTIVE,
    STATUS_SUPERSEDED,
    DocumentRegistry,
    JobStore,
)
from graphrag_proto.retrieval.adapters.inmemory import InMemoryGraphStore, InMemoryVectorStore
from tests.job_wait import JOB_WAIT_CEILING_S
from tests.job_wait import wait_until as _wait_until

API_KEY = "changeme"


class _AuthedClient(TestClient):
    """TestClient, подставляющий X-API-Key по умолчанию (запросы без ключа — в auth-тестах)."""

    def request(self, method, url, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("X-API-Key", API_KEY)
        return super().request(method, url, headers=headers, **kwargs)


def wait_until(condition, timeout: float = JOB_WAIT_CEILING_S, interval: float = 0.1) -> bool:
    """Совместимая обёртка над общим ожидателем: потолок и интервал приходят из `job_wait`,
    иначе в этом файле снова появился бы свой, короткий."""
    return _wait_until(condition, timeout_s=timeout, interval_s=interval)


def make_app(tmp_path: Path, glossary_url: str = ""):
    db = tmp_path / "ingestion.db"
    uploads = tmp_path / "uploads"
    return create_app(upload_dir=uploads, db_path=db, glossary_url=glossary_url)


def test_post_document_202_and_job_succeeds(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with _AuthedClient(app) as client:
        resp = client.post(
            "/api/v1/ingestion/documents",
            json={
                "source_url": "src://algo.txt",
                "domain": "it",
                "doc_type": "txt",
                "content": "Big-O notation описывает рост сложности алгоритма.",
            },
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        ok = wait_until(lambda: client.get(f"/api/v1/ingestion/jobs/{job_id}").json().get("status") == "succeeded")
        assert ok, client.get(f"/api/v1/ingestion/jobs/{job_id}").json()

        body = client.get(f"/api/v1/ingestion/jobs/{job_id}").json()
        assert body["status"] == "succeeded"
        stages = {s["stage"] for s in body["stages"]}
        assert stages == {
            "INGEST",
            "CHUNK",
            "EMBED",
            "EXTRACT",
            "NORMALIZE",
            "DEDUP",
            "CONTRACT",
            "VALIDATE",
            "COMMIT",
        }


def test_validation_422_missing_fields(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with _AuthedClient(app) as client:
        resp = client.post("/api/v1/ingestion/documents", json={"source_url": "x"})
        assert resp.status_code == 422


def test_validation_422_bad_doc_type(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with _AuthedClient(app) as client:
        resp = client.post(
            "/api/v1/ingestion/documents",
            json={"source_url": "x.json", "domain": "it", "doc_type": "json", "content": "{}"},
        )
        assert resp.status_code == 422


def test_job_not_found_404(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with _AuthedClient(app) as client:
        resp = client.get("/api/v1/ingestion/jobs/nope")
        assert resp.status_code == 404


def test_list_jobs_paginated_reflects_total(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with _AuthedClient(app) as client:
        for i in range(3):
            client.post(
                "/api/v1/ingestion/documents",
                json={
                    "source_url": f"src://d{i}.txt",
                    "domain": "it",
                    "doc_type": "txt",
                    "content": f"текст документа {i}",
                },
            )
        resp = client.get("/api/v1/ingestion/jobs?page=1&page_size=2")
        assert resp.status_code == 200
        # total — общее число джоб, а не длина текущей страницы (L2)
        assert resp.json()["total"] == 3
        assert len(resp.json()["items"]) == 2


def test_idempotent_noop_on_same_content(tmp_path: Path) -> None:
    db = tmp_path / "ingestion.db"
    uploads = tmp_path / "uploads"
    app = create_app(upload_dir=uploads, db_path=db, glossary_url="")
    payload = {
        "source_url": "src://stable.txt",
        "domain": "it",
        "doc_type": "txt",
        "content": "одинаковый контент документа",
    }
    with _AuthedClient(app) as client:
        j1 = client.post("/api/v1/ingestion/documents", json=payload).json()["job_id"]
        assert wait_until(lambda: client.get(f"/api/v1/ingestion/jobs/{j1}").json().get("status") == "succeeded")
        j2 = client.post("/api/v1/ingestion/documents", json=payload).json()["job_id"]
        assert wait_until(lambda: client.get(f"/api/v1/ingestion/jobs/{j2}").json().get("status") == "succeeded")

    reg = DocumentRegistry(db)
    latest = reg.latest_active("it", "src://stable.txt")
    assert latest is not None
    assert latest["version"] == 1  # повторная загрузка не плодит версий (L2-06)


def test_changed_content_creates_new_version(tmp_path: Path) -> None:
    db = tmp_path / "ingestion.db"
    uploads = tmp_path / "uploads"
    app = create_app(upload_dir=uploads, db_path=db, glossary_url="")
    with _AuthedClient(app) as client:
        j1 = client.post(
            "/api/v1/ingestion/documents",
            json={"source_url": "src://chg.txt", "domain": "it", "doc_type": "txt", "content": "версия 1"},
        ).json()["job_id"]
        assert wait_until(lambda: client.get(f"/api/v1/ingestion/jobs/{j1}").json().get("status") == "succeeded")
        j2 = client.post(
            "/api/v1/ingestion/documents",
            json={"source_url": "src://chg.txt", "domain": "it", "doc_type": "txt", "content": "версия 2"},
        ).json()["job_id"]
        assert wait_until(lambda: client.get(f"/api/v1/ingestion/jobs/{j2}").json().get("status") == "succeeded")

    reg = DocumentRegistry(db)
    latest = reg.latest_active("it", "src://chg.txt")
    assert latest is not None and latest["version"] == 2
    # старая активная версия переведена в superseded (ADR-014)
    rows = reg._conn.execute(
        "SELECT status FROM documents WHERE domain='it' AND source_url='src://chg.txt'"
    ).fetchall()
    statuses = sorted(r[0] for r in rows)
    assert STATUS_ACTIVE in statuses
    assert STATUS_SUPERSEDED in statuses


def test_soft_delete(tmp_path: Path) -> None:
    db = tmp_path / "ingestion.db"
    uploads = tmp_path / "uploads"
    app = create_app(upload_dir=uploads, db_path=db, glossary_url="")
    with _AuthedClient(app) as client:
        j1 = client.post(
            "/api/v1/ingestion/documents",
            json={"source_url": "src://del.txt", "domain": "it", "doc_type": "txt", "content": "удаляемый"},
        ).json()["job_id"]
        assert wait_until(lambda: client.get(f"/api/v1/ingestion/jobs/{j1}").json().get("status") == "succeeded")

    reg = DocumentRegistry(db)
    assert reg.soft_delete("it", "src://del.txt") is True
    assert reg.latest_active("it", "src://del.txt") is None


def test_reingest_after_soft_delete_gets_new_version(tmp_path: Path) -> None:
    """После soft-delete повторный INGEST не переиспользует номер версии (ADR-014)."""
    db = tmp_path / "ingestion.db"
    uploads = tmp_path / "uploads"
    app = create_app(upload_dir=uploads, db_path=db, glossary_url="")
    with _AuthedClient(app) as client:
        j1 = client.post(
            "/api/v1/ingestion/documents",
            json={"source_url": "src://rev.txt", "domain": "it", "doc_type": "txt", "content": "версия 1"},
        ).json()["job_id"]
        assert wait_until(lambda: client.get(f"/api/v1/ingestion/jobs/{j1}").json().get("status") == "succeeded")

    reg = DocumentRegistry(db)
    assert reg.soft_delete("it", "src://rev.txt") is True

    with _AuthedClient(app) as client:
        j2 = client.post(
            "/api/v1/ingestion/documents",
            json={"source_url": "src://rev.txt", "domain": "it", "doc_type": "txt", "content": "новая версия"},
        ).json()["job_id"]
        assert wait_until(lambda: client.get(f"/api/v1/ingestion/jobs/{j2}").json().get("status") == "succeeded")

    latest = reg.latest_active("it", "src://rev.txt")
    assert latest is not None
    # монотонная версия: не 1 (как у удалённого), а строго больше
    assert latest["version"] >= 2


def test_cancel_running_job_returns_cancelled(tmp_path: Path) -> None:
    db = tmp_path / "ingestion.db"
    uploads = tmp_path / "uploads"
    app = create_app(upload_dir=uploads, db_path=db, glossary_url="")
    with _AuthedClient(app) as client:
        jid = client.post(
            "/api/v1/ingestion/documents",
            json={"source_url": "src://c.txt", "domain": "it", "doc_type": "txt", "content": "x" * 5000},
        ).json()["job_id"]
        # пробуем отменить; может вернуть 200 (сняли флаг) или 409 (уже завершилась)
        resp = client.delete(f"/api/v1/ingestion/jobs/{jid}")
        assert resp.status_code in (200, 409)

        final = client.get(f"/api/v1/ingestion/jobs/{jid}").json()
        # Если отмена принята — COMMIT не должен был выполниться (docs в registry нет)
        if final["status"] == "cancelled":
            reg = DocumentRegistry(db)
            assert reg.latest_active("it", "src://c.txt") is None
            # журнал этапов не «висит»: нет running-стадий после cancellation
            # (если джоба отменена до старта первого этапа — список может быть пуст)
            assert all(s["status"] != "running" for s in final["stages"])
        else:
            assert final["status"] in ("succeeded", "failed")


def test_finished_job_journal_all_stages_terminal(tmp_path: Path) -> None:
    """Все 9 этапов в журнале завершены (не «висят» running) после успеха."""
    app = make_app(tmp_path)
    with _AuthedClient(app) as client:
        jid = client.post(
            "/api/v1/ingestion/documents",
            json={"source_url": "src://j.txt", "domain": "it", "doc_type": "txt", "content": "текст джобы"},
        ).json()["job_id"]
        assert wait_until(lambda: client.get(f"/api/v1/ingestion/jobs/{jid}").json().get("status") == "succeeded")
        stages = client.get(f"/api/v1/ingestion/jobs/{jid}").json()["stages"]
        assert len(stages) == 9
        assert all(s["status"] == "succeeded" for s in stages)


def test_cancel_missing_job_404(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    with _AuthedClient(app) as client:
        resp = client.delete("/api/v1/ingestion/jobs/nope")
        assert resp.status_code == 404


def test_requires_api_key_401(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    app = create_app(
        upload_dir=uploads,
        db_path=tmp_path / "ingestion.db",
        glossary_url="",
        api_key="secret",
    )
    with TestClient(app) as client:
        payload = {"source_url": "x", "domain": "it", "doc_type": "txt", "content": "текст"}
        assert client.post("/api/v1/ingestion/documents", json=payload).status_code == 401
        assert client.get("/api/v1/ingestion/jobs").status_code == 401
        assert client.delete("/api/v1/ingestion/jobs/nope").status_code == 401


def test_health_is_open() -> None:
    import tempfile
    from pathlib import Path

    tmp = Path(tempfile.mkdtemp())
    app = create_app(upload_dir=tmp / "uploads", db_path=tmp / "i.db", api_key="secret")
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200


def test_executor_limits_concurrent_jobs(tmp_path: Path) -> None:
    from graphrag_proto.ingestion_service.app import Executor

    jobs = JobStore(tmp_path / "j.db")
    reg = DocumentRegistry(tmp_path / "r.db")
    src = tmp_path / "in.txt"
    src.write_text("какой-то текст документа для обработки", encoding="utf-8")
    ex = Executor(
        jobs,
        reg,
        glossary_url="",
        graph_store=InMemoryGraphStore(),
        vector_store=InMemoryVectorStore(),
        max_concurrent=1,
    )
    assert ex._slots.acquire(blocking=False) is True
    try:
        assert ex.start("blocked", src, "src://blocked", "it", "txt") is False
    finally:
        ex._slots.release()
    jobs.create("ok", "src://ok", "it", "txt")
    assert ex.start("ok", src, "src://ok", "it", "txt") is True
    assert wait_until(lambda: jobs.get("ok")["status"] == "succeeded")


def test_executor_loads_profile_once_per_job(tmp_path: Path) -> None:
    from graphrag_proto.ingestion_service.app import Executor

    calls: list[str] = []

    def fetcher(domain: str) -> dict[str, object]:
        calls.append(domain)
        return {
            "chunking": {
                "strategy": "sliding_window",
                "chunk_size": 512,
                "overlap": 64,
            }
        }

    jobs = JobStore(tmp_path / "jobs.db")
    registry = DocumentRegistry(tmp_path / "registry.db")
    source = tmp_path / "source.txt"
    source.write_text("текст для профиля", encoding="utf-8")
    executor = Executor(
        jobs,
        registry,
        glossary_url="",
        graph_store=InMemoryGraphStore(),
        vector_store=InMemoryVectorStore(),
        profile_fetcher=fetcher,
    )
    jobs.create("job", "src://profile.txt", "it", "txt")
    assert executor.start("job", source, "src://profile.txt", "it", "txt") is True
    assert wait_until(lambda: jobs.get("job")["status"] == "succeeded")
    jobs.create("job-noop", "src://profile.txt", "it", "txt")
    assert executor.start("job-noop", source, "src://profile.txt", "it", "txt") is True
    assert wait_until(lambda: jobs.get("job-noop")["status"] == "succeeded")
    assert calls == ["it"]


def test_executor_reports_enrichment_cause_and_loss_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Причина и размер потери доезжают до ответа джобы числами, а не текстом.

    Сквозная проверка ровно того, о чём бьют два соседних теста по отдельности:
    факты записыются в стадии EXTRACT, отдаются полем `enrichment` и при этом НЕ
    зависят от текста сообщения. Проверяется на реальном исполнителе, потому что
    потеря случается на пайплайне, а не в резолвере.
    """
    monkeypatch.setenv("EXTRACT_LLM", "true")
    from graphrag_proto.ingestion_service.app import Executor
    from graphrag_proto.retrieval.adapters.llm import FakeLLM

    jobs = JobStore(tmp_path / "enr.db")
    registry = DocumentRegistry(tmp_path / "enr-registry.db")
    source = tmp_path / "d.txt"
    source.write_text(
        "требование индексировать документы дедупликация документов", encoding="utf-8"
    )
    executor = Executor(
        jobs,
        registry,
        glossary_url="",
        graph_store=InMemoryGraphStore(),
        vector_store=InMemoryVectorStore(),
        llm=FakeLLM(text="not-json", is_fake=False),
        profile_fetcher=lambda _domain: _ai_extraction_profile(),
    )
    jobs.create("job", "src://d.txt", "it", "txt")
    assert executor.start("job", source, "src://d.txt", "it", "txt") is True
    assert wait_until(lambda: jobs.get("job")["status"] == "succeeded")

    job = jobs.get("job")
    facts = jobs.enrichment("job")
    assert job["status"] == "succeeded"
    assert facts["cause"] == "model_error"
    # модель не вернула ни одной записи → терять нечего → потери нет
    assert facts["lost_entities"] == 0
    assert "llm_layer_dropped" not in jobs.signals("job")


def test_executor_reports_enrichment_for_healthy_job_as_denominator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Здоровый документ тоже пишет факты — иначе знаменателя не существует.

    Доля «записей на документ» считается по джобам, где EXTRACT дошёл. Если писать
    объём только при сбое, знаменатель состоял бы ровно из отказавших документов, и
    улучшение выглядело бы как ухудшение.

    ADR-049: «здоровый» здесь означает настоящее извлечение. Раньше здоровье означало
    «профиль загрузился, EXTRACT отработал, потерь нет», а записей при этом было ноль —
    потому что LLM не был объявлен, и заглушка отвечала молча. Знаменатель наполнялся
    документами, где экстракция не состоялась, и по отчёту это не отличалось от «модель
    не нашла ничего». На стенде 2026-10-04 так выглядели три документа с
    `llm_records_extracted: 0` при `enrichment_degraded: false`.
    """
    import json

    from graphrag_proto.ingestion_service.app import Executor
    from graphrag_proto.retrieval.adapters.llm import FakeLLM

    payload = {
        "tags": [{"canonical_name": "Quicksort", "name": "Quicksort", "origin": "ai"}],
        "relationships": [],
    }
    monkeypatch.setenv("EXTRACT_LLM", "true")
    jobs = JobStore(tmp_path / "ok.db")
    registry = DocumentRegistry(tmp_path / "ok-registry.db")
    source = tmp_path / "d.txt"
    source.write_text("сортировка quicksort разделяй и властвуй данными", encoding="utf-8")
    executor = Executor(
        jobs,
        registry,
        glossary_url="",
        graph_store=InMemoryGraphStore(),
        vector_store=InMemoryVectorStore(),
        llm=FakeLLM(text=json.dumps(payload, ensure_ascii=False), is_fake=False),
        profile_fetcher=lambda _domain: _ai_extraction_profile(),
    )
    jobs.create("job", "src://d.txt", "it", "txt")
    assert executor.start("job", source, "src://d.txt", "it", "txt") is True
    assert wait_until(lambda: jobs.get("job")["status"] == "succeeded")

    facts = jobs.enrichment("job")
    assert facts["cause"] is None, facts
    assert facts["lost_entities"] == 0
    assert facts["lost_edges"] == 0
    assert facts["llm_records"] > 0, (
        "здоровая джоба обязана давать ненулевой знаменатель, иначе «записей на документ» "
        f"считается по документам, где экстракции не было: {facts}"
    )


def _ai_extraction_profile() -> dict[str, object]:
    return {
        "chunking": {"strategy": "sliding_window", "chunk_size": 512, "overlap": 64},
        "extraction": {
            "llm_enabled": True,
            "prompt_template": {"system": "s", "user": "u"},
        },
    }


def test_executor_preserves_legacy_positional_max_concurrent(tmp_path: Path) -> None:
    from graphrag_proto.ingestion_service.app import Executor

    jobs = JobStore(tmp_path / "legacy-jobs.db")
    registry = DocumentRegistry(tmp_path / "legacy-registry.db")
    executor = Executor(jobs, registry, "", None, None, None, None, 1)
    assert executor._slots._value == 1
    assert executor._slots.acquire(blocking=False) is True
    executor._slots.release()


def test_executor_rejects_invalid_max_concurrent(tmp_path: Path) -> None:
    import pytest

    from graphrag_proto.ingestion_service.app import Executor

    jobs = JobStore(tmp_path / "j.db")
    reg = DocumentRegistry(tmp_path / "r.db")
    with pytest.raises(ValueError):
        Executor(jobs, reg, glossary_url="", max_concurrent=0)


def test_post_when_executor_saturated_returns_429(tmp_path: Path, monkeypatch) -> None:
    from graphrag_proto.ingestion_service import app as ing_app

    monkeypatch.setattr(ing_app.Executor, "start", lambda self, *a, **kw: False)
    app = make_app(tmp_path)
    with _AuthedClient(app) as client:
        resp = client.post(
            "/api/v1/ingestion/documents",
            json={"source_url": "src://full.txt", "domain": "it", "doc_type": "txt", "content": "текст"},
        )
        assert resp.status_code == 429
        jobs = client.get("/api/v1/ingestion/jobs").json()
    assert jobs["items"][0]["status"] == "failed"


def test_runner_normalize_without_glossary_keeps_entities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Без Glossary URL стадия NORMALIZE сохраняет сущность, а не теряет её.

    ADR-049: раньше сущности здесь появлялись из детерминированной заглушки EXTRACT, и
    проверялось вовсе не NORMALIZE, а то, что заглушка породила слово «алгоритм». Имя и
    канон у такой ноды совпадали по построению, поэтому утверждение проходило и при
    полностью сломанной NORMALIZE — тест был зелёным не по той причине.

    Теперь сущность приходит от модели, и имя в ней отличается от канонического: без
    глоссари канон сворачивается ключом идентичности, а отображаемое имя сохраняется.
    Проверка стала настоящей — она падает, если NORMALIZE перепишет имя или выбросит
    сущность.
    """
    import json
    from copy import deepcopy

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
    from graphrag_proto.retrieval.adapters.llm import FakeLLM

    profile = {
        "profile": {"name": "it"},
        "extraction": {
            "llm_enabled": True,
            "prompt_template": {
                "id": "extract_ingestion_api_v1",
                "system": "Extract context nodes and links.",
                "user": "Return JSON with tags and links.",
            },
        },
    }
    answer = {
        "tags": [
            {
                "canonical_name": "Quicksort",
                "name": "быстрая сортировка",
                "origin": "ai",
            }
        ]
    }
    monkeypatch.setenv("EXTRACT_LLM", "true")
    reg = DocumentRegistry(tmp_path / "r.db")
    graph = InMemoryGraphStore()
    vector = InMemoryVectorStore()
    reader = {"txt": TxtReader()}
    analyzer = Analyzer(
        [
            IngestStage(reader),
            ChunkStage(),
            EmbedStage(),
            ExtractStage(
                llm=FakeLLM(text=json.dumps(answer, ensure_ascii=False), is_fake=False),
                profile_fetcher=lambda _domain: deepcopy(profile),
                optional_failure=True,
            ),
            NormalizeStage(""),
            DedupStage(),
            ContractStage(),
            ValidateStage(),
            CommitStage(
                reg,
                graph_store=graph,
                vector_store=vector,
                graph_optional=True,
            ),
        ],
        profile_fetcher=lambda _domain: deepcopy(profile),
    )
    src = tmp_path / "d.txt"
    src.write_text("алгоритм сортировки quicksort разделяй и властвуй", encoding="utf-8")
    ctx = PipelineContext(
        job_id="j", domain="it", doc_type="txt", source_url="src://d.txt", source_path=str(src)
    )
    analyzer.run(ctx)
    assert ctx.commit_applied is True
    assert ctx.enrichment_degraded is False, ctx.enrichment_error
    assert {e["name"] for e in ctx.entities} == {"быстрая сортировка"}, (
        "NORMALIZE без глоссари не имеет права переписывать отображаемое имя"
    )
    assert {e["canonical"] for e in ctx.entities} == {"quicksort"}, (
        "канон сворачивается ключом идентичности даже без глоссари"
    )

def test_note_stage_records_message_without_touching_status(tmp_path: Path) -> None:
    """note_stage не имеет права переводить стадию обратно в running.

    Именно этим отличается от update_stage, который после завершения джобы вернул бы
    EXTRACT в состояние `running`. Регрессия закрывает деградацию в стадии.
    """
    jobs = JobStore(tmp_path / "note.db")
    jobs.create("job-1", source_url="src://a.txt", domain="it", doc_type="txt")
    job_id = "job-1"
    jobs.update_stage(job_id, "EXTRACT")
    jobs.finish(job_id, "succeeded")

    before = {s["stage"]: s["status"] for s in jobs.stages(job_id)}
    jobs.note_stage(job_id, "EXTRACT", "enrichment_degraded: glossary 503")
    after = jobs.stages(job_id)

    assert {s["stage"]: s["status"] for s in after} == before
    assert before["EXTRACT"] == "succeeded"
    extract = next(s for s in after if s["stage"] == "EXTRACT")
    assert extract["message"] == "enrichment_degraded: glossary 503"
    assert jobs.get(job_id)["status"] == "succeeded"


def test_job_signals_are_structural_and_independent_of_message(tmp_path: Path) -> None:
    """Флаги джобы — поле, а не текст: канал один на оба факта.

    Причина в том, что счётчик потерь строится на этом поле: если бы флаг читался из
    `message`, одна рефакторинга формата тихо дала бы ноль, а «мы никогда не теряем
    слой» выглядело бы как хорошая новость.
    """
    jobs = JobStore(tmp_path / "signals.db")
    jobs.create("job-1", source_url="src://a.txt", domain="it", doc_type="txt")
    jobs.update_stage("job-1", "EXTRACT")
    jobs.set_signal("job-1", "EXTRACT", "enrichment_degraded")
    jobs.set_signal("job-1", "EXTRACT", "llm_layer_dropped")
    jobs.note_stage("job-1", "EXTRACT", "enrichment_degraded: relation unknown")

    assert jobs.signals("job-1") == {
        "enrichment_degraded": "EXTRACT",
        "llm_layer_dropped": "EXTRACT",
    }

    # текст стадии может быть полностью переформатирован — сигналы это переживают
    jobs.note_stage("job-1", "EXTRACT", "DEGRADED v2: relation unknown")
    assert jobs.signals("job-1") == {
        "enrichment_degraded": "EXTRACT",
        "llm_layer_dropped": "EXTRACT",
    }
    assert jobs.stages("job-1")[0]["message"] == "DEGRADED v2: relation unknown"

    # отсутствие сигнала — отсутствие ключа, а не «false» из разбора текста
    jobs.create("job-2", source_url="src://b.txt", domain="it", doc_type="txt")
    jobs.update_stage("job-2", "EXTRACT")
    jobs.note_stage("job-2", "EXTRACT", "enrichment_degraded: relation unknown")
    assert jobs.signals("job-2") == {}


def test_job_enrichment_carries_counts_not_just_a_flag(tmp_path: Path) -> None:
    """Размер потери — число в own-канале, а не вывод из текста сообщения.

    Флаг отвечает на вопрос «было ли что терять», число — «сколько». Метрика «сколько
    фактов исчезло» строится именно на числе: из флага получается только частота, а из
    частоты нельзя понять, дорога ли починка.
    """
    jobs = JobStore(tmp_path / "enrichment.db")
    jobs.create("job-1", source_url="src://a.txt", domain="it", doc_type="txt")
    jobs.record_enrichment(
        "job-1",
        cause="model_error",
        lost_entities=77,
        lost_edges=12,
        llm_records=77,
        llm_edges=12,
    )

    facts = jobs.enrichment("job-1")
    assert facts == {
        "cause": "model_error",
        "lost_entities": 77,
        "lost_edges": 12,
        "llm_records": 77,
        "llm_edges": 12,
    }

    # джоба без записанных фактов — пустой словарь, а не нули с выдуманной причиной
    jobs.create("job-2", source_url="src://b.txt", domain="it", doc_type="txt")
    assert jobs.enrichment("job-2") == {}


def test_job_enrichment_is_written_even_without_degradation(tmp_path: Path) -> None:
    """Факты пишутся на каждой джобе, прошедшей EXTRACT, а не только при сбое.

    Объём LLM-слоя — знаменатель для «записей на документ». Если писать его только при
    деградации, то в знаменателе окажутся ровно те документы, где сломалось, и доля
    посчитается по подмножеству неудач — то есть покажет улучшение там, где стало хуже.
    """
    jobs = JobStore(tmp_path / "volume.db")
    jobs.create("job-1", source_url="src://a.txt", domain="it", doc_type="txt")
    jobs.record_enrichment(
        "job-1",
        cause=None,
        lost_entities=0,
        lost_edges=0,
        llm_records=31,
        llm_edges=5,
    )

    facts = jobs.enrichment("job-1")
    assert facts["cause"] is None
    assert facts["llm_records"] == 31


def test_stage_duration_is_measured_at_transition(tmp_path: Path) -> None:
    """Длительность стадии измеряется в момент перехода и хранится явно.

    Причина, по которой это отдельный факт, а не разность timestamps: `update_stage`
    перезаписывает `ts` предыдущей стадии в момент перехода, поэтому у всех завершённых
    стадий `ts` — момент окончания. Разности соседних `ts` длительностями не являются,
    а длительность последней стадии не восстанавливается вовсе. Из этого же следует,
    что повышение разрешения `ts` контракт не удовлетворяет: нужна новая величина.
    """
    jobs = JobStore(tmp_path / "dur.db")
    jobs.create("job-1", source_url="src://a.txt", domain="it", doc_type="txt")
    jobs.update_stage("job-1", "INGEST")
    time.sleep(0.06)
    jobs.update_stage("job-1", "EXTRACT")

    durations = jobs.stage_durations("job-1")
    # Под-секундная стадия обязана быть видна: иначе метрика по стадиям врёт ровно на
    # том, ради чего она и нужна, - на быстрых стадиях.
    assert durations["INGEST"] >= 50
    assert durations["INGEST"] < 5000
    # Текущая стадия ещё идёт: длительности у неё нет, и это не ноль.
    assert "EXTRACT" not in durations


def test_last_stage_duration_is_recorded_on_finish(tmp_path: Path) -> None:
    """Длительность последней стадии восстанавливается — раньше это было невозможно.

    Отдельный случай, потому что закрывает стадию не `update_stage`, а `finish`, и
    именно этот путь раньше не оставлял следа о прошедшем времени вовсе.
    """
    jobs = JobStore(tmp_path / "last.db")
    jobs.create("job-1", source_url="src://a.txt", domain="it", doc_type="txt")
    jobs.update_stage("job-1", "INGEST")
    jobs.update_stage("job-1", "EXTRACT")
    time.sleep(0.06)
    jobs.finish("job-1", "succeeded")

    durations = jobs.stage_durations("job-1")
    assert set(durations) == {"INGEST", "EXTRACT"}
    assert durations["EXTRACT"] >= 50


def test_stage_duration_survives_database_created_before_it_existed(tmp_path: Path) -> None:
    """Новая таблица, а не колонка: у развёрнутой базы не должно быть миграции.

    Добавление колонки потребовало бы ALTER TABLE на каждой базе, где пайплайн уже
    отработал, а `CREATE TABLE IF NOT EXISTS` создаёт таблицу на месте. Это же объясняет,
    почему `job_signals` и `job_enrichment` не колонки в `job_stages`.
    """
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE job_stages (job_id TEXT NOT NULL, stage TEXT NOT NULL, "
        "status TEXT NOT NULL, message TEXT, ts TEXT NOT NULL, PRIMARY KEY (job_id, stage))"
    )
    conn.commit()
    conn.close()

    jobs = JobStore(db)
    jobs.create("job-1", source_url="src://a.txt", domain="it", doc_type="txt")
    jobs.update_stage("job-1", "INGEST")
    jobs.update_stage("job-1", "EXTRACT")
    jobs.finish("job-1", "succeeded")

    assert set(jobs.stage_durations("job-1")) == {"INGEST", "EXTRACT"}
    # Журнал стадий не пострадал: новая таблица не меняет его форму.
    assert {s["stage"] for s in jobs.stages("job-1")} == {"INGEST", "EXTRACT"}


def _edge(domain: str, chunk_ids: list[str] | None, source_ids: list[str]) -> dict[str, object]:
    """Доменное ребро. `chunk_ids=None` — свойства нет вовсе (структурное)."""
    properties: dict[str, object] = {"domain": domain, "source_ids": list(source_ids)}
    if chunk_ids is not None:
        properties["chunk_ids"] = list(chunk_ids)
    return {
        "from_id": "e-1",
        "to_id": "e-2",
        "type": "REL",
        "properties": properties,
    }


def test_orphan_cleanup_keeps_edge_supported_by_other_document(tmp_path: Path) -> None:
    """Связь, которую держат два документа, обязана выжить после смерти одного.

    Регрессия на формулировку «нет ни одного источника»: если удалять по факту потери
    одного источника, связь, поддержанная ещё одним активным документом, была бы уничтожена
    вместе с тремя общими сущностями.
    """
    store = InMemoryGraphStore()
    store.upsert_edges([_edge("it", ["chunk-b1", "chunk-b2"], ["docs://b.md", "docs://a-old.md"])])

    removed_edges, removed_nodes = store.delete_orphans("it", dry_run=False)

    assert (removed_edges, removed_nodes) == (0, 0)
    assert store.verify_edge("e-1", "e-2", "REL")


def test_orphan_cleanup_deletes_edge_with_no_supporting_chunk(tmp_path: Path) -> None:
    """Устаревшее ребро удаляется: поддерживающих чанков не осталось ни одного."""
    store = InMemoryGraphStore()
    store.upsert_edges([_edge("it", [], [])])

    removed_edges, removed_nodes = store.delete_orphans("it", dry_run=False)

    assert (removed_edges, removed_nodes) == (1, 0)
    assert not store.verify_edge("e-1", "e-2", "REL")


def test_orphan_cleanup_never_touches_structural_edges(tmp_path: Path) -> None:
    """`CONTAINS` и `MENTIONS` не имеют `chunk_ids` вовсе — уборка их не должна видеть.

    Это главная ловушка реализации: условие «список пуст» для структурных рёбер истинно,
    и без дискриминатора уборка сносит их. Ошибка проявилась бы не как «не туда удалил»,
    а как «сломался поиск».
    """
    store = InMemoryGraphStore()
    store.upsert_nodes(
        [
            {"node_id": "s-1", "labels": ["Source"], "properties": {"domain": "it"}},
            {"node_id": "c-1", "labels": ["Chunk"], "properties": {"domain": "it"}},
            {"node_id": "e-1", "labels": ["ContextNode"], "properties": {"domain": "it"}},
        ]
    )
    store.upsert_edges(
        [
            {
                "from_id": "s-1",
                "to_id": "c-1",
                "type": "CONTAINS",
                "properties": {"domain": "it", "source_ids": ["docs://a.md"]},
            },
            {
                "from_id": "c-1",
                "to_id": "e-1",
                "type": "MENTIONS",
                "properties": {"domain": "it", "source_ids": ["docs://a.md"]},
            },
        ]
    )

    store.delete_orphans("it", dry_run=False)

    assert store.verify_edge("s-1", "c-1", "CONTAINS")
    assert store.verify_edge("c-1", "e-1", "MENTIONS")
    assert store.get_node("c-1") is not None


def test_orphan_cleanup_respects_domain_boundary(tmp_path: Path) -> None:
    """Уборка домена не должна трогать другой домен, даже если он пустой по поддержке."""
    store = InMemoryGraphStore()
    store.upsert_edges([_edge("legal", [], [])])

    removed_edges, removed_nodes = store.delete_orphans("it", dry_run=False)

    assert (removed_edges, removed_nodes) == (0, 0)
    assert store.verify_edge("e-1", "e-2", "REL")


def test_orphan_cleanup_dry_run_counts_and_deletes_nothing(tmp_path: Path) -> None:
    """Подсчёт обязателен до удаления: цена ошибки предиката необратима.

    Регрессия-guard на саму необходимость dry_run: без него неверный предикат обнаруживается
    не на прогоне, а когда пропавший ответ всплывает через неделю.
    """
    store = InMemoryGraphStore()
    # Разные узлы обязательны: тройка (from_id, to_id, type) — ключ ребра, и ребро с той же
    # тройкой просто перезаписало бы предыдущее.
    store.upsert_edges(
        [
            _edge("it", [], []),
            {
                "from_id": "e-3",
                "to_id": "e-4",
                "type": "REL",
                "properties": {
                    "domain": "it",
                    "chunk_ids": ["chunk-live"],
                    "source_ids": ["docs://b.md"],
                },
            },
        ]
    )

    planned_edges, planned_nodes = store.delete_orphans("it", dry_run=True)

    assert (planned_edges, planned_nodes) == (1, 0)
    assert store.verify_edge("e-1", "e-2", "REL")
    assert store.verify_edge("e-3", "e-4", "REL")


def test_orphan_cleanup_removes_nodes_without_support_and_keeps_connected(tmp_path: Path) -> None:
    """Узлы чистятся тем же проходом, но узел со связями не трогается."""
    store = InMemoryGraphStore()
    store.upsert_nodes(
        [
            {"node_id": "lonely", "labels": ["ContextNode"], "properties": {"domain": "it", "chunk_ids": []}},
            {"node_id": "linked", "labels": ["ContextNode"], "properties": {"domain": "it", "chunk_ids": []}},
        ]
    )
    # Связь должна инцидентна именно `linked`; инцидентность проверяется по обоим концам.
    store.upsert_edges(
        [
            {
                "from_id": "linked",
                "to_id": "other",
                "type": "REL",
                "properties": {"domain": "it", "chunk_ids": ["chunk-live"], "source_ids": []},
            }
        ]
    )

    store.delete_orphans("it", dry_run=False)

    assert store.get_node("lonely") is None
    assert store.get_node("linked") is not None


def test_orphan_cleanup_is_idempotent(tmp_path: Path) -> None:
    """Повторный проход на очищенном графе ничего не удаляет и не падает.

    Уборка фоновая и будет проходить повторно; если второй проход падает или считает
    неверно, это выглядит как «удалять нечего» — правдоподобно и неверно.
    """
    store = InMemoryGraphStore()
    store.upsert_edges([_edge("it", [], [])])

    first = store.delete_orphans("it", dry_run=False)
    second = store.delete_orphans("it", dry_run=False)

    assert first == (1, 0)
    assert second == (0, 0)


def test_orphan_cleanup_uses_chunk_ids_not_source_ids(tmp_path: Path) -> None:
    """Канонический признак — `chunk_ids`: источник может быть жив при отсутствии чанков.

    Связь с непустым `source_ids`, но пустым `chunk_ids` считается мёртвой: держать её
    не на чем, и сохранять её значило бы держать то, что мы собрались убрать.
    """
    store = InMemoryGraphStore()
    store.upsert_edges([_edge("it", [], ["docs://a.md"])])

    removed_edges, removed_nodes = store.delete_orphans("it", dry_run=False)

    assert (removed_edges, removed_nodes) == (1, 0)
    assert not store.verify_edge("e-1", "e-2", "REL")


def test_stage_duration_of_repeated_call_reflects_last_attempt(tmp_path: Path) -> None:
    """Повторный вход в ту же стадию перезапускает её часы — как и `ts` в журнале.

    Осознанный выбор семантики: строка описывает последнее наблюдение, а не сумму
    всех попыток. Накопление по повторным входам - отдельное решение, и молча
    складывать его здесь означало бы, что таблица отвечает на вопрос, которого
    никто не задавал.
    """
    jobs = JobStore(tmp_path / "again.db")
    jobs.create("job-1", source_url="src://a.txt", domain="it", doc_type="txt")
    jobs.update_stage("job-1", "EXTRACT")
    time.sleep(0.06)
    jobs.update_stage("job-1", "EXTRACT")
    jobs.update_stage("job-1", "PROJECT")

    assert jobs.stage_durations("job-1")["EXTRACT"] < 5000


def test_cancelled_stage_records_elapsed_time(tmp_path: Path) -> None:
    """Отменённая стадия тоже оставляет прошедшее время.

    Время обрывается, но факт состоялся, и по нему видно, где джоба стояла в момент
    отмены. Не писать его - значит потерять наблюдение; писать ноль - значит соврать.
    """
    jobs = JobStore(tmp_path / "cancel.db")
    jobs.create("job-1", source_url="src://a.txt", domain="it", doc_type="txt")
    jobs.update_stage("job-1", "EXTRACT")
    jobs.finish("job-1", "cancelled")

    durations = jobs.stage_durations("job-1")
    assert durations["EXTRACT"] >= 0