#!/usr/bin/env sh
# Демонстрационный стенд, ВАРИАНТ НА CPU.
#
# Отличается от demo.sh одной вещью: LLM запускается без видеокарты. Всё остальное -
# наборы, тома, учётные данные, команды - то же самое.
#
# Нужен на машине без NVIDIA. Видеокарту выдаёт compose сам, поэтому базового файла
# достаточно: без оверлея `compose.runtime-gpu.yaml` бронь устройства не объявлена вовсе.
#
# На Windows без bash: Demo/demo-cpu.ps1
#
# Учётные данные заданы здесь и больше нигде. Пароль базы менять нельзя: том набора
# `ready` создан один раз, Neo4j применяет NEO4J_AUTH только при первом старте, и смена
# пароля означала бы пересоздание тома вместе со всем наполнением.
set -eu

DEMO_PASSWORD='local-stand-not-a-secret'
DEMO_API_KEY='demo-key-2026'

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# Путь к compose вычисляется мягко: `help` должен работать откуда угодно, даже если
# репозиторий рядом не лежит. При `set -e` жёсткое вычисление убивало бы справку.
INFRA_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/../prototype/infra" 2>/dev/null && pwd || echo "")
COMPOSE_RUNTIME="$INFRA_DIR/compose.runtime.yaml"
# Оверлей реранкера: подключается только по флагу --with-reranker.
COMPOSE_RERANKER="$INFRA_DIR/compose.runtime-reranker.yaml"
# Оверлея GPU здесь нет и он не подкладывается: бронь видеокарты выдаёт compose сам, и без
# неё модель считает на процессоре. Базового файла достаточно.
FIXTURES_DIR="$SCRIPT_DIR/fixtures"

NEO4J_CONTAINER='ohw-runtime-neo4j'

usage() {
    cat <<'USAGE'
demo-cpu.sh up       [--set empty|ready]
    demo-cpu.sh down     [--set empty|ready]
    demo-cpu.sh status   [--set empty|ready]
    demo-cpu.sh restore  [--set empty|ready] [--archive ФАЙЛ]
    demo-cpu.sh export   [--set ready] [--out ФАЙЛ]

ИНГЕСТА ЗДЕСЬ НЕТ, и это не ограничение, а решение (ADR-065): он в отдельном скрипте.
Наполнение базы - `ingest.sh` (Linux, macOS) и `ingest.ps1` (Windows). Тома наборов
общие, поэтому наполненная база остаётся после `ingest.sh down`, а готовый набор
выгружается архивом и восстанавливается без ингеста: `demo-cpu.sh export` / `restore`.

Наборы:
  empty - пустая система, тома создаются заново.
  ready - набор с документами. Тома в репозитории не хранятся; чтобы получить его на
          чистой машине, сначала выполните `restore`.
USAGE
}

die() {
    echo "ОШИБКА: $*" >&2
    exit 1
}

need_docker() {
    command -v docker >/dev/null 2>&1 || die "не найден docker в PATH"
    docker info >/dev/null 2>&1 || die "демон docker не отвечает - запустите Docker Desktop или dockerd"
}

compose_cmd() {
    # Аргументы вызова сохраняются ДО `set --`: внутри функции `set --` заменяет
    # позиционные параметры, и подкоманда пропадала бы молча, а скрипт напечатал бы
    # «поднят» при неработающем compose. Подробно - в Demo/demo.sh, откуда этот
    # скрипт и взят.
    _compose_tail=$*
    set -- --project-directory "$INFRA_DIR" --file "$COMPOSE_RUNTIME"
    # Оверлей реранкера идёт после остальных: он переопределяет слот `RERANKER`.
    if [ "${WITH_RERANKER:-0}" = "1" ]; then
        set -- "$@" --file "$COMPOSE_RERANKER"
    fi
    # shellcheck disable=SC2086
    docker compose "$@" $_compose_tail
}

set_common_env() {
    VOL="$SET"
    NEO4J_PASSWORD="$DEMO_PASSWORD"
    GRAPH_AUTH_API_KEY="$DEMO_API_KEY"
    # Ноль слоёв на GPU. Без видеокарты значение 99 бессмысленно, но полагаться на то, что
    # llama.cpp сам перейдёт на процессор, нельзя: молчаливое решение за ресурс выглядело бы
    # как «тормозит», а не как «считает не там». Ноль говорит прямо.
    LLM_NGPU_LAYERS=0
    export VOL NEO4J_PASSWORD GRAPH_AUTH_API_KEY LLM_NGPU_LAYERS
}

cmd_up() {
    [ -f "$COMPOSE_RUNTIME" ] || die "не найден $COMPOSE_RUNTIME"
    set_common_env
    if [ "$SET" = "ready" ] && ! volume_exists "ohw-runtime_ready_neo4j"; then
        echo "ПРЕДУПРЕЖДЕНИЕ: том набора ready не создан. Поднимется ПУСТАЯ база с именем ready."
        echo "Чтобы получить наполненный набор: demo.sh restore"
    fi
    compose_cmd up -d --wait --wait-timeout "$WAIT_TIMEOUT"
    echo "Набор '$SET' поднят."
    echo "UI: http://localhost:8503"
}

cmd_down() {
    set_common_env
    compose_cmd down
    echo "Набор '$SET' остановлен. Тома сохранены."
}

volume_exists() {
    docker volume ls --format '{{.Name}}' | grep -qx "$1"
}

container_password() {
    docker exec "$NEO4J_CONTAINER" printenv NEO4J_AUTH 2>/dev/null | sed 's|^[^/]*/||'
}

cmd_status() {
    echo "=== контейнеры ==="
    running=$(docker ps --filter 'name=ohw-runtime' --format '{{.Names}}')
    if [ -z "$running" ]; then
        echo "  ни одного. Стенд не поднят."
    else
        printf '%s\n' "$running" | sed 's|^|  |'
    fi

    echo ""
    echo "=== тома набора '$SET' ==="
    vols=$(docker volume ls --format '{{.Name}}' | grep "^ohw-runtime_${SET}_" || true)
    if [ -z "$vols" ]; then
        echo "  тома нет. Набор ещё не поднимался."
    else
        printf '%s\n' "$vols" | sort | sed 's|^|  |'
    fi

    echo ""
    echo "=== наполненность ==="
    if ! docker ps --filter "name=^/${NEO4J_CONTAINER}$" --format '{{.Names}}' | grep -q .; then
        echo "  Neo4j не отвечает. Это не «база пуста», это «базы нет»."
        return 0
    fi
    pw=$(container_password)
    if [ -z "$pw" ]; then
        echo "  не удалось получить пароль из контейнера - количество нод НЕ измерено."
        return 0
    fi
    rows=$(docker exec "$NEO4J_CONTAINER" cypher-shell -u neo4j -p "$pw" --format plain \
        'MATCH (n) RETURN labels(n)[0] AS label, count(*) AS c ORDER BY c DESC;' 2>/dev/null | tail -n +2)
    if [ -z "$(printf '%s' "$rows" | tr -d '[:space:]')" ]; then
        echo "  база пуста: нод нет ни одного."
    else
        printf '%s\n' "$rows" | sed 's|^|  |'
        ctx=$(printf '%s' "$rows" | sed -n 's/.*ContextNode, *\([0-9]*\).*/\1/p')
        if [ -n "$ctx" ] && [ "$ctx" = "0" ]; then
            echo "  ВНИМАНИЕ: Chunk есть, а ContextNode нет - это vector-only загрузка."
            echo "  Джоба при этом завершается succeeded, то есть выглядит как успех."
        fi
    fi
}

# Тома набора. `query` намеренно не входит: в нём только журнал задач запросов, он пуст,
# и compose создаст его при первом же запросе. Том `projection` входит: в нём состояние
# инкрементальной проекции, без него набор не знает, что уже спроецировано.
VOLUMES='neo4j ingestion projection'

cmd_export() {
    for suffix in $VOLUMES; do
        vol="ohw-runtime_${SET}_${suffix}"
        volume_exists "$vol" || die "том $vol не найден: набор '$SET' ещё не поднимался"
    done

    was_up=$(docker ps --filter "name=^/${NEO4J_CONTAINER}$" --format '{{.Names}}' | grep -c . || true)
    [ "$was_up" = "0" ] || docker stop "$NEO4J_CONTAINER" >/dev/null

    for suffix in $VOLUMES; do
        vol="ohw-runtime_${SET}_${suffix}"
        out="${EXPORT_OUT:-$SCRIPT_DIR/fixtures/ready-${suffix}.tar.gz}"
        # Один -Out на все тома не годится: имена различаются.
        out=${out/ready.tar.gz/ready-${suffix}.tar.gz}
        mkdir -p "$(dirname -- "$out")"
        docker run --rm \
            --mount "type=volume,source=$vol,target=/from" \
            --mount "type=bind,source=$(CDPATH= cd -- "$(dirname -- "$out")" && pwd),target=/to" \
            alpine sh -c "tar czf /to/$(basename -- "$out") -C /from ."
        [ -f "$out" ] || die "архив не появился: $out"
        echo "  $vol -> $(basename -- "$out") ($(du -h "$out" | cut -f1))"
    done

    [ "$was_up" = "0" ] || docker start "$NEO4J_CONTAINER" >/dev/null
    echo "Набор '$SET' выгружен."
}

restore_one() {
    suffix="$1"
    archive="$2"
    vol="ohw-runtime_${SET}_${suffix}"

    [ -f "$archive" ] || die "архив не найден: $archive"

    if volume_exists "$vol"; then
        printf "ВНИМАНИЕ: том %s уже существует и будет ОЧИЩЕН. Продолжить? (yes/no) " "$vol"
        read -r answer
        [ "$answer" = "yes" ] || die "отменено"
        docker rm -f "$vol" >/dev/null 2>&1 || true
    fi

    docker volume create "$vol" >/dev/null
    docker run --rm \
        --mount "type=volume,source=$vol,target=/data" \
        --mount "type=bind,source=$(CDPATH= cd -- "$(dirname -- "$archive")" && pwd),target=/to" \
        alpine sh -c "tar xzf /to/$(basename -- "$archive") -C /data"
    echo "  восстановлен $vol из $(basename -- "$archive")"
}

cmd_restore() {
    # Набор - это несколько томов. Реестр источников обязателен: без него сервис не
    # знает, что документы уже загружены, и повторная загрузка того же документа создаёт
    # вторую копию вместо сигнала no-op. Проекция - чтобы набор знал, что уже спроецировано.
    for suffix in $VOLUMES; do
        archive="${RESTORE_ARCHIVE:-$FIXTURES_DIR/ready-${suffix}.tar.gz}"
        restore_one "$suffix" "$archive"
    done
    echo "Набор '$SET' восстановлен."
    echo "Пароль базы: $DEMO_PASSWORD   ключ API: $DEMO_API_KEY"
}

SET='empty'
WAIT_TIMEOUT=300
EXPORT_OUT=''
RESTORE_ARCHIVE=''

if [ $# -eq 0 ]; then
    usage
    exit 1
fi

COMMAND=$1
shift

while [ $# -gt 0 ]; do
    case "$1" in
        --set) SET="$2"; shift 2 ;;
    --with-reranker) WITH_RERANKER=1; shift ;;
        --wait-timeout) WAIT_TIMEOUT="$2"; shift 2 ;;
        --out) EXPORT_OUT="$2"; shift 2 ;;
        --archive) RESTORE_ARCHIVE="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "неизвестный аргумент: $1" ;;
    esac
done

case "$SET" in
    empty|ready) ;;
    *) die "--set принимает empty или ready, получено: $SET" ;;
esac

case "$COMMAND" in
    -h|--help|help)
        usage
        exit 0
        ;;
    up|down|status|export|restore)
        need_docker
        ;;
    *)
        usage
        die "неизвестная команда: $COMMAND"
        ;;
esac

case "$COMMAND" in
    up) cmd_up ;;
    down) cmd_down ;;
    status) cmd_status ;;
    export) cmd_export ;;
    restore) cmd_restore ;;
    help) usage ;;
esac