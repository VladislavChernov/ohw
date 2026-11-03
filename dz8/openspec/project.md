# Project: llm-degradation-lab

Учебный проект (Otus, ДЗ8): проверка локальной LLM на галлюцинации,
несогласованность и деградацию. Код живёт в `dz8`, UI — отдельной страницей
в рабочем месте Dz2, стенд — общий `infra/`.

## Стек

- Python 3.13, пакетный менеджер **uv**, lock-файл коммитится.
- HTTP: `httpx`; схемы: `pydantic`; тесты: `pytest`; UI: `streamlit`.
- Зависимость `ai-testcase-generator` — path-зависимость на `../dz2`, не
  копия кода.
- Инференс: существующий контейнер `ohw-ollama`, модель `llama3`.
- UI: существующий `dz2-ui-1` на 8501; `dz8` монтируется в него.

## Конвенции

- Спеки в формате OpenSpec: `openspec/changes/<change>/{proposal,design,tasks}.md`
  + дельты в `specs/<capability>/spec.md`.
- Требования — EARS: `SHALL` + сценарии `WHEN/THEN`.
- Код и комментарии — на английском; документация (README, TZ, спеки) — на
  русском.
- CLI exit codes: 0 ок, 2 аргументы/конфиг/вход, 3 Ollama недоступна,
  4 провал проверки/артефакта, 5 регрессия при `--fail-on-regression`.
- Один и тот же вход обязан проходить детерминированный нормализатор до
  сборки промпта.
- `prompt_sha256 = sha256(user_prompt.encode("utf-8"))`, как в Dz2.
- Отчёты — JSON (источник правды) + Markdown (представление).

## Окружение

- `OLLAMA_BASE_URL` — адрес Ollama (в Docker `http://ohw-ollama:11434`,
  с хоста `http://localhost:11434`).
- `OLLAMA_MODEL` — имя модели (по умолчанию `llama3`).
- `OLLAMA_TIMEOUT` — таймаут запроса (по умолчанию 600 с).
- `DZ2_PROMPT_DIR`, `DZ2_PRESETS_DIR`, `AI_TESTGEN_NORMALIZER_CONFIG` —
  ресурсы Dz2.
- `LLM_LAB_ROOT` — корень `dz8` в контейнере (`/opt/dz8`).
- `LLM_LAB_INPUT_DIR`, `LLM_LAB_OUTPUT_DIR` — вход и отчёты.

## Контейнеры

- Используем `infra/compose.yaml`: `ohw-ollama`, сеть `ohw_net`.
- Используем `infra/python`: `ohw-python:3.13`.
- Используем `dz2/compose.yaml`: `dz2-ui-1`.
- Новых постоянных контейнеров, сервисов и портов нет.
- `llama3` докачивается в `ohw-ollama`, второй Ollama не поднимается.
- Тесты идут в `dz2-ui-1` с временным venv в `/tmp`.

## Границы

Dz8 не меняет `dz2/src`, кроме UI-адаптера и тестов; не меняет
`dz2/prompts`, `dz2/presets`, `dz2/normalizer.toml`; не обучает модель и не
использует LLM-судью текста промпта как метрику деградации.
