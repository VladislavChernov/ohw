"""dz5 security-wall: Ollama-compatible reverse proxy with Guardrails AI validators.

Exposes:
  GET  /healthz  — liveness + Ollama reachability
  POST /api/chat — Ollama-compatible chat proxy with input/output validation

Environment variables:
  OLLAMA_BASE_URL   upstream Ollama (default http://ollama:11434)
  WALL_LISTEN       listen address (default :5000)
  SCAN_INPUT        validate user prompts with Guardrails AI (default on)
  SCAN_OUTPUT       validate assistant replies with Guardrails AI (default on)
  BLOCK_ON_FAIL     return 403 on violation instead of passthrough (default on)

Note: Guardrails AI is loaded lazily and optionally. If the package is not
available, the wall still works as a plain Ollama proxy — validation is
skipped and requests pass through.
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

app = FastAPI(title="dz5 security-wall", version="0.2.0")

# Lazy validator initialisation (Guardrails AI is heavy; load on first request).
# None = not yet initialised; False = unavailable/disabled.
_input_guard = None
_output_guard = None


def _init_guards():
    global _input_guard, _output_guard
    if _input_guard is None and SCAN_INPUT:
        try:
            from guardrails import Guard
            g = Guard()
            # Try dedicated prompt-injection validator if installed
            try:
                from guardrails_ai.detect_prompt_injection import DetectPromptInjection
                g = g.use(DetectPromptInjection())
            except Exception:
                pass
            _input_guard = g
        except Exception:
            _input_guard = False  # mark as unavailable
    if _output_guard is None and SCAN_OUTPUT:
        try:
            from guardrails import Guard
            g = Guard()
            # Try dedicated PII validator if installed
            try:
                from guardrails_ai.detect_pii import DetectPII
                g = g.use(DetectPII())
            except Exception:
                pass
            _output_guard = g
        except Exception:
            _output_guard = False


def _validate(text: str, kind: str):
    """Run Guardrails AI validators for *kind* (input|output).
    Returns (sanitized, violated, reason). If validator unavailable → passthrough.
    """
    guard = _input_guard if kind == "input" else _output_guard
    if guard is None:
        _init_guards()
        guard = _input_guard if kind == "input" else _output_guard
    if not guard:
        return text, False, ""
    try:
        result = guard.validate(text)
        violated = not getattr(result, "validation_passed", True)
        reason = ""
        if violated:
            failures = getattr(result, "failures", None) or getattr(result, "errors", None)
            if failures:
                reason = "; ".join(str(f) for f in failures)
            else:
                reason = "validation failed"
        return text, violated, reason
    except Exception as exc:
        # Guardrails raises ValidationError on violation (e.g. prompt injection
        # detected). Treat any validator exception as a violation so the wall
        # blocks instead of silently passing through.
        return text, True, f"validator error: {exc}"


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
            sanitized, violated, reason = _validate(m["content"], "input")
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
    # Ollama defaults to stream=true (NDJSON). Force stream=false so the
    # wall returns a single JSON object; callers can still override via body.
    body.setdefault("stream", False)
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
        sanitized, violated, reason = _validate(content, "output")
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