from __future__ import annotations

import copy
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from graphrag_proto.config_service.app import create_app
from graphrag_proto.config_service.domain import load_profile_yaml, validate_profile

MINIMAL_PROFILE = {
    "profile": {"name": "it", "description": "тест", "language": "ru", "version": "1"},
    "ontology": {"node_types": [], "edge_types": []},
    "extraction": {"prompt_template": {"id": "x"}, "temperature": 0.1, "max_tokens": 10},
    "validation": {"rules": []},
    "canonicalization": {"nodes": {}},
    "chunking": {"strategy": "sliding_window", "chunk_size": 1, "overlap": 0},
    "context_assembly": {"template": {"id": "x"}, "max_tokens": 10},
}

VALID_YAML = yaml.safe_dump(MINIMAL_PROFILE)
INVALID_YAML = yaml.safe_dump({"profile": {"name": "bad"}})  # нет обязательных секций


def make_profiles_dir(tmp_path: Path) -> Path:
    profiles = tmp_path / "domain_profiles"
    profiles.mkdir()
    for name in ("it", "library"):
        profile = copy.deepcopy(MINIMAL_PROFILE)
        profile["profile"]["name"] = name
        (profiles / f"domain_profile.{name}.yaml").write_text(
            yaml.safe_dump(profile), encoding="utf-8"
        )
    return profiles


def test_profiles_lists_domains(tmp_path: Path) -> None:
    app = create_app(profiles_dir=make_profiles_dir(tmp_path), db_path=tmp_path / "c.db")
    with TestClient(app) as client:
        resp = client.get("/api/v1/config/domain/profiles")
    assert resp.status_code == 200
    assert resp.json()["domains"] == ["it", "library"]


def test_active_default_profile(tmp_path: Path) -> None:
    app = create_app(profiles_dir=make_profiles_dir(tmp_path), db_path=tmp_path / "c.db")
    with TestClient(app) as client:
        resp = client.get("/api/v1/config/domain/active")
    assert resp.status_code == 200
    assert resp.json() == {"domain": "it"}


def test_validate_valid_profile(tmp_path: Path) -> None:
    app = create_app(profiles_dir=make_profiles_dir(tmp_path), db_path=tmp_path / "c.db")
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/config/domain/validate",
            content=VALID_YAML,
            headers={"Content-Type": "application/yaml"},
        )
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


def test_validate_invalid_profile(tmp_path: Path) -> None:
    app = create_app(profiles_dir=make_profiles_dir(tmp_path), db_path=tmp_path / "c.db")
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/config/domain/validate",
            content=INVALID_YAML,
            headers={"Content-Type": "application/yaml"},
        )
    assert resp.status_code == 200
    assert resp.json()["valid"] is False
    assert any("ontology" in e for e in resp.json()["errors"])


def test_validate_malformed_yaml_400(tmp_path: Path) -> None:
    app = create_app(profiles_dir=make_profiles_dir(tmp_path), db_path=tmp_path / "c.db")
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/config/domain/validate",
            content="[::broken::",
            headers={"Content-Type": "application/yaml"},
        )
    assert resp.status_code == 400


def test_validate_non_mapping_400(tmp_path: Path) -> None:
    app = create_app(profiles_dir=make_profiles_dir(tmp_path), db_path=tmp_path / "c.db")
    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/config/domain/validate",
            content="[1, 2]",
            headers={"Content-Type": "application/yaml"},
        )
    assert resp.status_code == 400


def test_activate_changes_active_profile(tmp_path: Path) -> None:
    app = create_app(profiles_dir=make_profiles_dir(tmp_path), db_path=tmp_path / "c.db")
    with TestClient(app) as client:
        resp = client.post("/api/v1/config/domain/activate", json={"domain": "library"})
        assert resp.status_code == 200
        assert resp.json()["activated"] is True
        active = client.get("/api/v1/config/domain/active")
    assert active.status_code == 200
    assert active.json()["domain"] == "library"


def test_activate_persists_across_app_instances(tmp_path: Path) -> None:
    db_path = tmp_path / "persist.db"
    profiles = make_profiles_dir(tmp_path)
    app1 = create_app(profiles_dir=profiles, db_path=db_path)
    with TestClient(app1) as client:
        assert client.post("/api/v1/config/domain/activate", json={"domain": "library"}).status_code == 200

    app2 = create_app(profiles_dir=profiles, db_path=db_path)
    with TestClient(app2) as client:
        assert client.get("/api/v1/config/domain/active").json()["domain"] == "library"


def test_activate_profile_name_mismatch_422(tmp_path: Path) -> None:
    profiles = tmp_path / "domain_profiles"
    profiles.mkdir()
    profile = copy.deepcopy(MINIMAL_PROFILE)
    profile["profile"]["name"] = "other"
    (profiles / "domain_profile.mismatch.yaml").write_text(
        yaml.safe_dump(profile), encoding="utf-8"
    )
    app = create_app(profiles_dir=profiles, db_path=tmp_path / "c.db")
    with TestClient(app) as client:
        resp = client.post("/api/v1/config/domain/activate", json={"domain": "mismatch"})
    assert resp.status_code == 422


def test_default_profile_from_namespaces(tmp_path: Path) -> None:
    profiles = make_profiles_dir(tmp_path)
    infra = tmp_path / "infra" / "config"
    infra.mkdir(parents=True)
    (infra / "namespaces.yaml").write_text(
        yaml.safe_dump({"domain": {"active_profile": "library"}}), encoding="utf-8"
    )
    app = create_app(profiles_dir=profiles, db_path=tmp_path / "c.db", namespaces_path=infra / "namespaces.yaml")
    with TestClient(app) as client:
        assert client.get("/api/v1/config/domain/active").json()["domain"] == "library"


def test_activate_unknown_profile_404(tmp_path: Path) -> None:
    app = create_app(profiles_dir=make_profiles_dir(tmp_path), db_path=tmp_path / "c.db")
    with TestClient(app) as client:
        resp = client.post("/api/v1/config/domain/activate", json={"domain": "nope"})
    assert resp.status_code == 404


def test_validate_profile_module(tmp_path: Path) -> None:
    path = tmp_path / "p.yaml"
    path.write_text(VALID_YAML, encoding="utf-8")
    data = load_profile_yaml(path)
    assert data["profile"]["name"] == "it"
    assert validate_profile(data) == []