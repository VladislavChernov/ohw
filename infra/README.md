# Shared ollama (ohw-infra)

Single shared `ollama` service for the whole homework monorepo.

Usage
-----
```bash
cd d:/Otus/ohw
# Launch the shared service (one-time model download, cached in ohw_ollama_models):
docker compose -f infra/compose.yaml up -d

# Check it's up and the model is loaded:
docker compose -f infra/compose.yaml ps
docker compose -f infra/compose.yaml logs -f
```

- Model is selected by `OLLAMA_MODEL=` in `infra/.env`
  (default `qwen2.5:7b-instruct`). To switch: edit `.env`, then
  `docker compose -f infra/compose.yaml restart ollama`.
- Every project compose stack points its app at the **shared** ollama via
  `OLLAMA_BASE_URL=http://host.docker.internal:11434` (see project `.env`).
  So `docker compose up --build` in a project starts **only the app** and
  reuses the shared ollama — no extra model download, no per-project volume.
- Port `11434` belongs to `ohw-ollama`. Do **not** run a per-project
  `--profile standalone` ollama while the shared one is up (port conflict).
  Standalone mode exists only for a fully self-contained run/snapshot:
  ```
  docker compose --profile standalone up --build
  ```


