# Proposal: локальный abliterated LLM (инференс стенда)

## Почему

Стенду нужна своя модель, у которой **намеренно отключена внутренняя защита**
(safety alignment). Без этого baseline-пресет не может честно показать
уязвимость: модель сама блокировала бы часть атак, и эффект нашего внешнего
файрвола был бы неотделим от защиты самой модели. Поэтому берём
abliterated-инструкт модель и поднимаем её **локально в `dz5`**, а не в общем
`infra/ollama` — чтобы не «травить» общий модельный volume/сеть других ДЗ.

Выбранная модель: `huihui_ai/llama3.2-abliterate:3b` — genuine
abliterated (refusal снят; Llama 3.2 3B, Q4_K_M, ~2 ГБ), тянется из библиотеки
Ollama одним `ollama pull`. Работает на **NVIDIA GPU** (проверено локально:
Docker Desktop, Ollama 0.40.4, `--gpus all`). Старая
`huihui_ai/qwen2.5-abliterate:1.5b-instruct` снята — слишком слабая для роли
«жертвы» стенда.

## Что делаем

- Сервис `ollama` (контейнер `ollama_target`, порт `11434`) на образе
  `ollama/ollama:0.40.4` (тег зафиксирован, не `latest`) с entrypoint, который
  поднимает сервер и однократно подтягивает модель
  `huihui_ai/llama3.2-abliterate:3b` (genuine abliterated).
- Модель переопределяется через env `OLLAMA_MODEL`.
- Порт `11434` публикуется только для локальной отладки; внутри compose сервис
  доступен как `ollama:11434`.
- Healthcheck: `ollama show <model>` — готовность модели.
- **L3 (Unbounded Consumption, LLM06):** `deploy.resources.limits.memory`
  ограничивает потребление, чтобы DoS/бесконечная генерация не уронили хост.

## Спека

Новая capability `abliterated-llm`: образ, модель, healthcheck, лимит памяти,
отсутствие связи с общим `ohw-ollama`.

## Проверка

- `docker compose build ollama` собирается.
- `docker compose up -d ollama` поднимает сервер и тянет модель; `ollama ls`
  показывает модель; `POST /api/generate` отвечает.
- Healthcheck переходит в `healthy`.

## Изменения

- `infra/ollama/Dockerfile` + `infra/ollama/entrypoint.sh`.
- Сервис `ollama`, volume `ollama_data` в `infra/compose.yaml`.

## Impact

- Новый автономный сервис; общий `infra/ollama` и `ohw_net` не затрагиваются.
