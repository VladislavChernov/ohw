"""Эндпоинт «загружен ли документ в текущем виде?» — проверка до стадии pre-ingest.

Главное проверяемое свойство здесь не «маршрут отвечает», а то, что `content_hash`
эндпоинта совпадает с тем, который записал реестр. Расхождение выглядело бы как
«документа нет», и отсечка молча перестала бы работать.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from graphrag_proto.ingestion_service.app import create_app

API_KEY = "changeme"
TEXT = "Document Registry построен поверх Glossary Service."
STATUS_URL = "/api/v1/ingestion/documents/status"


class _AuthedClient(TestClient):
    def request(self, method, url, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("X-API-Key", API_KEY)
        return super().request(method, url, headers=headers, **kwargs)


def _app(tmp_path: Path):
    return create_app(
        upload_dir=tmp_path / "uploads",
        db_path=tmp_path / "ingestion.db",
        glossary_url="",
    )


def _load(client: _AuthedClient, source_url: str, content: str = TEXT) -> str:
    """Кладёт документ и дожидается терминального состояния."""
    resp = client.post(
        "/api/v1/ingestion/documents",
        json={"source_url": source_url, "domain": "it", "doc_type": "txt", "content": content},
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    from tests.job_wait import wait_until as _wait

    assert _wait(
        lambda: client.get(f"/api/v1/ingestion/jobs/{job_id}").json().get("status")
        in {"succeeded", "failed"}
    )
    return job_id


def test_unknown_document_is_reported_as_unknown(tmp_path: Path) -> None:
    with _AuthedClient(_app(tmp_path)) as client:
        resp = client.post(
            STATUS_URL,
            json={
                "source_url": "src://нет-такого.txt",
                "domain": "it",
                "doc_type": "txt",
                "content": TEXT,
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["known"] is False
    assert body["unchanged"] is False
    assert body["version"] is None
    assert body["doc_id"] is None


def test_loaded_document_is_reported_as_unchanged(tmp_path: Path) -> None:
    """Главный сценарий: повторная отправка того же содержимого должна быть отсечена ДО
    запуска стадии pre-ingest, а не после неё."""
    with _AuthedClient(_app(tmp_path)) as client:
        _load(client, "src://algo.txt")

        resp = client.post(
            STATUS_URL,
            json={
                "source_url": "src://algo.txt",
                "domain": "it",
                "doc_type": "txt",
                "content": TEXT,
            },
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["known"] is True
    assert body["unchanged"] is True, (
        "тот же текст обязан узнаваться как неизменившийся: иначе отсечка не работает"
    )
    assert body["version"] == 1


def test_changed_content_is_reported_as_changed(tmp_path: Path) -> None:
    """Документ изменился - грузить надо, даже если source_url тот же."""
    with _AuthedClient(_app(tmp_path)) as client:
        _load(client, "src://algo.txt")

        resp = client.post(
            STATUS_URL,
            json={
                "source_url": "src://algo.txt",
                "domain": "it",
                "doc_type": "txt",
                "content": "Совершенно другой текст про сложность.",
            },
        )

    body = resp.json()
    assert body["known"] is True
    assert body["unchanged"] is False


def test_status_hash_equals_the_hash_the_registry_stored(tmp_path: Path) -> None:
    """Контракт свежести проверяется сравнением, а не формой ответа.

    Смысл: если эндпоинт считает хэш иначе, чем реестр, `unchanged` всегда будет `false`
    и отсечка не сработает ни разу - молча, без ошибок и без следов.
    """
    with _AuthedClient(_app(tmp_path)) as client:
        _load(client, "src://algo.txt")
        body = client.post(
            STATUS_URL,
            json={
                "source_url": "src://algo.txt",
                "domain": "it",
                "doc_type": "txt",
                "content": TEXT,
            },
        ).json()

        assert body["content_hash"].startswith("sha256:")
        assert body["unchanged"] is True, (
            "один и тот же код считает хэш для эндпоинта и для реестра; "
            "если здесь False - расхождение в разбиении или нормализации"
        )


def test_same_text_under_different_source_url_is_not_unchanged(tmp_path: Path) -> None:
    """Отсечка по `source_url`, а не по содержимому: тот же текст под другим именем -
    это другой документ и грузить его надо."""
    with _AuthedClient(_app(tmp_path)) as client:
        _load(client, "src://algo.txt")

        body = client.post(
            STATUS_URL,
            json={
                "source_url": "src://другой.txt",
                "domain": "it",
                "doc_type": "txt",
                "content": TEXT,
            },
        ).json()

    assert body["known"] is False
    assert body["unchanged"] is False


def test_status_requires_the_api_key(tmp_path: Path) -> None:
    """Маршрут отвечает по содержимому документа, то есть по существу читает данные.
    Без ключа он закрыт - как и остальные маршруты сервиса."""
    with TestClient(_app(tmp_path)) as client:
        resp = client.post(
            STATUS_URL,
            json={
                "source_url": "src://algo.txt",
                "domain": "it",
                "doc_type": "txt",
                "content": TEXT,
            },
        )

    assert resp.status_code in {401, 403}


def test_missing_fields_are_named(tmp_path: Path) -> None:
    """Отказ называет, чего не хватает: «неизвестно почему» - это молчание."""
    with _AuthedClient(_app(tmp_path)) as client:
        resp = client.post(STATUS_URL, json={"source_url": "src://algo.txt"})

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "domain" in detail
    assert "doc_type" in detail
    assert "content" in detail


def test_unknown_doc_type_is_refused_by_name(tmp_path: Path) -> None:
    with _AuthedClient(_app(tmp_path)) as client:
        resp = client.post(
            STATUS_URL,
            json={
                "source_url": "src://algo.bin",
                "domain": "it",
                "doc_type": "bin",
                "content": TEXT,
            },
        )

    assert resp.status_code == 422, resp.text
    assert "bin" in resp.json()["detail"]


def test_status_leaves_no_file_behind(tmp_path: Path) -> None:
    """Маршрут не грузит документ, значит и не должен оставлять его на диске: каталог
    загрузок читается только во время чтения."""
    app = _app(tmp_path)
    with _AuthedClient(app) as client:
        client.post(
            STATUS_URL,
            json={
                "source_url": "src://algo.txt",
                "domain": "it",
                "doc_type": "txt",
                "content": TEXT,
            },
        )

    uploads = tmp_path / "uploads"
    leftovers = sorted(p.name for p in uploads.glob("status-*")) if uploads.exists() else []
    assert leftovers == [], f"черновики не убраны: {leftovers}"