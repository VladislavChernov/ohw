# Tasks: add-abliterated-llm

## 1. Образ и entrypoint

- [x] `infra/ollama/Dockerfile`: `ollama/ollama:0.40.4` (GPU, проверен локально),
      копирование `entrypoint.sh`,
      `ENV OLLAMA_MODEL=huihui_ai/llama3.2-abliterate:3b`.
      `Dockerfile_ll_ablitirated` в работу НЕ принят: `:latest`, лишний `curl`-слой,
      русскоязычные комментарии — нарушает стандарты (см. proposal).
- [x] `infra/ollama/entrypoint.sh`: поднять `ollama serve`, дождаться API,
      однократно `ollama pull` модели, держать сервер на переднем плане.

## 2. Сервис в compose

- [x] Сервис `ollama` (контейнер `ollama_target`), volume `ollama_data`,
      порт `11434` (для отладки).
- [x] `deploy.resources.limits.memory` (L3 / LLM06) + GPU reservation
      (`driver: nvidia, count: all`).
- [x] Healthcheck `ollama show <model>`.

## 3. Проверка (прогнать вживую)

- [ ] `docker compose build ollama` — успешно.
- [ ] `docker compose up -d ollama` — сервер поднимается, модель тянутся.
- [ ] `ollama ls` показывает модель; `POST /api/generate` отвечает.
- [ ] Healthcheck `healthy`.
- [ ] Старый образ/контейнер qwen2.5-abliterate удалён
      (`docker rmi dz5-ollama-abliterated:local`, volume при необходимости).

## 4. Отметка выполнения

- [ ] Все проверки §3 зелёные; галочки по факту.
