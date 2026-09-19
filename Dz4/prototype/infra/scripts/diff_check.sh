#!/bin/sh
cd /app
uv run --no-sync ruff format --check \
  src/graphrag_proto/ingestion_service/pipeline/orchestrator.py \
  src/graphrag_proto/retrieval/adapters/base.py \
  src/graphrag_proto/retrieval/adapters/neo4j.py \
  tests/test_commit_stage.py \
  tests/test_run_eval_preflight.py
echo "FMT_DIFF_FILES_EXIT=$?"
uv run --no-sync ruff check \
  src/graphrag_proto/ingestion_service/pipeline/orchestrator.py \
  src/graphrag_proto/retrieval/adapters/base.py \
  src/graphrag_proto/retrieval/adapters/neo4j.py \
  tests/test_commit_stage.py \
  tests/test_run_eval_preflight.py
echo "RUFF_CHECK_DIFF_EXIT=$?"
