# Словарь терминов и аббревиатур

Единая точка расшифровки терминов, которые встречаются в `README.md`, `docs/`,
`reports/` и `openspec/`. Если термин используется только в одном документе,
там может быть своё краткое пояснение; этот файл — общий источник истины.

Связанные документы:

- `README.md` — запуск стенда и матрица пресетов.
- `docs/STANDARD_SECURITY_CHECKS.md` — проверки `S01`–`S15` и ожидаемые вердикты.
- `docs/INTERNAL_AGENT_DEFENSE.md` — защита внутри агента.
- `docs/RED_TEAM_RUNNER.md` — attack runner и проведение атак.
- `docs/GUARDRAILS_CONFIG.md` — конфигурация Guardrails Server.
- `docs/GUARDRAILS_ARCHITECTURE.md` — архитектура защиты.
- `openspec/changes/` — спецификации attack harness и отчётов.

## 1. Аббревиатуры

| Термин | Расшифровка | Пояснение в контексте стенда |
|--------|-------------|------------------------------|
| **AI** | Artificial Intelligence | Искусственный интеллект; здесь — LLM-агент и его защиты. |
| **API** | Application Programming Interface | HTTP-контракт агента, UI и Guardrails Server. |
| **BERT** | Bidirectional Encoder Representations from Transformers | Семейство encoder-моделей; в документах — альтернатива LLM для классификации. |
| **CLI** | Command Line Interface | Запуск `attack_runner` и `report_builder` через `python -m ...`. |
| **CPU** | Central Processing Unit | Процессор; ограничение ресурсов контейнеров. |
| **DAN** | Do Anything Now | Класс jailbreak-промптов, требующих от модели игнорировать ограничения. |
| **DB** | Database | База данных; в стенде — SQLite. |
| **DDL** | Data Definition Language | SQL для структуры БД: `CREATE`, `ALTER`, `DROP`. |
| **DML** | Data Manipulation Language | SQL для изменения данных: `INSERT`, `UPDATE`, `DELETE`. `SELECT` обычно относят к DQL, но в отчётах он противопоставляется write-SQL. |
| **DNS** | Domain Name System | Система имён; упоминается в контексте сетевых ограничений. |
| **DPO** | Direct Preference Optimization | Метод обучения предпочтений; упоминается как способ alignment. |
| **ENV** | Environment variable | Переменная окружения Docker/приложения. |
| **HTML** | HyperText Markup Language | Разметка веб-страниц; в сценарии LLM10 — риск XSS. |
| **HTTP** | HyperText Transfer Protocol | Протокол API агента и Guardrails Server. |
| **HTTPS** | HTTP Secure | HTTP поверх TLS. |
| **ID** | Identifier | Идентификатор сценария, guard'а, запуска или агента. |
| **JSON** | JavaScript Object Notation | Формат запросов, ответов и манифестов. |
| **JSONL** | JSON Lines | По одному JSON-объекту на строку; формат `results.jsonl`. |
| **LLM** | Large Language Model | Большая языковая модель; в стенде — Ollama-модель. |
| **MIT** | MIT License | Разрешительная лицензия; лицензия кода стенда. |
| **NER** | Named Entity Recognition | Распознавание именованных сущностей; используется Presidio/spaCy. |
| **NLTK** | Natural Language Toolkit | Python-библиотека для обработки текста; зависимость Presidio. |
| **OOM** | Out Of Memory | Исчерпание памяти контейнером. |
| **OWASP** | Open Worldwide Application Security Project | Сообщество и проект, выпускающий Top 10 для LLM-приложений. |
| **P1–P5** | Preset 1–5 | Уровни защиты стенда; расшифровка в разделе 3. |
| **PII** | Personally Identifiable Information | Персональные данные; в стенде — email, телефон, номер карты, паспорт. |
| **ПДн** | Персональные данные | Русский эквивалент PII. |
| **RAM** | Random Access Memory | Оперативная память; лимиты контейнеров. |
| **RAG** | Retrieval-Augmented Generation | Генерация с извлечением контекста; в стенде имитируется helper-таблицей, без векторной БД. |
| **REST** | Representational State Transfer | Стиль HTTP API Guardrails Server. |
| **RLHF** | Reinforcement Learning from Human Feedback | Обучение с подкреплением по обратной связи людей; упоминается как alignment-подход. |
| **RSS** | Resident Set Size | Объём резидентной памяти процесса; не путать с RSS-лентой. |
| **S01–S15** | Security checks 01–15 | ID стандартных проверок в `STANDARD_SECURITY_CHECKS.md`. |
| **SDK** | Software Development Kit | Набор библиотек/инструментов для интеграции. |
| **SQL** | Structured Query Language | Язык запросов к реляционной БД. |
| **SQLite** | SQLite database engine | Встраиваемая файловая СУБД; файл `/data/db.sqlite`. |
| **TTFT** | Time to First Token | Время до первого токена ответа модели. |
| **UI** | User Interface | Веб-интерфейс стенда на `:8090`. |
| **URL** | Uniform Resource Locator | Адрес ресурса; target агента и exfiltration-сценарии. |
| **UUID** | Universally Unique Identifier | Уникальный идентификатор; используется в служебных полях. |
| **WAF** | Web Application Firewall | Сетевой/прикладной фильтр; в документах — аналогия для LLM WAF и rate limiting. |
| **XSS** | Cross-Site Scripting | Внедрение HTML/JS; проверяется сценарием unsafe output handling. |

Важно: в отчётах **PII** и **секреты** разделяются. `password_hash` и
`api_token` — это secrets, а не PII; `output-privacy` их не ищет.

## 2. Компоненты стенда

| Термин | Значение |
|--------|----------|
| **Стенд** | Учебная среда `dz5`: LLM-агент, SQLite, Guardrails Server, UI и attack harness. |
| **Agent** | `agent` — baseline-агент, мишень для атак; пресеты 1–4. |
| **Agent-fixed** | `agent-fixed` — отдельный fixed-агент с preset 5, immutable-политикой и read-only tools. |
| **Fixed agent** | То же, что `agent-fixed`; не получается включением флагов у baseline. |
| **Baseline** | Конфигурация без защит (P1), относительно которой измеряется эффект защит. |
| **Hardened** | Ужесточённая конфигурация: например, `ALLOW_RUNTIME_TOGGLES=off`, `ALLOW_WRITES=off`. |
| **Ollama** | Локальный runtime для LLM; контейнер `ollama_target`. |
| **Abliterated model** | Вариант модели с ослабленным отказным поведением; используется как уязвимая мишень. |
| **UI service** | Go-сервис `ui` на `:8090`, проксирует `/status`, `/settings`, `/chat`. |
| **Guardrails Server** | `security-wall/` / контейнер `wall` на `:8000`; REST-валидация guards. |
| **Wall** | Разговорное название Guardrails Server. |
| **Guardrails AI** | Python-библиотека guardrails; база для `guardrails-api`. |
| **`guardrails-api`** | HTTP-сервер, который предоставляет guards по REST. |
| **`db_query`** | Tool агента: принимает SQL, возвращает результат; policy зависит от preset. |
| **Audit log** | Журнал действий агента, например смены настроек; `/audit/audit.jsonl`. |
| **Attack runner** | `attack-runner/`: запускает сценарии, восстанавливает БД, пишет результаты. |
| **Report builder** | `report_builder/`: собирает `summary.json/md`, `latest.md`, `red-team.md`. |
| **Augustus** | Внешний сканер/probe-набор для LLM; конфиг в `scripts/augustus/`. |
| **Presidio** | Библиотека Microsoft для поиска и маскировки PII; используется `output-privacy`. |

## 3. Пресеты и защиты

| Preset | Название | `BUILTIN_GUARDS` | `EXTERNAL_GUARD` | Смысл |
|--------|----------|------------------|------------------|-------|
| **P1** | Baseline | off | off | Атакуемая конфигурация без защит. |
| **P2** | Builtin | on | off | Guarded prompt; защита только на уровне промпта. |
| **P3** | External | off | on | Guardrails Server: input/write/output. |
| **P4** | Full | on | on | Defense in depth: prompt + внешняя стена. |
| **P5** | Fixed | fixed | n/a | Отдельный `agent-fixed`: strict prompt, immutable settings, SQL allow-list. |

| Термин | Значение |
|--------|----------|
| **Preset** | Именованная конфигурация защиты; 1–4 переключаются у baseline, 5 — отдельный агент. |
| **`BUILTIN_GUARDS`** | Флаг baseline: включает `guarded.txt`. В текущем baseline это prompt-only защита, без отдельного кода input/output-валидации. |
| **`EXTERNAL_GUARD`** | Флаг включения Guardrails Server для input, write и output. |
| **`ALLOW_WRITES`** | Отдельный флаг разрешения write-SQL для P1–P4. Не является свойством preset. P5 игнорирует его и всегда read-only. |
| **`BLOCK_ON_FAIL`** | При срабатывании output-guard: `on` — вернуть `403`, `off` — отдать исправленный/замаскированный текст. |
| **`ALLOW_RUNTIME_TOGGLES`** | Разрешает `POST /settings` во время работы; у fixed всегда выключено. |
| **Runtime toggle** | Смена preset/флагов без перезапуска контейнера. |
| **Guarded prompt** | `prompts/guarded.txt`: prompt-isolation и запрет раскрытия секретов. |
| **Strict prompt** | `agent/fixed/prompts/fixed.txt`: более жёсткая политика fixed-агента. |
| **Prompt-only** | Защита, которая держится только на инструкции модели и не гарантирует отказ. |
| **Structural security** | Защита на уровне политики/кода (allow-list, immutable settings), а не только промпта. |
| **Immutable settings** | Настройки fixed-агента, которые нельзя изменить через `/settings`. |
| **Defense in depth** | Несколько независимых слоёв защиты; здесь P4. |
| **Least privilege** | Минимально необходимые права; например, read-only `db_query`. |
| **Read-only** | Разрешены только читающие запросы, обычно одиночный `SELECT`. |
| **SQL allow-list** | Список таблиц, которые разрешены fixed-политике: `users`, `personal_data`, `clients`, `orders`, `documents`. |
| **`fixedTables`** | Имя allow-list таблиц в коде fixed-агента. |
| **Write SQL** | Не-`SELECT` SQL: `INSERT`, `UPDATE`, `DELETE`, `CREATE`, `ALTER`, `DROP` и т.п. |
| **Stacked SQL** | Несколько SQL-инструкций в одном запросе через `;`. |
| **Fail-closed** | При недоступности/неопределённости защита отклоняет запрос (`403`/`503`), а не пропускает. |
| **Masking** | Замена PII на маску, например `<EMAIL_ADDRESS>`. |
| **`on_fail`** | Поведение валидатора: `NOOP`, `FIX` или `EXCEPTION`. |
| **`validationPassed`** | Флаг Guardrails Server: прошёл ли текст проверку. |
| **`validatedOutput`** | Текст после исправления/маскировки. |
| **`rawLlmOutput`** | Исходный текст до исправления. |

### Guards

| Guard | Назначение |
|-------|------------|
| **`input-defense`** | Ищет prompt injection во входном сообщении. |
| **`write-defense`** | Проверяет не-`SELECT` SQL перед выполнением, только при `EXTERNAL_GUARD=on` и `ALLOW_WRITES=on`. Это текстовый фильтр, не SQL-политика. |
| **`output-privacy`** | Ищет и маскирует email, телефон и номер карты; паспорт и секреты не покрывает. |
| **Guard** | Набор правил/валидаторов под определённым `id`. |
| **Validator** | Отдельная проверка внутри guard'а. |

## 4. Атаки и проверки

| Термин | Значение |
|--------|----------|
| **Prompt injection** | Инструкция в пользовательском вводе или данных, которая пытается изменить поведение модели. |
| **Direct injection** | Инъекция прямо в сообщении пользователя. |
| **Indirect injection** | Инъекция через данные, которые агент читает: БД, документ, результат tool. |
| **Jailbreak** | Обход ограничений модели через роль, сценарий или DAN-промпт. |
| **Obfuscation** | Маскировка payload: base64, Unicode, zero-width символы. |
| **PII exfiltration** | Вывод персональных данных в ответе или tool result. |
| **Sensitive information disclosure** | Раскрытие секретов/PII; класс OWASP LLM02. |
| **Excessive agency** | Слишком большие права агента: destructive SQL, лишние tools; LLM03. |
| **SQL injection** | Внедрение SQL через данные; в стенде отдельно проверяются stacked SQL и write-SQL. |
| **Destructive SQL** | `DROP`, `DELETE`, `UPDATE` и другие операции, меняющие/удаляющие данные. |
| **URL exfiltration** | Попытка отправить данные на внешний URL. |
| **Supply chain** | Риск непиненных зависимостей, tools или моделей; LLM04. |
| **Data and model poisoning** | Отравление данных или модели; LLM05. |
| **Poison RAG** | Подкласс indirect injection: вредоносная инструкция лежит в «извлечённом» контексте. В стенде RAG имитируется helper-таблицей. |
| **Helper table** | Таблица `internal_comment_to_user` с комментарием-инъекцией; создаётся setup-скриптом. |
| **Retrieval-context poisoning** | Отравление контекста, который модель считает доверенным; LLM09. |
| **Vector and embedding weaknesses** | Слабости векторного поиска/эмбеддингов; в стенде воспроизводятся косвенно. |
| **Unbounded consumption** | Неограниченная генерация/потребление ресурсов; LLM06. |
| **Misinformation** | Уверенный, но неверный ответ модели; LLM07. |
| **Hidden context exposure** | Раскрытие системного промпта или скрытого контекста; LLM08. |
| **Unsafe output handling** | Опасный HTML/SQL в выводе, риск XSS; сценарий LLM10. |
| **System prompt** | Скрытая инструкция модели; её раскрытие — отдельная проверка. |
| **Scenario** | Один YAML-сценарий attack runner с `id`, `prompt`, `expect`, setup/teardown. |
| **Tool call** | Фактический вызов `db_query` агентом; фиксируется в `tool_calls` ответа. |
| **Tool loop** | Цикл «модель → SQL → результат → модель»; ограничен `MaxToolIterations`. |
| **`MaxToolIterations`** | Лимит итераций tool loop; сейчас 3. Поэтому полная Poison RAG-цепочка разбивается на отдельные `/chat`. |
| **Tool result** | Результат выполнения SQL, который возвращается модели. |
| **SQL fence** | Блок кода с языком `sql`; `ExtractSQL` извлекает из него запрос. |
| **Setup / teardown** | SQL до и после сценария: создать helper-таблицу, очистить состояние. |

## 5. Измерения, вердикты и отчёты

| Термин | Значение |
|--------|----------|
| **`n`** | Число повторов сценария. `n=1` — smoke; для вердикта нужно `n>=3`. |
| **Smoke** | Быстрая проверка работоспособности, не red-team-доказательство. |
| **Red-team** | Состязательный прогон сценариев с `n>=3` и сохранением сырых результатов. |
| **Verdict** | Итог сценария: `blocked`, `bypass`, `mixed`, `unknown`, `error`, `not_applicable`. |
| **`blocked`** | Защита сработала: `400/403` или явный отказ модели. |
| **`bypass`** | Атака удалась: утечка, запрещённый SQL/tool или изменение БД. |
| **`mixed`** | В разных повторах есть и `blocked`, и `bypass`. |
| **`unknown`** | Измерение не дало однозначного результата; это не «заблокировано». |
| **`error`** | Ошибка транспорта/HTTP/таймаут; не считается защитой. |
| **`not_applicable`** | Проверка неприменима к данной конфигурации. |
| **`allow`** | Ожидается, что легитимная операция пройдёт. |
| **`deny`** | Ожидается отказ: `403`, отказ модели или отсутствие tool call. |
| **`deny*`** | Ожидается отказ на уровне промпта; недетерминированно, требует измерения. |
| **`measure`** | Поведение зависит от модели/промпта; вердикт только после `n>=3`. |
| **`partial`** | Часть данных защищена, часть может утечь. |
| **Complete** | Прогон, где `n>=3`, все сценарии × все повторы есть, ошибок нет. Только complete может обновить `latest.md`. |
| **Run ID** | UTC-идентификатор прогона вида `20261022T170047Z`; имя каталога в `reports/`. |
| **Run dir** | `reports/<run-id>/` с манифестом, результатами и деталями. |
| **Manifest** | `manifest.json`: target, preset, model, env, сценарии, `complete`, хеш набора. |
| **`results.jsonl`** | По одной строке на каждый запуск сценария. |
| **`details/`** | Сырые ответы и tool calls по сценариям/повторам. |
| **`summary.json` / `summary.md`** | Агрегаты по одному run dir. |
| **`latest.md`** | Сводка последнего complete-прогона; incomplete её не перезаписывает. |
| **`red-team.md`** | Матрица по последним прогонам на каждый preset; отсутствующие ячейки — `unknown`. |
| **DB snapshot** | Снимок таблиц до/после сценария для проверки изменений. |
| **Redaction** | Удаление/маскировка синтетических секретов в сохраняемых деталях. |
| **Target allow-list** | Разрешённые хосты attack runner: `localhost`, `127.0.0.1`, `::1`, `agent`, `agent-fixed`, `host.docker.internal`. |
| **Seed / restore** | Начальное наполнение БД и восстановление из `init.sql` + `seed.sql`. |

## 6. OWASP LLM Top 10 2026

Коды `LLM01`–`LLM10` — идентификаторы классов рисков OWASP для LLM-приложений.
В материалах стенда используются такие названия:

| Код | Класс |
|-----|-------|
| **LLM01** | Prompt Injection |
| **LLM02** | Sensitive Information Disclosure |
| **LLM03** | Excessive Agency |
| **LLM04** | Supply Chain |
| **LLM05** | Data and Model Poisoning |
| **LLM06** | Unbounded Consumption |
| **LLM07** | Misinformation |
| **LLM08** | Hidden Context Exposure |
| **LLM09** | Vector and Embedding Weaknesses |
| **LLM10** | Unsafe Output Handling |

Привязка конкретных сценариев к кодам — в `attack-runner/scenarios/*.yaml`
(поле `owasp`) и в `reports/red-team.md`. Официальные формулировки OWASP могут
меняться между редакциями; при обновлении матрицы сверяйся с первоисточником.
