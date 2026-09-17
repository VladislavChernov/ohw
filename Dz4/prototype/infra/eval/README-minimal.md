# eval-minimal — прямой eval без пользовательского транспорта

Конфигурация: `D:\Otus\ohw\Dz4\prototype\infra\compose.eval-minimal.yaml`.
Самостоятельный Compose, **не объединять** с основным Compose/override. Шесть сервисов + одноразовый runner (седьмой контейнер только во время команды). Изолированные volumes и сеть по имени проекта, порты на хост не публикуются. Никакие данные full/demo не подключаются.

Требуются Docker Compose v2.20+, Linux-контейнеры, NVIDIA GPU/драйвер и поддержка GPU в Docker. `LLM_NGPU_LAYERS=0` меняет offload, но не убирает GPU reservation: CPU-only окружение этим файлом не обещается. Первая сборка/прогрев требуют интернета и места под образы/модели. BGE-M3 работает на CPU, reranker=noop, кэш off. Конфигурация не гарантирует размещения на конкретном объёме RAM/VRAM.

## Проверка и запуск (PowerShell)

Команды ниже — инструкция для оператора; они не выполнялись как live-приёмка при подготовке.

```powershell
$compose = 'D:\Otus\ohw\Dz4\prototype\infra\compose.eval-minimal.yaml'
# Новое имя проекта для нового корпуса — отдельные volumes, без удаления старых.
$project = 'ohw-eval-docs-v1'
$env:GRAPH_AUTH_API_KEY = [guid]::NewGuid().ToString('N')
$env:NEO4J_PASSWORD = [guid]::NewGuid().ToString('N')
# Сохраните значения безопасно для следующей сессии; смена пароля env не меняет БД в существующем volume.
docker compose -p $project -f $compose --profile eval config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Invalid Compose' }
# Общий образ сначала собирается один раз, затем используется glossary/ingestion/runner.
docker compose -p $project -f $compose build config-service embeddings-service llm
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
docker compose -p $project -f $compose up -d --wait --wait-timeout 600
if ($LASTEXITCODE -ne 0) { throw 'Services not ready' }
docker compose -p $project -f $compose ps
```

Не задавайте профиль eval для `up`: runner — одноразовый. Вызов именованного `run eval-runner` сам активирует его профиль. Запуск минимального проекта не останавливает ранее поднятый full-стек: его модели продолжат занимать память. Останавливать старый стек следует отдельно по его имени, без удаления volumes.

## Корпус

Пилотный датасет и полный порядок загрузки:
[D:\Otus\ohw\Dz4\prototype\infra\eval\pilots\docs-review\README.md](./pilots/docs-review/README.md).

Три из семи источников корпуса (исторические ревью и отчёт прогона) существуют только
локально по политике репозитория: подготовка snapshot выполняется на машине с полным
корпусом, либо используется укороченный manifest. Сначала offline `prepare`, затем запуск
стека и `upload` (7 документов, ожидание `succeeded`, ненулевая revision). Вопросы, эталоны
и документы с готовыми ответами в индекс не загружать.

## Диагностический запуск двух веток

Текущий `both` ошибочно выключает граф в обеих ветках. До исправления используйте **раздельные** режимы и разные каталоги. Корпус к этому моменту уже загружен; `--corpus` не передаётся.

```powershell
$run = Get-Date -Format 'yyyyMMdd-HHmmss'
docker compose -p $project -f $compose run --rm --no-deps eval-runner python /app/infra/eval/run_eval.py --domain it --mode baseline --dataset /proposal/questions.jsonl --out "/reports/docs-review-$run/baseline"
if ($LASTEXITCODE -ne 0) { throw 'Baseline failed' }
docker compose -p $project -f $compose run --rm --no-deps eval-runner python /app/infra/eval/run_eval.py --domain it --mode hybrid --dataset /proposal/questions.jsonl --out "/reports/docs-review-$run/hybrid"
if ($LASTEXITCODE -ne 0) { throw 'Hybrid failed' }
```

**Не использовать verdict этих одиночных отчётов как сравнительный гейт**: у каждого отсутствует противоположная ветка. Baseline-агрегаты находятся в baseline первого отчёта; hybrid-агрегаты — в target второго. Нулевой exit также не заменяет проверку verdict/полноты артефактов. Для итогового эксперимента исправить раннер и методику согласно ревью №09/10.

LLM_TEMPERATURE=0 здесь действует на генератор, но текущий build_judge не передаёт этот параметр судье: он остаётся с дефолтной температурой. Groundedness считается по golden_facts; sources не включают графовые доказательства; per-question ответы не сохраняются. Десять вопросов — development-пилот, не минимум 50 по ADR-015. Положительные числа не доказывают пользу GraphRAG.

## Остановка и ресурсы

```powershell
docker compose -p $project -f $compose stats --no-stream
docker compose -p $project -f $compose down
```

`down` сохраняет volumes. Не добавлять `-v` без осознанного решения удалить весь eval-корпус, registry, config и кэш моделей этого проекта. Для сравнения ресурсов фиксировать idle, прогрев, ingestion и query отдельно, GPU измерять отдельно от Docker RAM. Не запускать конкурентную запись между ветками.

Границы проверки: повторная проверка сохранена в `D:\Otus\ohw\Dz4\prototype\reports\eval-minimal-config-check.log` и прочитана независимо от терминала: CONFIG_EXIT=0, SERVICES_EXIT=0, семь сервисов. Build/up/модельный инференс не проверены.
