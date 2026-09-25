# eval-minimal — прямой eval без пользовательского транспорта

Конфигурация: `D:\Otus\ohw\Dz4\prototype\infra\compose.eval-minimal.yaml`.
Самостоятельный Compose, **не объединять** с основным Compose/override. Шесть сервисов + одноразовый runner (седьмой контейнер только во время команды). Изолированные volumes и сеть по имени проекта, порты на хост не публикуются. Никакие данные full/demo не подключаются.

Требуются Docker Compose v2.20+, Linux-контейнеры, NVIDIA GPU/драйвер и поддержка GPU в Docker. `LLM_NGPU_LAYERS=0` меняет offload, но не убирает GPU reservation: CPU-only окружение этим файлом не обещается. Первая сборка/прогрев требуют интернета и места под образы/модели. BGE-M3 работает на CPU, reranker=noop, кэш off. Конфигурация не гарантирует размещения на конкретном объёме RAM/VRAM.

Offline projection lease не передаётся флагом eval-раннера: значение берётся из
секции `projection` в `infra_topology.yaml` (default `300s`) или из
Configurator `GET/PUT /api/v1/config/projection`. Для повторяемого eval не меняйте
lease без фиксации в manifest/операторном журнале.

## Проверка и запуск (PowerShell)

### Рекомендуемый путь: хостовый wrapper

Раннер внутри контейнера **не видит** `docker stats`/`nvidia-smi` (нет docker
socket), поэтому пик памяти и логи сервисов снимает хостовый wrapper
`infra/scripts/run_eval_run.ps1`. Он поднимает стенд, запускает прогон, снимает
ресурсы в фоне, выгружает логи сервисов и дописывает строку в индекс прогонов.

```powershell
$env:GRAPH_AUTH_API_KEY = [guid]::NewGuid().ToString('N')
$env:NEO4J_PASSWORD    = [guid]::NewGuid().ToString('N')
$env:RUN_CODE_COMMIT   = (git rev-parse --short HEAD)

# Ступень 1: 3 документа, без генерации и судьи — проверка «стенд встаёт и не OOM»
.\prototype\infra\scripts\run_eval_run.ps1 `
    -RunName 'smoke-3docs' -Documents @(
        '01_ontology_and_domain_profile.md',
        'data_model.md',
        'invariants.md') `
    -RetrievalOnly -Note 'smoke: 3 документа, проверка стенда и RAM'
```

Wrapper сам подставляет `RUN_CODE_TREE` (dirty/clean + хеш состава изменений) и
`--note` в манифест, поэтому состояние кода фиксируется автоматически.

**Важно:** финальный запуск стенда инициируется оператором. Wrapper не стартует
прогоны сам — он только исполняет уже сказанную команду.

### Ручной запуск (когда wrapper не нужен)

```powershell
$compose = 'D:\Otus\ohw\Dz4\prototype\infra\compose.eval-minimal.yaml'
$project = 'ohw-eval-docs-v1'
$env:RUN_CODE_COMMIT = (git rev-parse --short HEAD)
docker compose -p $project -f $compose --profile eval config --quiet
docker compose -p $project -f $compose build config-service embeddings-service llm
docker compose -p $project -f $compose up -d --wait --wait-timeout 600
$run = Get-Date -Format 'yyyyMMdd-HHmmss'
docker compose -p $project -f $compose run --rm --no-deps eval-runner python /app/infra/eval/run_eval.py --domain it --mode both --corpus /repo/docs --source-prefix docs --dataset /app/infra/eval/it/questions.jsonl --extra-dataset /app/infra/eval/it/questions_graph.jsonl --out "/reports/experiment-$run"
```

Не задавайте профиль eval для `up`: runner — одноразовый. Вызов именованного `run eval-runner` сам активирует его профиль. Запуск минимального проекта не останавливает ранее поднятый full-стек: его модели продолжат занимать память. Останавливать старый стек следует отдельно по его имени, без удаления volumes.

## Артефакты прогона

Каждый прогон — отдельная папка `prototype/reports/<RunName>/` (**reports под
gitignore**: артефакты машинно-зависимы и не версионируются). Паспорт
`PASSPORT.md` описывает условия, что меряется и **что здесь не интерпретируется**;
`reports/INDEX.md` собирает все прогоны в одну таблицу.

| Файл | Кто пишет | Содержимое |
|---|---|---|
| `PASSPORT.md` | раннер | условия, метрики, явные ограничения, команда воспроизведения |
| `command.txt` | раннер | точная команда (внутри контейнера) |
| `command.host.txt` | wrapper | полная команда с хоста |
| `run_manifest.json` | раннер | условия машинно-читаемо, включая `golden_coverage` и projection lease |
| `ingest_report.json` | раннер | по каждому документу: время, статус, no-op-признак |
| `qa_log.jsonl` | раннер | по каждому вопросу: источники по осям, метрики, тайминги |
| `qa_review.md` | раннер | тот же разбор для чтения глазами (режим `--no-judge`) |
| `lift_report.json` / `.md` | раннер | агрегат и вердикт |
| `failures.jsonl` | раннер | вопросы с ошибками; прогон не теряется из-за одного сбоя |
| `trace.jsonl` | раннер | события pipeline (только с `--trace`) |
| `preflight.log` | раннер | доступность контуров |
| `resources.json` | **wrapper** | RAM/VRAM: старт, пик, финал + пик по каждому контейнеру |
| `logs/*.log` | **wrapper** | логи сервисов; иначе умрут вместе с `docker container prune` |

### Ограниченный корпус

`--documents` (явный список) и `--limit-docs N` (первые N после сортировки)
позволяют прогонять усечённый корпус, где полный ingest неподъёмен по времени.
Такой прогон **обязан** читаться вместе с `golden_coverage`: если у большинства
вопросов эталонные источники не попали в корпус, `recall@5` характеризует
усечение, а не систему. `corpus_documents`/`corpus_limit` входят в инварианты
парности — прогон на 3 документах получит `verdict: invalid` при сравнении с
прогоном на 8 и не может быть выдан за парный.


## Корпус

Для полного графа эксперимента `eval-runner` получает read-only корень репозитория в `/repo`.
Основной сценарий загружает публичный корпус `docs/` с префиксом `docs`, поэтому `golden_sources`
из `it/questions.jsonl` и `it/questions_graph.jsonl` совпадают с `source_url` в Neo4j:

```powershell
$run = Get-Date -Format 'yyyyMMdd-HHmmss'
docker compose -p $project -f $compose run --rm --no-deps eval-runner python /app/infra/eval/run_eval.py --domain it --mode both --corpus /repo/docs --source-prefix docs --dataset /app/infra/eval/it/questions.jsonl --extra-dataset /app/infra/eval/it/questions_graph.jsonl --out "/reports/experiment-$run"
if ($LASTEXITCODE -ne 0) { throw 'Run failed' }
```

Пилотный 7-документный корпус и его `upload`-процедура остаются отдельным development-сценарием
из [pilots/docs-review/README.md](./pilots/docs-review/README.md). Его нельзя использовать
для `questions_graph.jsonl`: этот набор ссылается на публичные документы `docs/01`, `docs/02`,
`docs/06`, `docs/data_model` и другие, которых нет в семидокументном pilot manifest.

## Парный прогон и режимы

Основной сценарий — парный прогон `--mode both`: `baseline` — vector-only, `target` —
vector search с последующим bounded sparse graph expansion и boost. Ветки используют одну
revision, chunking, embeddings и K; различается только graph expansion. В режимах `--no-judge`
и `--retrieval-only` judge не запускается; остальные слои артефактов сохраняются.

| Флаг | Судья | Генерация | Назначение |
|---|---|---|---|
| (default, `EVAL_LLM_ADAPTER=openai`) | да | да | приёмка, вердикт гейта |
| `--no-judge` | нет | да | пары для ручного разбора (fast-loop) |
| `--retrieval-only` | нет | нет | самый быстрый цикл: только источники и recall |

`--mode baseline`/`--mode hybrid` остаются для одиночных диагностических запусков, но их
verdict **не использовать как сравнительный гейт** — в отчёте нет противоположной ветки.
Для сравнения двух прогонов применяется `--compare-with <файл|дир>`: прогоны, отличающиеся
больше чем в одном поле фактора (`mode`/`graph_enabled`), помечаются «не парные», вердикт
становится `invalid`.

### Артефакты прогона (послойные, ADR-029 / L5-05)

Слои 1–3 пишутся **во всех режимах**, включая `--no-judge`/`--retrieval-only`: быстрый цикл
не зависит от судьи, а проверяемый след остаётся. Полный перечень файлов прогона — в
таблице раздела «Артефакты прогона» выше; `ingest_report.json`, `PASSPORT.md`,
`failures.jsonl` пишутся всегда, `resources.json` и `logs/*.log` — хостовым wrapper'ом.

Ограничения измерений, которые остаются честно зафиксированными:
LLM_TEMPERATURE=0 действует на генератор, но текущий `build_judge` не передаёт температуру
судье (остаётся дефолт). Пилотный корпус — development-подготовка, не минимум 50 вопросов
по ADR-015. Положительные числа парного прогона показывают вклад graph expansion на срезе
`golden_graph_evidence`, а не «пользу GraphRAG вообще»; trace должен содержать seed chunks,
paths, depth и boost. Срез `golden_graph_evidence` сейчас покрыт **9 вопросами** из 66 —
graph-lift опирается на малую выборку, это зафиксированное ограничение, а не результат.

## Остановка и ресурсы

```powershell
docker compose -p $project -f $compose stats --no-stream
docker compose -p $project -f $compose down
```

`down` сохраняет volumes. Не добавлять `-v` без осознанного решения удалить весь eval-корпус, registry, config и кэш моделей этого проекта. Для сравнения ресурсов фиксировать idle, прогрев, ingestion и query отдельно, GPU измерять отдельно от Docker RAM. Не запускать конкурентную запись между ветками.

Границы проверки: повторная проверка сохранена в `D:\Otus\ohw\Dz4\prototype\reports\eval-minimal-config-check.log` и прочитана независимо от терминала: CONFIG_EXIT=0, SERVICES_EXIT=0, семь сервисов. Build/up/модельный инференс не проверены.
