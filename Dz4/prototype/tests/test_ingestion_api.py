from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from graphrag_proto.ingestion_service.app import create_app
from graphrag_proto.ingestion_service.storage.registry import (
    STATUS_ACTIVE,
    STATUS_SUPERSEDED,
    DocumentRegistry,
    JobStore,
)

API_KEY = "changeme"


class _AuthedClient(TestClient):
    """TestClient, подставляющий X-API-Key по умолчанию (запросы без ключа — в auth-тестах)."""

    def request(self, method, url, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("X-API-Key", API_KEY)
        return super().request(method, url, headers=headers, **kwargs)


def wait_until(condition, timeout: float = 10.0, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return False


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
    ex = Executor(jobs, reg, glossary_url="", max_concurrent=1)
    assert ex._slots.acquire(blocking=False) is True
    try:
        assert ex.start("blocked", src, "src://blocked", "it", "txt") is False
    finally:
        ex._slots.release()
    jobs.create("ok", "src://ok", "it", "txt")
    assert ex.start("ok", src, "src://ok", "it", "txt") is True
    assert wait_until(lambda: jobs.get("ok")["status"] == "succeeded")


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


def test_runner_normしalize_without_glossary_keeps_entities(tmp_path: Path) -> None:
    """Без Glossary URL этап NORMALIZE оставляет entity name как canonical."""
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

    reg = DocumentRegistry(tmp_path / "r.db")
    reader = {"txt": TxtReader()}
    analyzer = Analyzer(
        [
            IngestStage(reader),
            ChunkStage(),
            EmbedStage(),
            ExtractStage(),
            NormalizeStage(""),
            DedupStage(),
            ContractStage(),
            ValidateStage(),
            CommitStage(reg),
        ]
    )
    src = tmp_path / "d.txt"
    src.write_text("алгоритм quicksort дедупликация алгоритм", encoding="utf-8")
    ctx = PipelineContext(
        job_id="j", domain="it", doc_type="txt", source_url="src://d.txt", source_path=str(src)
    )
    analyzer.run(ctx)
    assert ctx.commit_applied is True
    names = {e["name"] for e in ctx.entities}
    # заглушка EXTRACT: deteministic entities из слов >= 5 символов
    assert "алгоритм" in names