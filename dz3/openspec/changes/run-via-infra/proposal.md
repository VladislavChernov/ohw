# Proposal: запуск simple и advanced через общую инфраструктуру `/infra` (+ E2E)

> Смена затрагивает **оба** варианта Dz3: они теперь стартуют через общий
> каталог [`infra/`](../../../infra/README.md) (shared `ohw-ollama` на `ohw_net`),
> вместо того чтобы требовать per-project окружения. Shared ollama поднимается
> один раз и переиспользуется всеми проектами — контейнеры не плодятся.

## Почему

Каталог `infra/` (локальный `D:\Otus\infra`, публикуется в `ohw/infra`) —
это **кодификация запуска проектов**: общий компонент (`ollama`) описан один раз,
проекты лишь **объявляют** зависимости в своём `infra.yaml`, а `up.ps1` из
каталога поднимает именно общий контейнер `ohw-ollama` на общей сети `ohw_net`.

- Проектные app-контейнеры жёстко ждут общего `ohw-ollama:11434` по сети
  `ohw_net` (НЕ `localhost`, НЕ свой ollama).
- `simple` уже подключён к `/infra` (есть `infra.yaml` + compose на `ohw_net`).
- `advanced` — **нет**: у него нет `infra.yaml`, `compose.yaml`, `Dockerfile`,
  `__main__.py`, поэтому его **невозможно** запустить в контейнере через `/infra`.

Цель — довести **оба** варианта до запуска через `/infra` и прогнать E2E.

## Что делаем

- **advanced**: `infra.yaml` (`components: [ollama]`), `__main__.py`
  (`python -m json_testgen_advanced`), общая обвязка в корне **`dz3/`**
  (`Dockerfile` + `compose.yaml`), на которую ссылается advanced. Build-контекст —
  **корень монорепо** `ohw/`, чтобы `uv sync` в сборке нашёл `ohw/kit`
  (относительный путь `../../kit` из `dz3/advanced`).
- **`dz3/compose.yaml`**: сервис `app` с `build: {context: ../../, dockerfile: dz3/Dockerfile}`,
  join внешней сети `ohw_net`, `OLLAMA_BASE_URL=http://ohw-ollama:11434`,
  mount `advanced/input` и `advanced/output`; `command: ["json_testgen_advanced"]`.
- **`dz3/infra.yaml`**: `components: [ollama]` (общий для обоих вариантов).
- **`dz3/Dockerfile`** (общий): `FROM ohw-python:3.13`, `ENTRYPOINT ["python","-m"]` —
  конкретный модуль задаёт compose `command`.
- **simple — не трогаем** (остаётся как есть, у него своя обвязка).
- **openspec/README**: фиксация решения «build-контекст = корень монорепо» для
  сборки проектов с kit-зависимостью.
- **E2E** обоих проектов через shared `ohw-ollama` в `ohw_net`.

## Спека

Поведение CLI не меняется — это change уровня развёртывания (контракт уже
утверждён в `docs-driven-testgen`). ДелФтa спеки не пишется; дельта — только в
запуске/деплое.

## Проверка

- `uv run pytest -q` / `ruff` / `mypy` в advanced — зелёные (база без регрессий).
- `D:\Otus\infra\up.ps1 -Project <dz3>` → `ohw-ollama` поднят на `ohw_net`
  (порт 11434, модель `qwen2.5:7b-instruct`, volume общий).
- `docker compose -f dz3/compose.yaml up --build` → advanced генерирует JSON-план,
  ядро исполняет против JSONPlaceholder, отчёт в `advanced/output/report.md`.
- simple: `docker compose -f simple/compose.yaml up --build` → pytest-код,
  прогон, отчёт в `simple/output/`.
- E2E-статус docs-driven-testgen (5.3) закрывается.
