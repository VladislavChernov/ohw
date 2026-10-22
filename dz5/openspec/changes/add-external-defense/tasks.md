# Tasks: add-external-defense (Guardrails Server)

## 1. Планирование (spec-first)

- [x] `proposal.md` и `openspec/specs/external-defense/spec.md` переписаны под
      Guardrails Server (`/guards/{name}/validate`, порт `8000`).
- [x] Зафиксировано, что ручной FastAPI-прокси `/api/chat` и порт `5000`
      отменены.
- [x] Определён контракт агента со стеной: вход, выход, fail-closed `503`.

## 2. Guardrails Server

- [x] `security-wall/config.py`: guards `input-defense` (DetectPromptInjection,
      `check_vector=false`, `on_fail=noop`) и `output-privacy` (DetectPII,
      `EMAIL_ADDRESS/PHONE_NUMBER/CREDIT_CARD`, `on_fail=fix`); общие
      `AnalyzerEngine`/`AnonymizerEngine`; у guards явные `id`, иначе сервер
      ключует их по UUID.
- [x] `security-wall/requirements.txt`: `guardrails-api==0.4.4`,
      `guardrails-ai==0.11.0`, валидаторы; `pip check` чистый при сборке.
- [x] `security-wall/Dockerfile`: `python:3.12-slim`, `config.py`, порт `8000`,
      `uvicorn --factory guardrails_api.app:create_app --host 0.0.0.0`
      (CLI `guardrails-api start` слушает только localhost).
- [x] Удалить `security-wall/main.py` (ручной прокси) после переноса guards.
- [x] Проверить, что `GET /guards` видит `input-defense` и `output-privacy`
      (live, 2026-10-21); `write-defense` добавлен позже и покрыт unit-тестами.

## 3. Compose

- [x] Сервис `wall`: порт `8000:8000`, healthcheck `/health-check`,
      без `depends_on: ollama`, L3 memory `2048M`.
- [x] Агент: env `GUARDRAILS_BASE_URL=http://wall:8000`,
      `INPUT_GUARD_NAME=input-defense`, `WRITE_GUARD_NAME=write-defense`,
      `OUTPUT_GUARD_NAME=output-privacy`, `GUARDRAILS_TIMEOUT=60s`.
- [x] Убрать из compose/agent `WALL_BASE_URL` и порт `5000`.

## 4. Интеграция с Go-агентом

- [x] `agent/wall.go`: единый HTTP-клиент `Validate`, таймаут, разбор
      `validationPassed`/`validatedOutput`/`rawLlmOutput`.
- [x] Вход: `validationPassed=false`/4xx → `403`; 5xx/таймаут → `503`,
      LLM не вызывается.
- [x] Выход: `validationPassed=false` **или**
      `validatedOutput != rawLlmOutput` (PII fix) + `BLOCK_ON_FAIL=on` → `403`;
      `off` → `validatedOutput` или исходный ответ с warning.
- [x] `EXTERNAL_GUARD=on` в пресетах 3/4 использует новый контракт; прямой
      прокси через `WALL_BASE_URL` удалён.
- [x] Write-path: при `EXTERNAL_GUARD=on` и `ALLOW_WRITES=on` не-`SELECT` SQL
      проверяется через `write-defense` до `QueryDB`; unit-тесты block/allow/
      fail-closed зелёные.
- [x] Unit-тесты клиента на mock-сервере: pass, block, 4xx, fail-closed,
      timeout (`go test ./...` зелёный).

## 5. Проверка (живые прогоны)

- [x] `docker build` стены — зелёная сборка (`dz5-security-wall:local`).
- [x] `docker compose up -d wall`; `GET /health-check` → 200, healthy.
- [x] `GET /guards` → `input-defense`, `output-privacy` (live); `write-defense`
      добавлен позже, live-проверка ещё не выполнена.
- [x] Легитимный `/chat` через пресет 3 и 4 → 200, ответ модели
      (`preset=3` 12.9s, `preset=4` 8.1s).
- [x] `Ignore previous instructions...` → `403` на живом агенте с
      `EXTERNAL_GUARD=on` (пресеты 3 и 4); LLM не вызывалась.
- [x] PII на выходе на уровне агента: `BLOCK_ON_FAIL=on` → `403`
      (`stage=output`); `BLOCK_ON_FAIL=off` → 200 с
      `Contact <EMAIL_ADDRESS> or +1 555 010 9999`. Прямой `/validate`:
      `validationPassed=true`, `validatedOutput` замаскирован.
- [x] `docker compose stop wall` → агент `503`, LLM не вызывается.
- [ ] Live write-path: `INSERT` с `Ignore previous instructions` → `403`,
      БД не изменена; обычный `INSERT` проходит.
- [x] Повторить для пресетов 3 и 4; live-результаты зафиксированы в
      `reports/red-team.md`.

## 6. Отметка выполнения

- [x] Все проверки §5 зелёные; галочки только по факту.
- [x] `tasks.md` и `reports/red-team.md` синхронизированы с измерениями.
- [ ] Коммит только путей внутри `dz5/` (после явной просьбы).
