from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException

from graphrag_proto.glossary_service.glossary import Glossary, GlossaryError, load_glossary

HOST = "0.0.0.0"
PORT = 8003

FALLBACK_DOMAIN = "it"


def _profiles_dir() -> Path:
    env = os.environ.get("DOMAIN_PROFILES_DIR")
    return Path(env) if env else Path("domain_profiles")


def _config_url() -> str | None:
    env = os.environ.get("CONFIG_URL")
    return env.rstrip("/") if env else None


def _is_safe_domain(name: str) -> bool:
    return bool(name) and not ("/" in name or "\\" in name or name.startswith("."))


def _fetch_active_domain(config_url: str) -> str | None:
    try:
        with urllib.request.urlopen(f"{config_url}/api/v1/config/domain/active", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        domain = data.get("domain")
        return domain if isinstance(domain, str) and domain else None
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None


def create_app(profiles_dir: Path | None = None, config_url: str | None = None) -> FastAPI:
    profiles_dir = profiles_dir or _profiles_dir()
    config_url = config_url or _config_url()

    app = FastAPI(title="GraphRAG Glossary Service", version="0.1.0")

    def active_domain() -> str:
        if config_url:
            remote = _fetch_active_domain(config_url)
            if remote and _is_safe_domain(remote):
                return remote
        return FALLBACK_DOMAIN

    def resolve_domain(payload: dict[str, Any]) -> str:
        domain = payload.get("domain")
        if isinstance(domain, str) and domain:
            if not _is_safe_domain(domain):
                raise HTTPException(status_code=404, detail="словарь не найден")
            return domain
        return active_domain()

    def glossary_for(domain: str) -> Glossary:
        if not _is_safe_domain(domain):
            raise HTTPException(status_code=404, detail="словарь не найден")
        path = profiles_dir / f"glossary.{domain}.yaml"
        try:
            return load_glossary(path)
        except GlossaryError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/glossary/{domain}")
    def get_glossary(domain: str) -> dict[str, Any]:
        return glossary_for(domain).raw_dict()

    @app.post("/api/v1/glossary/resolve")
    def resolve(payload: dict[str, Any]) -> dict[str, Any]:
        term = payload.get("term")
        if not isinstance(term, str) or not term:
            raise HTTPException(status_code=422, detail="поле 'term' обязательно")
        canonical, variants = glossary_for(resolve_domain(payload)).resolve_with_variants(term)
        return {"canonical_name": canonical, "variants": variants}

    @app.post("/api/v1/glossary/validate")
    def validate(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload or {}
        duplicates = glossary_for(resolve_domain(body)).duplicates()
        return {"valid": not duplicates, "duplicates": duplicates}

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host=HOST, port=PORT)


if __name__ == "__main__":
    main()