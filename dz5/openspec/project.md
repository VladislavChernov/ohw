# Project: dz5 — ИБ-стенд «Атака и защита ИИ-агентов» (OWASP LLM Top 10 2026)

Учебный проект (Otus, ДЗ5): изолированный демонстрационный стенд для проверки
безопасности LLM-агента. Стенд поднимает агента на базе локальной
**abliterated**-модели (без внутренней цензуры) и демонстрирует, как один и тот
же агент ведёт себя под атаками в **пяти конфигурациях защиты**.

## Цель

Показать разницу в ответах модели при разных комбинациях защиты (0 / встроенная
/ внешняя / полная / fixed agent) на матрице атак **OWASP Top 10 for LLM
Applications 2026**.

## Стек

- **Агент (Go):** собственный HTTP-агент в `agent/` (порт `:8080`), а не
  сторонний фреймворк. Эндпоинты `/healthz`, `/status`, `/ui`, `/chat`;
  инструмент `db_query` для чтения SQLite. Пресеты 1–4.
- **Fixed agent (Go):** отдельный защищённый агент в `agent/fixed/` (порт
  `:8081`, preset 5): prompt isolation, input/output validation, tool whitelist,
  UI-переключатели.
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
  таблицы `users` и `personal_data` с секретами и «чужими» строками.
- **Внешняя защита:** `security-wall` — **Guardrails Server** (`guardrails-api`)
  в отдельном контейнере на `:8000`. Guards описываются в `config.py`; агент
  обращается к стандартному REST API (`/guards/{name}/validate`) и не зависит
  от Python-кода стены. Ручной FastAPI-прокси не используется.
- **Атака:** `attack-runner` — `Augustus` + собственный раннер сценариев
  (Python).
- **Инфраструктура стенда:** своя `dz5/infra/` (compose, Dockerfile'ы, `.env`).
  `security-wall` собирается на `python:3.12-slim`; общий `ohw-python:3.13`
  не используется из-за ошибки слоя на этой машине.
- **Отчёты:** каталог `dz5/reports/` (см. ниже).

## Архитектура защиты: 5 конфигураций (матрица 2×2 + fixed agent)

Пресеты 1–4 переключаются флагами `BUILTIN_GUARDS=on|off`,
`EXTERNAL_GUARD=on|off` и **на лету** через `POST /settings`/UI
(`add-defense-modes`). Env задаёт начальное состояние; изменения применяются
атомарно и пишутся в audit JSONL. `ALLOW_RUNTIME_TOGGLES=off` по умолчанию.

`L3` (Runtime-контроль) — предохранитель стенда, присутствует во всех режимах.

| Пресет | Промпт/ограничения агента | Внешний Guardrails Server | L3 (compose-лимиты) |
|---|:--:|:--:|:--:|
| 1 Baseline | ❌ | ❌ | ✅ floor |
| 2 Только встроенная | ✅ | ❌ | ✅ |
| 3 Только внешняя | ❌ | ✅ | ✅ |
| 4 Full Defense | ✅ | ✅ | ✅ |
| 5 Fixed agent | ✅ (отдельный агент) | n/a | ✅ |

`L3` реализован на уровне compose-лимитов (`deploy.resources.limits`: mem/CPU),
таймаутов и restart-policy. Концепция и обоснование —
в `docs/llm_security_architecture.md`.

## Покрытие атак: OWASP Top 10 for LLM 2026

Актуальный список (опубл. 2026-08-04). Наши сценарии покрывают все 10:

| Код | Категория | Сценарий стенда |
|---|---|---|
| LLM01 | Prompt Injection | direct, indirect (отравл. строка), encoding (Base64/ROT13/Unicode/zero-width), jailbreak/DAN, persona-override |
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

Целевая структура: каждый прогон — своя поддиректория `reports/<run-id>/`
(`run-id` = timestamp) с `manifest.json` (модель/тег, git-commit, пресеты) и
`preset-<N>-<name>/` (results + details). В корне `reports/` — сводный
`latest.md` по самым свежим отчётам (таблица «атака × пресет → вердикт +
latency + CPU/RAM»). Сейчас есть только черновик `reports/red-team.md`; он не
содержит измеренных результатов и будет перегенерирован из прогонов.

## Основные соглашения

- Spec-first: изменения начинаются с OpenSpec-бандла (`openspec/changes/<slug>/`),
  код и тесты — после.
- Определение done = зелёные проверки (`pytest`/`ruff`/`mypy` для Python;
  `go vet` для агента), не «код смотрится ок». Галочки в `tasks.md` ставятся
  только после фактической проверки.
- Неизмеренное — `unknown`, не `0` и не «заблокировано». Отчёт без
  предварительно записанного критерия считается логом, а не доказательством.
- Код и комментарии — на английском; документация (README, спеки) — на русском.
- Бандлы пишутся руками (CLI `openspec` отсутствует), по образцу `dz2`/`light-llm-engine`.
- Git: топик-ветка `dz5-agent-security-stand`, Conventional Commits на русском,
  в коммиты попадают **только** пути внутри `dz5/` (чужой WIP по `Dz4` не трогаем).

## Структура

- `agent/` — основной Go-агент (пресеты 1–4).
- `agent/fixed/` — fixed agent (preset 5).
- `security-wall/` — `config.py` и Dockerfile для Guardrails Server.
- `docs/` — design-заметки (архитектура защиты; SSOT авторитет — в `openspec/`).
- `openspec/` — спеки изменений (по вехам).
- `infra/` — собственный инфра-слой стенда (compose, Dockerfile'ы, конфиги).
- `infra/scripts/` — кросс-платформенные демо-скрипты (`*.sh` + `*.ps1`).
- `attack-runner/` — Python-раннер сценариев OWASP × пресеты.
- `scripts/` — security-тесты, конфиг Augustus, генератор отчётов.
- `reports/` — артефакты прогонов атак.

## Вехи (план работ)

1. `add-db-service` — SQLite + seed с ПДн-мишенями + восстановление. ✅
2. `add-abliterated-llm` — локальный контейнер abliterated-модели. Образ
   собирается, контейнер `healthy`; `/api/generate` вживую не проверен.
2a. `add-builtin-guards` — prompt-isolation для preset 2/4: `guarded.txt` с
   запретом раскрытия промпта/секретов, смены роли и обхода `db_query`.
   ✅ Реализовано; smoke P1→P2: leak/PII blocked, injection blocked, легитимный
   SELECT работает. Это `n=1`, не red-team-вердикт.
3. `add-agent-core` — Go-агент пресетов 1–4, tool `db_query`
   (read-only по умолчанию / tool-abuse за флагом), конфиг промпта.
   Сборка, `go test`, `go vet` зелёные; live smoke пресетов 1/2/4 есть.
   `ALLOW_WRITES` проверен unit-тестом; live-модель не выдала исполняемый SQL,
   `db-restore` выполнен.
4. `add-fixed-agent` — отдельный fixed agent (preset 5): реальный tool-loop,
   prompt isolation, input/output guards, tool whitelist, UI-переключатели.
   Сейчас echo-заглушка; сборка падает на `HandleStatus`/`HandleUI`, в работе.
5. `add-external-defense` — Guardrails Server (`guardrails-api`) в отдельном
   контейнере, guards в `config.py`, REST-контракт с агентом. ✅ Реализовано
   и проверено вживую: `/health-check`, `/guards`, легитимный `/chat` 3/4,
   инъекция `403`, PII `403`/маска, fail-closed `503`.
6. `add-defense-modes` / L3 — пресеты 1–4 через env и runtime-переключение
   (`POST /settings`, UI, audit JSONL, mutex), compose-лимиты ресурсов.
   Runtime-переключение и P1→P2 реализованы (`add-builtin-guards`). Wall после
   общего `AnalyzerEngine` стабилен: P3/P4 legit 0.2–0.6s warm, RSS ~0.9–1.3 GiB,
   лимит 2048M. 🟡 Остались live `allowWrites + db-restore` и запись полного
   `reports/<run-id>/`.
7. `add-attack-harness` — Augustus + свой раннер: OWASP Top-10 2026 × 5
   пресетов + DoS (LLM06); манифест и сырые результаты. Есть только каркас.
8. `add-reports` — структура `reports/<run-id>/` + сводный `reports/latest.md`,
   перегенерация red-team отчёта. Планируется.
9. `add-demo-scripts` — `infra/scripts/demo.{sh,ps1}`: поднять стенд → смоук →
   прогнать атаки по пресетам → сгенерировать отчёт. Планируется.
10. `add-compose-and-devcontainer` — финальный compose всех сервисов,
    `.devcontainer`, `infra.yaml`, README. Планируется.
