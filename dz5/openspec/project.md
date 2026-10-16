# Project: dz5 — ИБ-стенд «Атака и защита ИИ-агентов» (OWASP LLM Top 10 2026)

Учебный проект (Otus, ДЗ5): изолированный демонстрационный стенд для проверки
безопасности LLM-агента. Стенд поднимает агента на базе локальной
**abliterated**-модели (без внутренней цензуры) и демонстрирует, как один и тот
же агент ведёт себя под атаками в **четырёх конфигурациях защиты**.

## Цель

Показать разницу в ответах модели при разных комбинациях защиты (0 / встроенная
/ внешняя / полная) на матрице атак **OWASP Top 10 for LLM Applications 2026**.

## Стек

- **Агент (Go):** `drujensen/aiagent` — сторонний фреймворк (TUI + web-UI на
  `:8080`), подключение к LLM через OpenAI-совместимый эндпоинт. **Версия
  фиксируется коммитом**, не `main`. Инструмент `db_query` для чтения SQLite.
- **Инференс (LLM):** локальный автономный `ollama/ollama:0.40.4` с моделью
  `huihui_ai/llama3.2-abliterate:3b` — **genuine abliterated** (refusal снят
  техникой abliteration; ~2 ГБ, Q4_K_M). Снятая цензура нужна специально, чтобы
  baseline честно демонстрировал уязвимость. Работает на **NVIDIA GPU**
  (проверено локально в Docker Desktop, `--gpus all`). **Не** общий
  `infra/ollama`.
  ⚠️ Проверено: теги `qwen2.5:1.5b-instruct-q8_0` и
  `huihui_ai/qwen2.5-abliterate:1.5b-instruct` — слишком слабые/тяжёлые для
  роли «жертвы» стенда; для стенда непригодны.
- **БД:** SQLite-файл в volume, образ CLI `keinos/sqlite3` (seed/восстановление),
  таблица с секретами и «чужими» строками.
- **Внешняя защита:** `security-wall` — FastAPI + `llm-guard` (прокси между
  агентом и LLM), на базовом образе `ohw-python:3.13`.
- **Атака:** `attack-runner` — `garak` + собственный раннер сценариев (Python).
- **Инфраструктура стенда:** своя `dz5/infra/` (compose, Dockerfile'ы, `.env`).
  Переиспользуется только общий базовый образ `ohw-python:3.13`.
- **Отчёты:** каталог `dz5/reports/` (см. ниже).

## Архитектура защиты: 4 конфигурации (матрица 2×2)

Переключение через env: `BUILTIN_GUARDS=on|off`, `EXTERNAL_GUARD=on|off`.
`L3` (Runtime-контроль) — предохранитель стенда, присутствует во всех режимах.

| Пресет | Промпт/ограничения агента | Внешний llm-guard | L3 (compose-лимиты) |
|---|:--:|:--:|:--:|
| 1 Baseline | ❌ | ❌ | ✅ floor |
| 2 Только встроенная | ✅ | ❌ | ✅ |
| 3 Только внешняя | ❌ | ✅ | ✅ |
| 4 Full Defense | ✅ | ✅ | ✅ |

`L3` реализован на уровне compose-лимитов (`deploy.resources.limits`: mem/CPU),
таймаута прокси и restart-policy (без context-cancellation в коде чужого
фреймворка). Концепция и обоснование — в `docs/llm_security_architecture.md`.

## Покрытие атак: OWASP Top 10 for LLM 2026

Актуальный список (опубл. 2026-08-04). Наши сценарии покрывают все 10:

| Код | Категория | Сценарий стенда |
|---|---|---|
| LLM01 | Prompt Injection | direct, indirect (отравл. строка), encoding (Base64/ROT13/Unicode/zero-width), jailbreak/DAN, 🩸 persona-override («Кожанный ублюдок») |
| LLM02 | Sensitive Information Disclosure | утечка секретов/PII из БД |
| LLM03 | Excessive Agency | tool-abuse: DROP/DELETE, чужие строки, эксфильтрация |
| LLM04 | Supply Chain | unpinned-зависимости, «отравленный» инструмент |
| LLM05 | Data and Model Poisoning | отравленная строка в SQLite |
| LLM06 | Unbounded Consumption | DoS: бесконечная генерация / исчерпание ресурсов |
| LLM07 | Misinformation | галлюцинация / уверенная ложь |
| LLM08 | Hidden Context Exposure | «repeat your init message» → утечка промпта/контекста |
| LLM09 | Vector and Embedding Weaknesses | аналог: отравл. retrieval-контекст из БД |
| LLM10 | Improper Output Handling | вредоносный SQL/HTML, который исполняет потребитель |

## Отчёты (`dz5/reports/`)

Каждый прогон — своя поддиректория `reports/<run-id>/` (`run-id` = timestamp) с
`manifest.json` (модель/тег, git-commit, пресеты) и `preset-<N>-<name>/`
(results + details). В корне `reports/` — сводный `latest.md` по самым свежим
отчётам (таблица «атака × пресет → вердикт + latency + CPU/RAM»).

## Основные соглашения

- Spec-first: изменения начинаются с OpenSpec-бандла (`openspec/changes/<slug>/`),
  код и тесты — после.
- Определение done = зелёные проверки (`pytest`/`ruff`/`mypy` для Python;
  `go vet` для агента), не «код смотрится ок». Галочки в `tasks.md` ставятся
  только после фактической проверки.
- Код и комментарии — на английском; документация (README, спеки) — на русском.
- Бандлы пишутся руками (CLI `openspec` отсутствует), по образцу `dz2`/`light-llm-engine`.
- Git: топик-ветка `dz5-agent-security-stand`, Conventional Commits на русском,
  в коммиты попадают **только** пути внутри `dz5/` (чужой WIP по `Dz4` не трогаем).

## Структура

- `docs/` — design-заметки (архитектура защиты; SSOT авторитет — в `openspec/`).
- `openspec/` — спеки изменений (по вехам).
- `infra/` — собственный инфра-слой стенда (compose, Dockerfile'ы, конфиги).
- `infra/scripts/` — кросс-платформенные демо-скрипты (`*.sh` + `*.ps1`),
  по образцу `Dz4/Demo/demo-cpu.*`.
- `reports/` — артефакты прогонов атак.

## Вехи (план работ)

1. `add-db-service` — SQLite + seed с ПДн-мишенями + восстановление. ✅
2. `add-abliterated-llm` — локальный контейнер abliterated-модели.
3. `add-agent-core` — Go-агент (`drujensen@<commit>`), tool `db_query`
   (read-only по умолчанию / tool-abuse за флагом), конфиг промпта.
4. `add-external-defense` — FastAPI + `llm-guard` (внешний контур). 🔄 в работе
5. `add-defense-modes` / L3 — 4 пресета через env + compose-лимиты ресурсов.
6. `add-attack-harness` — garak + свой раннер: OWASP Top-10 2026 × 4 пресета +
   DoS (LLM06); сравнительный отчёт.
7. `add-reports` — структура `reports/<run-id>/` + сводный `reports/latest.md`.
8. `add-demo-scripts` — `infra/scripts/demo.{sh,ps1}`: поднять стенд → смоук →
   прогнать атаки по 4 пресетам → сгенерировать отчёт (оркестрация всей демонстрации).
9. `add-compose-and-devcontainer` — финальный compose всех сервисов,
   `.devcontainer`, `infra.yaml`, README.
