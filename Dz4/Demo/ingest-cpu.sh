#!/usr/bin/env sh
# Наполнение базы демо-стенда НА ПРОЦЕССОРЕ. Отдельный скрипт: стенду ингест не нужен.
#
# Только Linux и macOS. На Windows скрипта нет намеренно: см.
# docs/prototype_requirements.md §3.3 - там написано, что запускать вручную.
#
# Стенд (Demo/demo.sh) поднимает девять сервисов и НЕ поднимает ingestion-api: запросы идут
# и без него. Это разделение сделано в инфраструктуре (ADR-058) и оформлено здесь, в виде
# отдельной пары скриптов. На Windows без bash: Demo/ingest.ps1
#
# Почему это не мешает: тома наборов общие. Ингест пишет в тот же Neo4j, который читает
# стенд, и после остановки оверлея база остаётся на месте. Стенду нужен наполненный том,
# а не сервис, который его наполняет.
#
# Учётные данные заданы здесь и больше нигде. Пароль базы менять нельзя: том набора создан
# один раз, Neo4j применяет NEO4J_AUTH только при первом старте.
set -eu

DEMO_PASSWORD='local-stand-not-a-secret'
DEMO_API_KEY='demo-key-2026'

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INFRA_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/../prototype/infra" 2>/dev/null && pwd || echo "")
COMPOSE_RUNTIME="$INFRA_DIR/compose.runtime.yaml"
COMPOSE_INGEST="$INFRA_DIR/compose.ingest.yaml"
# Оверлей видеокарты НЕ подкладывается: вариант считает на процессоре.

NEO4J_CONTAINER='ohw-runtime-neo4j'
INGEST_CONTAINER='ohw-runtime-ingestion'

SET=empty
LOAD_PATH=''
LOAD_DOMAIN=it
WAIT_TIMEOUT=300

usage() {
    cat <<'USAGE'
Наполнение базы демо-стенда. Стенд поднимается отдельно: Demo/demo.sh (Linux, macOS),
Demo/demo.ps1 (Windows).

    ingest-cpu.sh up      [--set empty|ready]
    ingest-cpu.sh load    --path ФАЙЛ|ПАПКА [--domain it]
    ingest-cpu.sh down    [--set empty|ready]
    ingest-cpu.sh status  [--set empty|ready]

`up` поднимает стенд ВМЕСТЕ с ingestion-api. Всё остальное - запросы, поиск, UI - живёт
на стенде и ингеста не требует.

`load` принимает и файл, и папку: в папке берутся `*.md` и `*.txt`, по алфавиту.

Наполнение занимает часы, а не минуты: на одном документе измерено около 33 с на чанк
(см. Demo/measured-load-time.md), а извлечение на процессоре медленнее ещё. Для быстрой
демонстрации набор `ready` выгружается архивом и восстанавливается без ингеста:
Demo/demo.sh export / restore.

Этот вариант существует для машин без видеокарты. Наполнение базы на процессоре занимает
часы на документ, поэтому для демонстрации он практически непригоден - им пользуются, когда
демо нужно показать на машине без GPU, а не когда готовят набор заранее.

На Windows аналогичного скрипта нет намеренно; что запускать вручную, написано в
docs/prototype_requirements.md §3.3.
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

# Оверлей ингеста подключается ВСЕГДА: это и есть назначение скрипта. Без него
# ingestion-api не существует и грузить нечем.
compose_cmd() {
    # Аргументы вызова сохраняются ДО `set --`. Внутри функции `set --` заменяет
    # позиционные параметры, и подкоманда (`up`, `down`, `status`) вместе с флагами
    # пропала бы молча: compose получил бы только список файлов, а скрипт - успех.
    # Раскрытие `$_compose_tail` без кавычек здесь безопасно: токены compose
    # (подкоманда, `-d`, `--wait`, `--wait-timeout`, число) пробелов не содержат.
    _compose_tail=$*
    set -- --project-directory "$INFRA_DIR" --file "$COMPOSE_RUNTIME"
    set -- "$@" --file "$COMPOSE_INGEST" --profile ingest
    # shellcheck disable=SC2086
    docker compose "$@" $_compose_tail
}

container_running() {
    docker ps --filter "name=^/$1$" --format '{{.Names}}' | grep -q .
}

container_password() {
    docker exec "$NEO4J_CONTAINER" printenv NEO4J_AUTH 2>/dev/null | sed 's#.*/##' || true
}

set_common_env() {
    VOL="$SET"
    NEO4J_PASSWORD="$DEMO_PASSWORD"
    GRAPH_AUTH_API_KEY="$DEMO_API_KEY"
    export VOL NEO4J_PASSWORD GRAPH_AUTH_API_KEY
}

upload_doc() {
    src=$1
    name=$(basename -- "$src")
    echo "Загружаю $src как docs/$name"
    docker cp "$src" "$INGEST_CONTAINER:/tmp/demo_doc.md"

    # Запросы идут изнутри контейнера: его Python уже есть, и это снимает зависимость
    # от Python на хосте и от вложенных кавычек в sh.
    docker exec -i "$INGEST_CONTAINER" python - "$LOAD_DOMAIN" "$name" <<'PY'
import json
import sys
import time
import urllib.request

domain, name = sys.argv[1], sys.argv[2]
API = "demo-key-2026"
BASE = "http://localhost:8002"
SOURCE_URL = "docs/" + name


def call(method, url, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", "X-API-Key": API},
    )
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


text = open("/tmp/demo_doc.md", encoding="utf-8").read()

try:
    st = call("POST", f"{BASE}/api/v1/ingestion/documents/status",
              {"source_url": SOURCE_URL, "domain": domain,
               "doc_type": "md", "content": text})
    if st.get("unchanged"):
        print(f"  уже загружен в текущем виде (версия {st.get('version')}). Пропуск.")
        sys.exit(0)
    print(f"  загружен ранее: {st.get('known')}")
except Exception as exc:
    print(f"  спросить статус не удалось ({exc}). Продолжаю без проверки.")

job = call("POST", f"{BASE}/api/v1/ingestion/documents",
           {"source_url": SOURCE_URL, "domain": domain,
            "doc_type": "md", "content": text})
job_id = job["job_id"]
print(f"  джоба {job_id}, жду...")

final = None
for _ in range(3600):
    time.sleep(5)
    final = call("GET", f"{BASE}/api/v1/ingestion/jobs/{job_id}")
    if final.get("status") in ("succeeded", "failed", "cancelled"):
        break
else:
    raise SystemExit(f"  джоба {job_id} не завершилась за 3 часа")

print(f"  статус: {final['status']}")
if final.get("total_duration_ms"):
    print(f"  всего {final['total_duration_ms']} мс")
    print(f"  по стадиям {json.dumps(final.get('stage_durations_ms'), ensure_ascii=False)}")
if final["status"] != "succeeded":
    raise SystemExit(f"  джоба завершилась статусом {final['status']}")

# ПОТЕРЯ СЛОЯ LLM - ГЛАВНАЯ ПРОВЕРКА ЗДЕСЬ. Джоба завершается `succeeded` и при полной
# потере: конвейер уничтожает негодный LLM-слой и идёт дальше (ADR-032). Статус джобы
# этого не показывает, общее число узлей в базе - тоже (оно велико от других документов).
# Единственный канал, где потеря видна, - `enrichment` в ответе джобы.
enrichment = final.get("enrichment") or {}
lost_entities = int(enrichment.get("lost_entities") or 0)
lost_edges = int(enrichment.get("lost_edges") or 0)
cause = enrichment.get("cause")
if cause:
    print(f"  ДЕГРАДАЦИЯ: причина {cause}, потеряно сущностей {lost_entities}, связей {lost_edges}")
    print(f"    объявлено связей {enrichment.get('llm_relations_declared')}, "
          f"уникальных {enrichment.get('llm_relations_unique')}"
          f" | строк ответа {enrichment.get('llm_answer_lines')}, "
          f"уникальных {enrichment.get('llm_answer_lines_unique')}")
if lost_entities or lost_edges:
    raise SystemExit(
        f"  ПОТЕРЯ LLM-СЛОЯ: {lost_entities} сущностей и {lost_edges} связей уничтожены, "
        f"причина {cause}. Документ записан в базу БЕЗ этого слоя, а джоба числится "
        f"успешной. Повторная загрузка того же content_hash - no-op, экстракция не "
        f"перезапустится."
    )
print("  загрузка завершилась, потерь нет")
PY
}

# Проверка графа - НА ХОСТЕ. Внутри контейнера `docker` нет, и вызов оттуда падал бы
# FileNotFoundError уже после успешной загрузки, то есть выглядел бы как сбой загрузки.
assert_graph_grew() {
    echo '  проверяю, что граф вырос'
    pw=$(container_password)
    if [ -z "$pw" ]; then
        echo '  ВНИМАНИЕ: количество нод НЕ измерено: не удалось получить пароль из контейнера' >&2
        return 0
    fi
    out=$(docker exec "$NEO4J_CONTAINER" cypher-shell -u neo4j -p "$pw" --format plain \
        'MATCH (n:ContextNode) RETURN count(*) AS c;' 2>/dev/null || true)
    count=$(printf '%s\n' "$out" | tail -n +2 | tr -cd '0-9')
    if [ -z "$count" ]; then
        echo '  ВНИМАНИЕ: количество нод НЕ измерено: запрос к базе не прошёл' >&2
        return 0
    fi
    echo "  ContextNode в базе: $count"
    if [ "$count" = "0" ]; then
        die 'граф пуст, а джоба succeeded - это vector-only загрузка'
    fi
}

    cmd_up() {
    set_common_env
    compose_cmd up -d --wait --wait-timeout "$WAIT_TIMEOUT"
    echo "База набора '$SET' поднята вместе с ingestion-api."
    echo 'После наполнения опустите ингест: ingest.sh down'
    echo 'Стенд поднимается отдельно: demo.sh up'
}

cmd_down() {
    set_common_env
    compose_cmd down
    echo "Набор '$SET' остановлен. Тома сохранены, наполнение осталось."
    echo 'Запросы работают без ингеста: demo.sh up'
}

cmd_load() {
    [ -n "$LOAD_PATH" ] || die "нужен --path ФАЙЛ или --path ПАПКА"
    container_running "$NEO4J_CONTAINER" || die "база не поднята. Сначала: ingest.sh up"
    container_running "$INGEST_CONTAINER" || die "ingestion-api не запущен. Нужен: ingest.sh up"

    if [ -d "$LOAD_PATH" ]; then
        # `find` идёт в цикл через `while read`, а не через `for x in $(...)`:
        # подстановка списка в `for` требует разбора командной строки, и `dash`
        # спотыкается на скобках `\( -name ... \)`.
        total=0
        list=$(find "$LOAD_PATH" -maxdepth 1 -type f \( -name '*.md' -o -name '*.txt' \) \
            | LC_ALL=C sort)
        [ -n "$list" ] || die "в папке $LOAD_PATH нет файлов *.md и *.txt"
        total=$(printf '%s\n' "$list" | wc -l | tr -d ' ')
        echo "В папке найдено файлов: $total. Порядок - по алфавиту."
        printf '%s\n' "$list" | while IFS= read -r src; do
            [ -n "$src" ] || continue
            upload_doc "$src"
            assert_graph_grew
            echo ''
        done
        echo "Загружено файлов: $total"
    else
        [ -f "$LOAD_PATH" ] || die "путь не найден: $LOAD_PATH"
        upload_doc "$LOAD_PATH"
        assert_graph_grew
        echo 'Загружено.'
    fi
}

cmd_status() {
    echo '=== контейнеры ==='
    running=$(docker ps --filter 'name=ohw-runtime' --format '{{.Names}}' || true)
    if [ -z "$running" ]; then
        echo '  ни одного. Ни стенд, ни ингест не подняты.'
    else
        printf '%s\n' "$running" | while read -r name; do
            [ -n "$name" ] && echo "  $name"
        done
    fi
    if ! container_running "$INGEST_CONTAINER"; then
        echo '  ingestion-api не запущен - грузить нечем, читать можно.'
    fi

    echo ''
    echo "=== наполненность набора '$SET' ==="
    if ! container_running "$NEO4J_CONTAINER"; then
        echo '  Neo4j не отвечает. Это не «база пуста», это «базы нет».'
        return 0
    fi
    pw=$(container_password)
    if [ -z "$pw" ]; then
        echo '  не удалось получить пароль из контейнера - количество нод НЕ измерено.'
        return 0
    fi
    rows=$(docker exec "$NEO4J_CONTAINER" cypher-shell -u neo4j -p "$pw" --format plain \
        'MATCH (n) RETURN labels(n)[0] AS label, count(*) AS c ORDER BY c DESC;' 2>/dev/null || true)
    if [ -z "$rows" ]; then
        echo '  база пуста: нод нет ни одного.'
        return 0
    fi
    printf '%s\n' "$rows" | tail -n +2 | while read -r line; do
        [ -n "$line" ] && echo "  $line"
    done
    case "$rows" in
        *ContextNode*0*)
            echo 'ВНИМАНИЕ: Chunk есть, а ContextNode нет - это vector-only загрузка.' >&2
            echo 'ВНИМАНИЕ: Джоба при этом завершается succeeded, то есть выглядит как успех.' >&2
            ;;
    esac
}

case "$SET" in
    empty|ready) ;;
    *) die "--set принимает empty или ready, получено: $SET" ;;
esac

COMMAND=$1
shift

while [ $# -gt 0 ]; do
    case "$1" in
        --set) SET="$2"; shift 2 ;;
        --wait-timeout) WAIT_TIMEOUT="$2"; shift 2 ;;
        --path) LOAD_PATH="$2"; shift 2 ;;
        --domain) LOAD_DOMAIN="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) die "неизвестный аргумент: $1" ;;
    esac
done

case "$COMMAND" in
    -h|--help|help)
        usage
        exit 0
        ;;
    up|down|status|load)
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
    load) cmd_load ;;
esac
