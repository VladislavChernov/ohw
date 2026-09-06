from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from graphrag_proto.glossary_service.app import create_app
from graphrag_proto.glossary_service.glossary import Glossary, GlossaryError, load_glossary

VALID_GLOSSARY = {
    "terms": [
        {"canonical_name": "big_o", "aliases": ["Big-O", "big o"]},
        {"canonical_name": "b-tree", "aliases": ["BTree", "b tree"]},
    ],
    "data_types": [{"canonical_name": "integer", "aliases": ["int"]}],
    "complexity_aliases": [{"canonical_name": "o(n)", "aliases": ["O(N)"]}],
    "function_synonyms": {"log": ["ln", "lg"], "sqrt": ["√"]},
    "unicode_map": {"²": "2", "√": "sqrt"},
}

DUP_GLOSSARY = {
    "terms": [
        {"canonical_name": "a", "aliases": ["x"]},
        {"canonical_name": "b", "aliases": ["x"]},
    ],
    "data_types": [],
    "complexity_aliases": [],
}


def make_glossary_dir(tmp_path: Path) -> Path:
    profiles = tmp_path / "domain_profiles"
    profiles.mkdir()
    (profiles / "glossary.it.yaml").write_text(yaml.safe_dump(VALID_GLOSSARY), encoding="utf-8")
    (profiles / "glossary.lib.yaml").write_text(yaml.safe_dump(DUP_GLOSSARY), encoding="utf-8")
    return profiles


def test_get_glossary(tmp_path: Path) -> None:
    app = create_app(profiles_dir=make_glossary_dir(tmp_path), config_url=None)
    with TestClient(app) as client:
        resp = client.get("/api/v1/glossary/it")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["terms"]) == 2
    assert data["terms"][0]["canonical_name"] == "big_o"


def test_get_glossary_unknown_404(tmp_path: Path) -> None:
    app = create_app(profiles_dir=make_glossary_dir(tmp_path), config_url=None)
    with TestClient(app) as client:
        resp = client.get("/api/v1/glossary/cinema")
    assert resp.status_code == 404


def test_resolve_default_domain(tmp_path: Path) -> None:
    app = create_app(profiles_dir=make_glossary_dir(tmp_path), config_url=None)
    with TestClient(app) as client:
        resp = client.post("/api/v1/glossary/resolve", json={"term": "BTree"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["canonical_name"] == "b-tree"
    assert set(body["variants"]) == {"b-tree", "BTree", "b tree"}


def test_resolve_explicit_domain_in_payload(tmp_path: Path) -> None:
    app = create_app(profiles_dir=make_glossary_dir(tmp_path), config_url=None)
    with TestClient(app) as client:
        resp = client.post("/api/v1/glossary/resolve", json={"term": "zzz", "domain": "lib"})
    assert resp.status_code == 200
    assert resp.json()["canonical_name"] is None


def test_resolve_explicit_domain_variants(tmp_path: Path) -> None:
    app = create_app(profiles_dir=make_glossary_dir(tmp_path), config_url=None)
    with TestClient(app) as client:
        resp = client.post("/api/v1/glossary/resolve", json={"term": "x", "domain": "lib"})
    assert resp.status_code == 200
    assert resp.json()["canonical_name"] in ("a", "b")  # x дублируется -> последний выигрывает


def test_resolve_case_insensitive(tmp_path: Path) -> None:
    app = create_app(profiles_dir=make_glossary_dir(tmp_path), config_url=None)
    with TestClient(app) as client:
        resp = client.post("/api/v1/glossary/resolve", json={"term": "BIG-O"})
    body = resp.json()
    assert body["canonical_name"] == "big_o"


def test_resolve_not_found(tmp_path: Path) -> None:
    app = create_app(profiles_dir=make_glossary_dir(tmp_path), config_url=None)
    with TestClient(app) as client:
        resp = client.post("/api/v1/glossary/resolve", json={"term": "zzz"})
    assert resp.status_code == 200
    assert resp.json()["canonical_name"] is None


def test_resolve_empty_term_422(tmp_path: Path) -> None:
    app = create_app(profiles_dir=make_glossary_dir(tmp_path), config_url=None)
    with TestClient(app) as client:
        resp = client.post("/api/v1/glossary/resolve", json={"term": ""})
    assert resp.status_code == 422


def test_resolve_unknown_domain_404(tmp_path: Path) -> None:
    app = create_app(profiles_dir=make_glossary_dir(tmp_path), config_url=None)
    with TestClient(app) as client:
        resp = client.post("/api/v1/glossary/resolve", json={"term": "x", "domain": "nope"})
    assert resp.status_code == 404


def test_validate_duplicates_reported(tmp_path: Path) -> None:
    app = create_app(profiles_dir=make_glossary_dir(tmp_path), config_url=None)
    with TestClient(app) as client:
        resp = client.post("/api/v1/glossary/validate", json={"domain": "lib"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert any(d["tag"] == "x" for d in body["duplicates"])


def test_validate_clean_default_domain(tmp_path: Path) -> None:
    app = create_app(profiles_dir=make_glossary_dir(tmp_path), config_url=None)
    with TestClient(app) as client:
        resp = client.post("/api/v1/glossary/validate")
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


def test_glossary_class_resolve_and_unicode() -> None:
    gl = Glossary(VALID_GLOSSARY)
    assert gl.resolve("ln") == "log"
    assert gl.resolve("√") == "sqrt"  # "√" — синоним функции sqrt
    assert gl.unicode_map()["²"] == "2"


def test_load_glossary_missing_raises(tmp_path: Path) -> None:
    path = tmp_path / "glossary.nope.yaml"
    with pytest.raises(GlossaryError) as exc:
        load_glossary(path)
    assert "не найден" in str(exc.value)


def test_glossary_malformed_yaml_returns_404(tmp_path: Path) -> None:
    profiles = tmp_path / "domain_profiles"
    profiles.mkdir()
    (profiles / "glossary.bad.yaml").write_text("[::not-dict::", encoding="utf-8")
    app = create_app(profiles_dir=profiles, config_url=None)
    with TestClient(app) as client:
        resp = client.get("/api/v1/glossary/bad")
    assert resp.status_code == 404