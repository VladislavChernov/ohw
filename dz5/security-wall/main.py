"""dz5 security-wall: Ollama-compatible reverse proxy with llm-guard scanners.

Exposes:
  GET  /healthz  — liveness + Ollama reachability
  POST /api/chat — Ollama-compatible chat proxy with input/output scanning

Environment variables:
  OLLAMA_BASE_URL   upstream Ollama (default http://ollama:11434)
  WALL_LISTEN       listen address (default :5000)
  SCAN_INPUT        scan user prompts with llm-guard (default on)
  SCAN_OUTPUT       scan assistant replies with llm-guard (default on)
  BLOCK_ON_FAIL     return 403 on violation instead of passthrough (default on)

Note: llm-guard is loaded lazily and optionally. If the package is not
available (heavy torch/transformers deps), the wall still works as a plain
Ollama proxy — scanning is skipped and requests pass through.
"""
import os
from typing import List, Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import httpx

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434").rstrip("/")
SCAN_INPUT = os.getenv("SCAN_INPUT", "on").lower() == "on"
SCAN_OUTPUT = os.getenv("SCAN_OUTPUT", "on").lower() == "on"
BLOCK_ON_FAIL = os.getenv("BLOCK_ON_FAIL", "on").lower() == "on"

app = FastAPI(title="dz5 security-wall", version="0.1.0")

# Lazy scanner initialisation (llm-guard is heavy; load on first request).
# None = llm-guard not installed or scanning disabled.
_input_fn = None
_output_fn = None


def _init_scanners():
    global _input_fn, _output_fn
    if _input_fn is None and SCAN_INPUT:
        try:
            from llm_guard import scan_prompt
            from llm_guard.scanners import PromptInjection, Toxicity
            _input_fn = (scan_prompt, [PromptInjection(), Toxicity()])
        except Exception:
            _input_fn = False  # mark as unavailable
    if _output_fn is None and SCAN_OUTPUT:
        try:
            from llm_guard import scan_response
            from llm_guard.scanners import SensitiveInformation
            _output_fn = (scan_response, [SensitiveInformation()])
        except Exception:
            _output_fn = False


def _scan(text: str, kind: str):
    """Run scanners for *kind* (input|output).
    Returns (sanitized, violated, reason). If scanner unavailable → passthrough.
    """
    fn = _input_fn if kind == "input" else _output_fn
    if fn is None:
        _init_scanners()
        fn = _input_fn if kind == "input" else _output_fn
    if not fn:
        return text, False, ""
    scan_fn, scanners = fn
    try:
        sanitized, results, is_violated = scan_fn(scanners, text)
    except Exception:
        return text, False, ""
    reason = ""
    if is_violated:
        reasons = [name for name, res in results.items() if res.get("is_violated")]
        reason = "; ".join(reasons)
    return sanitized, is_violated, reason


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    stream: bool = False
    options: Optional[dict] = None
    format: Optional[str] = None


@app.get("/healthz")
async def healthz():
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{OLLAMA_BASE_URL}/api/tags")
            if r.status_code == 200:
                return {"status": "ok"}
    except Exception:
        pass
    raise HTTPException(status_code=503, detail="ollama unreachable")


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()

    # ---- input scan -------------------------------------------------------
    messages = body.get("messages", [])
    blocked_reasons: List[str] = []
    for m in messages:
        if m.get("role") == "user" and m.get("content"):
            sanitized, violated, reason = _scan(m["content"], "input")
            if violated:
                blocked_reasons.append(f"input:{reason}")
            m["content"] = sanitized

    if blocked_reasons and BLOCK_ON_FAIL:
        return JSONResponse(
            status_code=403,
            content={
                "error": "blocked by security-wall",
                "reason": "; ".join(blocked_reasons),
            },
        )

    # ---- proxy to Ollama --------------------------------------------------
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            upstream = await client.post(
                f"{OLLAMA_BASE_URL}/api/chat",
                json=body,
            )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"ollama upstream error: {exc}")

    if upstream.status_code != 200:
        return JSONResponse(
            status_code=upstream.status_code,
            content=upstream.json() if upstream.headers.get("content-type", "").startswith("application/json") else {"error": upstream.text},
        )

    data = upstream.json()

    # ---- output scan ------------------------------------------------------
    message = data.get("message", {})
    content = message.get("content", "")
    if content:
        sanitized, violated, reason = _scan(content, "output")
        if violated:
            blocked_reasons.append(f"output:{reason}")
            message["content"] = sanitized
            data["message"] = message
            if BLOCK_ON_FAIL:
                return JSONResponse(
                    status_code=403,
                    content={
                        "error": "blocked by security-wall",
                        "reason": "; ".join(blocked_reasons),
                        "original_model": data,
                    },
                )

    return data