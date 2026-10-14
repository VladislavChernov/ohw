# Tasks: add-abliterated-llm

## 1. Образ и entrypoint

- [ ] `infra/ollama/Dockerfile`: `ollama/ollama:0.33.3`, копирование
      `entrypoint.sh`, `ENV OLLAMA_MODEL=qwen2.5:1.5b-instruct-q8_0`.
- [ ] `infra/ollama/entrypoint.sh`: поднять `ollama serve`, дождаться API,
      однократно `ollama pull` модели, держать сервер на переднем плане.

## 2. Сервис в compose

- [ ] Сервис `ollama`, volume `ollama_data`, порт `11434` (для отладки).
- [ ] `deploy.resources.limits.memory` (L3 / LLM06).
- [ ] Healthcheck `ollama show <model>`.

## 3. Проверка (прогнать вживую)

- [ ] `docker compose build ollama` — успешно.
- [ ] `docker compose up -d ollama` — сервер поднимается, модель тянутся.
- [ ] `ollama ls` показывает модель; `POST /api/generate` отвечает.
- [ ] Healthcheck `healthy`.

## 4. Отметка выполнения

- [ ] Все проверки §3 зелёные; галочки по факту.
