"""L1-01: runtime-активация домена it -> library -> cinema без рестарта.

Проверяет pull-механизм переключения активного домена:
- DomainProfileLoader (query-сервис) на следующем запросе загружает профиль
  НОВОГО активного домена из Config Service (перезапуск процесса не требуется);
- Glossary Service без явного `domain` резолвит термин по активному домену.

Config Service заменяется стаб-сервером с тем же JSON-контрактом
(`GET /api/v1/config/domain/active`, `GET /api/v1/config/domain/profile/{name}`);
сама активация покрыта в tests/test_config.py.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from graphrag_proto.glossary_service.app import create_app as create_glossary_app
from graphrag_proto.retrieval.profile import DomainProfileLoader

IT_PROFILE: dict[str, Any] = {
    "profile": {"name": "it"},
    "ontology": {"node_types": [{"type": "Requirement"}]},
    "context_assembly": {"max_tokens": 4096},
}

LIBRARY_PROFILE: dict[str, Any] = {
    "profile": {"name": "library"},
    "ontology": {"node_types": [{"type": "Author"}]},
    "context_assembly": {"max_tokens": 2048},
}

CINEMA_PROFILE: dict[str, Any] = {
    "profile": {"name": "cinema"},
    "ontology": {"node_types": [{"type": "Film"}]},
    "context_assembly": {"max_tokens": 2048},
}


class _State:
    def __init__(self) -> None:
        self.active = "it"
        self.profiles: dict[str, dict[str, Any]] = {}


class _StubConfig(BaseHTTPRequestHandler):
    """Мини-CONFIG_URL: активный домен + профили по имени (контракт docs/04 §2)."""

    state: _State | None = None

    def _send(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        assert self.state is not None
        if self.path == "/api/v1/config/domain/active":
            self._send(200, {"domain": self.state.active})
            return
        prefix = "/api/v1/config/domain/profile/"
        if self.path.startswith(prefix):
            name = self.path[len(prefix):]
            profile = self.state.profiles.get(name)
            if profile is None:
                self._send(404, {"detail": f"профиль '{name}' не найден"})
                return
            self._send(200, profile)
            return
        self._send(404, {"detail": "not found"})

    def log_message(self, *args: Any) -> None:
        return None


@contextmanager
def _stub_config_server(state: _State) -> Generator[str, None, None]:
    _StubConfig.state = state
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubConfig)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()


def test_loader_follows_activation_switch() -> None:
    state = _State()
    state.active = "it"
    state.profiles = {"it": IT_PROFILE, "library": LIBRARY_PROFILE, "cinema": CINEMA_PROFILE}
    with _stub_config_server(state) as url:
        loader = DomainProfileLoader(config_url=url)

        assert loader.active_domain() == "it"
        assert loader.load() == IT_PROFILE

        # активация library — следующий запрос уже новый профиль, без рестарта
        state.active = "library"
        assert loader.active_domain() == "library"
        assert loader.load() == LIBRARY_PROFILE

        # активация cinema
        state.active = "cinema"
        assert loader.load() == CINEMA_PROFILE


def test_loader_falls_back_when_config_unreachable(tmp_path: Path) -> None:
    profiles = tmp_path / "domain_profiles"
    profiles.mkdir()
    (profiles / "domain_profile.it.yaml").write_text(
        "profile:\n  name: it\nontology:\n  node_types: [{type: Requirement}]\n",
        encoding="utf-8",
    )
    loader = DomainProfileLoader(config_url="http://127.0.0.1:1", profiles_dir=profiles)
    assert loader.active_domain() == "it"
    assert loader.load()["profile"]["name"] == "it"


def test_glossary_resolves_via_active_domain(tmp_path: Path) -> None:
    profiles = tmp_path / "domain_profiles"
    profiles.mkdir()
    (profiles / "glossary.it.yaml").write_text("terms:\n  - canonical_name: big_o\n    aliases: [Big-O]\n", encoding="utf-8")
    (profiles / "glossary.library.yaml").write_text(
        "terms:\n  - canonical_name: zjdanov\n    aliases: [Жданов, Zhdanov]\n",
        encoding="utf-8",
    )
    state = _State()
    state.active = "it"
    with _stub_config_server(state) as url:
        app = create_glossary_app(profiles_dir=profiles, config_url=url)
        with TestClient(app) as client:
            before = client.post("/api/v1/glossary/resolve", json={"term": "Жданов"}).json()
            state.active = "library"
            after = client.post("/api/v1/glossary/resolve", json={"term": "Жданов"}).json()
    assert before["canonical_name"] is None
    assert after["canonical_name"] == "zjdanov"