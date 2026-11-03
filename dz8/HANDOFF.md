# HANDOFF: инструкция для локальной кодинг-модели

Ты реализуешь ДЗ8 в `D:\Otus\ohw\dz8`. Это отдельная домашка, но UI —
страница внутри существующего рабочего места Dz2, а стенд — общий `infra/`.
Ниже — порядок чтения, жёсткие правила и порядок работы.

## 1. Порядок чтения

1. `dz8/TZ.md` — полное ТЗ.
2. `dz8/openspec/project.md` — конвенции.
3. `dz8/openspec/changes/add-llm-degradation-lab/proposal.md` — зачем.
4. `dz8/openspec/changes/add-llm-degradation-lab/design.md` — как.
5. `dz8/openspec/changes/add-llm-degradation-lab/specs/llm-degradation-lab/spec.md`.
6. `dz8/openspec/changes/add-llm-degradation-lab/specs/normalizer-canary/spec.md`.
7. `dz8/openspec/changes/add-llm-degradation-lab/specs/ollama-api-contract/spec.md`.
8. `dz8/openspec/changes/add-llm-degradation-lab/tasks.md` — что делать.
9. `dz8/README.md` — как это выглядит для человека.

Если спека и код расходятся — остановись и напиши вопрос, не выбирай сам.

## 2. Что менять можно

### В `dz8/`

Всё новое:

- `pyproject.toml`, `uv.lock`, `config.toml`;
- `src/llm_degradation/**`;
- `tests/**`;
- `golden/**`, `input/**`, `output/**`;
- `README.md`, `TZ.md`, `HANDOFF.md`, `openspec/**`.

### В `dz2/`

Только тонкий адаптер:

- `src/ai_testgen/ui/app.py` — radio страниц и защищённый импорт;
- `compose.yaml` — mount `dz8` и env для сервиса `ui`;
- `tests/test_ui_app.py` — тест переключателя;
- `README.md` — раздел про страницу.

### Не менять

- `dz2/src/ai_testgen/**`, кроме `ui/app.py`;
- `dz2/prompts/**`;
- `dz2/presets/**`;
- `dz2/normalizer.toml`;
- `dz2/Dockerfile` (если не согласовано отдельно);
- `dz2/scripts/**`;
- `dz7/**`;
- `infra/**`.

Проверка перед коммитом:

```powershell
git -C D:\Otus\ohw diff -- dz2/prompts dz2/presets dz2/normalizer.toml
# должно быть пусто
```

## 3. Контейнеры и infra

- Используем `infra/compose.yaml`: `ohw-ollama`, сеть `ohw_net`.
- Используем `infra/python`: `ohw-python:3.13`.
- Используем `dz2/compose.yaml`: `dz2-ui-1` на 8501.
- **Новых постоянных контейнеров нет.**
- Нет `dz8/Dockerfile`, `dz8/compose.yaml`, `llm-lab-ui`, порта 8502.
- `llama3` докачивается в `ohw-ollama`:
  `docker exec ohw-ollama ollama pull llama3`.
- CLI и тесты — в `dz2-ui-1`; временное venv — `/tmp/dz8-venv`.
- Не делать `ollama pull` в тестах и при старте UI.

Проверка:

```powershell
docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}"
docker exec ohw-ollama ollama list
```

Ожидание: `ohw-ollama` и `dz2-ui-1`, среди моделей `llama3`.

## 4. Порядок работы

1. Прочитай документы из раздела 1.
2. Проверь стенд и `llama3`.
3. Создай каркас `dz8` (фаза 1 в `tasks.md`).
4. Делай фазы 2–11 по порядку, после каждой:
   ```powershell
   docker compose exec ui sh -lc "cd /opt/dz8 && /tmp/dz8-venv/bin/pytest -m 'not integration'"
   ```
5. Только после зелёных CLI и метрик — UI-страница (фаза 12).
6. Затем адаптер Dz2 (фаза 13).
7. Golden и integration (фаза 14).
8. README с реальными числами, финальный прогон, commit/push.

Не перескакивай через фазы: UI не должен появляться раньше метрик.

## 5. Команды

### Стенд

```powershell
cd D:\Otus\ohw\dz2
.\scripts\up-gpu.ps1          # или .\scripts\up-cpu.ps1
docker exec ohw-ollama ollama pull llama3
docker exec ohw-ollama ollama list
```

### Временное venv и тесты

```powershell
cd D:\Otus\ohw\dz2

docker compose exec ui sh -lc "cd /opt/dz8 && uv lock"

docker compose exec -e UV_PROJECT_ENVIRONMENT=/tmp/dz8-venv ui sh -lc `
  "cd /opt/dz8 && uv sync --frozen --extra ui --dev"

docker compose exec ui sh -lc `
  "cd /opt/dz8 && /tmp/dz8-venv/bin/pytest -m 'not integration'"

docker compose exec ui sh -lc `
  "cd /opt/dz8 && OLLAMA_BASE_URL=http://ohw-ollama:11434 OLLAMA_MODEL=llama3 /tmp/dz8-venv/bin/pytest -m integration"

docker compose exec ui sh -lc `
  "cd /opt/dz8 && /tmp/dz8-venv/bin/ruff check src tests && /tmp/dz8-venv/bin/mypy src"
```

### CLI

```powershell
docker compose exec ui python -m llm_degradation health --model llama3 --json
docker compose exec ui python -m llm_degradation api-check --model llama3 --json
docker compose exec ui python -m llm_degradation hallucinations --model llama3 --runs 3 --json
docker compose exec ui python -m llm_degradation consistency --model llama3 --runs 10 --seed 42 --json
docker compose exec ui python -m llm_degradation canary --model llama3 --json
docker compose exec ui python -m llm_degradation degradation --baseline-model llama3 --candidate-model llama3 --runs 5 --json
```

### UI

```powershell
# после изменения compose
cd D:\Otus\ohw\dz2
docker compose up -d --no-build ui
# http://localhost:8501 -> sidebar «Страница» -> «Проверка модели»
```

## 6. Жёсткие правила кода

- Python 3.13, type hints, `from __future__ import annotations` где уместно.
- Код и комментарии — на английском; UI/документация — на русском.
- Никаких `print` в библиотеке: `logging`; CLI — в stderr.
- Никаких `shell=True`.
- Все HTTP-вызовы с таймаутом.
- Все входы — через нормализатор.
- `prompt_sha256` — только `sha256(user.encode("utf-8"))`.
- Не копировать нормализатор/валидаторы/шаблоны из Dz2 — импортировать.
- Не менять `dz2/prompts`, `dz2/presets`, `dz2/normalizer.toml`.
- Не использовать LLM-судью для деградации.
- Не писать baseline-числа руками.
- Не добавлять зависимости без необходимости.
- Все новые widget keys — с `lab_`.
- `render_page()` не вызывает `st.set_page_config`.

## 7. Тестовые правила

- Unit — без сети, `httpx.MockTransport`, записанные ответы.
- Integration — маркер `integration`, skip без Ollama/модели.
- Не мокать нормализатор.
- Не мокать валидаторы Dz2 в тестах метрик.
- Не вызывать `ollama pull` из тестов.
- Не писать `assert True`.
- Не поднимать порог, чтобы скрыть падение.
- Минимум 60 unit-тестов.
- `ruff` и `mypy` — чисто.

## 8. Definition of Done

Смотри `TZ.md`, раздел 17. Кратко:

- `llama3` в `ohw-ollama`;
- `health`/`api-check` → 0;
- `hallucinations`/`consistency`/`canary`/`degradation` пишут отчёты;
- UI 8501: страница «Проверка модели», 7 вкладок;
- UI 8501: «Генерация артефактов» работает;
- unit ≥ 60 passed, integration зелёный/ skip с причиной;
- ruff/mypy чисто;
- `dz2` изменён только в UI-адаптере, compose, тестах, README;
- новых постоянных контейнеров нет;
- commit/push в `origin/normalizer`.

## 9. Чего не делать

- Не создавать `dz8/Dockerfile`, `dz8/compose.yaml`, сервис `lab`, порт 8502.
- Не поднимать второй Ollama.
- Не менять `dz2/src`, кроме UI-адаптера.
- Не менять `dz2/prompts`, `dz2/presets`, `dz2/normalizer.toml`.
- Не переносить код Dz2 в Dz8 копированием.
- Не использовать судью текста как основную метрику.
- Не автоматизировать `ollama pull`.
- Не коммитить `dz8/output/`.
- Не писать «всё работает», если integration-тесты не запускались.

## 10. Финальный отчёт

В ответе укажи:

1. созданные/изменённые файлы;
2. команды, которыми проверял;
3. результаты `pytest`, `ruff`, `mypy`;
4. реальные выводы `health`, `api-check`, `hallucinations`, `consistency`,
   `canary`, `degradation`;
5. какие пункты DoD не выполнены и почему;
6. открытые вопросы.

Если какой-то пункт заблокирован (например, Docker не запущен), напиши это
явно и не помечай пункт выполненным.
