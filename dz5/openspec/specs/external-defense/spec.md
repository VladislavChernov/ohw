# Spec: add-external-defense (security-wall)

## Goal
External defense contour for LLM agent: `security-wall` — Ollama-compatible proxy
on FastAPI + Guardrails AI (`guardrails-ai`), standing between the agent and local
abliterated model. Validates incoming prompt (prompt injection, toxicity)
and outgoing response (PII, data leak), blocks or masks on violation.

## Integration with agent
Agent (Go) already supports `EXTERNAL_GUARD=on`: in this mode `LLMBaseURL`
is taken from `WALL_BASE_URL` (`http://wall:5000`) and all `/api/chat` requests
are proxied through wall. Agent does not change — wall accepts the same Ollama contract.

## security-wall endpoints

### `GET /healthz` → `ok`
Smoke check that wall is alive and Ollama is accessible.

### `POST /api/chat` (Ollama-compatible)
Accepts Ollama `/api/chat` request body (`model`, `messages`, `stream`),
scans message `content`, proxies to Ollama, scans response.

| Behavior | Condition |
|----------|-----------|
| Input blocking | `SCAN_INPUT=on` and any input scanner returned violation |
| Output blocking | `SCAN_OUTPUT=on` and output scanner returned violation |
| Proxy unchanged | Both flags off, or scanning passed |
| Block response | JSON `{"error":"blocked by security-wall","reason":...}` with HTTP 403 |

## Guardrails AI validators
- Input: `DetectPromptInjection` (prompt injection, jailbreak) — if package missing, validator is skipped (passthrough)
- Output: `DetectPII` (PII — names, email, phones, addresses) — if package missing, validator is skipped (passthrough)

### Validator package installation (required for threat blocking)

Base package `guardrails-ai` contains only core `Guard`. Specialized
validators — separate packages, taken from **PyPI** (https://pypi.org):

| Threat | Validator | PyPI package | Command |
|--------|-----------|--------------|---------|
| Prompt injection / jailbreak | `DetectPromptInjection` | `guardrails-ai-detect-prompt-injection` | `pip install guardrails-ai-detect-prompt-injection` |
| PII (names, email, phones, addresses) | `DetectPII` | `guardrails-ai-detect-pii` | `pip install guardrails-ai-detect-pii` |

Without these packages `security-wall` works in **passthrough** mode (proxy without
blocking). To make blocking work, install both packages in wall environment/image:

```bash
# locally / in wall container
pip install guardrails-ai-detect-prompt-injection guardrails-ai-detect-pii
```

In `dz5/security-wall/Dockerfile` dependencies are installed from `requirements.txt`
(`pip install --no-cache-dir -r requirements.txt`), so it's enough to add both
packages to `requirements.txt` and rebuild the image:

```bash
cd dz5/security-wall
DOCKER_BUILDKIT=1 docker build -t dz5-security-wall:local .
```

## Configuration (env)
| Variable | Default | Purpose |
|----------|---------|---------|
| `OLLAMA_BASE_URL` | `http://ollama:11434` | Where to proxy chat to |
| `WALL_LISTEN` | `:5000` | Wall port |
| `SCAN_INPUT` | `on` | Scan incoming prompt |
| `SCAN_OUTPUT` | `on` | Scan LLM response |
| `BLOCK_ON_FAIL` | `on` | Block on violation (otherwise warning in response body) |

## Image
Base: `python:3.12-slim` (locally verified; `ohw-python:3.13` from Dz4 not
extracted due to Docker layer error `0xc000013a` on this machine).
Dependencies: `fastapi`, `uvicorn`, `guardrails-ai`, `httpx`. Build:
`dz5/security-wall/Dockerfile`, image `dz5-security-wall:local`.

## Service composition in compose (`dz5/infra/compose.yaml`)
- Service `wall`, container `dz5-wall`, port `5000`, `depends_on: ollama`
  (condition: service_healthy), env `OLLAMA_BASE_URL=http://ollama:11434`,
  `SCAN_INPUT=on`, `SCAN_OUTPUT=on`, `BLOCK_ON_FAIL=on`.
- L3 limits: `deploy.resources.limits.memory: 1024M`.
- Network `ib-network`.

## Preset matrix (project.md §Protection architecture)
| Preset | BUILTIN_GUARDS | EXTERNAL_GUARD | Agent routes |
|--------|----------------|----------------|--------------|
| 1 Baseline | off | off | Direct to Ollama |
| 2 Builtin Guards | on | off | Direct to Ollama |
| 3 External Wall only | off | on | Via wall:5000 |
| 4 Full Defense | on | on | Via wall:5000 |

## Definition of done
- [x] Spec written (this file)
- [ ] `dz5/security-wall/` — FastAPI + Guardrails AI, `/api/chat` proxy, `/healthz`
- [ ] `dz5/security-wall/Dockerfile` on `ohw-python:3.13`, build green
- [ ] Service `wall` in `dz5/infra/compose.yaml`, comes up via `docker compose up -d wall`
- [ ] `EXTERNAL_GUARD=on` agent routes through wall and gets a response
- [ ] Wall blocks injection in input (example: `Ignore previous instructions`)
- [ ] tasks.md checkboxes green; commit only in `dz5/`

## Framework licenses (MIT/Apache)

- `Augustus` (OWASP LLM Top 10 2026): Apache 2.0 (https://github.com/praetorian-inc/augustus). Auto-attack tests in `scripts/augustus/`.
- `pytest` (security auto-tests): MIT (https://github.com/pytest-dev/pytest). In `scripts/test_security.py`.
- `guardrails-ai` (external wall protection): MIT (https://github.com/guardrails-ai/guardrails). `security-wall/`.
- `FastAPI` + `Uvicorn`: MIT (https://github.com/tiangolo/fastapi). `security-wall/`.
- `ollama` client: MIT (https://github.com/ollama/ollama). `agent`, `security-wall`.
- `modernc.org/sqlite`: MIT (https://github.com/mattn/go-sqlite3). `agent`.
- `OWASP LLM Top 10 2026`: MIT (https://github.com/OWASP/AI-Security-Guide). Attack map in `scripts/owasp/`.