# GraphRAG Prototype — Deployment Guide

Три режима локального запуска стека GraphRAG Prototype.

---

## 1. Full Development Stack (`compose.yaml`)

- **Состав**: config-service, glossary, ingestion-api, neo4j, embeddings-service, llm, reranker, demo-ui, valkey
- **Порты наружу**: 8001, 8002, 8003, 8004, 8006, 8501 (+ 7474/7687 для Neo4j, 6379 для Valkey)
- **GPU**: LLM — 1 GPU (NVidia)
- **Когда**: разработка, интеграционные тесты, debug полного стека
- **RAM/VRAM**: ~13–15GB / ~4–5GB
- **Место на диске**: ~37GB

```powershell
$env:GRAPH_AUTH_API_KEY = "dev-key"
$env:NEO4J_PASSWORD = "graphrag"
$env:LLM_NGPU_LAYERS = "99"        # все слои LLM на GPU

docker compose -f prototype/infra/compose.yaml build
docker compose -f prototype/infra/compose.yaml up -d --wait --wait-timeout 300
docker compose -f prototype/infra/compose.yaml ps       # ждём всех "healthy"
```

Остановка: `docker compose -f prototype/infra/compose.yaml down` (volumes сохранятся; добавьте `-v` чтобы удалить).

---

## 2. Eval Minimal Stack (`compose.eval-minimal.yaml`)

- **Состав**: config-service, glossary, ingestion-api, neo4j, embeddings-service, llm, eval-runner (one-shot)
- **Порты наружу**: ❌ нет (внутри сети `ohw-eval-<project>`)
- **GPU**: только LLM (1 GPU)
- **BGE-M3**: всегда CPU
- **Reranker**: `noop` (переменная из эксперимента убрана)
- **Когда**: чистый eval, baseline/hybrid сравнение, CI/CD regression, изолированный запуск
- **RAM/VRAM**: ~11–13GB / ~4–5GB

```powershell
# Переменные окружения (генерируются случайно)
$env:GRAPH_AUTH_API_KEY = [guid]::NewGuid().ToString("N")
$env:NEO4J_PASSWORD = [guid]::NewGuid().ToString("N")
$env:RUN_CODE_COMMIT = (git rev-parse --short HEAD)

$project  = "ohw-eval-minimal"
$compose  = "prototype/infra/compose.eval-minimal.yaml"

docker compose -p $project -f $compose build config-service embeddings-service llm
docker compose -p $project -f $compose up -d --wait --wait-timeout 600
docker compose -p $project -f $compose ps              # ждём "healthy"
```

### Загрузка корпуса и eval

```powershell
# 1. Pilot snapshot: путь --root обязателен, snapshot не пишется в read-only mount
$snapshot = "/reports/docs-review-snapshot"
docker compose -p $project -f $compose run --rm eval-runner python /proposal/corpus_tools.py prepare --root /repo --snapshot $snapshot

# 2. Загрузка 7 документов (требуется запущенный стек)
docker compose -p $project -f $compose run --rm eval-runner python /proposal/corpus_tools.py upload --snapshot $snapshot --out /reports/docs-review-upload.json

# 3. Полный graph experiment на публичном docs/корпусе
$run = Get-Date -Format "yyyyMMdd-HHmmss"
docker compose -p $project -f $compose run --rm --no-deps eval-runner python /app/infra/eval/run_eval.py --domain it --mode both --corpus /repo/docs --source-prefix docs --dataset /app/infra/eval/it/questions.jsonl --out "/reports/experiment-$run"

# 4. Остановка (volumes сохранятся)
docker compose -p $project -f $compose down
```

---

## 3. Runtime-стенд: два набора данных (`compose.runtime.yaml`)

- **Состав**: 9 сервисов — config, glossary, neo4j, embeddings, llm, valkey, query-api, query-worker, demo-ui
- **Порты наружу**: 8001, 8003, 8005, 8006, 8503
- **Ingestion-api**: **нет**
- **Когда**: работа с запросами и UI, когда загрузка корпуса уже закончена или не нужна

Зачем отдельный файл: в `compose.yaml` ingestion-api и query-api живут рядом, и чтобы «просто
убрать ингест», надо было гасить сервисы руками. Здесь ингеста нет вовсе, и вопрос «нужен ли
он для запросов» снят: `revision_client.py` объявлен fail-open, без ingestion-api он отдаёт
`revision = None`, и ответы всё равно формируются.

**Что здесь принято, а что нет.** ADR-058 фиксирует намерение разделить приём документов и
извлечение и отдельно требует **двух наборов внешних томов** — пустого и заполненного, «иначе
прогон на пустой базе и прогон на заполненной смешивать нельзя». Форма разделения в ADR-058
**не выбрана**: там прямо сказано, что возможны отдельный сервис, отдельный Compose при том же
образе, отдельный образ или разделение по профилям, и «выбор формы не сделан и должен быть
отдельной записью».

`compose.runtime.yaml` — реализация этого выбора для стенда, а не утверждённая замена
`compose.yaml`. `compose.yaml` жив и нужен: в нём есть `topology-orchestrator`, `projection`,
`reranker`, `configurator-ui`, которых в runtime нет. Требование двух наборов томов этот файл
выполняет, а вопрос «как правильно разделить контуры в принципе» — остаётся открытым.

Что теряется без ingestion-api ровно одно — гарантия актуальности ревизии: нельзя отличить
«ответ по свежему корпусу» от «ответ по устаревшему». Это видимый счётчик
`revision_poll_errors_total`, а не молчание.

### 3.1 Видеокарта или процессор (ADR-060)

Runtime-стенд поднимается **и без видеокарты**: модель тогда считает на процессоре.
Бронь устройства выдаёт compose, и в базовом `compose.runtime.yaml` её нет ни у одного
сервиса — иначе на машине без драйвера NVIDIA стенд не поднимется вообще.

| | GPU-вариант | CPU-вариант |
|---|---|---|
| скрипты | `Demo/demo.sh`, `Demo/demo.ps1` | `Demo/demo-cpu.sh`, `Demo/demo-cpu.ps1` |
| compose-файлы | `compose.runtime.yaml` + `compose.runtime-gpu.yaml` | только `compose.runtime.yaml` |
| требуется | видеокарта NVIDIA | ничего, кроме Docker |
| `LLM_NGPU_LAYERS` | 99 (вся модель) | 0 |
| скорость ответа | минуты | десятки минут |

Перекрыть бронь сверху нельзя: проверено, что ни `devices: []`, ни `devices: null` её не
снимают — она приходит через YAML-merge базового файла. Тот же приём в общем слое:
`infra/compose.yaml` без брони плюс `infra/compose.gpu.yaml`, который выбирает
`infra/up.sh --gpu`.

Модель в обоих вариантах одна — Qwen 2.5 Coder 7B Abliterate q4_K_M, ~5,35 ГБ VRAM при
полной выгрузке. Целевые 14B и 72B на карте в 8 ГБ целиком не помещаются.

**Отладочный стенд `compose.yaml` по-прежнему требует GPU** — он не входит в Demo, и
раздельные варианты для него не заводились. Это осознанная граница.

### Как выбрать набор

Имя тома собирается из переменной `VOL`, поэтому **один и тот же файл поднимает и пустую
базу, и заполненную** — различается только значение `VOL`.

| | Пустой стенд | Стенд с документами |
|---|---|---|
| `VOL` | `empty` | `ready` |
| Тома | `ohw-runtime_empty_*` | `ohw-runtime_ready_*` |
| Состояние графа | 0 нод, 0 связей | 717 `ContextNode`, 1731 связь |
| Как получить | сразу | сперва заполнить `ready` (§4) |

```powershell
$env:GRAPH_AUTH_API_KEY = [guid]::NewGuid().ToString("N")
$env:NEO4J_PASSWORD     = [guid]::NewGuid().ToString("N")

# Пустой стенд
$env:VOL = "empty"
docker compose -f prototype/infra/compose.runtime.yaml up -d --wait --wait-timeout 300

# Стенд с документами (требует заполненного тома ready)
$env:VOL = "ready"
docker compose -f prototype/infra/compose.runtime.yaml up -d --wait --wait-timeout 300
```

Наборы **раздельные**: поднятый `empty` не увидит данных из `ready` и наоборот. Переключение
`VOL` при уже поднятом стенде не переносит данные — том выбирается при создании.

Эти два набора — **демонстрационные**: они показывают систему в двух состояниях и не заменяют
стенд для отладки кода. Для отладки есть §1 (`compose.yaml`, полный контур с ингестом) и §2
(`compose.eval-minimal.yaml`, eval-прогон). Здесь ингеста нет намеренно, поэтому правку
контракта или пайплайна на этом стенде не отладить — только приём и выдачу.

> ⚠️ **Том `ready` наполнен частично: 17 документов из 30.** Из 17 джоб **6 завершились с
> деградацией** (35%), причина не разобрана. Наполненность графа нельзя читать как
> «извлечение работает». Подробности — в `openspec/changes/fill-ready-corpus/`.

---

## 4. Загрузка корпуса в набор `ready` (`compose.ingest.yaml`)

Оверлей поверх runtime, добавляющий **ровно один сервис** — `ingestion-api`. Итоговый стенд
наполняется и остаётся рабочим: ингест можно погасить, том `ready` сохранится.

```powershell
$env:VOL = "ready"
$env:NEO4J_PASSWORD = [guid]::NewGuid().ToString("N")

# Поднять runtime + ингест
docker compose -f prototype/infra/compose.runtime.yaml -f prototype/infra/compose.ingest.yaml `
  --profile ingest up -d --wait --wait-timeout 300

# Наполнить корпус (ограничение задаёт владелец запуска)
docker exec ohw-runtime-ingestion /app/.venv/bin/python /tmp/f.py --limit 17

# Проверить, что набор действительно наполнен, а не только назван готовым
docker exec ohw-runtime-neo4j cypher-shell -u neo4j -p $env:NEO4J_PASSWORD --format plain `
  "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS c ORDER BY c DESC;"
# ожидание: ContextNode > 0 И связи > 0. Одного Chunk недостаточно -
# это признак vector-only загрузки, а не успеха (ADR-053).
```

Тома у оверлея общие с runtime (`${VOL}`), поэтому загруженное видно и после того, как
ингест погашен.

**Отладка разбора: оставить файлы кандидатов.** По умолчанию `<документ>_candidates.json`
удаляется после состоявшейся загрузки. Чтобы посмотреть, чем именно разбирался документ,
уборку выключают на хосте:

```powershell
$env:CANDIDATES_KEEP = "1"   # отладка: файлы остаются
$env:CANDIDATES_KEEP = "0"   # обычный режим: файлы удаляются
```

При `1` джоба **пишет в отчёт**, что кандидаты оставлены для отладки, и сколько их осталось.
Отключённая уборка никогда не выглядит как неисправная. После отладки переменную обязательно
сбросить — иначе папка будет копить файлы и следующий запуск примет их за списки текущей
версии документов.

> ⚠️ **Незакрытые ограничения загрузки.** Корпус сейчас копируется в контейнер вручную, и
> после `--force-recreate` папка пуста. Прибор `fill_ready.py` попадает в контейнер
> копированием же — на диске его нет. Список открытых задач —
> `openspec/changes/fill-ready-corpus/tasks.md`.

---

## 5. Pre-ingest: посмотреть кандидатов без загрузки (`compose.preingest.yaml`)

> **Стадия больше не является шагом конвейера.** Кандидаты производит ingestion-пайплайн сам,
> перед `EXTRACT` и после проверки no-op (ADR-053, «Форма производства и место хранения»).
> Ничего запускать перед загрузкой **не нужно**. Одноразовый runner ниже остался как
> диагностика: посмотреть, какие имена стадия нашла в документе, ничего не загружая.

Одноразовый runner. **Не входит в стенд**, не имеет фиксированного имени контейнера,
поднимается командой запуска и уходит. На вход папка с документами, на выход папка с
`_*_candidates.json`.

```powershell
# Штатный путь: скрипт сам разбирает пути и проверяет результат
powershell -File prototype\infra\scripts\run_pre_ingest.ps1

# Произвольные каталоги
powershell -File prototype\infra\scripts\run_pre_ingest.ps1 -Src .\docs -Dst .\pre-ingest

# Или напрямую (тогда пути обязаны быть абсолютными - дефолты в compose
# указывают уровнем выше корня задачи)
$env:PRE_INGEST_SRC = (Resolve-Path .\docs).Path
$env:PRE_INGEST_DST = (Resolve-Path .\pre-ingest).Path
docker compose -f prototype/infra/compose.preingest.yaml run --rm pre-ingest
```

Сеть у раннера отключена намеренно: модель NER лежит в образе, а попытка докачать её
превращается в явную ошибку, а не в молчаливое отличие от прогона на стенде.

> ⚠️ **Этот режим не участвует в проверке загрузки.** Файлы, которые он напишет, пайплайн не
> читает: он получает кандидатов сам, из текста документа. Прибор `verify_candidates.py`
> удалён вместе с папкой `pre-ingest/` — сверять после уборки нечего, а взамен работают два
> гарда: кандидаты получены для текущей версии документа, и каталог не копит.

---

## 6. Оверлеи eval-стенда

`compose.eval-minimal.yaml` самодостаточен, но два оверлея меняют его поведение. Оверлей
**не запускается одиночным файлом** — базовый идёт первым через `-f`.

| Оверлей | Что меняет | Зачем |
|---|---|---|
| `compose.eval.yaml` | `EMBEDDER=bge_m3_service`, `EMBEDDINGS_DEVICE=cpu`, `LLM_NGPU_LAYERS=${LLM_NGPU_LAYERS:-99}` (2 = 0, CPU) | прогон M5, когда LLM целиком на CPU |
| `compose.eval-qwen3b.yaml` | модель и имя проекта: `ohw-eval-qwen3b_*` | прогон на instruct-варианте |

```powershell
$env:VOL = ""   # eval-стенд не использует наборы томов runtime - сбросьте, если задавали

# M5: LLM на CPU
$env:LLM_NGPU_LAYERS = "0"
docker compose -p ohw-eval-m5 -f prototype/infra/compose.eval-minimal.yaml -f prototype/infra/compose.eval.yaml `
  up -d --wait --wait-timeout 600

# 3B instruct
docker compose -p ohw-eval-qwen3b -f prototype/infra/compose.eval-minimal.yaml `
  -f prototype/infra/compose.eval-qwen3b.yaml up -d --wait --wait-timeout 600
```

`compose.eval-qwen3b.yaml` объявляет `name: ohw-eval-qwen3b`, поэтому проект с другим `-p`
не совпадёт и compose пересоздаст тома с префиксом `-` вместо подстановки в имя.

> ⚠️ **`run_eval_run.ps1` не умеет оверлеи.** На строке 85 он жёстко зашивает
> `compose.eval-minimal.yaml`, параметра для второго `-f` нет. Прогоны с оверлеями запускаются
> либо вручную, либо через `run_e2e_depth.ps1`, который оверлей собирает сам.

---

## 7. Образы: что внутри и что собирается само

Раздел отвечает на вопрос «что нужно собрать руками». Он возник из наблюдения: **`docker
compose build` молча ничего не делает для большинства сервисов**, потому что у них нет
секции `build` — compose берёт готовый образ с именем. Это уже стоило прогона, который
отработал на устаревшем коде и выглядел как успех.

| образ | Dockerfile | что внутри | собирается compose'ом |
|---|---|---|---|
| `ohw/config-service:prototype` | `Dockerfile` | ядро пакета без dev-зависимостей, точка входа `graphrag-config` | да |
| `ohw/glossary-service:prototype` | `Dockerfile` | тот же код, вход `graphrag-glossary` | да |
| `ohw/ingestion-service:prototype` | `Dockerfile` | тот же код, вход `graphrag-ingestion` | **нет** |
| `ohw/query-api:prototype` | `Dockerfile` | тот же код, вход `graphrag-query-api` | да |
| `ohw/query-worker:prototype` | `Dockerfile` | тот же код, вход `graphrag-query-worker` | да |
| `ohw/demo-ui:prototype` | `Dockerfile` | тот же код, вход `graphrag-demo-ui` (Streamlit :8503) | да |
| `ohw/topology-orchestrator:prototype` | `Dockerfile` | тот же код, вход оркестратора топологии | да |
| `ohw/embeddings-service:prototype` | `Dockerfile.embeddings` | **отдельная база**: torch, bge-m3 | да |
| `ohw/reranker:prototype` | `Dockerfile.reranker` | **отдельная база**: bge-reranker, работает на CPU | да |
| `ohw/llm:prototype` | `Dockerfile.llm` | **отдельная база**: llama.cpp + gguf | да |
| `ohw/configurator-ui:prototype` | — | UI конфигуратора | **нет** |

Runtime-стенд (§3) переиспользует образы первой группы: `config-service`, `glossary-service`,
`query-api`, `query-worker`, `demo-ui` из `compose.yaml`, плюс `embeddings` и `llm`. Отличия
намерены и перечислены в комментарии самого `compose.runtime.yaml`: том `config_data` объявлен
внешним (молча заменить конфиг пустым значит собрать систему из другого адаптера), а набор
томов задаётся `VOL`.

Три вещи, которые из этой таблицы следуют и которые стоит знать до прогона:

1. **Прав `src/` — образ пересобирать не нужно**: код смонтирован как `../src:/app/src:ro` в
   пяти самостоятельных файлах (`compose.yaml`, `compose.eval-minimal.yaml`,
   `compose.runtime.yaml`, `compose.ingest.yaml`, `compose.preingest.yaml`), поэтому правила и
   пайплайн всегда те, что в рабочей копии. Оверлеи `compose.eval.yaml` и
   `compose.eval-qwen3b.yaml` собственного тома не объявляют — compose наследует тома
   базового файла, поэтому код монтируется и в них. Томами подключаются только профили,
   конфиг и отчёты. Проверка монтирований: `prototype\infra\checks\check_mounts.ps1`.
2. **`ohw/ingestion-service:prototype` собирается руками.** Это единственный сервис без
   секции `build`, и он же самый нужный. Команда:
   `docker build -f prototype/Dockerfile -t ohw/ingestion-service:prototype prototype/`
3. **Три образа тяжёлые и собраны на разных базах** (`embeddings`, `reranker`, `llm`) —
   их нельзя заменить одним общим, они различаются зависимостями, а не кодом.

---

## 8. Monolith (single-container) — не реализован

Раздел описывает вариант, которого нет. `prototype/infra/Dockerfile.monolith` **не существует**,
и ни в compose, ни в коде, ни в openspec монолит не упоминается. Реальные Dockerfile'ы:
`Dockerfile`, `Dockerfile.embeddings`, `Dockerfile.llm`, `Dockerfile.preingest`,
`Dockerfile.reranker`, `infra/Dockerfile.dev`.

Идея: один контейнер со всеми сервисами как процессами внутри (supervisord) — для машины с
16 ГБ RAM и слабым CPU, CI без GPU-резервации, быстрого smoke-теста. Приведённые ниже числа
памяти относятся к несуществующей сборке и приведены как намерение, а не как измерение.

Если он понадобится, ему нужны `Dockerfile.monolith`, описание сервисов в compose и способ
проверки, что монолит поднимается. Все три отсутствуют, поэтому пользоваться разделом нельзя.

---

## Сравнительная таблица

| Параметр | Full Stack | Eval Minimal | Runtime | Monolith |
|---|---|---|---|---|
| Файл | `compose.yaml` | `compose.eval-minimal.yaml` | `compose.runtime.yaml` | — |
| Кол-во контейнеров | 8–10 | 7 (1 one-shot) | 9 | 1 |
| Публичные порты | ✓ | ✗ | ✓ | ✓ (по желанию) |
| Ingestion-api | ✓ | ✓ | **✗** (ADR-058) | ✓ |
| Наборы данных | общие тома | общие тома | `VOL=empty` / `VOL=ready` | — |
| GPU | LLM | LLM | LLM | LLM |
| LLM на CPU | ✓ (через env) | ✗ (только GPU) | ✓ (через env) | ✓ |
| BGE-M3 | CPU | CPU | CPU | CPU |
| Reranker | опционален | noop | noop | noop |
| RAM | 13–15GB | 11–13GB | ~13GB | — |
| VRAM | 4–5GB | 4–5GB | 4–5GB | — |

Колонка Monolith пустая везде, где нужны измерения: файла сборки нет (§8).

---

## Быстрый старт

**Eval Minimal** (рекомендуется для начала):
```powershell
$env:GRAPH_AUTH_API_KEY = "eval-key"
$env:NEO4J_PASSWORD = "eval-pass"
$env:RUN_CODE_COMMIT = (git rev-parse --short HEAD)
docker compose -p ohw-eval-minimal -f prototype/infra/compose.eval-minimal.yaml up -d --wait --wait-timeout 600
```

**Full Stack**:
```powershell
$env:GRAPH_AUTH_API_KEY = "dev-key"
$env:NEO4J_PASSWORD = "graphrag"
$env:LLM_NGPU_LAYERS = "99"
docker compose -f prototype/infra/compose.yaml build
docker compose -f prototype/infra/compose.yaml up -d --wait --wait-timeout 300
```

**Runtime** (пустой стенд, без ingestion):
```powershell
$env:GRAPH_AUTH_API_KEY = [guid]::NewGuid().ToString("N")
$env:NEO4J_PASSWORD     = [guid]::NewGuid().ToString("N")
$env:VOL = "empty"
docker compose -f prototype/infra/compose.runtime.yaml up -d --wait --wait-timeout 300
```

> Если после Full Stack задана `$env:VOL`, следующий запуск Runtime его подхватит. Перед
> запуском runtime сбросьте переменную явно: `$env:VOL = "empty"`.

---

## Troubleshooting

**GPU не распознаётся:**
```powershell
docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
```

**VRAM недостаточен для LLM:**
```powershell
$env:LLM_NGPU_LAYERS = "0"
docker compose -f ... restart llm
```

**Neo4j не стартует:**
```powershell
docker compose -f ... logs neo4j
docker compose -f ... down -v   # удалит volumes
```