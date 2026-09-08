"""Ingestion API (:8002): POST/GET/DELETE jobs, in-process executor.

Lifecycle (ADR-018): queued -> running (INGEST..COMMIT) -> succeeded | failed | cancelled.
Джоба исполняется в фоновом потоке (in-process executor); Task Queue — M2.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from graphrag_proto.ingestion_service.pipeline.orchestrator import (
    STAGES,
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
from graphrag_proto.ingestion_service.readers.registry import factory as readers_factory
from graphrag_proto.ingestion_service.storage.registry import DocumentRegistry, JobStore

HOST = "0.0.0.0"
PORT = 8002
ALLOWED_DOC_TYPES = {"txt", "md", "pdf"}


def _upload_dir() -> Path:
    env = os.environ.get("INGESTION_UPLOAD_DIR")
    return Path(env) if env else Path("runtime/uploads")


def _db_path() -> Path:
    env = os.environ.get("INGESTION_DB_PATH")
    return Path(env) if env else Path("runtime/ingestion.db")


def _glossary_url() -> str:
    return os.environ.get("GLOSSARY_URL", "")


def build_analyzer(registry: DocumentRegistry, glossary_url: str) -> Analyzer:
    readers = {doc_type: readers_factory(doc_type) for doc_type in sorted(ALLOWED_DOC_TYPES)}
    return Analyzer(
        [
            IngestStage(readers),
            ChunkStage(),
            EmbedStage(),
            ExtractStage(),
            NormalizeStage(glossary_url),
            DedupStage(),
            ContractStage(),
            ValidateStage(),
            CommitStage(registry),
        ]
    )


class Executor:
    """In-process executor: фон. поток + журнал этапов в JobStore."""

    def __init__(self, jobs: JobStore, registry: DocumentRegistry, glossary_url: str) -> None:
        self._jobs = jobs
        self._registry = registry
        self._analyzer = build_analyzer(registry, glossary_url)

    def start(
        self,
        job_id: str,
        target: Path,
        source_url: str,
        domain: str,
        doc_type: str,
    ) -> None:
        thread = threading.Thread(
            target=self._run,
            args=(job_id, target, source_url, domain, doc_type),
            daemon=True,
        )
        thread.start()

    def _run(
        self,
        job_id: str,
        target: Path,
        source_url: str,
        domain: str,
        doc_type: str,
    ) -> None:
        ctx = PipelineContext(
            job_id=job_id,
            domain=domain,
            doc_type=doc_type,
            source_url=source_url,
            source_path=str(target),
        )
        try:
            for stage_name in STAGES:
                if self._jobs.is_cancelled(job_id):
                    self._jobs.finish(job_id, "cancelled")
                    return
                self._jobs.update_stage(job_id, stage_name)
                self._analyzer.run_one(stage_name, ctx)
            self._jobs.finish(job_id, "succeeded")
        except Exception as exc:  # noqa: BLE001 - разнородные источники сбоев этапов
            self._jobs.finish(job_id, "failed", error=str(exc))

    def cancel(self, job_id: str) -> bool:
        return self._jobs.cancel(job_id)


def create_app(
    upload_dir: Path | None = None,
    db_path: Path | None = None,
    glossary_url: str | None = None,
) -> FastAPI:
    upload_dir = upload_dir or _upload_dir()
    db_path = db_path or _db_path()
    glossary_url = glossary_url if glossary_url is not None else _glossary_url()
    upload_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    jobs = JobStore(db_path)
    registry = DocumentRegistry(db_path)
    executor = Executor(jobs, registry, glossary_url)

    app = FastAPI(title="GraphRAG Ingestion Service", version="0.1.0")

    @app.post("/api/v1/ingestion/documents")
    def create_document(payload: dict[str, Any] = Body(...)) -> JSONResponse:  # noqa: B008
        source_url = payload.get("source_url")
        domain = payload.get("domain")
        doc_type = payload.get("doc_type")
        content = payload.get("content")
        if not source_url or not domain or not doc_type:
            raise HTTPException(status_code=422, detail="source_url/domain/doc_type обязательны")
        if doc_type not in ALLOWED_DOC_TYPES:
            raise HTTPException(
                status_code=422,
                detail=(
                    "doc_type={!r} не поддерживается (допустимо: {})".format(
                        doc_type, ", ".join(sorted(ALLOWED_DOC_TYPES))
                    )
                ),
            )
        job_id = str(uuid.uuid4())
        jobs.create(job_id, source_url, domain, doc_type)
        target = upload_dir / f"{job_id}.{doc_type}"
        if content is not None:
            raw = content.encode("utf-8") if isinstance(content, str) else content
            target.write_bytes(raw)
        executor.start(job_id, target, source_url, domain, doc_type)
        return JSONResponse(
            {
                "job_id": job_id,
                "status": "queued",
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            },
            status_code=202,
        )

    @app.get("/api/v1/ingestion/jobs")
    def list_jobs(page: int = 1, page_size: int = 20) -> dict[str, Any]:
        items, total = jobs.list(page, page_size)
        return {"items": items, "page": page, "page_size": page_size, "total": total}

    @app.get("/api/v1/ingestion/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"джоба {job_id} не найдена")
        job["stages"] = jobs.stages(job_id)
        return job

    @app.delete("/api/v1/ingestion/jobs/{job_id}")
    def cancel_job(job_id: str) -> dict[str, Any]:
        cancelled = executor.cancel(job_id)
        if not cancelled:
            job = jobs.get(job_id)
            if job is None:
                raise HTTPException(status_code=404, detail=f"джоба {job_id} не найдена")
            raise HTTPException(
                status_code=409,
                detail=f"джоба уже в статусе {job['status']}",
            )
        return {"job_id": job_id, "status": "cancelled"}

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host=HOST, port=PORT)


if __name__ == "__main__":
    main()