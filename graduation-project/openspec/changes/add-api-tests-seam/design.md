# Дизайн: шов API-тестов

## Контекст

У выпускного проекта пока нет HTTP API: интерфейс — CLI, артефакты — файлы.
ДЗ3 advanced исполняет HTTP-запросы по JSON-плану, значит, для реального
прогона понадобится целевой сервис (свой фасад или заглушка). Шов не требует
его сегодня: порт принимает `base_url` и работает с любым адаптером.

## Граница

```text
workflow ──▶ ApiTestRunner (Protocol) ──▶ ApiTestReport
                    │
                    ├── DisabledApiTestRunner   (сейчас)
                    └── Dz3AdvancedRunner       (потом)
```

Порт возвращает **готовый отчёт**, а не план. Внутренние типы ДЗ3 (JSON-план,
шаги, проверки) остаются в адаптере и не протекают в `models.py`, отчёт или
графики.

```text
src/autotests/
├── __init__.py
├── models.py      # ApiTestRequest, ApiCaseResult, ApiTestReport
├── ports.py       # ApiTestRunner, ApiTestError
├── disabled.py    # DisabledApiTestRunner
└── service.py     # выбор раннера, запуск, запись api-tests.json
```

### Контракт

```python
@dataclass(frozen=True)
class ApiTestRequest:
    base_url: str
    contract_path: Path | None
    cases: int
    timeout: float
    model: str

@dataclass
class ApiCaseResult:
    name: str
    method: str
    path: str
    ok: bool
    status_code: int | None
    duration_ms: float
    error: str | None

@dataclass
class ApiTestReport:
    runner: str
    base_url: str
    model: str
    generated_at: str
    total: int
    passed: int
    failed: int
    duration_ms: float
    cases: list[ApiCaseResult]
    errors: list[str]

class ApiTestRunner(Protocol):
    name: str
    def run(self, request: ApiTestRequest) -> ApiTestReport: ...
```

`total = passed + failed`; `failed` включает кейсы с ошибкой транспорта.
`duration_ms` — суммарное время исполнения.

## Поток данных

```text
--api-tests --api-base-url URL [--api-contract FILE]
      │
      ▼
ApiTestRunner.run(request)
      │
      ▼
output/api-tests.json
      │
      ▼
quality-metrics.json: необязательный ключ "api_tests"
      │
      ▼
report.md: раздел "## API-тесты"
```

Фазы: `generate → assess → visualize → api-tests → report`. `api-tests`
выполняется только при явном `--api-tests`. `report` читает
`output/api-tests.json`, если файл существует.

## CLI

| Флаг | По умолчанию | Смысл |
|---|---|---|
| `--api-tests` | выкл. | выполнить фазу API-тестов |
| `--api-base-url` | env `API_TEST_BASE_URL` | базовый URL целевого API |
| `--api-contract` | — | файл контракта/описания API |
| `--api-cases` | `5` | сколько кейсов запросить у генератора |

Если указан `--api-tests`, но нет `--api-base-url` или раннер не подключён —
код 2 с сообщением, что именно отсутствует.

## Ошибки и коды возврата

| Ситуация | Поведение |
|---|---|
| нет `--api-base-url` | код 2 |
| раннер не подключён | код 2, сообщение «адаптер API-тестов не подключён» |
| ошибка исполнения одного кейса | кейс попадает в `cases` и `errors`, прогон продолжается |
| ошибка записи `api-tests.json` | код 4 |
| `--api-tests` не указан | фаза не выполняется, файл не создаётся |

Существующие коды 0/2/3/4/5/130 не меняются.

## Отчёт и метрики

- `api-tests.json` — самостоятельный источник правды раздела.
- В `quality-metrics.json` добавляется необязательный ключ `api_tests`;
  при отсутствии фазы ключа нет, схема прежняя.
- `report.md` получает раздел `## API-тесты` только при наличии данных:
  URL, раннер, всего/успешно/провалено, длительность, таблица кейсов, ошибки.
- `overall` и четыре метрики документации **не меняются**: исполнительные
  результаты нельзя смешивать с оценкой текста.

## План подключения ДЗ3 advanced

1. Вендорить `plan.py`, `core.py`, `docs.py`, `generator.py`, `report.py`
   в `src/autotests/dz3_advanced/`.
2. Заменить `ohw_kit.ollama_client` на `src/generator/ollama_client.py` и
   `ohw_kit.jsonreply` на существующий паттерн validate/retry.
3. Перенести только `ohw_kit.checks` (JSONPath + evaluate) как локальный модуль.
4. Реализовать `Dz3AdvancedRunner.run()`: контракт → план → исполнение →
   `ApiTestReport`.
5. Поднять целевой API (свой фасад `src/api/` или заглушка) и добавить e2e
   с маркером `integration`.

## Тестирование

- Unit: фейковый раннер возвращает предсказуемый отчёт; проверяются JSON,
  раздел отчёта, отсутствие изменений без флага, коды возврата.
- Интеграционный тест появится вместе с адаптером, не раньше.

## Отклонённые альтернативы

- **Передавать план через шов.** Протекают типы ДЗ3, ломается при смене
  формата; отчёт как контракт стабильнее.
- **Сразу вендорить ДЗ3.** Нет времени, нет целевого API, высокий риск.
- **ДЗ6 в том же шве.** AST-анализ работает с pytest-кодом, а advanced даёт
  JSON-план — контракты не совпадают. Отдельный порт позже.
