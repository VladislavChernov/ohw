# Proposal: внешняя защита (Guardrails Server)

## Почему

Встроенные guard'ы в промпте — вероятностная защита: модель может
проигнорировать инструкцию. Для пресетов 3/4 нужен детерминированный внешний
контур, который проверяет вход и выход независимо от модели и возвращает
предсказуемый вердикт. Ручной FastAPI-прокси `/api/chat` на `:5000` отменён:
он смешивал проксирование LLM и валидацию, требовал своего кода и не давал
стандартного API.

## Что делаем

- Сервис `security-wall` на базе официального `guardrails-api==0.4.4` и
  `guardrails-ai==0.11.0`; guards описаны в `security-wall/config.py`.
- Два guard'а: `input-defense` (prompt injection/jailbreak) и
  `output-privacy` (PII: email, телефон, имя, карта).
- HTTP-контракт: `GET /health-check`, `GET /guards`,
  `POST /guards/{name}/validate` с `{"llm_output": "..."}`; ответ
  `ValidationOutcome` (`validationPassed`, `validatedOutput`).
- Контейнер слушает `0.0.0.0:8000` через официальный factory-запуск
  `uvicorn --factory guardrails_api.app:create_app`; CLI `guardrails-api start`
  не подходит, потому что биндится на localhost.
- Go-агент вызывает `/guards/{name}/validate` по HTTP; `WALL_BASE_URL` и порт
  `5000` не используются. При недоступной стене — fail-closed `503`.
- Compose: healthcheck по HTTP 200 от `/health-check`, без `wget` в
  `python:3.12-slim` (использовать Python-`urllib` или добавить `curl`).

## Спека

Capability `external-defense`: guards, HTTP-контракт, порт `8000`, fail-closed,
healthcheck, зависимости и factory-запуск.

## Проверка

- `docker compose build wall` — успешно, `pip check` чистый.
- `docker compose up -d wall` → `GET /health-check` возвращает 200,
  `GET /guards` содержит `input-defense` и `output-privacy`.
- Легитимный текст проходит `validationPassed=true`; prompt injection и PII
  дают `validationPassed=false` (или исправленный `validatedOutput`).
- Агент в пресете 3/4 ходит через стену; при остановленной стене получает
  `503`, а не идёт напрямую в Ollama.
- Результаты сохраняются в `reports/<run-id>/`.

## Изменения

- `dz5/security-wall/config.py` — guards.
- `dz5/security-wall/Dockerfile`, `requirements.txt` — сервер и зависимости.
- `dz5/security-wall/main.py` — удалить после перехода на Guardrails Server.
- `dz5/agent/` — клиент `/guards/{name}/validate`.
- `dz5/infra/compose.yaml` — сервис `wall` на `:8000`.

## Impact

- Новый сервис; Ollama и БД не затрагиваются.
- Задержка и память стены добавляются к пресетам 3/4; это измеряется harness'ом,
  а не декларируется.
