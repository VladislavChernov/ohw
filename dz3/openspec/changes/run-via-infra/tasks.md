# Задачи: запуск simple и advanced через /infra (+ E2E)

## advanced — точки входа и общая обвязка в корне dz3

- [x] 1.1 `advanced/src/json_testgen_advanced/__main__.py` — `python -m json_testgen_advanced` работает (call `cli.main`)
- [x] 1.2 `advanced/infra.yaml` — `components: [ollama]` (объявление зависимости)
- [x] 1.3 `dz3/Dockerfile` (общий) — `FROM ohw-python:3.13`, unprivileged `app`, `ENTRYPOINT ["python","-m"]`
- [x] 1.4 `dz3/compose.yaml` (общий) — `build: {context: ../../, dockerfile: dz3/Dockerfile}`,
      `command: ["json_testgen_advanced"]`, join `ohw_net`, `OLLAMA_BASE_URL=http://ohw-ollama:11434`,
      mount `advanced/input` и `advanced/output`
- [x] 1.5 `dz3/infra.yaml` — `components: [ollama]`

## simple — не трогаем код; только проверка согласованности

- [x] 2.1 Проверить, что `simple/infra.yaml` и `simple/compose.yaml` корректно объявляют
      `/infra` (shared ollama на `ohw_net`); пути в комментариях актуализированы под monorepo

## Верификация advanced (база + сборка)

- [x] 3.1 `uv run pytest -q` — зелёные (без регрессий, включая plan-codec)
- [x] 3.2 `uv run ruff check src tests` — чисто
- [x] 3.3 `uv run mypy src` — чисто
- [x] 3.4 `docker build`/`docker compose build` через `dz3/compose.yaml` проходит
      (uv находит `ohw/kit` по context=корень монорепо)

## Запуск инфраструктуры (shared ollama)

- [x] 4.1 Убрать битый `ohw-ollama` (сел на `ohw-infra_default` вместо `ohw_net`)
- [x] 4.2 `D:\Otus\infra\up.ps1 -Project <dz3>` — `ohw-ollama` поднят на `ohw_net`, модель готова

## E2E (оба проекта)

- [x] 5.1 advanced: `docker compose -f dz3/compose.yaml up --build` — JSON-план →
      ядро исполняет против JSONPlaceholder → `advanced/output/report.md` человекочитаем;
      покрытие всех ресурсов (16 тестов); фидбек-луп не срабатывает зря (план с 1-й попытки).
      Итог 8 OK / 8 FAIL — найден дефект ядра: не подставляются `{vars}` в path
      (`/albums/{id}` уходит литералом → 404) и строковое тело `{post_data}` не
      интерполируется → см. follow-up
- [ ] 5.2 simple: `docker compose -f simple/compose.yaml up --build` — pytest-код →
      прогон → `simple/output/report.md`
- [ ] 5.3 E2E-статус `docs-driven-testgen` 5.3 закрыть (галочка `[x]`)
