"""HTTP-адаптеры bge-сервисов: контракт, fail-fast — бандл M3.2.

Сервисы заменяются стаб-сервером с тем же JSON-контрактом
(`POST /api/v1/embed`, `POST /api/v1/rerank`); проверяется payload запроса,
парсинг ответа и явная ошибка при сбое/неверной размерности.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from graphrag_proto.retrieval.adapters.bge import (
    BgeM3ServiceAdapter,
    BgeRerankerAdapter,
    EmbeddingServiceError,
    RerankerServiceError,
)


class _State:
    def __init__(self) -> None:
        self.responses: dict[str, tuple[int, str]] = {}
        self.seen: list[dict[str, Any]] = []


class _StubModel(BaseHTTPRequestHandler):
    state: _State | None = None

    def _send(self, code: int, body: str) -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        assert self.state is not None
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        self.state.seen.append({"path": self.path, "payload": payload})
        code, body = self.state.responses.get(self.path, (404, '{"detail": "not found"}'))
        self._send(code, body)

    def log_message(self, *args: Any) -> None:
        return None


@contextmanager
def _stub_model_server(state: _State) -> Iterator[str]:
    _StubModel.state = state
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubModel)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()


VECTOR = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]


def test_bge_m3_adapter_embed_contract() -> None:
    state = _State()
    state.responses = {"/api/v1/embed": (200, json.dumps({"vector": VECTOR, "dimensions": 8}))}
    with _stub_model_server(state) as url:
        adapter = BgeM3ServiceAdapter(base_url=url, dimensions=8)
        vector = adapter.embed("кэширование данных", domain="it")
    assert vector == VECTOR
    sent = state.seen[0]
    assert sent["path"] == "/api/v1/embed"
    assert sent["payload"] == {"text": "кэширование данных", "domain": "it"}


def test_bge_m3_adapter_dimension_mismatch_fails_fast() -> None:
    state = _State()
    state.responses = {"/api/v1/embed": (200, json.dumps({"vector": [0.5, 0.5], "dimensions": 2}))}
    with _stub_model_server(state) as url:
        adapter = BgeM3ServiceAdapter(base_url=url, dimensions=8)
        with pytest.raises(EmbeddingServiceError, match="EMBEDDING_DIMENSIONS"):
            adapter.embed("текст")


def test_bge_m3_adapter_http_error_fails_fast() -> None:
    state = _State()
    state.responses = {"/api/v1/embed": (500, '{"detail": "model unavailable"}')}
    with _stub_model_server(state) as url:
        adapter = BgeM3ServiceAdapter(base_url=url, dimensions=8)
        with pytest.raises(EmbeddingServiceError):
            adapter.embed("текст")


def test_bge_m3_adapter_malformed_json_fails_fast() -> None:
    state = _State()
    state.responses = {"/api/v1/embed": (200, "not-json")}
    with _stub_model_server(state) as url:
        adapter = BgeM3ServiceAdapter(base_url=url, dimensions=8)
        with pytest.raises(EmbeddingServiceError):
            adapter.embed("текст")


CHUNKS: list[dict[str, Any]] = [
    {"id": "chk:a", "text": "про базы данных"},
    {"id": "chk:b", "text": "про граф знаний"},
    {"id": "chk:c", "text": "про кино"},
]
SCORES = [0.9, 0.6, 0.1]


def test_bge_reranker_adapter_contract() -> None:
    state = _State()
    state.responses = {"/api/v1/rerank": (200, json.dumps({"scores": SCORES}))}
    with _stub_model_server(state) as url:
        adapter = BgeRerankerAdapter(base_url=url)
        scores = adapter.rerank("базы данных", CHUNKS)
    assert scores == SCORES
    sent = state.seen[0]
    assert sent["path"] == "/api/v1/rerank"
    assert sent["payload"]["query"] == "базы данных"
    assert [c["text"] for c in sent["payload"]["chunks"]] == [c["text"] for c in CHUNKS]


def test_bge_reranker_adapter_len_mismatch_fails_fast() -> None:
    state = _State()
    state.responses = {"/api/v1/rerank": (200, json.dumps({"scores": [0.9, 0.6]}))}
    with _stub_model_server(state) as url:
        adapter = BgeRerankerAdapter(base_url=url)
        with pytest.raises(RerankerServiceError, match="неожиданный ответ"):
            adapter.rerank("базы данных", CHUNKS)


def test_bge_reranker_adapter_http_error_fails_fast() -> None:
    state = _State()
    state.responses = {"/api/v1/rerank": (503, '{"detail": "unavailable"}')}
    with _stub_model_server(state) as url:
        adapter = BgeRerankerAdapter(base_url=url)
        with pytest.raises(RerankerServiceError):
            adapter.rerank("базы данных", CHUNKS)


def test_bge_reranker_adapter_malformed_json_fails_fast() -> None:
    state = _State()
    state.responses = {"/api/v1/rerank": (200, "not-json")}
    with _stub_model_server(state) as url:
        adapter = BgeRerankerAdapter(base_url=url)
        with pytest.raises(RerankerServiceError):
            adapter.rerank("базы данных", CHUNKS)