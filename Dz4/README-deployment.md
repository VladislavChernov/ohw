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

$project  = "ohw-eval-minimal"
$compose  = "prototype/infra/compose.eval-minimal.yaml"

docker compose -p $project -f $compose build config-service embeddings-service llm
docker compose -p $project -f $compose up -d --wait --wait-timeout 600
docker compose -p $project -f $compose ps              # ждём "healthy"
```

### Загрузка корпуса и eval

```powershell
# 1. Формируем snapshot (offline, без стека)
docker compose -p $project -f $compose run --rm eval-runner python /app/infra/eval/pilots/docs-review/corpus_tools.py prepare

# 2. Загрузка 7 документов (требуется запущенный стек)
docker compose -p $project -f $compose run --rm eval-runner python /app/infra/eval/pilots/docs-review/corpus_tools.py upload --out /reports/docs-review-upload.json

# 3. Диагностический запуск baseline + hybrid
$run = Get-Date -Format "yyyyMMdd-HHmmss"
docker compose -p $project -f $compose run --rm --no-deps eval-runner python /app/infra/eval/run_eval.py --domain it --mode baseline --dataset /proposal/questions.jsonl --out "/reports/docs-review-$run/baseline"
docker compose -p $project -f $compose run --rm --no-deps eval-runner python /app/infra/eval/run_eval.py --domain it --mode hybrid  --dataset /proposal/questions.jsonl --out "/reports/docs-review-$run/hybrid"

# 4. Остановка (volumes сохранятся)
docker compose -p $project -f $compose down
```

---

## 3. Monolith (single-container) — future-work

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