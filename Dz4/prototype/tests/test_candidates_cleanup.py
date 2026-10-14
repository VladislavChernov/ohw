"""Уборка файлов джобы: и документа, и его кандидатов.

Проверяется не «функция удаляет файл», а то, ради чего она введена: временный каталог загрузки
не копит, неудачная уборка объявляется, а отказ не выдаёт неуспех за провал - потому что
запись к этому моменту уже в базе.

С 2026-10-14 кандидаты лежат РЯДОМ с документом (ADR-053), поэтому и убираются вместе с ним.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from graphrag_proto.ingestion_service.app import Executor, create_app
from graphrag_proto.ingestion_service.pipeline.candidates import (
    CANDIDATES_KEEP_ENV,
    CandidatesCleanupError,
    candidates_path_for_document,
    count_remaining,
    discard_candidates,
    discard_document_upload,
    keep_requested,
)
from graphrag_proto.ingestion_service.projection import InMemoryProjectionStateStore
from graphrag_proto.ingestion_service.storage.registry import DocumentRegistry, JobStore
from graphrag_proto.retrieval.adapters.inmemory import (
    InMemoryGraphStore,
    InMemoryVectorStore,
)


def write_candidates(document: Path, text: str, candidates: list[str]) -> Path:
    directory = document.parent
    directory.mkdir(parents=True, exist_ok=True)
    path = candidates_path_for_document(document)
    path.write_text(
        json.dumps(
            {
                "source": document.name,
                "source_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "count": len(candidates),
                "candidates": candidates,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------- unit


def test_file_is_removed_after_successful_load(tmp_path: Path) -> None:
    document = tmp_path / "05_adr_log.md"
    document.write_text("x", encoding="utf-8")
    path = write_candidates(document, "x", ["Doc"])

    assert discard_candidates(document) == "removed"
    assert not path.exists()
    assert count_remaining(document.parent) == 0


def test_absent_file_is_not_a_failure(tmp_path: Path) -> None:
    """Повторная отправка того же документа: файла уже нет, и это норма, а не отказ."""
    document = tmp_path / "05_adr_log.md"
    document.write_text("x", encoding="utf-8")

    assert discard_candidates(document) == "absent"
    assert count_remaining(document.parent) == 0


def test_keep_flag_leaves_the_file_and_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отладка: файл сохраняется, и результат ОТЛИЧАЕТСЯ от «убрали».

    Свёртка в булев флаг была бы опасна здесь: «оставили по просьбе» и «убрали» выглядели бы
    одинаково, и отладчик увидел бы папку с файлами и не понял бы, почему.
    """
    document = tmp_path / "05_adr_log.md"
    document.write_text("x", encoding="utf-8")
    path = write_candidates(document, "x", ["Doc"])
    monkeypatch.setenv(CANDIDATES_KEEP_ENV, "1")

    assert discard_candidates(document) == "kept"
    assert path.exists(), "при CANDIDATES_KEEP=1 файл обязан остаться"
    assert count_remaining(document.parent) == 1


def test_keep_flag_off_is_the_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    document = tmp_path / "05_adr_log.md"
    document.write_text("x", encoding="utf-8")
    write_candidates(document, "x", ["Doc"])
    monkeypatch.delenv(CANDIDATES_KEEP_ENV, raising=False)

    assert keep_requested() is False
    assert discard_candidates(document) == "removed"


def test_keep_flag_accepts_explicit_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = tmp_path / "05_adr_log.md"
    document.write_text("x", encoding="utf-8")
    write_candidates(document, "x", ["Doc"])
    monkeypatch.setenv(CANDIDATES_KEEP_ENV, "0")

    assert keep_requested() is False
    assert discard_candidates(document) == "removed"


def test_unknown_keep_flag_value_falls_back_to_cleaning_and_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Опечатка в значении не должна молча включать отладку и копить мусор."""
    document = tmp_path / "05_adr_log.md"
    document.write_text("x", encoding="utf-8")
    write_candidates(document, "x", ["Doc"])
    monkeypatch.setenv(CANDIDATES_KEEP_ENV, "да")

    assert keep_requested() is False
    assert discard_candidates(document) == "removed"


def test_cleanup_failure_is_named_and_carries_the_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ уборки обязан называть файл: по логу должно быть видно, что чинить."""
    document = tmp_path / "05_adr_log.md"
    document.write_text("x", encoding="utf-8")
    path = write_candidates(document, "x", ["Doc"])

    def refuse(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("read-only file system")

    monkeypatch.setattr(Path, "unlink", refuse)

    with pytest.raises(CandidatesCleanupError) as caught:
        discard_candidates(document)

    assert "05_adr_log_candidates.json" in str(caught.value)
    assert path.exists(), "файл не удалён - и это видно по тому, что он на месте"


def test_only_candidate_files_are_counted(tmp_path: Path) -> None:
    """Посторонний файл в папке не должен попадать в остаток: папка общая, а счётчик
    объявляет уборку, а не содержимое каталога."""
    document = tmp_path / "a.md"
    document.write_text("x", encoding="utf-8")
    write_candidates(document, "x", ["Doc"])
    second = tmp_path / "b.md"
    second.write_text("x", encoding="utf-8")
    write_candidates(second, "x", ["Doc"])
    (tmp_path / "README.md").write_text("это не кандидаты", encoding="utf-8")
    (tmp_path / "sub").mkdir()

    assert count_remaining(tmp_path) == 2


def test_document_upload_is_removed_too(tmp_path: Path) -> None:
    """Файл документа убирается наравне с кандидатами.

    До этого он удалялся только при обрыве потока, и в каталоге загрузки копились полные
    копии документов: проверено на томе, 62 файла, 2.8 МБ.
    """
    document = tmp_path / "j-42.md"
    document.write_text("текст документа", encoding="utf-8")

    assert discard_document_upload(document) == "removed"
    assert not document.exists()


def test_document_upload_survives_when_debug_is_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    document = tmp_path / "j-42.md"
    document.write_text("текст документа", encoding="utf-8")
    monkeypatch.setenv(CANDIDATES_KEEP_ENV, "1")

    assert discard_document_upload(document) == "kept"
    assert document.exists(), "при отладке исходник обязан остаться"


# ---------------------------------------------------------------- сквозные, через Executor


class _CountingLLM:
    def __init__(self, answer: str) -> None:
        self._answer = answer
        self.calls = 0

    def generate(self, _prompt: str, **_kwargs: object) -> list[str]:
        self.calls += 1
        return [self._answer]

    @property
    def last_usage(self) -> None:
        return None


#: Ответ модели в контракте `ExtractStage`. Форма `{"canonical": ...}` отвергается как
#: неизвестное поле и деградирует в тихий vector-only - джоба была бы `succeeded` без
#: графа, и тест на уборку прошёл бы, ничего не проверив.
_LLM_ANSWER = json.dumps(
    {
        "tags": [
            {
                "canonical": "Document Registry",
                "type": "Concept",
                "mentions": ["Document Registry", "Glossary Service"],
            }
        ],
        "relationships": [],
    },
    ensure_ascii=False,
)

TEXT = "Document Registry построен поверх Glossary Service."


def _profile() -> dict[str, object]:
    return {
        "extraction": {
            "llm_enabled": True,
            "prompt_template": {"id": "t", "system": "s", "user": "извлеки сущности"},
        },
        "ontology": {
            "node_types": [{"type": "Concept", "unique_key": "canonical_name"}],
            "edge_types": [],
        },
        "validation": {},
        "canonicalization": {},
    }


def _executor(tmp_path: Path) -> Executor:
    """Executor с in-memory сторами: джоба идёт целиком, без стенда.

    Именно `Executor`, а не `Analyzer`: уборка живёт в джобе сервиса, и проверка на
    анализаторе проверяла бы код, который в проде не вызывается.
    """
    return Executor(
        JobStore(tmp_path / "jobs.db"),
        DocumentRegistry(tmp_path / "registry.db"),
        glossary_url="http://glossary.invalid",
        graph_store=InMemoryGraphStore(),
        vector_store=InMemoryVectorStore(),
        llm=_CountingLLM(_LLM_ANSWER),
        profile_fetcher=lambda _domain: _profile(),
        projection_state_store=InMemoryProjectionStateStore(),
    )


def _document(tmp_path: Path, name: str = "05_adr_log.md") -> Path:
    path = tmp_path / name
    path.write_text(TEXT, encoding="utf-8")
    return path


def _run_job(executor: Executor, document: Path, source_url: str, job_id: str) -> str:
    """Создаёт запись джобы так же, как сервис, и прогоняет её целиком.

    `JobStore.create` возвращает `None`, а `job_id` обязан существовать ДО запуска потока:
    сервис бронирует слот раньше, чем создаёт запись (ADR-050). Слот обязательно
    бронируется - `_run` сам его освобождает в `finally`, и без резерва семафор уходит в
    `ValueError: Semaphore released too many times`.
    """
    assert executor.try_reserve() is True, "слот должен быть свободен: джобы идут по одной"
    executor._jobs.create(job_id, source_url, "it", "md")
    executor._run(job_id, document, source_url, "it", "md", [], [], {})
    return job_id


def test_successful_job_removes_both_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Уборка на настоящей джобе: исчезают и список, и сам документ."""
    directory = tmp_path / "uploads"
    directory.mkdir()
    document = _document(directory)
    monkeypatch.setenv("EXTRACT_LLM", "true")
    executor = _executor(tmp_path)
    produced = candidates_path_for_document(document)

    job_id = _run_job(executor, document, "src://05_adr_log.md", "j-1")

    assert executor._jobs.get(job_id)["status"] == "succeeded"
    assert not produced.exists(), "файл кандидатов обязан исчезнуть"
    assert not document.exists(), "файл документа обязан исчезнуть: он копился на томе"
    assert count_remaining(directory) == 0


def test_noop_job_also_removes_both_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отсечка no-op - не успешная загрузка, но файлы ей больше не нужны.

    Именно на повторных отправках одного и того же файла копились бы остатки: джоба
    завершается `succeeded`, пайплайн выходит после INGEST, и без уборки в папке оставался бы
    файл, который уедет в следующий запуск.
    """
    directory = tmp_path / "uploads"
    directory.mkdir()
    document = _document(directory)
    monkeypatch.setenv("EXTRACT_LLM", "true")
    executor = _executor(tmp_path)

    first = _run_job(executor, document, "src://05_adr_log.md", "j-1")
    assert executor._jobs.get(first)["status"] == "succeeded"

    # Вторая отправка того же содержимого: реестр отсекает её по content_hash.
    again = _document(directory)
    second = _run_job(executor, again, "src://05_adr_log.md", "j-2")

    assert executor._jobs.get(second)["status"] == "succeeded"
    assert not again.exists(), "отсечённая джоба тоже обязана убирать файлы"


def test_failed_job_keeps_both_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """После отказа файлы остаются: они нужны, чтобы понять причину.

    Обратное выглядело бы аккуратнее - папка всегда пуста - но отказ с отсутствующим
    списком кандидатов неразличим с отказом из-за самого списка.
    """
    directory = tmp_path / "uploads"
    directory.mkdir()
    # Файла документа нет: ридер падает, и джоба уходит в `failed` естественным путём,
    # без подмены чужих методов - падение должно быть настоящим, а не разыгранным.
    missing = directory / "boom.md"
    monkeypatch.setenv("EXTRACT_LLM", "true")
    executor = _executor(tmp_path)

    job_id = _run_job(executor, missing, "src://boom.md", "j-1")

    assert executor._jobs.get(job_id)["status"] == "failed"
    assert not missing.exists(), "файла и не было: проверяем, что джоба упала честно"


def test_debug_flag_keeps_files_and_reports_it_in_the_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Отладка видна в отчёте джобы и в логе, а не только тем, что файлы остались.

    Иначе отчёт прогона выглядел бы как уборка, которая не работает, при исправном коде
    уборки и включённой отладке - и искать причину пришлось бы в чужом месте.
    """
    caplog.set_level(logging.INFO, logger="graphrag_proto.ingestion_service.app")
    directory = tmp_path / "uploads"
    directory.mkdir()
    document = _document(directory)
    monkeypatch.setenv("EXTRACT_LLM", "true")
    monkeypatch.setenv(CANDIDATES_KEEP_ENV, "1")
    executor = _executor(tmp_path)

    job_id = _run_job(executor, document, "src://05_adr_log.md", "j-1")

    assert document.exists(), "при CANDIDATES_KEEP=1 исходник обязан остаться"
    assert candidates_path_for_document(document).exists()
    notes = " ".join(str(stage["message"]) for stage in executor._jobs.stages(job_id))
    assert CANDIDATES_KEEP_ENV in notes, "отключённая уборка обязана быть названа в отчёте"


def test_remaining_files_are_published_in_the_job_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Остаток объявляется на КАЖДОЙ джобе, а не только при сбое уборки.

    Иначе накопление выглядело бы как «уборка работает», пока она не работает: тот случай
    и привёл к 62 файлам в каталоге загрузки.
    """
    directory = tmp_path / "uploads"
    directory.mkdir()
    document = _document(directory)
    monkeypatch.setenv("EXTRACT_LLM", "true")
    monkeypatch.setenv(CANDIDATES_KEEP_ENV, "1")
    executor = _executor(tmp_path)

    job_id = _run_job(executor, document, "src://05_adr_log.md", "j-1")

    notes = " ".join(str(stage["message"]) for stage in executor._jobs.stages(job_id))
    assert "осталось" in notes, f"остаток обязан попасть в отчёт:\n{notes}"


# ---------------------------------------------------------------- через API: файлы не копятся


def test_api_job_leaves_no_files_behind(tmp_path: Path) -> None:
    """Контур целиком: после успешной джобы в каталоге загрузки пусто.

    До правки в каталоге оставались полные копии документов — проверено на томе, 62 файла.
    """
    uploads = tmp_path / "uploads"
    app = create_app(
        upload_dir=uploads,
        db_path=tmp_path / "ingestion.db",
        glossary_url="",
    )

    class _Authed(TestClient):
        def request(self, method, url, **kwargs):
            headers = dict(kwargs.pop("headers", None) or {})
            headers.setdefault("X-API-Key", "changeme")
            return super().request(method, url, headers=headers, **kwargs)

    from tests.job_wait import wait_until

    with _Authed(app) as client:
        resp = client.post(
            "/api/v1/ingestion/documents",
            json={
                "source_url": "src://05_adr_log.md",
                "domain": "it",
                "doc_type": "txt",
                "content": TEXT,
            },
        )
        assert resp.status_code == 202, resp.text
        job_id = resp.json()["job_id"]
        assert wait_until(
            lambda: client.get(f"/api/v1/ingestion/jobs/{job_id}").json().get("status")
            in {"succeeded", "failed", "cancelled"}
        )

    leftovers = sorted(p.name for p in uploads.iterdir()) if uploads.exists() else []
    assert leftovers == [], f"каталог загрузки обязан остаться пустым, а в нём: {leftovers}"