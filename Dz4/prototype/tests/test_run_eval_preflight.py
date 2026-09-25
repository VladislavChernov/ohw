"""Fail-fast preflight eval-раннера (ADR-015): доступность контуров, лимиты ожидания."""

from __future__ import annotations

import importlib.util
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

EVAL_PY = Path(__file__).resolve().parent.parent / "infra" / "eval" / "run_eval.py"


def _load_run_eval() -> Any:
    spec = importlib.util.spec_from_file_location("run_eval", EVAL_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_run_eval = _load_run_eval()


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _start_http(status: int = 200) -> tuple[HTTPServer, int]:
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, format: str, *args: Any) -> None:
            pass

    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


# --- probes -----------------------------------------------------------

def test_probe_http_ok() -> None:
    server, port = _start_http(200)
    try:
        ok, detail = _run_eval._probe_http(f"http://127.0.0.1:{port}", timeout_s=2)
        assert ok is True
        assert detail == "HTTP 200"
    finally:
        server.shutdown()


def test_probe_http_down_returns_false() -> None:
    ok, _ = _run_eval._probe_http("http://127.0.0.1:1", timeout_s=1)
    assert ok is False


def test_probe_bolt_ok_and_down() -> None:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        ok, detail = _run_eval._probe_bolt(f"bolt://127.0.0.1:{port}", timeout_s=2)
        assert ok is True
        assert detail == f"TCP 127.0.0.1:{port}"

        ok_down, _ = _run_eval._probe_bolt("bolt://127.0.0.1:1", timeout_s=1)
        assert ok_down is False


def test_probe_bolt_invalid_uri() -> None:
    ok, detail = _run_eval._probe_bolt("не-uri", timeout_s=1)
    assert ok is False
    assert "невалидный" in detail


# --- contours ---------------------------------------------------------

def test_required_contours_defaults_include_ingestion_and_llm(monkeypatch: Any) -> None:
    monkeypatch.delenv("GRAPH_STORE", raising=False)
    monkeypatch.delenv("VECTOR_STORE", raising=False)
    monkeypatch.delenv("EMBEDDER", raising=False)
    monkeypatch.delenv("RERANKER", raising=False)
    monkeypatch.delenv("LLM_ADAPTER", raising=False)
    contours = _run_eval.required_contours()
    names = {c["name"] for c in contours}
    assert "ingestion-api" in names
    assert "neo4j" not in names
    assert "llm" in names  # дефолт LLM_ADAPTER=openai → нужен LLM-контур


def test_required_contours_include_backends_when_configured(monkeypatch: Any) -> None:
    monkeypatch.setenv("GRAPH_STORE", "neo4j")
    monkeypatch.setenv("VECTOR_STORE", "neo4j")
    monkeypatch.setenv("EMBEDDER", "bge_m3_service")
    monkeypatch.setenv("RERANKER", "bge_reranker")
    monkeypatch.setenv("LLM_ADAPTER", "openai")
    contours = _run_eval.required_contours()
    names = {c["name"] for c in contours}
    assert {"ingestion-api", "neo4j", "embeddings-service", "reranker-service", "llm"} <= names


def test_required_contours_include_config_and_judge_llm(monkeypatch: Any) -> None:
    monkeypatch.setenv("CONFIG_URL", "http://config-service:8001")
    monkeypatch.setenv("LLM_ADAPTER", "fake")
    monkeypatch.setenv("EVAL_LLM_ADAPTER", "openai")
    monkeypatch.delenv("GRAPH_STORE", raising=False)
    monkeypatch.delenv("VECTOR_STORE", raising=False)
    monkeypatch.delenv("EMBEDDER", raising=False)
    names = {c["name"] for c in _run_eval.required_contours()}
    assert {"config-service", "llm"} <= names


def test_retrieval_only_keeps_ingestion_llm_contour(monkeypatch: Any) -> None:
    monkeypatch.setenv("EXTRACT_LLM", "true")
    monkeypatch.setenv("LLM_ADAPTER", "fake")
    monkeypatch.setenv("EVAL_LLM_ADAPTER", "none")
    contours = _run_eval.required_contours(include_generation=False, include_ingestion=True)
    assert "llm" in {contour["name"] for contour in contours}


def test_preflight_writes_log_and_verdict(monkeypatch: Any, tmp_path: Path) -> None:
    server, port = _start_http(200)
    try:
        monkeypatch.setattr(_run_eval, "INGESTION_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("LLM_ADAPTER", "fake")
        monkeypatch.setenv("EVAL_LLM_ADAPTER", "fake")
        monkeypatch.delenv("GRAPH_STORE", raising=False)
        monkeypatch.delenv("EMBEDDER", raising=False)
        ok = _run_eval.preflight(tmp_path, timeout_s=2)
        assert ok is True
    finally:
        server.shutdown()
    log = (tmp_path / "preflight.log").read_text(encoding="utf-8")
    assert "verdict: READY" in log
    assert "ingestion-api" in log


def test_preflight_fails_when_contour_down(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setattr(_run_eval, "INGESTION_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("LLM_ADAPTER", "fake")
    monkeypatch.delenv("GRAPH_STORE", raising=False)
    monkeypatch.delenv("EMBEDDER", raising=False)
    ok = _run_eval.preflight(tmp_path, timeout_s=1)
    assert ok is False
    log = (tmp_path / "preflight.log").read_text(encoding="utf-8")
    assert "verdict: FAIL" in log
    assert "DOWN" in log