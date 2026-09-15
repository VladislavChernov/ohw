from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from graphrag_proto.ingestion_service.app import create_app
from graphrag_proto.ingestion_service.document import Document
from graphrag_proto.ingestion_service.storage.registry import DocumentRegistry

API_KEY = "changeme"


class _AuthedClient(TestClient):
    """TestClient, подставляющий X-API-Key по умолчанию (запросы без ключа — в auth-тестах)."""

    def request(self, method, url, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("X-API-Key", API_KEY)
        return super().request(method, url, headers=headers, **kwargs)


def _doc(domain: str, source_url: str, content_hash: str) -> Document:
    return Document(
        source_id=source_url,
        source_url=source_url,
        domain=domain,
        doc_type="txt",
        content_hash=content_hash,
    )


def test_data_revision_empty_domain_is_none(tmp_path: Path) -> None:
    registry = DocumentRegistry(tmp_path / "r.db")
    assert registry.data_revision("it") is None
    assert registry.data_revision_updated_at("it") is None


def test_data_revision_changes_with_set_and_is_order_independent(tmp_path: Path) -> None:
    registry = DocumentRegistry(tmp_path / "r.db")
    registry.upsert(_doc("it", "src://a.txt", "hash-a"))
    registry.upsert(_doc("it", "src://b.txt", "hash-b"))
    rev = registry.data_revision("it")
    assert rev is not None
    assert len(rev) == 64

    registry2 = DocumentRegistry(tmp_path / "r2.db")
    registry2.upsert(_doc("it", "src://b.txt", "hash-b"))
    registry2.upsert(_doc("it", "src://a.txt", "hash-a"))
    assert registry2.data_revision("it") == rev


def test_data_revision_noop_keep_and_delete_changes(tmp_path: Path) -> None:
    registry = DocumentRegistry(tmp_path / "r.db")
    registry.upsert(_doc("it", "src://a.txt", "hash-a"))
    registry.upsert(_doc("it", "src://b.txt", "hash-b"))
    before = registry.data_revision("it")

    # но no-op (тот же content_hash) — не меняет ревизию (ADR-026)
    registry.upsert(_doc("it", "src://b.txt", "hash-b"))
    assert registry.data_revision("it") == before

    # добавление третьей книги меняет
    registry.upsert(_doc("it", "src://c.txt", "hash-c"))
    assert registry.data_revision("it") != before

    # soft-delete меняет честно
    registry.soft_delete("it", "src://c.txt")
    assert registry.data_revision("it") == before


def test_data_revision_isolated_per_domain(tmp_path: Path) -> None:
    registry = DocumentRegistry(tmp_path / "r.db")
    registry.upsert(_doc("it", "src://a.txt", "hash-a"))
    rev_it = registry.data_revision("it")
    registry.upsert(_doc("legal", "src://x.txt", "hash-x"))
    assert registry.data_revision("it") == rev_it


def test_endpoint_revision_success(tmp_path: Path) -> None:
    registry = DocumentRegistry(tmp_path / "i.db")
    registry.upsert(_doc("it", "src://a.txt", "hash-a"))
    registry.upsert(_doc("it", "src://b.txt", "hash-b"))
    app = create_app(upload_dir=tmp_path / "u", db_path=tmp_path / "i.db")
    with _AuthedClient(app) as client:
        resp = client.get("/api/v1/ingestion/revision?domain=it")
        assert resp.status_code == 200
        body = resp.json()
        assert body["revision"] == registry.data_revision("it")
        assert body["updated_at"] is not None
        assert body["revision"] is not None


def test_endpoint_revision_empty_domain_none(tmp_path: Path) -> None:
    app = create_app(upload_dir=tmp_path / "u", db_path=tmp_path / "i.db")
    with _AuthedClient(app) as client:
        resp = client.get("/api/v1/ingestion/revision?domain=legal")
        assert resp.status_code == 200
        assert resp.json() == {"revision": None, "updated_at": None}


def test_endpoint_revision_401_without_key(tmp_path: Path) -> None:
    app = create_app(upload_dir=tmp_path / "u", db_path=tmp_path / "i.db")
    with TestClient(app) as client:
        resp = client.get("/api/v1/ingestion/revision?domain=it")
        assert resp.status_code == 401


def test_endpoint_revision_422_without_domain(tmp_path: Path) -> None:
    app = create_app(upload_dir=tmp_path / "u", db_path=tmp_path / "i.db")
    with _AuthedClient(app) as client:
        resp = client.get("/api/v1/ingestion/revision")
        assert resp.status_code == 422