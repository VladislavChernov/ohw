"""Контрактный тест OpenAICompatibleAdapter (ADR-022): streaming, non-streaming, ошибки."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from graphrag_proto.retrieval.adapters.llm import OpenAICompatibleAdapter


def _make_handler(payloads: dict[str, Any]):
    """Фабрика HTTPHandler: payloads — mapping path -> (status, body)."""

    class _Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = payloads.get(self.path)
            if body is None:
                self.send_error(404)
                return
            status, response = body
            if isinstance(response, str):
                response = response.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format: str, *args: Any) -> None:
            pass  # тихий HTTP

    return _Handler


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _start_server(handler_class: type) -> tuple[HTTPServer, int]:
    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, port


# --- streaming --------------------------------------------------------

def test_streaming_sse_deltas() -> None:
    sse = (
        'data: {"choices":[{"delta":{"content":"привет"}}]}\n'
        'data: {"choices":[{"delta":{"content":" мир"}}]}\n'
        "data: [DONE]\n"
    )
    handler = _make_handler({"/v1/chat/completions": (200, sse)})
    server, port = _start_server(handler)
    try:
        llm = OpenAICompatibleAdapter(f"http://127.0.0.1:{port}", model="test", timeout_s=5)
        deltas = list(llm.generate("вопрос", stream=True))
        assert deltas == ["привет", " мир"]
    finally:
        server.shutdown()


# --- non-streaming ----------------------------------------------------

def test_non_streaming_content() -> None:
    resp = json.dumps({"choices": [{"message": {"content": "ответ"}}]})
    handler = _make_handler({"/v1/chat/completions": (200, resp)})
    server, port = _start_server(handler)
    try:
        llm = OpenAICompatibleAdapter(f"http://127.0.0.1:{port}", model="test", timeout_s=5)
        deltas = list(llm.generate("вопрос", stream=False))
        assert deltas == ["ответ"]
    finally:
        server.shutdown()


# --- ошибки ----------------------------------------------------------

def test_http_500_raises_runtime_error() -> None:
    handler = _make_handler({"/v1/chat/completions": (500, b"Internal Server Error")})
    server, port = _start_server(handler)
    try:
        llm = OpenAICompatibleAdapter(f"http://127.0.0.1:{port}", model="test", timeout_s=5)
        with pytest.raises(RuntimeError, match="LLM HTTP 500"):
            list(llm.generate("вопрос"))
    finally:
        server.shutdown()


def test_timeout_raises_runtime_error() -> None:
    """Сервис не отвечает дольше timeout_s → RuntimeError."""

    class _SlowHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            time.sleep(2)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"choices":[{"message":{"content":"late"}}]}')

        def log_message(self, format: str, *args: Any) -> None:
            pass

    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), _SlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        llm = OpenAICompatibleAdapter(f"http://127.0.0.1:{port}", model="test", timeout_s=0.1)
        with pytest.raises(RuntimeError, match="LLM недоступен"):
            list(llm.generate("вопрос"))
    finally:
        server.shutdown()


def test_connection_refused_raises_runtime_error() -> None:
    llm = OpenAICompatibleAdapter("http://127.0.0.1:59999", model="test", timeout_s=1)
    with pytest.raises(RuntimeError, match="LLM недоступен"):
        list(llm.generate("вопрос"))


# --- factory wiring (LLM_ADAPTER=openai + LLM_BASE_URL) ----------------

def test_factory_openai_adapter_hits_stub(monkeypatch: Any) -> None:
    """LLM_ADAPTER=openai + LLM_BASE_URL собирают OpenAICompatibleAdapter против стаб-сервера."""
    sse = (
        'data: {"choices":[{"delta":{"content":"factory"}}]}\n'
        'data: {"choices":[{"delta":{"content":" ok"}}]}\n'
        "data: [DONE]\n"
    )
    handler = _make_handler({"/v1/chat/completions": (200, sse)})
    server, port = _start_server(handler)
    try:
        from graphrag_proto.retrieval.adapters.factory import build_llm

        monkeypatch.setenv("LLM_ADAPTER", "openai")
        monkeypatch.setenv("LLM_BASE_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("LLM_MODEL", "stub-model")
        llm = build_llm()
        deltas = list(llm.generate("вопрос"))
        assert deltas == ["factory", " ok"]
    finally:
        server.shutdown()