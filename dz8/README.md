# Dz8 — проверка глюков и деградации LLM

Домашняя работа: проверить локальную LLM на галлюцинации, несогласованность и
деградацию, используя **существующий стенд** `ohw-ollama`, детерминированный
нормализатор и промпты/пресеты Dz2/Dz7.

Проверки встраиваются **отдельной страницей в рабочее место Dz2**:
`http://localhost:8501` → sidebar «Страница» → **«Проверка модели»**.
Нового приложения, порта и контейнера нет.

| Страница / вкладка | Что проверяет |
|---|---|
| Генерация артефактов | существующая страница Dz2, не меняется |
| Проверка модели → Стенд | доступность Ollama, наличие `llama3`, digest, версия |
| Проверка модели → Галлюцинации | выдуманные требования, URL, сущности, результаты прогонов |
| Проверка модели → Согласованность | стабильность артефактов между повторами |
| Проверка модели → API | контракт Ollama HTTP API и коды ошибок |
| Проверка модели → Канарейка | LLM-нормализация против детерминированного нормализатора |
| Проверка модели → Деградация | baseline vs candidate, серии, pass rate, доверительные интервалы |
| Проверка модели → Отчёты | сохранённые JSON/Markdown-отчёты, сравнение |

> Это документация. Код пишет локальная кодинг-модель по [`TZ.md`](TZ.md) и
> [`HANDOFF.md`](HANDOFF.md). Команды ниже — целевой контракт.

## Контейнеры и infra

Да, используем общий каталог `infra/` и **не плодим новые контейнеры**:

| Что | Откуда | Как используется |
|---|---|---|
| `ohw-ollama` | `infra/compose.yaml`, профиль `ollama` | единственный Ollama, порт 11434 |
| `ohw-python:3.13` | `infra/python/Dockerfile` | базовый образ для `ohw/dz2:local` |
| `ohw/dz2:local` | `dz2/Dockerfile` | образ рабочего места |
| `dz2-ui-1` | `dz2/compose.yaml`, сервис `ui` | существующий UI на 8501, в него монтируется `dz8` |

Новых постоянных сервисов нет:

- нет второго Ollama;
- нет контейнера `dz8-lab`;
- нет порта 8502;
- нет `dz8/Dockerfile` и `dz8/compose.yaml`.

`llama3` просто докачивается в существующий `ohw-ollama`:

```powershell
cd D:\Otus\ohw\dz2
.\scripts\up-gpu.ps1          # или .\scripts\up-cpu.ps1
docker exec ohw-ollama ollama pull llama3
docker exec ohw-ollama ollama list
```

Для тестов используется тот же контейнер `dz2-ui-1`: в нём создаётся
временное venv в `/tmp` (см. [`TZ.md`](TZ.md), раздел 14). Одноразовый
`docker run --rm` допустим только как аварийный вариант; постоянных
контейнеров он не создаёт.

## Зачем отдельная страница, а не отдельное приложение

- Рабочее место уже есть: требования → промпт → генерация → оценка.
- Проверки используют тот же нормализатор, шаблоны, пресеты и валидаторы.
- Один UI и один порт — меньше поводов запутаться на демо.
- Код проверок остаётся в `dz8`; `dz2` получает только тонкий переключатель
  страниц и mount в compose.

Плата: `dz2` теперь необязательно зависит от `dz8`. Поэтому импорт страницы
защищён: если `dz8` не смонтирован, страница показывает подсказку, а
«Генерация артефактов» работает как раньше.

## Что считается результатом

1. **Галлюцинации** — детерминированные проверки артефакта: модель не должна
   выдумывать `BR-N`, URL, сущности и результаты прогонов, которых нет во
   входных требованиях.
2. **Согласованность** — серия из N прогонов одного промпта: структура,
   количество кейсов, покрытие требований, пересечение `TC-id` и заголовков.
3. **API** — контракт `/api/version`, `/api/tags`, `/api/show`, `/api/chat`:
   коды, схема JSON, `format=json`, `seed`, `num_ctx`, `num_predict`, ошибки
   неизвестной модели и битого запроса.
4. **Канарейка** — модель просят нормализовать тот же документ, что и
   детерминированный нормализатор; расхождение ID, URL и текста — ранний
   сигнал глюка. Результат канарейки **не** попадает в генерацию.
5. **Деградация** — baseline и candidate на одном `prompt_sha256`;
   сравниваются pass@1, валидность, покрытие `BR`, выдуманные ID, задержка,
   стабильность; Wilson CI для долей и bootstrap CI для средних.

Подробные определения, пороги и формулы — в [`TZ.md`](TZ.md), разделы 5–9.

## Быстрый старт (целевой)

```powershell
cd D:\Otus\ohw\dz2

# 1. Поднять существующий стенд (ohw-ollama + dz2-ui-1)
.\scripts\up-gpu.ps1          # или .\scripts\up-cpu.ps1

# 2. Докачать llama3 в существующий Ollama
docker exec ohw-ollama ollama pull llama3

# 3. UI: http://localhost:8501 -> sidebar «Страница» -> «Проверка модели»
```

CLI работает внутри существующего контейнера `dz2-ui-1`:

```powershell
cd D:\Otus\ohw\dz2

docker compose exec ui python -m llm_degradation health --model llama3 --json
docker compose exec ui python -m llm_degradation api-check --model llama3 --json
docker compose exec ui python -m llm_degradation hallucinations --model llama3 --runs 3 --json
docker compose exec ui python -m llm_degradation consistency --model llama3 --runs 10 --seed 42 --json
docker compose exec ui python -m llm_degradation canary --model llama3 --json
docker compose exec ui python -m llm_degradation degradation --baseline-model llama3 --candidate-model llama3 --runs 5 --json
```

Тесты — в том же контейнере, без нового сервиса:

```powershell
cd D:\Otus\ohw\dz2

# Один раз: lock-файл и временное venv в /tmp внутри работающего ui
docker compose exec ui sh -lc "cd /opt/dz8 && uv lock"
docker compose exec -e UV_PROJECT_ENVIRONMENT=/tmp/dz8-venv ui sh -lc `
  "cd /opt/dz8 && uv sync --frozen --extra ui --dev"

# Unit-тесты без Ollama
docker compose exec ui sh -lc `
  "cd /opt/dz8 && /tmp/dz8-venv/bin/pytest -m 'not integration'"

# Integration-тесты с живым Ollama
docker compose exec ui sh -lc `
  "cd /opt/dz8 && OLLAMA_BASE_URL=http://ohw-ollama:11434 OLLAMA_MODEL=llama3 /tmp/dz8-venv/bin/pytest -m integration"
```

## Документация

| Файл | Для кого | Что внутри |
|---|---|---|
| [`TZ.md`](TZ.md) | человек и кодинг-модель | полное ТЗ: архитектура, интерфейсы, метрики, CLI, UI, тесты, DoD |
| [`HANDOFF.md`](HANDOFF.md) | локальная кодинг-модель | порядок чтения, правила, запреты, порядок реализации |
| [`openspec/project.md`](openspec/project.md) | все | конвенции Dz8 |
| [`openspec/changes/add-llm-degradation-lab/proposal.md`](openspec/changes/add-llm-degradation-lab/proposal.md) | все | зачем и что меняем |
| [`openspec/changes/add-llm-degradation-lab/design.md`](openspec/changes/add-llm-degradation-lab/design.md) | разработчик | архитектура и алгоритмы |
| [`openspec/changes/add-llm-degradation-lab/tasks.md`](openspec/changes/add-llm-degradation-lab/tasks.md) | кодинг-модель | чек-лист реализации по фазам |
| [`openspec/changes/add-llm-degradation-lab/specs/llm-degradation-lab/spec.md`](openspec/changes/add-llm-degradation-lab/specs/llm-degradation-lab/spec.md) | все | EARS-требования к приложению и странице |
| [`openspec/changes/add-llm-degradation-lab/specs/normalizer-canary/spec.md`](openspec/changes/add-llm-degradation-lab/specs/normalizer-canary/spec.md) | все | EARS-требования к канарейке |
| [`openspec/changes/add-llm-degradation-lab/specs/ollama-api-contract/spec.md`](openspec/changes/add-llm-degradation-lab/specs/ollama-api-contract/spec.md) | все | EARS-требования к проверке API |

## Соответствие заданию

| Требование из `hi level task.txt` | Где закрывается |
|---|---|
| Развернуть в Docker Ollama | существующий `ohw-ollama` из `infra/`; новых контейнеров нет |
| Загрузить модель `llama3` | `docker exec ohw-ollama ollama pull llama3`; проверка в «Стенд» и `health` |
| Python 3 + pytest | Python 3.13, `pytest`, `pytest-mock`, `httpx.MockTransport` |
| Тест на галлюцинации | `tests/test_hallucinations.py`, `llm-lab hallucinations`, вкладка «Галлюцинации» |
| Тест на согласованность | `tests/test_consistency.py`, `llm-lab consistency`, вкладка «Согласованность» |
| Тест на корректность API | `tests/test_api_contract.py`, `tests/integration/test_live_ollama.py`, `llm-lab api-check`, вкладка «API» |
| Проверка деградации и глюков | `llm-lab degradation`, `llm-lab canary`, вкладки «Деградация» и «Канарейка» |

## Ограничения

- Один прогон при `temperature=0.7` ничего не доказывает: для вывода о
  деградации нужны серии (рекомендуется 10–20 прогонов на ячейку).
- `seed` в Ollama не гарантирует байт-в-байт повторяемость между версиями,
  квантованиями и разным железом. Он фиксируется в отчёте, но не считается
  доказательством детерминизма.
- Судья текста промпта для деградации не используется: промпт фиксирован,
  поэтому меняться должна не его оценка, а метрики артефакта.
- `llama3` на CPU медленная; интеграционные тесты помечены `integration` и по
  умолчанию не запускаются.
- Страница проверки живёт в UI Dz2, но весь код проверок — в `dz8`. Если
  каталог `dz8` не смонтирован в `ui`, страница показывает подсказку, а
  генерация Dz2 продолжает работать.
- Dz8 не переписывает требования и не «улучшает» промпт: вход — канонический
  текст нормализатора, выход — измерение.

## Ветка и коммиты

Работа идёт в `D:\Otus\ohw`, ветка `normalizer`. Изменения Dz8 и тонкого
адаптера Dz2 коммитятся отдельными смысловыми коммитами и пушатся в
`origin/normalizer`. Код — на английском, документация и UI — на русском.
