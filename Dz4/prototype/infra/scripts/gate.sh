#!/bin/sh
# Гейт Dz4. Инструменты запускаются через `uv`, как принято в проекте (`lint.sh`),
# а не прямым вызовом интерпретатора venv.
#
# Режимы:
#   gate.sh full                 полный гейт (перед коммитом, минуты)
#   gate.sh docs|adapters|...    одна область (секунды)
#   gate.sh changed <файл>...    области по списку файлов, который дал хост
#
# Git в контейнере нет и быть не должно: source control принадлежит хосту, поэтому
# список изменённых файлов передаётся аргументами. Хостская команда — в AGENTS.md,
# секция «Dz4: гейт».
#
# Окружение uv кладётся в записываемый каталог, потому что рабочая копия смонтирована
# read-only. `--frozen` берёт состав из `uv.lock` и не переписывает лок, так что гейт
# не может молча починить рассинхрон зависимостей - он на нём падает.
#
# ruff/mypy/прибор grounding всегда идут по всему репозиторию: они дешёвые, и
# селектор по ним ничего не выигрывает. Режим влияет только на выбор pytest.
#
# ruff format сюда не входит намеренно: `ruff format --check` не проходит даже на
# файлах из `diff_check.sh`, то есть форматирование в проекте не enforced. Гейт,
# который не может стать зелёным, хуже отсутствия гейта.
set -u

MODE="${1:-changed}"
if [ "$#" -gt 0 ]; then
  shift
fi

PROTO_DIR=$(cd "$(dirname "$0")/../.." && pwd)
SELECTORS="$PROTO_DIR/infra/gate_selectors.yaml"
CHECKS="$PROTO_DIR/infra/checks"

UV_PROJECT_ENVIRONMENT="${GATE_VENV:-/tmp/dz4-gate-venv}"
INGESTION_UPLOAD_DIR="${GATE_UPLOADS:-/tmp/dz4-gate-uploads}"
# Образ задаёт PYTHONPATH=/app/src, то есть на код, который в нём запечён. Он попадает в
# sys.path раньше .pth из editable-установки, и гейт молча проверяет старый снимок вместо
# рабочей копии. Поэтому путь переопределяется явно, а не наследуется.
PYTHONPATH="${GATE_SRC:-$PROTO_DIR/src}"
export UV_PROJECT_ENVIRONMENT INGESTION_UPLOAD_DIR PYTHONPATH
# Кэши ruff и mypy по умолчанию пишутся в рабочий каталог, который смонтирован
# read-only: ruff падает с «Read-only file system», mypy - с внутренней ошибкой.
# Оба кэша уводятся в записываемый каталог.
CACHE_DIR="${GATE_CACHE_DIR:-/tmp/dz4-gate-cache}"
export CACHE_DIR
UV_RUN="uv run --frozen"

mkdir -p "$UV_PROJECT_ENVIRONMENT" "$INGESTION_UPLOAD_DIR" "$CACHE_DIR"

FAILED=0
step() {
  label="$1"
  shift
  if "$@"; then
    echo "$label: OK"
  else
    echo "$label: FAIL"
    FAILED=1
  fi
}

cd "$PROTO_DIR" || exit 2

echo "=== uv sync (состав из uv.lock) ==="
step SYNC uv sync --frozen

echo "=== ruff check ==="
step RUFF uv run --frozen ruff check --cache-dir "$CACHE_DIR" .

echo "=== mypy ==="
step MYPY uv run --frozen mypy --cache-dir "$CACHE_DIR" src

echo "=== score_extraction_grounding ==="
step GROUNDING uv run --frozen python infra/eval/score_extraction_grounding.py --check

case "$MODE" in
full)
  echo "=== pytest: полный ==="
  step PYTEST uv run --frozen pytest -q -p no:cacheprovider
  exit "$FAILED"
  ;;
changed)
  if [ "$#" -eq 0 ]; then
    echo "changed: список файлов не передан" >&2
    echo "посчитай его на хосте (git status --porcelain) и передай аргументами" >&2
    exit 3
  fi
  MODE=$(uv run --frozen python "$CHECKS/gate_changed_areas.py" --selectors "$SELECTORS" "$@") || exit 3
  [ -n "$MODE" ] || { echo "=== pytest: изменённых файлов нет ==="; exit 0; }
  ;;
docs | adapters | demo_scripts | gate_tooling)
  ;;
*)
  echo "режим неизвестен: $MODE" >&2
  exit 2
  ;;
esac

echo "=== pytest: области [$MODE] ==="
TARGETS=$(uv run --frozen python "$CHECKS/gate_select_targets.py" --selectors "$SELECTORS" --areas "$MODE") || exit 2
step PYTEST uv run --frozen pytest -q -p no:cacheprovider $TARGETS

exit "$FAILED"