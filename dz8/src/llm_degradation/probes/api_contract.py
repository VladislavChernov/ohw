"""Ollama HTTP API contract checks (API-01 ... API-12)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from llm_degradation.models import VerdictInfo
from llm_degradation.ollama import LabOllamaClient, OllamaResponseError, OllamaUnavailable
from llm_degradation.prompts import load_prompt

UNKNOWN_MODEL = "llm-lab-model-does-not-exist:latest"
TOTAL_API_CHECKS = 12


class ApiCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    status: str
    http_status: int | None = None
    latency_ms: float = 0.0
    detail: str = ""
    raw: dict[str, Any] | None = None
    critical: bool = True


def run_api_checks(
    client: LabOllamaClient,
    model: str,
    *,
    unknown_model: str = UNKNOWN_MODEL,
    progress: Callable[[int, int, ApiCheck], None] | None = None,
    prompt_dir: Path | None = None,
) -> list[ApiCheck]:
    checks: list[ApiCheck] = []

    def add(check: ApiCheck) -> None:
        checks.append(check)
        if progress is not None:
            progress(len(checks), TOTAL_API_CHECKS, check)

    add(_version(client))
    add(_tags(client))
    add(_show(client, model))
    add(_chat_minimal(client, model, prompt_dir))
    add(_chat_json(client, model, prompt_dir))
    add(_chat_unknown_model(client, unknown_model, prompt_dir))
    add(_chat_malformed_json(client))
    add(_chat_options(client, model, prompt_dir))
    add(_chat_stream(client, model, prompt_dir))
    add(_timeout(client, model, prompt_dir))
    add(_ps(client))
    add(_parallel(client, model, prompt_dir))
    return checks


def api_verdict(checks: list[ApiCheck]) -> VerdictInfo:
    failed = [item.id for item in checks if item.status == "fail" and item.critical]
    warned = [item.id for item in checks if item.status == "warn"]
    if failed:
        return VerdictInfo(
            status="fail",
            reasons=["contract checks failed: " + ", ".join(failed)],
            thresholds={"critical_checks": float(len(checks))},
        )
    if warned:
        return VerdictInfo(
            status="warn",
            reasons=["optional checks warned: " + ", ".join(warned)],
            thresholds={"critical_checks": float(len(checks))},
        )
    return VerdictInfo(
        status="pass", reasons=[], thresholds={"critical_checks": float(len(checks))}
    )


def unavailable_verdict(message: str) -> VerdictInfo:
    return VerdictInfo(status="fail", reasons=[message], thresholds={})


def _check(
    check_id: str,
    name: str,
    *,
    status: str,
    http_status: int | None,
    started: float,
    detail: str = "",
    raw: dict[str, Any] | None = None,
    critical: bool = True,
) -> ApiCheck:
    return ApiCheck(
        id=check_id,
        name=name,
        status=status,
        http_status=http_status,
        latency_ms=round((time.perf_counter() - started) * 1000.0, 1),
        detail=detail,
        raw=raw,
        critical=critical,
    )


def _version(client: LabOllamaClient) -> ApiCheck:
    started = time.perf_counter()
    try:
        response = client.request("GET", "/api/version")
    except OllamaUnavailable as exc:
        return _check(
            "API-01",
            "GET /api/version",
            status="fail",
            http_status=None,
            started=started,
            detail=str(exc),
        )
    payload = _safe_json(response)
    ok = response.status_code == 200 and bool(payload.get("version"))
    return _check(
        "API-01",
        "GET /api/version",
        status="pass" if ok else "fail",
        http_status=response.status_code,
        started=started,
        detail="" if ok else "version is missing",
        raw=payload,
    )


def _tags(client: LabOllamaClient) -> ApiCheck:
    started = time.perf_counter()
    try:
        response = client.request("GET", "/api/tags")
    except OllamaUnavailable as exc:
        return _check(
            "API-02",
            "GET /api/tags",
            status="fail",
            http_status=None,
            started=started,
            detail=str(exc),
        )
    payload = _safe_json(response)
    models = payload.get("models")
    ok = (
        response.status_code == 200
        and isinstance(models, list)
        and all(
            isinstance(item, dict) and {"name", "model", "digest", "size", "details"}.issubset(item)
            for item in models
        )
    )
    return _check(
        "API-02",
        "GET /api/tags",
        status="pass" if ok else "fail",
        http_status=response.status_code,
        started=started,
        detail="" if ok else "models array is missing required fields",
        raw=payload,
    )


def _show(client: LabOllamaClient, model: str) -> ApiCheck:
    started = time.perf_counter()
    try:
        response = client.request("POST", "/api/show", json_body={"model": model})
    except OllamaUnavailable as exc:
        return _check(
            "API-03",
            "POST /api/show",
            status="fail",
            http_status=None,
            started=started,
            detail=str(exc),
        )
    payload = _safe_json(response)
    # Ollama >= 0.12 stopped returning ``digest`` from /api/show (it lives in
    # /api/tags and /api/ps); the stable contract is details + model_info.
    ok = (
        response.status_code == 200
        and isinstance(payload.get("details"), dict)
        and isinstance(payload.get("model_info"), dict)
    )
    return _check(
        "API-03",
        "POST /api/show",
        status="pass" if ok else "fail",
        http_status=response.status_code,
        started=started,
        detail="" if ok else "details/model_info are missing",
        raw=payload,
    )


def _chat_minimal(
    client: LabOllamaClient, model: str, prompt_dir: Path | None = None
) -> ApiCheck:
    started = time.perf_counter()
    try:
        response = client.request(
            "POST",
            "/api/chat",
            json_body={
                "model": model,
                "messages": [
                    {"role": "user", "content": load_prompt("api-ping.txt", prompt_dir)}
                ],
                "stream": False,
                "options": {"num_predict": 16},
            },
        )
    except OllamaUnavailable as exc:
        return _check(
            "API-04",
            "POST /api/chat minimal",
            status="fail",
            http_status=None,
            started=started,
            detail=str(exc),
        )
    payload = _safe_json(response)
    message = payload.get("message")
    content = message.get("content", "") if isinstance(message, dict) else ""
    ok = (
        response.status_code == 200
        and bool(content)
        and bool(payload.get("done"))
        and int(payload.get("eval_count", 0) or 0) > 0
    )
    return _check(
        "API-04",
        "POST /api/chat minimal",
        status="pass" if ok else "fail",
        http_status=response.status_code,
        started=started,
        detail="" if ok else "empty content or eval_count=0",
        raw=payload,
    )


def _chat_json(
    client: LabOllamaClient, model: str, prompt_dir: Path | None = None
) -> ApiCheck:
    started = time.perf_counter()
    try:
        response = client.request(
            "POST",
            "/api/chat",
            json_body={
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": load_prompt("api-json.txt", prompt_dir),
                    }
                ],
                "stream": False,
                "format": "json",
                "options": {"num_predict": 32},
            },
        )
    except OllamaUnavailable as exc:
        return _check(
            "API-05",
            "POST /api/chat format=json",
            status="fail",
            http_status=None,
            started=started,
            detail=str(exc),
        )
    payload = _safe_json(response)
    message = payload.get("message")
    content = message.get("content", "") if isinstance(message, dict) else ""
    ok = response.status_code == 200 and _parses(content)
    return _check(
        "API-05",
        "POST /api/chat format=json",
        status="pass" if ok else "fail",
        http_status=response.status_code,
        started=started,
        detail="" if ok else "message.content is not a JSON object",
        raw=payload,
    )


def _chat_unknown_model(
    client: LabOllamaClient, model: str, prompt_dir: Path | None = None
) -> ApiCheck:
    started = time.perf_counter()
    try:
        response = client.request(
            "POST",
            "/api/chat",
            json_body={
                "model": model,
                "messages": [
                    {"role": "user", "content": load_prompt("api-ping.txt", prompt_dir)}
                ],
                "stream": False,
            },
        )
    except OllamaUnavailable as exc:
        return _check(
            "API-06",
            "POST /api/chat unknown model",
            status="fail",
            http_status=None,
            started=started,
            detail=str(exc),
        )
    payload = _safe_json(response)
    ok = response.status_code == 404 and bool(payload.get("error"))
    return _check(
        "API-06",
        "POST /api/chat unknown model",
        status="pass" if ok else "fail",
        http_status=response.status_code,
        started=started,
        detail="" if ok else "expected 404 with a non-empty error",
        raw=payload,
    )


def _chat_malformed_json(client: LabOllamaClient) -> ApiCheck:
    started = time.perf_counter()
    try:
        response = client.request(
            "POST",
            "/api/chat",
            content=b'{"model":',
            headers={"Content-Type": "application/json"},
        )
    except OllamaUnavailable as exc:
        return _check(
            "API-07",
            "POST /api/chat malformed JSON",
            status="fail",
            http_status=None,
            started=started,
            detail=str(exc),
        )
    ok = response.status_code == 400
    return _check(
        "API-07",
        "POST /api/chat malformed JSON",
        status="pass" if ok else "fail",
        http_status=response.status_code,
        started=started,
        detail="" if ok else f"expected 400, got {response.status_code}",
    )


def _chat_options(
    client: LabOllamaClient, model: str, prompt_dir: Path | None = None
) -> ApiCheck:
    started = time.perf_counter()
    try:
        response = client.request(
            "POST",
            "/api/chat",
            json_body={
                "model": model,
                "messages": [
                    {"role": "user", "content": load_prompt("api-say-ok.txt", prompt_dir)}
                ],
                "stream": False,
                "options": {"temperature": 0, "seed": 7, "num_predict": 16},
            },
        )
    except OllamaUnavailable as exc:
        return _check(
            "API-08",
            "POST /api/chat options",
            status="fail",
            http_status=None,
            started=started,
            detail=str(exc),
        )
    payload = _safe_json(response)
    eval_count = int(payload.get("eval_count", 0) or 0)
    prompt_eval = int(payload.get("prompt_eval_count", 0) or 0)
    status = "pass"
    detail = ""
    if response.status_code != 200:
        status = "fail"
        detail = f"expected 200, got {response.status_code}"
    elif prompt_eval <= 0:
        status = "fail"
        detail = "prompt_eval_count is not positive"
    elif eval_count > 32:
        status = "warn"
        detail = f"eval_count {eval_count} > 32"
    return _check(
        "API-08",
        "POST /api/chat options",
        status=status,
        http_status=response.status_code,
        started=started,
        detail=detail,
        raw=payload,
    )


def _chat_stream(
    client: LabOllamaClient, model: str, prompt_dir: Path | None = None
) -> ApiCheck:
    started = time.perf_counter()
    try:
        response = client.request(
            "POST",
            "/api/chat",
            json_body={
                "model": model,
                "messages": [
                    {"role": "user", "content": load_prompt("api-say-ok.txt", prompt_dir)}
                ],
                "stream": True,
                "options": {"num_predict": 8},
            },
        )
    except OllamaUnavailable as exc:
        return _check(
            "API-09",
            "POST /api/chat stream=true",
            status="warn",
            http_status=None,
            started=started,
            detail=str(exc),
            critical=False,
        )
    lines = [line for line in response.text.splitlines() if line.strip()]
    ok = response.status_code == 200 and bool(lines) and all(_parses(line) for line in lines)
    return _check(
        "API-09",
        "POST /api/chat stream=true",
        status="pass" if ok else "warn",
        http_status=response.status_code,
        started=started,
        detail="" if ok else "stream did not return NDJSON",
        critical=False,
    )


def _timeout(
    client: LabOllamaClient, model: str, prompt_dir: Path | None = None
) -> ApiCheck:
    started = time.perf_counter()
    try:
        client.request(
            "POST",
            "/api/chat",
            json_body={
                "model": model,
                "messages": [
                    {"role": "user", "content": load_prompt("api-ping.txt", prompt_dir)}
                ],
                "stream": False,
            },
            timeout=0.001,
        )
    except (OllamaUnavailable, OllamaResponseError):
        return _check("API-10", "timeout 0.001s", status="pass", http_status=None, started=started)
    return _check(
        "API-10",
        "timeout 0.001s",
        status="fail",
        http_status=None,
        started=started,
        detail="the request did not time out",
    )


def _ps(client: LabOllamaClient) -> ApiCheck:
    started = time.perf_counter()
    try:
        response = client.request("GET", "/api/ps")
    except OllamaUnavailable as exc:
        return _check(
            "API-11",
            "GET /api/ps",
            status="warn",
            http_status=None,
            started=started,
            detail=str(exc),
            critical=False,
        )
    ok = response.status_code in {200, 404}
    return _check(
        "API-11",
        "GET /api/ps",
        status="pass" if ok else "warn",
        http_status=response.status_code,
        started=started,
        detail="" if ok else f"expected 200 or 404, got {response.status_code}",
        raw=_safe_json(response),
        critical=False,
    )


def _parallel(
    client: LabOllamaClient, model: str, prompt_dir: Path | None = None
) -> ApiCheck:
    started = time.perf_counter()
    prompt = load_prompt("api-say-ok.txt", prompt_dir)

    def call() -> int:
        try:
            response = client.request(
                "POST",
                "/api/chat",
                json_body={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"num_predict": 8},
                },
            )
        except (OllamaUnavailable, OllamaResponseError):
            return 0
        return response.status_code

    with ThreadPoolExecutor(max_workers=3) as pool:
        statuses = list(pool.map(lambda _: call(), range(3)))
    ok = all(item == 200 for item in statuses)
    return _check(
        "API-12",
        "3 parallel chats",
        status="pass" if ok else "warn",
        http_status=statuses[0] if statuses else None,
        started=started,
        detail="" if ok else f"statuses: {statuses}",
        critical=False,
    )


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _parses(text: str) -> bool:
    try:
        json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False
    return True
