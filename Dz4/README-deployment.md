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

## 3. Образы: что внутри и что собирается само

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

Три вещи, которые из этой таблицы следуют и которые стоит знать до прогона:

1. **Прав `src/` — пересобери образ**, иначе изменения не попадут внутрь. Код запечён в образ;
   томами подключаются только профили, конфиг и отчёты.
2. **`ohw/ingestion-service:prototype` собирается руками.** Это единственный сервис без
   секции `build`, и он же самый нужный. Команда:
   `docker build -f prototype/Dockerfile -t ohw/ingestion-service:prototype prototype/`
3. **Три образа тяжёлые и собраны на разных базах** (`embeddings`, `reranker`, `llm`) —
   их нельзя заменить одним общим, они различаются зависимостями, а не кодом.

---

## 4. Monolith (single-container) — future-work

- **Состав**: один контейнер со всеми сервисами как процессами внутри (supervisord)
- **Когда**: машина с 16GB RAM и слабым CPU, CI без GPU-резервации, быстрый smoke-test
- **Docker build**: `docker build -f prototype/infra/Dockerfile.monolith -t ohw/monolith:dev prototype/`
- **Docker run**: `docker run --rm -d --gpus all ...`
- **RAM/VRAM**: ~12–14GB / ~4–5GB
- **⚠️ Dockerfile.monolith пока не реализован**

---

## Сравнительная таблица

| Параметр | Full Stack | Eval Minimal | Monolith |
|---|---|---|---|
| Кол-во контейнеров | 8–10 | 7 (1 one-shot) | 1 |
| Публичные порты | ✓ | ✗ | ✓ (по желанию) |
| GPU | LLM | LLM | LLM |
| LLM на CPU | ✓ (через env) | ✗ (только GPU) | ✓ (`LLM_NGPU_LAYERS=0`) |
| BGE-M3 | CPU | CPU | CPU |
| Reranker | опционален | noop | noop |
| RAM | 13–15GB | 11–13GB | 12–14GB |
| VRAM | 4–5GB | 4–5GB | 4–5GB |

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