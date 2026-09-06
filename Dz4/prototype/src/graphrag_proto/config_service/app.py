from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from graphrag_proto.config_service.domain import (
    DomainProfileError,
    domain_from_filename,
    domain_from_profile,
    load_profile_yaml,
    validate_profile,
)
from graphrag_proto.config_service.namespaces import default_active_profile, load_namespaces
from graphrag_proto.config_service.store import ConfigStore

HOST = "0.0.0.0"
PORT = 8001


def _profiles_dir() -> Path:
    env = os.environ.get("DOMAIN_PROFILES_DIR")
    return Path(env) if env else Path("domain_profiles")


def _db_path() -> Path:
    env = os.environ.get("CONFIG_DB_PATH")
    return Path(env) if env else Path("config.db")


def _namespaces_path() -> Path:
    env = os.environ.get("NAMESPACES_PATH")
    return Path(env) if env else Path("infra/config/namespaces.yaml")


def _is_safe_domain(name: str) -> bool:
    return bool(name) and not ("/" in name or "\\" in name or name.startswith("."))


def create_app(
    profiles_dir: Path | None = None,
    db_path: Path | None = None,
    namespaces_path: Path | None = None,
    default_profile: str | None = None,
) -> FastAPI:
    profiles_dir = profiles_dir or _profiles_dir()
    db_path = db_path or _db_path()
    namespaces_path = namespaces_path or _namespaces_path()
    namespaces = load_namespaces(namespaces_path)
    default_profile = default_profile or default_active_profile(namespaces, "it")
    store = ConfigStore(db_path)

    app = FastAPI(title="GraphRAG Config Service", version="0.1.0")

    def listed_domains() -> list[str]:
        domains: list[str] = []
        for path in profiles_dir.glob("domain_profile.*.yaml"):
            domain = domain_from_filename(path)
            if domain is not None:
                domains.append(domain)
        return sorted(set(domains))

    def load_profile(domain: str) -> dict[str, Any]:
        path = profiles_dir / f"domain_profile.{domain}.yaml"
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"профиль '{domain}' не найден")
        try:
            return load_profile_yaml(path)
        except DomainProfileError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/config/domain/active")
    def active() -> dict[str, str]:
        return {"domain": store.get_active_profile(default_profile)}

    @app.get("/api/v1/config/domain/profiles")
    def profiles() -> dict[str, list[str]]:
        return {"domains": listed_domains()}

    @app.get("/api/v1/config/domain/profile/{name}")
    def profile(name: str) -> dict[str, Any]:
        if not _is_safe_domain(name):
            raise HTTPException(status_code=404, detail=f"профиль '{name}' не найден")
        return load_profile(name)

    @app.post("/api/v1/config/domain/validate")
    def validate(body: str = Body(..., media_type="application/yaml")) -> dict[str, Any]:
        try:
            data = yaml.safe_load(body)
        except yaml.YAMLError as exc:
            raise HTTPException(status_code=400, detail=f"невалидный YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise HTTPException(status_code=400, detail="профиль должен быть YAML-маппингом")
        errors = validate_profile(data)
        return {"valid": not errors, "errors": errors}

    @app.post("/api/v1/config/domain/activate")
    def activate(payload: dict[str, str]) -> JSONResponse:
        domain = payload.get("domain")
        if not domain:
            raise HTTPException(status_code=422, detail="поле 'domain' обязательно")
        if not _is_safe_domain(domain):
            raise HTTPException(status_code=404, detail=f"профиль '{domain}' не найден")
        profile_data = load_profile(domain)
        profile_name = domain_from_profile(profile_data, default="")
        if profile_name and profile_name != domain:
            raise HTTPException(
                status_code=422,
                detail=f"profile.name='{profile_name}' не совпадает с доменом '{domain}'",
            )
        store.set_active_profile(domain)
        return JSONResponse({"domain": domain, "activated": True})

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host=HOST, port=PORT)


if __name__ == "__main__":
    main()