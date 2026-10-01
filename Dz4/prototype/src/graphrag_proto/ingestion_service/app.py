"""Ingestion API (:8002): POST/GET/DELETE jobs, in-process executor.

Lifecycle (ADR-018): queued -> running (INGEST..COMMIT) -> succeeded | failed | cancelled.
Джоба исполняется в фоновом потоке (in-process executor); Task Queue — M2.
Все эндпоинты требуют `X-API-Key` (L5-01).
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from graphrag_proto.ingestion_service.pipeline.chunker import Chunker
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
    ProfileFetcher,
    ValidateStage,
    soft_delete_source,
)
from graphrag_proto.ingestion_service.projection import (
    ProjectionStateStore,
    try_build_projection_state_store,
)
from graphrag_proto.ingestion_service.readers.registry import factory as readers_factory
from graphrag_proto.ingestion_service.retention_policy import run_orphan_cleanup
from graphrag_proto.ingestion_service.storage.registry import (
    STATUS_DELETED,
    DocumentRegistry,
    JobStore,
)
from graphrag_proto.retrieval.adapters.base import Embedder
from graphrag_proto.retrieval.adapters.factory import (
    build_embedder,
    build_graph_store,
    build_llm,
    build_vector_store,
)
from graphrag_proto.retrieval.profile import DomainProfileLoader

HOST = "0.0.0.0"
PORT = 8002
ALLOWED_DOC_TYPES = {"txt", "md", "pdf"}

# Префикс пометки деградации optional-ингеста в message стадии EXTRACT. Владелец
# контракта — здесь; читает его eval-раннер (`infra/eval/run_eval.py`), который
# общается с сервисом по HTTP. Деградация иначе неотличима от успеха: джоба
# остаётся `succeeded`, просто рёбер и фактов в графе меньше.
ENRICHMENT_DEGRADED_PREFIX = "enrichment_degraded"
# Имена структурных сигналов в ответе джобы (`job["signals"]`). Один канал на оба
# факта: «документ обработан хуже» и «LLM-слой потерян». Именно они, а не текст
# сообщения, являются источником данных для счётчиков: тихий ноль в метрике потерь
# опаснее, чем её отсутствие, а один флаг полем и другой текстом означают два
# механизма у потребителя.
ENRICHMENT_DEGRADED_SIGNAL = "enrichment_degraded"
# Суффикс к сообщению деградации, когда LLM-слой потерян ЦЕЛИКОМ, а не просто
# обработан хуже. Отдельное имя, потому что `enrichment_degraded` ставится в двух
# местах с противоположным смыслом: профиль не загрузился (ничего не потеряно) и
# исключение в экстракции (потерян весь слой). Одна метка на оба факта не
# интерпретируется: счётчик может показать высокую деградацию при нулевой потере.
LLM_LAYER_DROPPED_MARKER = "[llm_layer_dropped]"
# Имя структурного сигнала в ответе джобы (`job["signals"]`). Именно он, а не текст
# сообщения, является источником данных для счётчика потерь: тихий ноль в метрике
# потерь опаснее, чем её отсутствие.
LLM_LAYER_DROPPED_SIGNAL = "llm_layer_dropped"


def _upload_dir() -> Path:
    env = os.environ.get("INGESTION_UPLOAD_DIR")
    return Path(env) if env else Path("runtime/uploads")


def _db_path() -> Path:
    env = os.environ.get("INGESTION_DB_PATH")
    return Path(env) if env else Path("runtime/ingestion.db")


def _glossary_url() -> str:
    return os.environ.get("GLOSSARY_URL", "")


def _max_concurrent() -> int:
    try:
        return max(int(os.environ.get("INGEST_MAX_CONCURRENT", "2")), 1)
    except ValueError:
        return 2


def _strict_profile_loading() -> bool:
    return os.environ.get("EXTRACT_LLM", "").strip().lower() in {"1", "true", "yes", "on"}


#: Переопределение температуры именно для извлечения. Общая `LLM_TEMPERATURE` остаётся
#: для генерации ответов на вопросы: там ненулевая температура осмысленна, здесь - нет.
EXTRACTION_LLM_TEMPERATURE_ENV = "EXTRACTION_LLM_TEMPERATURE"

#: Префикс паспорта извлечения в сообщении стадии. Префикс, а не отдельное поле, потому
#: что канал для машинных значений один - `signals`, и вводить второй значило бы дать
#: потребителю два механизма там, где он рассчитывает на один.
EXTRACTION_PASSPORT_PREFIX = "extraction_passport: "


def _extraction_temperature() -> float:
    """Температура для стадии EXTRACT, отдельно от генерации ответов.

    Извлечение - структурированный JSON, и семплирование здесь неуместно: при
    `temperature` из профиля (0.3) выход модели гулял между прогонами на одном и том же
    корпусе - от 15 до 214 связей, - то есть результат зависел от случайности, а не от
    кода. Генерация ответа на вопрос с `0.3` разумна, поэтому переопределение точечное, а
    не глобальное.

    Дефолт здесь - сознательное решение, а не молчаливое значение: он виден в коде и
    переопределяется переменной окружения, если понадобится осознанно семплировать.
    """
    raw = os.environ.get(EXTRACTION_LLM_TEMPERATURE_ENV, "").strip()
    if not raw:
        return 0.0
    try:
        return float(raw)
    except ValueError as exc:
        raise RuntimeError(
            f"{EXTRACTION_LLM_TEMPERATURE_ENV}={raw!r} не число: температура извлечения "
            f"обязана быть разбираемым числом, иначе поведение зависит от опечатки в конфиге"
        ) from exc


def build_analyzer(
    registry: DocumentRegistry,
    glossary_url: str,
    graph_store: Any = None,
    vector_store: Any = None,
    embedder: Embedder | None = None,
    chunker: Chunker | None = None,
    llm: Any | None = None,
    profile_fetcher: ProfileFetcher | None = None,
    projection_state_store: ProjectionStateStore | None = None,
) -> Analyzer:
    readers = {doc_type: readers_factory(doc_type) for doc_type in sorted(ALLOWED_DOC_TYPES)}
    return Analyzer(
        [
            IngestStage(readers),
            ChunkStage(chunker, profile_fetcher=profile_fetcher),
            EmbedStage(embedder or build_embedder()),
            ExtractStage(
                llm=llm if llm is not None else build_llm(
            temperature=_extraction_temperature(),
            # Метка стадии в журнале обмена: из ingest модель зовёт только извлечение, и
            # без метки записи обмена не отличить от вызовов генерации в query-service.
            exchange_stage="extract",
        ),
                profile_fetcher=profile_fetcher,
                optional_failure=True,
            ),
            NormalizeStage(glossary_url),
            DedupStage(),
            ContractStage(),
            ValidateStage(),
            CommitStage(
                registry,
                graph_store=graph_store,
                vector_store=vector_store,
                profile_fetcher=profile_fetcher,
                graph_optional=True,
                projection_state_store=projection_state_store,
            ),
        ]
    )


class Executor:
    """In-process executor: фон. поток + журнал этапов в JobStore."""

    def __init__(
        self,
        jobs: JobStore,
        registry: DocumentRegistry,
        glossary_url: str,
        graph_store: Any = None,
        vector_store: Any = None,
        embedder: Embedder | None = None,
        chunker: Chunker | None = None,
        max_concurrent: int = 2,
        llm: Any | None = None,
        profile_fetcher: ProfileFetcher | None = None,
        projection_state_store: ProjectionStateStore | None = None,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent должен быть >= 1")
        self._jobs = jobs
        self._registry = registry
        self._profile_fetcher = profile_fetcher
        self._slots = threading.BoundedSemaphore(max_concurrent)
        self._analyzer = build_analyzer(
            registry,
            glossary_url,
            graph_store=graph_store,
            vector_store=vector_store,
            embedder=embedder,
            chunker=chunker,
            llm=llm,
            profile_fetcher=profile_fetcher,
            projection_state_store=projection_state_store,
        )

    def start(
        self,
        job_id: str,
        target: Path,
        source_url: str,
        domain: str,
        doc_type: str,
        tags: list[dict[str, Any]] | None = None,
        links: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Запускает фоновый поток; False, если все слоты заняты (429)."""
        if not self._slots.acquire(blocking=False):
            return False
        thread = threading.Thread(
            target=self._run,
            args=(job_id, target, source_url, domain, doc_type, tags or [], links or [], metadata or {}),
            daemon=True,
        )
        thread.start()
        return True

    def _run(
        self,
        job_id: str,
        target: Path,
        source_url: str,
        domain: str,
        doc_type: str,
        tags: list[dict[str, Any]],
        links: list[dict[str, Any]],
        metadata: dict[str, Any],
    ) -> None:
        ctx = PipelineContext(
            job_id=job_id,
            domain=domain,
            doc_type=doc_type,
            source_url=source_url,
            source_path=str(target),
            tags=tags,
            links=links,
            metadata=metadata,
        )
        try:
            for stage_name in STAGES:
                if self._jobs.is_cancelled(job_id):
                    self._jobs.finish(job_id, "cancelled")
                    return
                self._jobs.update_stage(job_id, stage_name)
                self._analyzer.run_one(stage_name, ctx)
                if stage_name == "INGEST":
                    self._analyzer.try_noop(ctx)
                if stage_name == "EXTRACT":
                    # Паспорт извлечения: всё, чем выполнялся разбор, кроме текста
                    # документа. Пишется на каждой джобе, прошедшей EXTRACT, а не только
                    # при деградации, - иначе «чем именно извлечено» остаётся неизвестным
                    # именно там, где всё прошло хорошо и расходиться не с чем.
                    if ctx.extraction_passport:
                        passport = ctx.extraction_passport
                        self._jobs.record_extraction_passport(job_id, passport)
                        self._jobs.set_signal(job_id, stage_name, f"extraction:{passport['identity']}")
                    # Факты об объёме пишутся на КАЖДОЙ джобе, прошедшей EXTRACT, а не
                    # только при деградации: объём LLM-слоя — это знаменатель для
                    # «записей на документ», и без него доля считается по документам,
                    # где экстракция не запускалась вовсе.
                    self._jobs.record_enrichment(
                        job_id,
                        cause=ctx.enrichment_cause,
                        lost_entities=ctx.llm_layer_lost_entities,
                        lost_edges=ctx.llm_layer_lost_edges,
                        llm_records=ctx.llm_records,
                        llm_edges=ctx.llm_edges,
                    )
                    # Неразрешённые концы — фактом, а не поводом снести слой (ADR-037).
                    # Пишутся на каждой джобе, где они были; джоба уборки обходит их по
                    # `endpoint_key`, поэтому отдельная запись на конец, а не счётчик.
                    self._jobs.record_missing_endpoints(
                        job_id, source_url, ctx.unresolved_endpoints
                    )
                if ctx.enrichment_degraded:
                    # Оба факта идут ОДНИМ каналом — полем `signals`. Канал заводится
                    # один раз на оба флага: если один придёт полем, а другой текстом,
                    # у потребителя будет два механизма, а объяснение «почему так»
                    # станет историческим вместо замысла.
                    self._jobs.set_signal(job_id, stage_name, ENRICHMENT_DEGRADED_SIGNAL)
                    if ctx.llm_layer_dropped:
                        self._jobs.set_signal(job_id, stage_name, LLM_LAYER_DROPPED_SIGNAL)
                    # Сообщение остаётся человекочитаемой пометкой и несёт ПРИЧИНУ,
                    # которой больше негде жить. Источником данных не является: рефакторинг
                    # формата не должен обнулять счётчики.
                    dropped = f" {LLM_LAYER_DROPPED_MARKER}" if ctx.llm_layer_dropped else ""
                    self._jobs.note_stage(
                        job_id,
                        stage_name,
                        f"{ENRICHMENT_DEGRADED_PREFIX}: {ctx.enrichment_error}{dropped}",
                    )
            self._jobs.finish(job_id, "succeeded")
        except Exception as exc:  # noqa: BLE001 - разнородные источники сбоев этапов
            self._jobs.finish(job_id, "failed", error=str(exc))
        finally:
            self._slots.release()

    def cancel(self, job_id: str) -> bool:
        return self._jobs.cancel(job_id)


def create_app(
    upload_dir: Path | None = None,
    db_path: Path | None = None,
    glossary_url: str | None = None,
    api_key: str = "changeme",
    max_concurrent: int | None = None,
) -> FastAPI:
    upload_dir = upload_dir or _upload_dir()
    db_path = db_path or _db_path()
    glossary_url = glossary_url if glossary_url is not None else _glossary_url()
    max_concurrent = max_concurrent if max_concurrent is not None else _max_concurrent()
    upload_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    jobs = JobStore(db_path)
    registry = DocumentRegistry(db_path)
    graph_store = build_graph_store()
    vector_store = build_vector_store()
    projection_state_store = try_build_projection_state_store()
    profile_loader = DomainProfileLoader(
        config_url=os.environ.get("CONFIG_URL", ""),
        strict=False,
    )
    executor = Executor(
        jobs,
        registry,
        glossary_url,
        graph_store=graph_store,
        vector_store=vector_store,
        profile_fetcher=profile_loader.load,
        projection_state_store=projection_state_store,
        max_concurrent=max_concurrent,
    )

    app = FastAPI(title="GraphRAG Ingestion Service", version="0.1.0")

    def require_key(x_api_key: str | None = Header(None, alias="X-API-Key")) -> None:
        if api_key and x_api_key != api_key:
            raise HTTPException(status_code=401, detail="неверный или отсутствующий X-API-Key")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/v1/ingestion/documents", dependencies=[Depends(require_key)])
    def create_document(payload: dict[str, Any] = Body(...)) -> JSONResponse:  # noqa: B008
        source_url = payload.get("source_url")
        domain = payload.get("domain")
        doc_type = payload.get("doc_type")
        content = payload.get("content")
        tags = payload.get("tags") or []
        links = payload.get("links") or []
        metadata = payload.get("metadata") or {}
        if not isinstance(tags, list) or not isinstance(links, list) or not isinstance(metadata, dict):
            raise HTTPException(status_code=422, detail="tags/links должны быть списками, metadata — mapping")
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
        if not executor.start(
            job_id,
            target,
            source_url,
            domain,
            doc_type,
            tags=tags,
            links=links,
            metadata=metadata,
        ):
            jobs.finish(job_id, "failed", error="перегрузка: все слоты исполнения заняты")
            raise HTTPException(
                status_code=429,
                detail=f"все {max_concurrent} слотов исполнения заняты; повторите позже",
            )
        return JSONResponse(
            {
                "job_id": job_id,
                "status": "queued",
                "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            },
            status_code=202,
        )

    @app.get("/api/v1/ingestion/jobs", dependencies=[Depends(require_key)])
    def list_jobs(page: int = 1, page_size: int = 20) -> dict[str, Any]:
        items, total = jobs.list(page, page_size)
        return {"items": items, "page": page, "page_size": page_size, "total": total}

    @app.get("/api/v1/ingestion/jobs/{job_id}", dependencies=[Depends(require_key)])
    def get_job(job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"джоба {job_id} не найдена")
        job["stages"] = jobs.stages(job_id)
        # Структурные сигналы (например, потеря LLM-слоя) отдельным полем: consumer
        # не должен разбирать `message`, чтобы получить число.
        job["signals"] = jobs.signals(job_id)
        # Количественные факты обогащения — тот же принцип, но числа: сколько записей
        # исчезло и сколько модель вернула. Флаг без размера потери отвечает на вопрос
        # «было ли что терять», а не «сколько».
        job["enrichment"] = jobs.enrichment(job_id)
        return job

    @app.delete("/api/v1/ingestion/jobs/{job_id}", dependencies=[Depends(require_key)])
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

    @app.delete("/api/v1/ingestion/documents", dependencies=[Depends(require_key)])
    def delete_document(domain: str, source_url: str) -> dict[str, Any]:
        """Soft-delete источника (ADR-014): чанки снимаются с поиска (L2-05)."""
        deleted = soft_delete_source(
            registry,
            graph_store,
            vector_store,
            domain,
            source_url,
            projection_state_store=projection_state_store,
        )
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=f"источник domain={domain!r} source_url={source_url!r} не найден или уже удалён",
            )
        return {"domain": domain, "source_url": source_url, "status": STATUS_DELETED}

    @app.get("/api/v1/ingestion/revision", dependencies=[Depends(require_key)])
    def get_revision(domain: str | None = None) -> dict[str, Any]:
        """Ревизия данных домена (ADR-026, Веха 4-хвост)."""
        if not domain:
            raise HTTPException(status_code=422, detail="query-параметр domain обязателен")
        return {
            "revision": registry.data_revision(domain),
            "updated_at": registry.data_revision_updated_at(domain),
        }

    @app.post("/api/v1/maintenance/orphan-cleanup", dependencies=[Depends(require_key)])
    def maintenance_orphan_cleanup(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008
        """Один проход уборки сиротских связей и узлов по домену (ADR-014, B3+C).

        Маршрут существует, потому что уборка без маршрута не воспроизводима: прогон,
        где её вызвали руками и не оставили следа, не отвечает на вопрос «что именно
        удалилось». Здесь вызов возвращает подсчёты и `job_id`, а факт уборки пишется
        в реестр — то есть след есть и в ответе, и в данных.

        Отдельного планировщика нет намеренно: уборка фоновая, а её цена - молчаливая
        потеря данных в графе, поэтому решение о моменте остаётся за оператором.
        Режим политики (`DATA_RETENTION_MODE`) не переопределяется здесь: он свойство
        развёртывания, см. `docs/02` §4.3.
        """
        domain = payload.get("domain")
        if not domain:
            raise HTTPException(status_code=422, detail="query-поле domain обязательно")
        job_id = f"maintenance:{uuid.uuid4()}"
        fact = run_orphan_cleanup(graph_store, jobs, job_id=job_id, domain=str(domain))
        return {
            "job_id": job_id,
            "domain": domain,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            **fact,
        }

    return app


def main() -> None:
    import uvicorn

    from graphrag_proto.security import install_redaction

    install_redaction()
    api_key = os.environ.get("AUTH_API_KEY") or os.environ.get("GRAPH_AUTH_API_KEY", "changeme")
    uvicorn.run(create_app(api_key=api_key), host=HOST, port=PORT)


if __name__ == "__main__":
    main()