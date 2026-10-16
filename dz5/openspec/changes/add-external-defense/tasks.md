# Tasks: add-external-defense (security-wall)

## 1. Планирование (spec-first)

- [x] `proposal.md`:Spielbericht внешнего контура (см. project.md §4 конфигурации).
- [x] Delta-spec `specs/external-defense/spec.md`: эндпоинты `/healthz` и `/api/chat`,
      сканеры llm-guard, матрица пресетов, env-конфигурация.

## 2. Каркас security-wall — Вариант №1 (FastAPI + llm-guard)

- [ ] `security-wall/requirements.txt`: `fastapi`, `uvicorn`, `llm-guard`, `httpx`
- [ ] `security-wall/main.py`: FastAPI приложение на `:5000`
      - `GET /healthz` → `ok` + проверка Ollama
      - `POST /api/chat` — Ollama-совместимый прокси с сканированием input/output
- [ ] `security-wall/Dockerfile`: образ на `ohw-python:3.13`, `dz5-security-wall:local`

## 3. Сервис в compose

- [ ] Сервис `wall` (контейнер `dz5-wall`, порт `5000`, `depends_on: ollama`,
      env `OLLAMA_BASE_URL=http://ollama:11434`, `SCAN_INPUT=on`,
      `SCAN_OUTPUT=on`, `BLOCK_ON_FAIL=on`), L3-лимиты памяти.

## 4. Проверка (прогнать вживую)

- [ ] `docker compose build wall` — зелёная сборка.
- [ ] `docker compose up -d ollama wall` + `GET /healthz` → `ok`.
- [ ] Агент с `EXTERNAL_GUARD=on`, `WALL_BASE_URL=http://wall:5000` — легитимный
      `/chat` проходит через wall и возвращает ответ.
- [ ] Wall блокирует input-инъекцию (например, `Ignore previous instructions`) —
      HTTP 403 с `blocked by security-wall`.
- [ ] Агент с `EXTERNAL_GUARD=on` не ломает пресет 3 (только внешняя защита).

## 5. Отметка выполнения

- [ ] Все проверки §4 зелёные; галочки по факту.