# Design: лаборатория глюков и деградации LLM

## 1. Контекст и ограничения

Dz8 — измерительный слой над стендом Dz2. Он не генерирует артефакты «для
продукта», а проверяет, насколько модель предсказуема, честна и устойчива.
Главные ограничения:

- вход фиксирован детерминированным нормализатором;
- промпт собирается внешним шаблоном Dz7/Dz2, а не моделью;
- сравнивать можно только одинаковые `prompt_sha256`;
- один прогон ничего не доказывает, нужны серии;
- судья текста не используется как основная метрика;
- `dz2` меняется только в UI-адаптере;
- новых постоянных контейнеров нет.

## 2. Компоненты и зависимости

```
┌──────────────────────── dz2-ui-1 (8501) ─────────────────────┐
│                                                              │
│  app.py                                                      │
│   ├─ sidebar: «Генерация артефактов» ──► существующий UI     │
│   └─ sidebar: «Проверка модели» ──────► dz8 page             │
│                                            │                 │
│  ┌──────────────────────── dz8 ────────────┼───────────────┐ │
│  │                                         ▼               │ │
│  │  ui/page.py ──► runner.py ──► ai_testgen.service        │ │
│  │       │              │              │                   │ │
│  │       │              │              ▼                   │ │
│  │       │              │       ai_testgen.validator       │ │
│  │       │              ▼                   │              │ │
│  │       │        metrics.py ◄──────────────┘              │ │
│  │       │              │                                  │ │
│  │       ├─ probes/hallucinations.py                       │ │
│  │       ├─ probes/consistency.py                          │ │
│  │       ├─ probes/canary.py                               │ │
│  │       ├─ probes/api_contract.py                         │ │
│  │       └─ degradation.py ──► report.py ──► output/       │ │
│  │                                                         │ │
│  │  normalizer_bridge.py ──► ai_testgen.normalizer         │ │
│  │  ollama.py ──► httpx ──► ohw-ollama:11434               │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
```

Зависимости:

- `ai-testcase-generator` (path `../dz2`, editable);
- `httpx`, `pydantic`;
- `streamlit` — extra `ui`;
- `pytest`, `pytest-mock`, `ruff`, `mypy` — dev.

Почему path-зависимость, а не копия: нормализатор, шаблоны, пресеты и
валидаторы должны быть **теми же**, что в Dz2. Копия разъедется, и
`prompt_sha256` перестанет быть сравнимым.

## 3. Интеграция с Dz2

### 3.1. Переключатель страниц

В `dz2/src/ai_testgen/ui/app.py`:

```python
PAGE_GENERATION = "Генерация артефактов"
PAGE_MODEL_CHECK = "Проверка модели"


def main() -> None:
    st.set_page_config(
        page_title="Генератор тестов по требованиям", layout="wide"
    )
    page = st.sidebar.radio(
        "Страница",
        (PAGE_GENERATION, PAGE_MODEL_CHECK),
        key="workspace_page",
    )
    if page == PAGE_MODEL_CHECK:
        _render_model_check()
        return
    _render()


def _render_model_check() -> None:
    try:
        from llm_degradation.ui.page import render_page
    except ImportError as exc:
        st.error("Страница проверки модели недоступна: dz8 не смонтирован.")
        st.code("docker compose -f dz2/compose.yaml up -d --no-build ui")
        with st.expander("Подробности"):
            st.exception(exc)
        return
    render_page()
```

Свойства:

- импорт внутри функции — отсутствие `dz8` не ломает старт;
- `render_page()` не вызывает `st.set_page_config`;
- widget keys Dz8 — с префиксом `lab_`;
- страница генерации не меняется;
- ошибка — сообщение с командой, не traceback на всю страницу.

### 3.2. Mount в compose

`dz2/compose.yaml`, сервис `ui`:

```yaml
environment:
  PYTHONPATH: /opt/dz8/src
  LLM_LAB_ROOT: /opt/dz8
  LLM_LAB_INPUT_DIR: /data/dz8-input
  LLM_LAB_OUTPUT_DIR: /data/dz8-output
volumes:
  - ../dz2:/opt/dz2:ro
  - ../dz8:/opt/dz8
  - ../dz8/input:/data/dz8-input:ro
  - ../dz8/output:/data/dz8-output
```

- `../dz2:/opt/dz2:ro` нужен для `uv lock`/`uv sync` в тестах:
  path-зависимость `../dz2` внутри `/opt/dz8` указывает на `/opt/dz2`.
- `../dz8` — код, конфиг, golden; writable, чтобы `uv lock` и кэши
  `pytest`/`ruff`/`mypy` могли писать. `.gitignore` их исключает.
- `../dz8/output` — отдельный writable mount для отчётов.
- `PYTHONPATH` добавляет `/opt/dz8/src`, не заменяя site-packages.
- Без mount UI работает, страница показывает подсказку.

### 3.3. Контейнеры

Используем:

| Компонент | Источник | Контейнер |
|---|---|---|
| Ollama | `infra/compose.yaml`, профиль `ollama` | `ohw-ollama` |
| Python base | `infra/python/Dockerfile` | `ohw-python:3.13` |
| UI | `dz2/compose.yaml` | `dz2-ui-1` |

Новых постоянных контейнеров нет. `llama3` докачивается в `ohw-ollama`.
Тесты и CLI идут в `dz2-ui-1`; временное venv — в `/tmp/dz8-venv`.

## 4. Поток данных

### 4.1. Нормализация

```
bytes ──decode_bytes──► text ──split_front_matter──► body
     ──normalize_text(body, url, encoding, settings)──► NormalizedInput
```

- `decode_bytes`, `normalize_text`, `load_settings` — из
  `ai_testgen.normalizer`.
- Порядок поиска настроек: `--normalizer-config` →
  `AI_TESTGEN_NORMALIZER_CONFIG` → `./normalizer.toml` → defaults.
- `NormalizedInput` хранит исходные байты (sha256), кодировку, канонический
  текст, заголовок, URL, `BR-N`, настройки, предупреждения.
- Идемпотентность проверяется повторной нормализацией; расхождение — код 2.

### 4.2. Промпт

```
handler = ai_testgen.handlers.get_handler(artifact_type, prompt_dir)
system = handler.build_system()
user = handler.build_user(doc, count, extras=presets, testcases=index)
prompt_sha256 = sha256(user.encode("utf-8")).hexdigest()
```

- `RequirementDoc` из `NormalizedInput`: `content=text`, `url`, `type`, `path`.
- Пресеты — `ai_testgen.presets.select_extras`.
- Нет `{extra}` при выбранном пресете — `PromptError`, код 2.
- `testcases` (индекс `TC-NN`) передаётся только если он есть; для
  `coveragematrix` без индекса `None` — probe `fake_tc` ловит выдуманные ID.
- `prompt.txt` — system + user, ровно то, что ушло в модель.

### 4.3. Генерация

```
RecordingClient.chat(system, user, temperature, json_mode)
  └─► LabOllamaClient.chat(..., seed, num_ctx, num_predict)
        └─► POST /api/chat {model, messages, stream:false, options:{...}}
  └─► ChatCall(request, response, latency_ms, raw_sha256)

ai_testgen.service.generate_artifact(
    client, handler, doc, options, user_prompt=user
)
  └─► prompt → chat → validate → retry → GeneratedArtifact | GenerationError
```

`RecordingClient` — тонкая обёртка с сигнатурой
`OllamaClient.chat`. Она нужна, потому что `GeneratedArtifact` не хранит
сырой ответ и все попытки. Цикл retry остаётся в
`ai_testgen.service.generate_artifact`.

`GenerationOptions` Dz2: `count`, `temperature`, `max_retries`. `seed`,
`num_ctx`, `num_predict` фиксируются в `RecordingClient` на серию.

### 4.4. Метрики

`artifact_metrics` — чистая функция:

1. размеры и хеш;
2. testcases: JSON + `ai_testgen.validator.validate`;
3. Markdown: `validate_markdown` с секциями типа;
4. ID, URL, заголовки, секции, обрезка, ложные вердикты;
5. `ArtifactMetrics`.

Не зависит от модели, не ходит в сеть, покрывается unit-тестами.

## 5. Probe-ы

### 5.1. Галлюцинации

Probe — проверяемая гипотеза с детерминированным критерием:

```json
{
  "id": "H-04",
  "kind": "traceability",
  "input": "golden/inputs/catalog.md",
  "artifact_type": "testcases",
  "count": 5,
  "prompt": null,
  "expected": {},
  "forbidden_terms": [],
  "critical": true,
  "checks": ["br_ids_subset"]
}
```

Два режима:

- `prompt != null` — прямой вопрос, проверка регулярками/маркерами;
- `artifact_type != null` — генерация артефакта, проверка метрик.

Критерии:

| kind | Проверка |
|---|---|
| `count` | ровно одно число равно `expected.number` |
| `missing_feature` | `forbidden_terms` отсутствуют; есть отказ |
| `missing_url` | `urls_invented == []`; есть отказ |
| `entity` | `forbidden_terms` отсутствуют |
| `traceability` | `br_invented == []`; `requirement` непустые |
| `execution_claims` | `claims_execution_results == false` |
| `fake_tc` | все `TC-NN` в индексе; нет дублей |
| `numeric` | нет валютных сумм; есть отказ |

Отрицание: при `allow_negated=true` вхождение forbidden-термина в радиусе
80 символов от `нет`, `не`, `отсутствует`, `не должен`, `without`, `no `
игнорируется.

Вердикт — по `critical` и `hallucination_rate`; подробности в спеке.

### 5.2. Согласованность

Серия `N` прогонов; каждый — `RunRecord` с индексом, временем, latency,
attempts, raw sha, метриками, ошибкой. Сохраняются `request.json` и
`response.txt`.

Сводка: доли (`pass@1`, `pass_all`, `valid_json_rate`, `count_match_rate`),
средние/минимумы (`br_coverage_mean`, `tc_jaccard_mean`),
`self_consistency`, `unique_artifacts`, `identical_runs`.

Jaccard:

```python
def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)
```

Попарно для всех `i < j`; среднее по парам. Пусто с одной стороны — 0.0.

Почему не байтовое сравнение: при `temperature=0.7` два корректных ответа
почти никогда не совпадут; сравниваются инварианты.

### 5.3. API

`probes/api_contract.py` работает с `httpx.Client` и принимает
`transport: httpx.BaseTransport | None` для `MockTransport`.

- `POST /api/chat` всегда `stream=false`; `stream=true` проверяется отдельно.
- `format=json` — парсинг `message.content`.
- 400/404 — по коду и `error`.
- Таймаут — `OllamaConnectionError`, не `httpx.ReadTimeout` наружу.
- Параллельные запросы — 3 потока, warning.
- `/api/ps` 404 — warning.

### 5.4. Канарейка

Промпт просит нормализовать документ и вернуть только Markdown; готовый
нормализованный текст не даётся. Правила формата — без примера с ID.

Разбор:

```python
ID_RE = re.compile(rf"^###\s+({prefix}-\d+)\s*$", re.MULTILINE)
URL_RE = re.compile(r"^\*\*URL:\*\*\s+(\S+)\s*$", re.MULTILINE)
TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
```

Тело — между `### ID` и следующим `###`/`##`. Токены:

```python
def tokens(text: str) -> set[str]:
    text = text.casefold()
    text = re.sub(r"[^\w\-]+", " ", text, flags=re.UNICODE)
    return {t for t in text.split() if t}
```

ID сравниваются как упорядоченный список: порядок — часть контракта.
`body_overlap_mean >= 0.85`: нормализатор не меняет формулировки; LLM,
переписавшая текст, уже подозрительна. 1.0 не требуется из-за пунктуации.

### 5.5. Деградация

Ячейка — модель + параметры. Baseline:

- живой прогон (`--baseline-model`);
- сохранённый отчёт (`--baseline-report`), берётся `summary`.

Проверка совместимости:

```python
assert baseline.prompt_sha256 == candidate.prompt_sha256
assert baseline.artifact_type == candidate.artifact_type
assert baseline.count == candidate.count
```

Не совпало — код 4.

Статистика:

- Wilson CI для долей;
- bootstrap 1000 ресэмплов, `random.Random(12345)`;
- Cohen's h;
- независимые выборки по умолчанию; `--paired` при совпадении seed-списка.

`runs >= 5` для вердикта; меньше — `insufficient_data`, отчёт всё равно
пишется. `--fail-on-regression` → код 5.

## 6. Отчёты

`report.json` — pydantic с `extra="forbid"`. Запись атомарная:

```python
tmp = path.with_suffix(".tmp")
tmp.write_text(json.dumps(...), encoding="utf-8")
os.replace(tmp, path)
```

`report.md` генерируется из JSON, не наоборот.

Каталог:

- `manifest.json` — окружение, вход, промпт, параметры;
- `normalized.md`;
- `prompt.txt`;
- `runs/run-NN/{request.json,response.txt,metrics.json}`;
- `report.json`, `report.md`.

Сырые ответы не попадают в Markdown целиком, только цитаты до 300 символов.

Каталог `output/` не растёт бесконечно: после записи отчёта `prune_reports`
удаляет старые каталоги с валидным `report.json`, оставляя последние
`reports.keep_runs` (по умолчанию 3; CLI `--keep-runs`, env
`LLM_LAB_KEEP_RUNS`). `golden/baseline/` не затрагивается.

## 7. CLI и UI

CLI — `argparse`, без Click/Typer: меньше зависимостей, проще тестировать
`main(argv) -> int`. Подкоманда возвращает `int`; `main` ловит доменные ошибки
и печатает JSON при `--json`.

UI — `ui/page.py`, `render_page()`. Все вызовы — те же функции, что CLI
(`run_hallucinations`, `run_consistency`, ...), не копия логики.

Состояние:

- `st.session_state["lab_run_id"]` — последний прогон;
- `st.session_state["lab_report"]` — последний отчёт;
- настройки подключения — в sidebar Dz8;
- тяжёлые объекты не сохраняются.

## 8. Ошибки

```python
class LabError(Exception): ...
class ConfigError(LabError): ...
class InputError(LabError): ...
class OllamaUnavailable(LabError): ...
class ModelMissing(LabError): ...
class ProbeFailed(LabError): ...
class ReportError(LabError): ...
```

| Исключение | Код |
|---|---|
| `ConfigError`, `InputError`, `ReportError` | 2 |
| `OllamaUnavailable`, `ModelMissing` | 3 |
| `ProbeFailed`, `GenerationError`, `CanaryParseError` | 4 |
| `RegressionDetected` | 5 |

`main` не печатает traceback без `--debug`; печатает `code`, `message`,
`hint`.

## 9. Тестовая стратегия

- Unit: без сети, `httpx.MockTransport`, записанные ответы, реальный
  нормализатор и валидаторы.
- Integration: маркер `integration`, skip без Ollama/модели.
- UI: `streamlit.testing.v1.AppTest` для `dz2` app и `dz8` page.
- Golden: фиксированные входы, ожидаемые инварианты, записанные ответы.
- Статистика: детерминированные тесты Wilson/bootstrap.

## 10. Риски и компромиссы

| Риск | Митигация |
|---|---|
| `dz2` зависит от `dz8` | импорт внутри функции; сообщение при отсутствии; тест без dz8 |
| `llama3` медленная на CPU | таймаут 600 с, integration-маркер, малые smoke-серии |
| `seed` не гарантирует повторяемость | сравнивать инварианты, не байты; предупреждение |
| 7B часто галлюцинирует | порог 0.25, critical отдельно; не выдавать pass за идеал |
| Валидатор Dz2 мягкий | метрики дополняют его |
| Разные `num_ctx` меняют возможность ответить | фиксировать; сравнивать при одинаковых параметрах |
| UI блокирует Streamlit | `st.status`, предупреждение, один запуск |
| Path-зависимость `../dz2` в контейнере | mount `../dz2:/opt/dz2:ro` |
| Большие raw-ответы | лимит 1 МБ, `truncated=true` |
| Случайное изменение `dz2` | DoD `git diff -- dz2`, ревью |

## 11. Внедрение

1. Каркас пакета, `pyproject.toml`, `config.toml`, `uv.lock`.
2. Нормализация и метрики.
3. API и runner.
4. Probe-ы.
5. Канарейка.
6. Деградация и отчёты.
7. CLI.
8. UI page.
9. Dz2 adapter + compose mounts.
10. Golden и integration.
11. README с реальными числами.

Каждая фаза заканчивается `pytest -m "not integration"`. Переход к UI — после
зелёных CLI и метрик.
