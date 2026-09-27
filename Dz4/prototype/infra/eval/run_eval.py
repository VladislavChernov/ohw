"""Eval-раннер (ADR-015): ingest corpus → baseline vs hybrid → lift-отчёт.

Запуск:
    python infra/eval/run_eval.py --domain it --corpus docs/ --dataset infra/eval/it/questions.jsonl --out reports/
    python infra/eval/run_eval.py --domain it --mode baseline  # только vector-only
    python infra/eval/run_eval.py --domain it --mode hybrid    # graph + vector

Env:
    EVAL_LLM_ADAPTER=openai|fake|none — judge (defaults to openai for full runs)
    RETRIEVAL_GRAPH_ENABLED=true|false — override graph axis
    INGESTION_URL, QUERY_URL, X_API_KEY — API endpoints

Fail-fast (не тратим время на упавший стек):
    Перед прогоном раннер health-пробами проверяет доступность контуров,
    необходимых для сконфигурированных адаптеров; при падении обязательного —
    отчёт в ``<out>/preflight.log`` и выход с кодом 1.
    Ожидание джоб ограничено разделёнными окнами (B3), а не одним общим дедлайном:
    ``--unreachable-timeout`` (опросы не отвечают ⇒ стенд недоступен),
    ``--job-timeout`` (опросы идут, но джоба не двигается) и ``--wait-ceiling``
    (жёсткий кап на пачку). Условия взаимоисключающие, поэтому соотношение констант
    ничего не решает. Фаза подачи ограничена отдельно: ``--slot-timeout`` на документ
    и ``--submit-budget`` на весь корпус.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from graphrag_proto.eval.metrics import (
    generation_metrics,
    graph_contribution,
    lift_report,
    retrieval_metrics,
)

INGESTION_URL = os.environ.get("INGESTION_URL", "http://localhost:8002")
QUERY_URL = os.environ.get("QUERY_URL", "http://localhost:8000")
X_API_KEY = os.environ.get("X_API_KEY") or os.environ.get("GRAPH_AUTH_API_KEY", "changeme")

# Политика ожидания (B3). Пять величин, и каждая ограничивает своё: смешивать их
# нельзя, потому что «бюджет» одного этапа под именем другого — это ловушка, а не
# настройка.
#
# Окна разделены РАЗДЕЛИТЕЛЕМ, а не порядком срабатывания: два независимых таймаута плюс
# «первым сработает меньший» дают конструкцию, в которой при неверном соотношении
# констант одно окно становится мёртвым кодом — ровно тот баг, который чинится.
DEFAULT_UNREACHABLE_TIMEOUT_S = 300.0  # опросы не отвечают ⇒ стенд недоступен
DEFAULT_JOB_TIMEOUT_S = 900.0          # опросы идут, но эта джоба не двигается
DEFAULT_WAIT_CEILING_S = 7200.0        # жёсткий кап ожидания джоб (backstop)
# Бюджеты фазы ПОДАЧИ (POST /documents), отдельной от ожидания джоб: 429 означает «слот
# занят», и очередь на полном корпусе законна, поэтому слот ждём терпеливо, а всю фазу
# ограничиваем отдельно — иначе N документов умножают лимит на себя.
DEFAULT_SLOT_TIMEOUT_S = 300.0         # свободный слот исполнителя для одного документа
DEFAULT_SUBMIT_BUDGET_S = 7200.0       # вся фаза подачи корпуса
# Таймаут одного HTTP-вызова LLM (не окно ожидания джоб): та же величина, что была
# до B3, но с собственным именем — иначе «300 с» означал бы сразу три разных вещи.
DEFAULT_LLM_TIMEOUT_S = 300.0

# Wire-контракт с ingestion-сервисом, а не общий модуль: раннер общается с пайплайном
# по HTTP. Владелец префикса — ingestion-сервис (`app.ENRICHMENT_DEGRADED_PREFIX`).
# Согласованность сторон закреплена тестами job API и build_ingest_report.
ENRICHMENT_DEGRADED_PREFIX = "enrichment_degraded"
# Имена структурных сигналов в ответе джобы (владелец — ingestion-service,
# `app.ENRICHMENT_DEGRADED_SIGNAL` / `app.LLM_LAYER_DROPPED_SIGNAL`). Оба факта
# приходят одним каналом — полем `signals`. Пока не существовало структурного
# канала, деградация читалась из текста сообщения по префиксу; это значило, что одна
# рефакторинга формата тихо обнулила бы метрику потерь, а «мы никогда не теряем
# слой» выглядело бы как хорошая новость. Тихий ноль в метрике потерь — худший вид
# поломки, он не выглядит как поломка.
ENRICHMENT_DEGRADED_SIGNAL = "enrichment_degraded"
LLM_LAYER_DROPPED_SIGNAL = "llm_layer_dropped"
DEFAULT_CONTOUR_TIMEOUT_S = 5.0

EVAL_K = 5
_REVISION_RE = re.compile(r"^[0-9a-fA-F]{64}$")

# generation.mode (design.md §5.2): judge / n/a / skipped / empty
GENERATION_MODE_SKIPPED = "skipped"
GENERATION_MODE_NO_JUDGE = "n/a"

# Факторы парности прогонов (design.md §5.1: манифесты парных веток различаются
# не более чем в одном поле фактора).
PAIR_FACTOR_FIELDS = ("mode", "graph_enabled")
PAIR_INVARIANT_FIELDS = (
    "code_commit",
    "code_tree",
    "domain",
    "revision",
    "revision_fingerprint",
    "datasets",
    "corpus",
    "corpus_documents",
    "corpus_limit",
    "source_prefix",
    "k",
    "components",
    "projection",
    "generation",
    "flags",
)


def _api_headers() -> dict[str, str]:
    return {"X-API-Key": X_API_KEY, "Content-Type": "application/json"}


def _post_json(url: str, body: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_api_headers(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} {url}: {exc.read()[:200]!r}") from exc


def _get_json(url: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers=_api_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} {url}") from exc


# ---------------------------------------------------------------------------
# Preflight: fail-fast проверка доступности контуров (не ждём на упавший стек)
# ---------------------------------------------------------------------------

def _probe_http(base_url: str, timeout_s: float = DEFAULT_CONTOUR_TIMEOUT_S) -> tuple[bool, str]:
    """Health-проба HTTP-сервиса (GET /health) с коротким таймаутом."""
    url = f"{base_url.rstrip('/')}/health"
    req = urllib.request.Request(url, headers=_api_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return resp.status == 200, f"HTTP {resp.status}"
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return False, str(exc)


def _probe_bolt(uri: str, timeout_s: float = DEFAULT_CONTOUR_TIMEOUT_S) -> tuple[bool, str]:
    """TCP-проба bolt://host:port (Neo4j) с коротким таймаутом."""
    try:
        rest = uri.split("://", 1)[1]
    except IndexError:
        return False, f"невалидный bolt-URI: {uri!r}"
    host, _, port_raw = rest.partition(":")
    if not host:
        return False, f"нет хоста в bolt-URI: {uri!r}"
    try:
        port: int = int(port_raw) if port_raw else 7687
    except ValueError:
        return False, f"невалидный порт в bolt-URI: {uri!r}"
    try:
        with socket.create_connection((host, port), timeout=timeout_s):
            return True, f"TCP {host}:{port}"
    except OSError as exc:
        return False, str(exc)


def required_contours(
    *,
    include_generation: bool = True,
    include_ingestion: bool = False,
) -> list[dict[str, Any]]:
    """Контуры, нужные для сконфигурированных адаптеров (env): ingestion + backend'ы.

    Всегда требуется Ingestion API (revision fetch). Neo4j/embeddings/reranker/llm
    проверяются, только если соответствующий адаптер выбран env-переменными.
    `include_generation=False` (--retrieval-only) исключает LLM генерации;
    `include_ingestion=True` сохраняет LLM-контур, если ingestion использует
    `EXTRACT_LLM=true`.
    """
    contours: list[dict[str, Any]] = [
        {"name": "ingestion-api", "kind": "http", "target": INGESTION_URL},
    ]
    config_url = os.environ.get("CONFIG_URL", "").strip()
    if config_url:
        contours.append({"name": "config-service", "kind": "http", "target": config_url})
    if os.environ.get("GRAPH_STORE", "inmemory").strip().lower() == "neo4j" or os.environ.get("VECTOR_STORE", "inmemory").strip().lower() == "neo4j":
        contours.append({"name": "neo4j", "kind": "bolt", "target": os.environ.get("NEO4J_URI", "bolt://neo4j:7687")})
    if os.environ.get("EMBEDDER", "deterministic").strip().lower() == "bge_m3_service":
        contours.append({"name": "embeddings-service", "kind": "http", "target": os.environ.get("EMBEDDINGS_URL", "http://embeddings-service:8004")})
    if os.environ.get("RERANKER", "noop").strip().lower() == "bge_reranker":
        contours.append({"name": "reranker-service", "kind": "http", "target": os.environ.get("RERANKER_URL", "http://reranker:8006")})
    ingestion_llm = include_ingestion and os.environ.get("EXTRACT_LLM", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    generation_llm = include_generation and (
        os.environ.get("LLM_ADAPTER", "openai").strip().lower() == "openai"
        or os.environ.get("EVAL_LLM_ADAPTER", "openai").strip().lower() == "openai"
    )
    if ingestion_llm or generation_llm:
        contours.append({"name": "llm", "kind": "http", "target": os.environ.get("LLM_BASE_URL", "http://llm:8080")})
    return contours


def preflight(
    out_dir: Path,
    timeout_s: float = DEFAULT_CONTOUR_TIMEOUT_S,
    *,
    include_generation: bool = True,
    include_ingestion: bool = False,
) -> bool:
    """Проверить доступность контуров; результаты — в лог и в reports/preflight.log.

    Возвращает True, если все обязательные контуры живы; False — прерывать прогон.
    """
    contours = required_contours(
        include_generation=include_generation,
        include_ingestion=include_ingestion,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "preflight.log"

    lines = [
        f"# Preflight (fail-fast) {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        f"Ingestion URL: {INGESTION_URL} | Query URL: {QUERY_URL}",
    ]
    all_ok = True
    for contour in contours:
        name, kind, target = contour["name"], contour["kind"], contour["target"]
        if kind == "http":
            ok, detail = _probe_http(target, timeout_s)
        else:
            ok, detail = _probe_bolt(target, timeout_s)
        status = "OK" if ok else "DOWN"
        print(f"preflight: [{status}] {name} ({kind}) {target} — {detail}")
        lines.append(f"{status}\t{name}\t{kind}\t{target}\t{detail}")
        all_ok = all_ok and ok

    verdict = "READY" if all_ok else "FAIL"
    lines.append(f"verdict: {verdict}")
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"preflight: verdict={verdict}; отчёт: {log_path}")
    return all_ok


# ---------------------------------------------------------------------------
# Corpus ingestion (through Ingestion API :8002)
# ---------------------------------------------------------------------------

def select_corpus_files(
    corpus_dir: Path,
    *,
    documents: list[str] | None = None,
    limit: int | None = None,
) -> list[tuple[Path, str]]:
    """Выбрать документы корпуса: вернуть отсортированный список (путь, source_url).

    По умолчанию — все ``*.md`` рекурсивно (прежнее поведение). ``--documents``
    ограничивает список явными путями относительно корпуса, ``--limit-docs``
    берёт первые N после сортировки — оба нужны для усечённых (smoke) прогонов,
    где полный ingest неподъёмен по времени.

    ``source_url`` = ``<source_prefix>/<relpath>`` — тот же контракт, что и раньше,
    поэтому ``golden_sources`` датасета продолжают совпадать.
    """
    if documents:
        selected: list[Path] = []
        for rel in documents:
            candidate = (corpus_dir / rel).resolve()
            # Защита от выхода за пределы корпуса (--documents не должен читать что угодно).
            if not str(candidate).startswith(str(corpus_dir.resolve())):
                raise ValueError(f"документ вне корпуса: {rel!r}")
            if not candidate.is_file():
                raise FileNotFoundError(f"документ не найден в корпусе: {rel!r}")
            selected.append(candidate)
    else:
        selected = sorted(corpus_dir.rglob("*.md"))
    if limit is not None:
        if limit <= 0:
            raise ValueError(f"--limit-docs должен быть положительным, получено {limit}")
        selected = selected[:limit]
    return [
        (path, str(path.relative_to(corpus_dir)).replace("\\", "/")) for path in selected
    ]


def ingest_corpus(
    corpus_dir: Path,
    domain: str,
    source_prefix: str = "",
    slot_timeout_s: float = DEFAULT_SLOT_TIMEOUT_S,
    *,
    submit_budget_s: float = DEFAULT_SUBMIT_BUDGET_S,
    documents: list[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Загрузить выбранные .md в Ingestion API (async). Возвращает записи джобов.

    Каждая запись несёт ``source_url``/``bytes``/``job_id``/``submitted_at`` —
    этого хватает ``ingest_report.json`` для попер-документной экстраполяции
    времени (стоимость ingest зависит от размера документа, а не от их числа).

    **Два разных бюджета, потому что очередь законна.** ``slot_timeout_s`` ждёт свободного
    слота исполнителя для одного документа: 429 означает «сервис жив и занят», и на полном
    корпусе с двумя слотами это норма, а не авария — поэтому попытка повторяется, пока не
    выйдет этот бюджет. ``submit_budget_s`` ограничивает **всю** фазу подачи целиком: без
    него ``N`` документов умножили бы ``slot_timeout_s`` на себя, и «бюджет» в
    ``run_manifest.timeouts`` не был бы бюджетом. Раньше дедлайн считался на документ, а поле
    называлось «жёсткий кап на всю пачку», то есть имя не соответствовало поведению.
    """
    job_ids: list[dict[str, Any]] = []
    selected = select_corpus_files(corpus_dir, documents=documents, limit=limit)
    submit_deadline = time.monotonic() + submit_budget_s
    for md_file, rel in selected:
        source_url = f"{source_prefix}/{rel}" if source_prefix else rel
        content = md_file.read_text(encoding="utf-8")
        body = {
            "source_url": source_url,
            "domain": domain,
            "doc_type": "md",
            "content": content,
        }
        deadline = min(time.monotonic() + slot_timeout_s, submit_deadline)
        while True:
            try:
                resp = _post_json(
                    f"{INGESTION_URL}/api/v1/ingestion/documents",
                    body,
                )
                break
            except RuntimeError as exc:
                # 429: все слоты исполнения заняты — ждём и повторяем (async-пайплайн).
                if "429" not in str(exc):
                    raise
                if time.monotonic() >= deadline:
                    # Различаем, что кончилось: слот для этого документа или бюджет всей
                    # фазы подачи. Сообщение обязано называть, потому что лечится это
                    # по-разному: слот — подождать или снизить параллелизм, бюджет — снять
                    # лимит с корпуса либо поднять `submit_budget_s`.
                    budget = (
                        f"бюджет фазы подачи {submit_budget_s:.0f}с исчерпан"
                        if time.monotonic() >= submit_deadline
                        else f"слот исполнителя не освободился за {slot_timeout_s:.0f}с"
                    )
                    raise RuntimeError(f"{budget} для {source_url}") from exc
                time.sleep(5.0)
        job_id = resp.get("job_id")
        if not job_id:
            raise RuntimeError(f"ingestion не вернул job_id для {source_url}")
        job_ids.append(
            {
                "source_url": source_url,
                "relpath": rel,
                "bytes": len(content.encode("utf-8")),
                "job_id": str(job_id),
                "submitted_at": time.time(),
            }
        )
    if not job_ids:
        raise RuntimeError(f"корпус не содержит .md документов: {corpus_dir}")
    return job_ids


TERMINAL_JOB_STATUSES = {"succeeded", "failed", "cancelled"}


def _progress_signature(job: dict[str, Any]) -> tuple[Any, ...]:
    """Отпечаток прогресса джобы: чем он отличается от молчания.

    Учитываются статус, текущая стадия и ``(stage, status, ts)`` всех стадий —
    ровно те поля, которые пайплайн меняет по мере движения. ``message`` намеренно
    НЕ учитывается: ``note_stage`` пишет туда ``enrichment_degraded`` (B1) и не
    двигает стадию, поэтому такой «прогресс» держал бы прогон живым молча.
    """
    stages = job.get("stages") or []
    return (
        str(job.get("status") or ""),
        str(job.get("stage") or ""),
        tuple(
            (str(entry.get("stage") or ""), str(entry.get("status") or ""), str(entry.get("ts") or ""))
            for entry in stages
            if isinstance(entry, dict)
        ),
    )


def _pending_detail(
    pending: list[str],
    last_seen: dict[str, dict[str, Any]],
) -> str:
    """Диагностика висящих джоб: стадия каждой, а не голый список id.

    По последней известной стадии видно, где именно прогон встал (INGEST / EXTRACT /
    ...), иначе таймаут неотличим от «сеть отвалилась» и упирается в перебор логов вслепую.
    """
    return ", ".join(
        f"{job_id}@{(last_seen.get(job_id) or {}).get('stage') or '?'}" for job_id in pending
    )


def wait_jobs(
    job_ids: list[str],
    poll_s: float = 5.0,
    *,
    unreachable_timeout_s: float = DEFAULT_UNREACHABLE_TIMEOUT_S,
    job_timeout_s: float = DEFAULT_JOB_TIMEOUT_S,
    ceiling_s: float = DEFAULT_WAIT_CEILING_S,
    clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> dict[str, dict[str, Any]]:
    """Дождаться завершения async-джобов ingestion (200 → task complete).

    Возвращает ``job_id → тело ответа GET /jobs/{id}`` (в т.ч. ``stage``), а не
    только статус: ``stage == "INGEST"`` при ``succeeded`` — признак no-op
    (документ не изменился, пайплайн встал после INGEST и не дошёл до EXTRACT).

    **Окна разделены разделителем, а не соотношены порядком.** Два независимых
    таймаута плюс «первым срабатывает меньший» — это конструкция, которая ломается
    конфигурацией: если одно окно меньше другого, второе становится мёртвым кодом, и
    ровно тот баг, который чинится, возвращается молча. Поэтому условия тут
    взаимоисключающие, и ни одна конфигурация ничего не решает:

    * ``unreachable_timeout_s`` — **опросы не отвечают** всё это время ⇒ стенд
      недоступен. Считается по неудачным опросам, а не по отсутствию прогресса:
      «мы не видим джоб» и «джобы не двигаются» — разные факты.
    * ``job_timeout_s`` — **опросы отвечают, но конкретная джоба не двигается**
      ⇒ виновата названная джоба и её стадия. Это общий случай живого стенда,
      включая хвост батча, где не завершена одна джоба.
    * ``ceiling_s`` — жёсткий кап на всю пачку: страховка от «медленно, но живо».

    «Прогресс» — не эвристика по таймеру, а изменение отпечатка ``_progress_signature``
    между опросами.

    ``clock``/``sleeper`` — внедряемый источник времени: тесты идут секундами
    модельного времени, без стенда.
    """
    now = clock or time.monotonic
    sleep = sleeper or time.sleep
    started = now()
    statuses: dict[str, dict[str, Any]] = {}
    last_seen: dict[str, dict[str, Any]] = {}
    signatures: dict[str, tuple[Any, ...]] = {}
    last_progress: dict[str, float] = {}
    last_progress_any = started
    unreachable_since: float | None = None

    while len(statuses) < len(job_ids):
        current = now()
        if current - started > ceiling_s:
            pending = [job_id for job_id in job_ids if job_id not in statuses]
            raise TimeoutError(
                f"исчерпан жёсткий кап ожидания {ceiling_s:.0f}s: не завершены джобы "
                f"{pending} (стадия на момент таймаута: {_pending_detail(pending, last_seen)})"
            )
        polled = failed = 0
        for job_id in job_ids:
            if job_id in statuses:
                continue
            try:
                resp = _get_json(f"{INGESTION_URL}/api/v1/ingestion/jobs/{job_id}")
            except (RuntimeError, OSError):
                # RuntimeError — приведённая ошибка HTTP-контура, OSError (включая
                # URLError) — недоступность стенда. Уже��о другое не ловим: дефект нашего
                # кода обязан упасть громко, а не выглядеть как «стенд молчит».
                failed += 1
                continue
            polled += 1
            last_seen[job_id] = resp
            signature = _progress_signature(resp)
            if signatures.get(job_id) != signature:
                signatures[job_id] = signature
                last_progress[job_id] = current
                last_progress_any = current
            status = str(resp.get("status", ""))
            if status in TERMINAL_JOB_STATUSES:
                statuses[job_id] = resp
            if status in {"failed", "cancelled"}:
                raise RuntimeError(f"джоба {job_id} завершилась статусом {status}: {resp.get('error')}")
        pending = [job_id for job_id in job_ids if job_id not in statuses]
        if not pending:
            break
        # --- разделитель: стенд отвечает или нет
        if polled == 0 and failed:
            if unreachable_since is None:
                unreachable_since = current
            for job_id in pending:
                # джобы, до которых не достучались, не получают отметку времени: их
                # состояние неизвестно, а не «зависло N секунд»
                last_progress.pop(job_id, None)
            silent_for = current - unreachable_since
            if silent_for > unreachable_timeout_s:
                raise TimeoutError(
                    f"стенд не отвечает {silent_for:.0f}s (окно {unreachable_timeout_s:.0f}s): "
                    f"не удалось опросить джобы {pending} "
                    f"(последняя известная стадия: {_pending_detail(pending, last_seen)})"
                )
        else:
            unreachable_since = None
            stuck = [
                job_id
                for job_id in pending
                if current - last_progress.get(job_id, started) > job_timeout_s
            ]
            if stuck:
                worst = max(current - last_progress.get(job_id, started) for job_id in stuck)
                raise TimeoutError(
                    f"джобы {stuck} не продвинулись {worst:.0f}s "
                    f"(окно {job_timeout_s:.0f}s) при живом стенде; ещё движется "
                    f"{len(pending) - len(stuck)} из {len(pending)} "
                    f"(стадия на момент таймаута: {_pending_detail(stuck, last_seen)})"
                )
        sleep(poll_s)
    return statuses


def _int_field(source: dict[str, Any], name: str) -> int:
    """Целое из структурного поля; отсутствие/мусор — ноль, а не исключение.

    Отчёт о прогоне не должен падать из-за одного нечитаемого значения: тогда вместо
    данных о потере получается стоп-слово «ошибка чтения отчёта», а потеря как раз
    требовала внимания.
    """
    try:
        return int(source.get(name) or 0)
    except (TypeError, ValueError):
        return 0


def _extraction_stage_ran(job: dict[str, Any]) -> bool:
    """Выполнялась ли стадия EXTRACT у этой джобы.

    Независимый свидетель, а не счётчик: `job_stages` — журнал фактического
    прохождения стадий, тогда как объём записей живёт в `enrichment`. Два источника
    нужны именно потому, что независимы: сломавшийся счётчик не исчезнет из знаменателя,
    и дыра в одном станет видна по расхождению с другим.
    """
    for entry in job.get("stages") or []:
        if isinstance(entry, dict) and entry.get("stage") == "EXTRACT":
            return True
    return False


def _parse_enrichment_degradation(
    job: dict[str, Any],
) -> tuple[bool, str | None, bool, dict[str, Any]]:
    """Достать признаки деградации, потери LLM-слоя и количественные факты.

    Возвращает `(деградация, причина, слой_потерян, факты)`.

    **Флаги читаются из поля `signals`, а числа — из поля `enrichment`.** Оба канала
    структурные: если деградация приходит текстом, а потеря полем, у потребителя два
    механизма, а объяснение «почему так» становится историческим вместо замысла.
    Причина деградации остаётся свободным текстом, и это текст, но не источник
    счётчиков.

    **Зачем два флага.** `enrichment_degraded` ставится в двух местах с противоположным
    смыслом: профиль домена не загрузился (ничего не потеряно — LLM-слой и не пытались
    построить) и исключение внутри экстракции (потерян весь слой сущностей и связей).
    Сумма этих двух в одном счётчике неинтерпретируема: высокое число может означать
    нулевую потерю.

    **`llm_layer_dropped` теперь означает ровно «факты исчезли», а не «сработал путь
    сброса».** Сброс до первого ответа модели терять нечего, и раньше такой документ
    попадал в счётчик потерь, завышая его. Размер потери приходит отдельными числами
    (`lost_entities`/`lost_edges`), потому что флаг отвечает на вопрос «было ли что
    терять», а для решения «сколько» нужен размер.
    """
    signals = job.get("signals")
    flags = signals if isinstance(signals, dict) else {}
    enrichment = job.get("enrichment")
    facts = enrichment if isinstance(enrichment, dict) else {}
    reason: str | None = None
    for entry in job.get("stages") or []:
        if not isinstance(entry, dict) or entry.get("stage") != "EXTRACT":
            continue
        message = str(entry.get("message") or "")
        if message.startswith(f"{ENRICHMENT_DEGRADED_PREFIX}:"):
            reason = message[len(ENRICHMENT_DEGRADED_PREFIX) + 1 :].strip() or None
            break
    return (
        bool(flags.get(ENRICHMENT_DEGRADED_SIGNAL)),
        reason,
        bool(flags.get(LLM_LAYER_DROPPED_SIGNAL)),
        {
            "cause": str(facts["cause"]) if facts.get("cause") else None,
            "lost_entities": _int_field(facts, "lost_entities"),
            "lost_edges": _int_field(facts, "lost_edges"),
            "llm_records": _int_field(facts, "llm_records"),
            "llm_edges": _int_field(facts, "llm_edges"),
        },
    )


def build_ingest_report(
    submitted: list[dict[str, Any]],
    statuses: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Попер-документный отчёт ingest: время, статус, no-op-признак.

    Основа для экстраполяции «сколько будет стоить полный корпус»: время
    экстракции зависит от размера документа, поэтому среднее по счётчику
    не годится — нужен ряд «документ → секунды».
    """
    documents: list[dict[str, Any]] = []
    for record in submitted:
        job = statuses.get(record["job_id"], {})
        status = str(job.get("status", "unknown"))
        stage = job.get("stage")
        is_noop = status == "succeeded" and stage == "INGEST"
        degraded, degradation_error, layer_dropped, facts = _parse_enrichment_degradation(job)
        extraction_ran = _extraction_stage_ran(job)
        documents.append(
            {
                "source_url": record["source_url"],
                "relpath": record["relpath"],
                "bytes": record["bytes"],
                "job_id": record["job_id"],
                "status": status,
                "last_stage": stage,
                "noop": is_noop,
                "error": job.get("error"),
                "enrichment_degraded": degraded,
                "enrichment_error": degradation_error,
                "enrichment_cause": facts["cause"],
                "llm_layer_dropped": layer_dropped,
                # Свидетель: EXTRACT фактически выполнялся. Отдельно от `llm_layer_dropped`
                # и `records`, потому что «стадия не запускалась» (no-op тёплого корпуса) —
                # законное объяснение нуля, а «стадия была, а записей ноль» — повод
                # смотреть, а не вывод.
                "extraction_stage_ran": extraction_ran,
                "llm_records_extracted": facts["llm_records"],
                "llm_edges_extracted": facts["llm_edges"],
                "llm_layer_lost_entities": facts["lost_entities"],
                "llm_layer_lost_edges": facts["lost_edges"],
                "wall_time_s": round(float(record.get("waited_s") or 0.0), 2),
                "seconds_per_kb": (
                    round(float(record["waited_s"]) / max(record["bytes"] / 1024.0, 0.001), 2)
                    if not is_noop
                    else None
                ),
            }
        )
    cold = [d["wall_time_s"] for d in documents if not d["noop"]]
    noop = [d["wall_time_s"] for d in documents if d["noop"]]
    extraction_docs = [d for d in documents if d["extraction_stage_ran"]]
    llm_records_total = sum(d["llm_records_extracted"] for d in extraction_docs)
    causes: dict[str, int] = {}
    for doc in documents:
        cause = doc["enrichment_cause"]
        if cause:
            causes[cause] = causes.get(cause, 0) + 1
    return {
        "documents_total": len(documents),
        "noop_documents": len(noop),
        "cold_documents": len(cold),
        "enrichment_degraded_documents": sum(
            1 for d in documents if d["enrichment_degraded"]
        ),
        # Отдельно от umbrella-счётчика: сколько именно документов потеряло LLM-слой
        # целиком. Без этого числа деградацию нельзя читать как потерю — см.
        # `_parse_enrichment_degradation`.
        "llm_layer_dropped_documents": sum(1 for d in documents if d["llm_layer_dropped"]),
        # Сколько ФАКТОВ исчезло, а не сколько документов пострадало. Первое — величина
        # потери для планирования, второе — частота. Одно без другого неинтерпретируемо:
        # «2 документа» ничего не говорит о том, потеряны две записи или две тысячи.
        "llm_layer_lost_entities": sum(d["llm_layer_lost_entities"] for d in documents),
        "llm_layer_lost_edges": sum(d["llm_layer_lost_edges"] for d in documents),
        # Причины разведены поимённо: чинить профиль, промпт и модель — разные работы.
        "enrichment_causes": causes,
        # Знаменатель — документы, где экстракция ФАКТИЧЕСКИ выполнялась. Считать по
        # всем документам нельзя: у части EXTRACT не запускался (no-op тёплого корпуса,
        # детерминированный путь), и их нулевой вклад выглядел бы как «модель вернула
        # ноль», то есть как поломка.
        "documents_with_extraction_stage": len(extraction_docs),
        "llm_records_extracted": llm_records_total,
        # Место, где счётчик может сломаться: стадия была, а записей ноль. Само по себе
        # не приговор — модель действительно могла ничего не найти, — но такой документ
        # обязателен для ручной проверки, и поэтому назван числом, а не спрятан.
        "documents_with_extraction_stage_without_records": sum(
            1 for d in extraction_docs if not d["llm_records_extracted"]
        ),
        # Доля НЕ выводится при нулевом знаменателе: `0%` на пустой базе читается как
        # «модель не работает», хотя правильный смысл — «нечего было измерять».
        "llm_records_per_extraction_document": (
            round(llm_records_total / len(extraction_docs), 2) if extraction_docs else None
        ),
        "total_wall_time_s": round(sum(d["wall_time_s"] for d in documents), 2),
        "cold_wall_time_sum_s": round(sum(cold), 2),
        "noop_wall_time_sum_s": round(sum(noop), 2),
        "cold_wall_time_mean_s": round(sum(cold) / len(cold), 2) if cold else None,
        "documents": documents,
    }


def build_ingest_quality(ingest: dict[str, Any] | None) -> dict[str, Any]:
    """Плохие условия измерения, принесённые ингестом, в форме для вердикта.

    Живёт в отчёте рядом с метриками, а не только в `ingest_report.json`: вердикт
    вычисляется раньше, чем отчёт ingest дописывается, и потребитель вердикта не
    обязан читать второй файл, чтобы понять, можно ли этому вердикту верить.

    Считаются ТОЛЬКО те условия, которые делают измерение смещённым:

    * потеря LLM-слоя смещает `recall_graph`/`necessity`/`delta_recall` вниз —
      отсутствующие факты нельзя «вспомнить», поэтому недооценка тут односторонняя;
    * потеря LLM-слоя смещает и groundedness/coverage: эталонные факты, которые модель
      не извлекла, не попадут ни в один ответ.

    Деградация БЕЗ потери слоя (профиль не загрузился, экстракция не запускалась) сюда
    не попадает намеренно: слой не строился, терять было нечего, и векторная ось не
    затронута. Считать это смещением — значит заблокировать вердикт прогона, у
    которого деградация ничего не испортила.

    **Правило: потеря инвалидирует прогон, а не предупреждает о нём.** Для
    смещённой величины «прогон прошёл» не является утверждением о системе, а суждением
    о прогоне, который этого не доказывает. Раньше та же мысль выражалась словами в
    паспорте прог��на, то есть была нечитаема для автоматики.
    """
    if not ingest:
        return {
            "llm_layer_dropped_documents": 0,
            "llm_layer_lost_entities": 0,
            "llm_layer_lost_edges": 0,
            "enrichment_degraded_documents": 0,
            "loss": False,
        }
    dropped = int(ingest.get("llm_layer_dropped_documents") or 0)
    lost_entities = int(ingest.get("llm_layer_lost_entities") or 0)
    lost_edges = int(ingest.get("llm_layer_lost_edges") or 0)
    return {
        "llm_layer_dropped_documents": dropped,
        "llm_layer_lost_entities": lost_entities,
        "llm_layer_lost_edges": lost_edges,
        "enrichment_degraded_documents": int(ingest.get("enrichment_degraded_documents") or 0),
        "enrichment_causes": ingest.get("enrichment_causes") or {},
        "documents_with_extraction_stage": int(
            ingest.get("documents_with_extraction_stage") or 0
        ),
        "llm_records_extracted": int(ingest.get("llm_records_extracted") or 0),
        "llm_records_per_extraction_document": ingest.get(
            "llm_records_per_extraction_document"
        ),
        # Слой потерян, если исчезли факты, а не если сработал путь сброса. Ноль
        # потерь при ненулевой деградации — законное состояние.
        "loss": dropped > 0 or lost_entities > 0 or lost_edges > 0,
    }


# ---------------------------------------------------------------------------
# Revision fetch
# ---------------------------------------------------------------------------

def _invalid_revision(domain: str) -> RuntimeError:
    return RuntimeError(f"revision для домена {domain!r} некорректна")


def fetch_revision(domain: str) -> str:
    """GET /api/v1/ingestion/revision?domain= → non-empty revision string."""
    try:
        resp = _get_json(f"{INGESTION_URL}/api/v1/ingestion/revision?domain={domain}")
    except RuntimeError as exc:
        raise RuntimeError(f"не удалось получить revision для домена {domain!r}") from exc
    if not isinstance(resp, dict):
        raise _invalid_revision(domain)
    rev = resp.get("revision")
    if not isinstance(rev, str) or not _REVISION_RE.fullmatch(rev):
        raise _invalid_revision(domain)
    return rev


# ---------------------------------------------------------------------------
# Dataset loading (ADR-015 format + v2 delta, design.md §4)
# ---------------------------------------------------------------------------

# Официальные значения v2 (design.md §4) + совместимость с пилотом docs-review:
# пилот использует reasoning_type {historical-fact, conditional, evidence-boundary,
# insufficient-evidence}, answerability="partial", evidence_policy="alternatives".
REASONING_TYPES = frozenset(
    {
        "single-hop",
        "cross-document",
        "temporal-cross-document",
        "contradiction",
        "causal-risk",
        "multi-hop",
        "historical-fact",
        "conditional",
        "evidence-boundary",
        "insufficient-evidence",
    }
)
ANSWERABILITY_TYPES = frozenset({"answerable", "unanswerable", "partial"})
EVIDENCE_POLICIES = frozenset({"joint", "any", "graph_required", "alternatives"})

_AS_OF_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}Z)?")


def validate_question(q: dict[str, Any]) -> None:
    """Валидация вопроса eval (v2, ADR-015 delta): обязательные поля + типы
    опциональных v2-полей; `evidence_policy=graph_required` влечёт
    `golden_graph_evidence: true`. Наборы без v2-полей остаются валидными."""
    qid = repr(q.get("id"))
    for key in ("id", "query", "golden_sources", "golden_facts"):
        if not q.get(key):
            raise ValueError(f"вопрос без обязательного поля {key!r}: {qid}")
    if not isinstance(q["query"], str) or not q["query"].strip():
        raise ValueError(f"query не строка/пустой: {qid}")
    if not isinstance(q["golden_sources"], list) or not q["golden_sources"]:
        raise ValueError(f"golden_sources непустой список обязателен: {qid}")
    if not all(isinstance(s, str) and s.strip() for s in q["golden_sources"]):
        raise ValueError(f"golden_sources содержит не строки: {qid}")
    if not isinstance(q["golden_facts"], list) or not q["golden_facts"]:
        raise ValueError(f"golden_facts непустой список обязателен: {qid}")
    if not all(isinstance(f, str) and f.strip() for f in q["golden_facts"]):
        raise ValueError(f"golden_facts содержит не строки: {qid}")

    reasoning = q.get("reasoning_type")
    if reasoning is not None and (
        not isinstance(reasoning, str) or reasoning not in REASONING_TYPES
    ):
        raise ValueError(f"неизвестный reasoning_type {reasoning!r}: {qid}")
    answerability = q.get("answerability")
    if answerability is not None and (
        not isinstance(answerability, str) or answerability not in ANSWERABILITY_TYPES
    ):
        raise ValueError(f"неизвестный answerability {answerability!r}: {qid}")
    as_of = q.get("as_of")
    if as_of is not None and (not isinstance(as_of, str) or not _AS_OF_RE.fullmatch(as_of)):
        raise ValueError(f"as_of не дата ISO {as_of!r}: {qid}")
    for key in ("evidence_sections",):
        value = q.get(key)
        if value is not None and (
            not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value)
        ):
            raise ValueError(f"{key} должен быть списком строк: {qid}")
    policy = q.get("evidence_policy")
    if policy is not None:
        if not isinstance(policy, str) or policy not in EVIDENCE_POLICIES:
            raise ValueError(f"неизвестный evidence_policy {policy!r}: {qid}")
        if policy == "graph_required" and q.get("golden_graph_evidence") is not True:
            raise ValueError(
                f"evidence_policy=graph_required требует golden_graph_evidence=true: {qid}"
            )
    rubric = q.get("rubric")
    if rubric is not None and not isinstance(rubric, str):
        raise ValueError(f"rubric не строка: {qid}")
    evidence = q.get("golden_graph_evidence")
    if evidence is not None and not isinstance(evidence, bool):
        raise ValueError(f"golden_graph_evidence не boolean: {qid}")


def load_dataset(path: Path) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            question = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: некорректный JSON: {exc}") from exc
        if not isinstance(question, dict):
            raise TypeError(f"{path}:{line_number}: строка не JSON-объект")
        validate_question(question)
        questions.append(question)
    return questions


def merge_datasets(datasets: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Склейка нескольких наборов (--dataset + --extra-dataset): id сохраняются
    и обязаны быть уникальными в пределах объединения."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for dataset in datasets:
        for question in dataset:
            qid = str(question.get("id", ""))
            if not qid or qid in seen:
                raise ValueError(f"дублирующийся id в объединённом наборе: {qid!r}")
            seen.add(qid)
            merged.append(question)
    return merged


# ---------------------------------------------------------------------------
# Pipeline builder with graph toggle
# ---------------------------------------------------------------------------

@contextmanager
def graph_toggle(graph_enabled: bool) -> Iterator[Any]:
    """Тумблер графовой оси: env живёт ВО ВРЕМЯ pipeline.run() (не при сборке).

    `graph_search_enabled(profile)` читает `RETRIEVAL_GRAPH_ENABLED` в каждом
    run() (pipeline.py:150), поэтому env должен быть выставлен на весь прогон,
    а не только на момент построения пайплайна.
    """
    old_val = os.environ.get("RETRIEVAL_GRAPH_ENABLED")
    os.environ["RETRIEVAL_GRAPH_ENABLED"] = "true" if graph_enabled else "false"
    try:
        yield
    finally:
        if old_val is None:
            os.environ.pop("RETRIEVAL_GRAPH_ENABLED", None)
        else:
            os.environ["RETRIEVAL_GRAPH_ENABLED"] = old_val


def build_eval_pipeline() -> Any:
    """Собрать QueryPipeline из env (runtime.build_pipeline)."""
    from graphrag_proto.query_service.runtime import build_pipeline

    return build_pipeline(strict_profile=True)


# ---------------------------------------------------------------------------
# Judge builder
# ---------------------------------------------------------------------------

def build_judge() -> Any | None:
    """Собрать judge из EVAL_LLM_ADAPTER=openai|fake|none; fake/none → None."""
    adapter_kind = os.environ.get("EVAL_LLM_ADAPTER", "openai").strip().lower()
    if adapter_kind in ("fake", "none", ""):
        return None
    if adapter_kind == "openai":
        from graphrag_proto.retrieval.adapters.llm import OpenAICompatibleAdapter
        return OpenAICompatibleAdapter(
            base_url=os.environ.get("LLM_BASE_URL", "http://llm:8080"),
            model=os.environ.get("LLM_MODEL", "qwen2.5-coder-7b-instruct-abliterated-q4_k_m"),
            timeout_s=float(os.environ.get("LLM_TIMEOUT_S", "600")),
        )
    raise ValueError(f"EVAL_LLM_ADAPTER={adapter_kind!r}: допустимо fake|none|openai")


# ---------------------------------------------------------------------------
# Run artifacts (design.md §5): манифест, qa_log, trace
# ---------------------------------------------------------------------------

def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Append одной строки (переживает прерывание): артефакты пишутся по вопросу."""
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def code_commit() -> str:
    """Короткий хэш HEAD, переданный хостом через RUN_CODE_COMMIT."""
    override = os.environ.get("RUN_CODE_COMMIT", "").strip()
    if not override:
        raise RuntimeError("RUN_CODE_COMMIT должен быть передан хостом в контейнер")
    return override


def _embedding_dimensions() -> int:
    """Размерность эмбеддера из env (manifest.components.dimensions)."""
    raw = os.environ.get("EMBEDDING_DIMENSIONS", "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    return 1024 if os.environ.get("EMBEDDER", "deterministic") == "bge_m3_service" else 8


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def mode_graph_enabled(mode_arg: str) -> list[bool]:
    """Поле manifest.graph_enabled: какие ветки включила команда `--mode`."""
    if mode_arg == "both":
        return [False, True]
    return [False] if mode_arg == "baseline" else [True]


def projection_policy_snapshot() -> dict[str, Any]:
    """Эффективная projection-политика для манифеста (ADR-019 + LP-13).

    Условия прогона включают состояние projection: readiness-gate решает, откроется
    ли graph axis в target-ветке. Поэтому эффективный lease кладём в манифест —
    иначе повторяемость прогона недоказуема. Значение берётся из того же
    load_projection_settings(), что и в job'е (Configurator → YAML → env).
    """
    snapshot: dict[str, Any] = {
        "config_fingerprint": os.environ.get("PROJECTION_CONFIG_FINGERPRINT", "default"),
        "state_db": os.environ.get("PROJECTION_STATE_DB_PATH", "runtime/projection.db"),
    }
    config_path = (
        os.environ.get("PROJECTION_CONFIG_PATH")
        or os.environ.get("INFRA_TOPOLOGY_PATH")
        or "infra_topology.yaml"
    )
    snapshot["config_path"] = config_path
    try:
        from graphrag_proto.projection_config import load_projection_settings

        settings = load_projection_settings()
    except Exception as exc:  # noqa: BLE001 — манифест не должен ронять прогон
        snapshot["lease_seconds"] = None
        snapshot["lease_error"] = type(exc).__name__
        return snapshot
    snapshot["lease_seconds"] = settings.lease_seconds
    snapshot["lease_source"] = (
        "configurator"
        if os.environ.get("TOPOLOGY_URL", "").strip()
        else ("env" if os.environ.get("PROJECTION_LEASE_SECONDS", "").strip() else "config_file")
    )
    return snapshot


def build_run_manifest(
    *,
    run_id: str,
    started_at: str,
    domain: str,
    revision: str,
    datasets: list[str],
    mode_arg: str,
    corpus: str | None = None,
    source_prefix: str = "",
    k: int,
    judge: Any | None,
    flags: list[str],
    corpus_documents: list[str] | None = None,
    corpus_limit: int | None = None,
    golden: dict[str, Any] | None = None,
    timeouts: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Манифест условий прогона (design.md §5.1) — «что именно сравнивалось».

    ``corpus_documents``/``corpus_limit``/``golden`` — обязательны для усечённых
    прогонов: без них прогон на 3 документах неотличим от прогона на 36 и
    recall неинтерпретируем (эталонных источников в корпусе может не быть вовсе).

    ``timeouts`` — фактически применённые пороги: три окна ожидания джоб и два бюджета
    фазы подачи. В пару с ``ingest_report.json`` это то, чем калибруются будущие
    прогоны: без них «долго» и «зависло» неразличимы, потому что оба кончаются ошибкой
    прогона.

    ``config`` — идентичность, момент применения и право на устаревание конфигурации
    запроса. Без неё артефакт неполон по построению: версия кода конфигурацию не
    определяет, а профиль меняется на лету, так что восстановить применённые настройки
    из коммита нельзя.
    """
    return {
        "run_id": run_id,
        "started_at": started_at,
        "code_commit": code_commit(),
        "code_tree": os.environ.get("RUN_CODE_TREE", "unknown"),
        "domain": domain,
        "revision": revision,
        "revision_fingerprint": revision,
        "datasets": datasets,
        "corpus": corpus,
        "config": config or {},
        "timeouts": timeouts or {},
        "corpus_documents": sorted(corpus_documents or []),
        "corpus_documents_count": len(corpus_documents or []),
        "corpus_limit": corpus_limit,
        "golden_coverage": golden or {},
        "note": note,
        "source_prefix": source_prefix,
        "k": k,
        "mode": mode_arg,
        "graph_enabled": mode_graph_enabled(mode_arg),
        "projection": projection_policy_snapshot(),
        "components": {
            "embedder": os.environ.get("EMBEDDER", "deterministic"),
            "dimensions": _embedding_dimensions(),
            "reranker": os.environ.get("RERANKER", "noop"),
            "chunker": os.environ.get(
                "INGEST_CHUNKER", os.environ.get("CHUNKER", "sliding_window")
            ),
            "chunk_size": _env_int("INGEST_CHUNK_SIZE", 512),
            "chunk_overlap": _env_int("INGEST_CHUNK_OVERLAP", 64),
        },
        "generation": {
            "llm_adapter": os.environ.get("LLM_ADAPTER", "openai"),
            "model": os.environ.get(
                "LLM_MODEL", "qwen2.5-coder-7b-instruct-abliterated-q4_k_m"
            ),
            "temperature": _env_float("LLM_TEMPERATURE", 0.3),
            "judge_adapter": "none" if judge is None else os.environ.get("EVAL_LLM_ADAPTER", "openai"),
        },
        "flags": flags,
    }


def write_run_manifest(manifest: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "run_manifest.json"
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def compare_run_manifests(manifest_a: dict[str, Any], manifest_b: dict[str, Any]) -> dict[str, Any]:
    """Проверить одну revision и неизменность условий, допуская один toggle-фактор."""
    missing_fields = sorted(
        {
            field
            for manifest in (manifest_a, manifest_b)
            for field in (*PAIR_FACTOR_FIELDS, *PAIR_INVARIANT_FIELDS)
            if field != "projection" and field not in manifest
        }
    )
    differing_factors = [
        field for field in PAIR_FACTOR_FIELDS if manifest_a.get(field) != manifest_b.get(field)
    ]
    def _invariant_value(manifest: dict[str, Any], field: str) -> Any:
        if field == "projection":
            projection = manifest.get("projection") or {}
            if not isinstance(projection, dict):
                return projection
            return {
                key: projection.get(key)
                for key in ("config_fingerprint", "state_db")
            }
        if field == "flags":
            return sorted(
                flag
                for flag in manifest.get("flags", [])
                if isinstance(flag, str) and not flag.startswith("--compare-with")
            )
        return manifest.get(field)

    differing_invariants = [
        field
        for field in PAIR_INVARIANT_FIELDS
        if _invariant_value(manifest_a, field) != _invariant_value(manifest_b, field)
    ]
    missing_revision = not manifest_a.get("revision") or not manifest_b.get("revision")
    paired = (
        not missing_fields
        and not differing_invariants
        and not missing_revision
        and len(differing_factors) <= 1
    )
    reasons: list[str] = []
    if missing_fields:
        reasons.append(f"отсутствуют поля: {', '.join(missing_fields)}")
    if missing_revision:
        reasons.append("revision отсутствует")
    if differing_invariants:
        reasons.append(f"различаются инварианты: {', '.join(differing_invariants)}")
    if len(differing_factors) > 1:
        reasons.append(f"различаются факторы: {', '.join(differing_factors)}")
    return {
        "paired": paired,
        "differing_factors": differing_factors,
        "differing_invariants": differing_invariants,
        "missing_fields": missing_fields,
        "message": None if paired else "; ".join(reasons) or "прогоны не парные",
    }


def load_compare_manifest(compare_with: str) -> dict[str, Any]:
    """Манифест парного прогона: путь к файлу либо директория с run_manifest.json."""
    path = Path(compare_with)
    if path.is_dir():
        path = path / "run_manifest.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"не удалось прочитать манифест для сравнения: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise TypeError(f"манифест не объект: {path}")
    return data


# ---------------------------------------------------------------------------
# Single-question eval
# ---------------------------------------------------------------------------

def eval_question(
    question: dict[str, Any],
    pipeline: Any,
    domain: str,
    revision: str | None,
    judge: Any | None,
    *,
    mode: str,
    graph_enabled: bool,
    run_id: str,
    generate: bool = True,
    trace_enabled: bool = False,
) -> dict[str, Any]:
    """Прогон одного вопроса; возвращает строку qa_log (design.md §5.2).

    Записывает источники по осям (`retrieved[]` c axis), retrieval-метрики,
    graph_contribution по срезу `golden_graph_evidence`, answer, generation
    (mode: judge / n/a / skipped) и timings. При `trace_enabled` кладёт
    `trace_events` из `done["trace"]` (слой 4; из qa_log-строки удаляются).
    """
    golden_sources = set(question.get("golden_sources", []))
    golden_facts = question.get("golden_facts", [])
    graph_evidence = bool(question.get("golden_graph_evidence", False))

    events: list[tuple[str, dict[str, Any]]] = []
    done = pipeline.run(
        question["query"],
        domain,
        emit=lambda t, p: events.append((t, p)),
        revision=revision,
        generate=generate,
        trace=trace_enabled or graph_enabled,
    )

    observed_revision = done.get("revision")
    if observed_revision != revision:
        raise RuntimeError(
            f"revision mismatch: expected {revision!r}, got {observed_revision!r}"
        )
    sources = done.get("sources") or []
    raw_urls = [
        src.get("source_url")
        for src in sources
        if isinstance(src, dict) and src.get("source_url")
    ]
    # Дубль одного URL по обеим осям устраняется по первому вхождению (лучший ранг):
    # метрики считаются по уникальным URL (design.md §2), иначе recall/nDCG > 1.0.
    retrieved_urls = list(dict.fromkeys(raw_urls))
    ret = retrieval_metrics(retrieved_urls, golden_sources, k=EVAL_K)

    graph_urls = [src["source_url"] for src in sources if isinstance(src, dict) and src.get("axis") == "graph"]
    vector_urls = [src["source_url"] for src in sources if isinstance(src, dict) and src.get("axis") == "vector"]
    graph_degraded = bool(done.get("graph_degraded"))
    trace_events = done.get("trace") or []
    graph_trace = next(
        (event for event in trace_events if isinstance(event, dict) and event.get("stage") == "graph_expansion"),
        {},
    )
    gc = graph_contribution(graph_urls, vector_urls, golden_sources, golden_graph_evidence=graph_evidence)
    if graph_enabled and graph_degraded:
        gc["mode"] = "degraded"
        gc["degraded"] = True

    answer = done.get("text", "") if generate else ""
    if not generate:
        gen: dict[str, Any] = {
            "groundedness": None, "coverage": None, "hallucination_rate": None,
            "mode": GENERATION_MODE_SKIPPED,
        }
    elif judge is None:
        gen = {
            "groundedness": None, "coverage": None, "hallucination_rate": None,
            "mode": GENERATION_MODE_NO_JUDGE,
        }
    else:
        gen = generation_metrics(answer, golden_facts, judge)

    record: dict[str, Any] = {
        "run_id": run_id,
        "id": question.get("id", ""),
        "mode": mode,
        "graph_enabled": bool(graph_enabled),
        "graph_degraded": graph_degraded,
        "projection_status": done.get("projection_status"),
        "projection_revision": done.get("projection_revision"),
        # Применённый boost берётся из `done`, а не из trace-события: при выключенном trace
        # trace-события нет, и поле было всегда `None` — то есть запись о применённой
        # настройке зависела от отладочного флага.
        "graph_boost": (done.get("effective_retrieval") or {}).get("graph_boost"),
        "profile_fingerprint": done.get("profile_fingerprint"),
        "profile_pinned_at": done.get("profile_pinned_at"),
        "config_fallbacks": done.get("config_fallbacks") or [],
        "effective_retrieval": done.get("effective_retrieval") or {},
        "seed_chunk_ids": graph_trace.get("seed_chunk_ids", []),
        "graph_paths": graph_trace.get("paths", []),
        "graph_depths": graph_trace.get("depths", []),
        "query": question.get("query", ""),
        "revision": revision,
        "retrieved": [
            {
                "source_url": src.get("source_url"),
                "axis": src.get("axis"),
                "score": src.get("relevance"),
            }
            for src in sources
            if isinstance(src, dict) and src.get("source_url")
        ],
        "golden_sources": question.get("golden_sources") or [],
        "golden_facts": golden_facts,
        "golden_graph_evidence": graph_evidence,
        "retrieval": ret,
        "graph_contribution": gc,
        "answer": answer,
        "generation": gen,
        "timings": {
            "retrieval_time_s": done.get("retrieval_time_s", 0.0),
            "generation_time_s": done.get("generation_time_s", 0.0),
            "total_time_s": done.get("total_time_s", 0.0),
        },
        "trace_events": done.get("trace") if trace_enabled else [],
    }
    return record


def _aggregate_graph_contribution(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Агрегат вклада графа по прогону на срезе `measured` (design.md §3).

    Вне среза (нет `golden_graph_evidence`) — `mode="not_measured"`, без
    интерпретации (necessity/delta обнулены).
    """
    metric_slice = [
        r.get("graph_contribution")
        for r in results
        if isinstance(r.get("graph_contribution"), dict)
        and r["graph_contribution"].get("mode") == "measured"
    ]
    degraded_questions = len(
        [
            r
            for r in results
            if isinstance(r.get("graph_contribution"), dict)
            and r["graph_contribution"].get("mode") == "degraded"
        ]
    )
    if not metric_slice:
        return {
            "mode": "not_measured",
            "questions": len([r for r in results if isinstance(r.get("graph_contribution"), dict)]),
            "degraded_questions": degraded_questions,
            "necessity": 0.0,
            "delta_recall": 0.0,
            "recall_graph": None,
            "recall_vector": None,
            "evidence_recall_graph": None,
        }
    n = len(metric_slice)

    def _avg(key: str) -> float:
        return round(sum(float(m[key]) for m in metric_slice) / n, 4)

    return {
        "mode": "measured",
        "questions": n,
        "degraded_questions": degraded_questions,
        "necessity": _avg("necessity"),
        "delta_recall": _avg("delta_recall"),
        "recall_graph": _avg("recall_graph"),
        "recall_vector": _avg("recall_vector"),
        "evidence_recall_graph": _avg("evidence_recall_graph"),
    }


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {"retrieval": {}, "generation": {}, "graph_contribution": {}, "question_count": 0}

    def _avg(key: str, section: str) -> float:
        vals = [r[section][key] for r in results if key in r.get(section, {})]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    # Генерация: n/a/skipped несут None (судьи нет) — пустой срез агрегируется как None.
    def _avg_gen(key: str) -> float | None:
        vals = [
            r["generation"][key]
            for r in results
            if key in r.get("generation", {}) and r["generation"][key] is not None
        ]
        return round(sum(vals) / len(vals), 4) if vals else None

    return {
        "retrieval": {
            "recall_at_k": _avg("recall_at_k", "retrieval"),
            "precision_at_k": _avg("precision_at_k", "retrieval"),
            "mrr_at_k": _avg("mrr_at_k", "retrieval"),
            "ndcg_at_k": _avg("ndcg_at_k", "retrieval"),
            "k": EVAL_K,
        },
        "generation": {
            "groundedness": _avg_gen("groundedness"),
            "coverage": _avg_gen("coverage"),
            "hallucination_rate": _avg_gen("hallucination_rate"),
            "mode": results[0]["generation"].get("mode", "unknown") if results else "unknown",
        },
        "graph_contribution": _aggregate_graph_contribution(results),
        # Графовая ось фактически сработала? Без этого флага прогон, где граф
        # «готов», но ничего не дал, неотличим от чистого (verdict без судьи = n/a).
        "graph_axis_active": any(
            source.get("axis") == "graph"
            for r in results
            for source in (r.get("retrieved") or [])
            if isinstance(source, dict)
        ),
        "graph_axis_sources": sum(
            1
            for r in results
            for source in (r.get("retrieved") or [])
            if isinstance(source, dict) and source.get("axis") == "graph"
        ),
        "projection_statuses": sorted(
            {str(result.get("projection_status") or "unknown") for result in results}
        ),
        "projection_revisions": sorted(
            {str(result.get("projection_revision") or "unknown") for result in results}
        ),
        "question_count": len(results),
    }


def golden_coverage(
    questions: list[dict[str, Any]],
    ingested_urls: set[str],
) -> dict[str, Any]:
    """Доля вопросов, чьи ``golden_sources`` реально лежат в загруженном корпусе.

    Обязательно для усечённых прогонов: если корпус — 3 документа, то у
    большинства вопросов эталонных источников в базе нет, и recall низкий не
    из-за качества системы, а из-за усечения. Без этой цифры любой вывод по
    smoke-прогону ложен.
    """
    if not questions:
        return {
            "questions_total": 0,
            "fully_covered": 0,
            "partially_covered": 0,
            "uncovered": 0,
            "fully_covered_ratio": None,
        }
    fully = partially = 0
    for question in questions:
        golden = [str(url) for url in (question.get("golden_sources") or [])]
        if not golden:
            partially += 1  # вопрос без эталонов: не мешает, но и не покрыт
            continue
        hits = sum(1 for url in golden if url in ingested_urls)
        if hits == len(golden):
            fully += 1
        elif hits:
            partially += 1
    total = len(questions)
    return {
        "questions_total": total,
        "fully_covered": fully,
        "partially_covered": partially,
        "uncovered": total - fully - partially,
        "fully_covered_ratio": round(fully / total, 4),
    }


def write_command_file(out_dir: Path, argv: list[str]) -> Path:
    """Точная команда запуска — для воспроизведения прогона руками."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "command.txt"
    path.write_text(" ".join(argv) + "\n", encoding="utf-8")
    return path


def write_ingest_report(report: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "ingest_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_qa_review(results: list[dict[str, Any]], out_dir: Path) -> Path:
    """Человекочитаемый разбор «вопрос → ответ → источники» (режим --no-judge)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = ["# Разбор пар (ручная оценка)\n"]
    for index, record in enumerate(results, 1):
        retrieval = record.get("retrieval") or {}
        contribution = record.get("graph_contribution") or {}
        lines.append(f"## {index}. {record.get('query', '')}\n")
        lines.append(f"- **id**: `{record.get('id', '')}`  ")
        lines.append(f"- **mode**: {record.get('mode')} (graph_enabled={record.get('graph_enabled')})  ")
        lines.append(
            f"- **projection**: status={record.get('projection_status')}, "
            f"revision={record.get('projection_revision')}  "
        )
        if record.get("graph_degraded"):
            lines.append("- **ВНИМАНИЕ**: graph_degraded — графическая ось не использована  ")
        lines.append(
            f"- **метрики**: recall@{retrieval.get('k')}={retrieval.get('recall_at_k')}, "
            f"mrr={retrieval.get('mrr_at_k')}, ndcg={retrieval.get('ndcg_at_k')}  "
        )
        lines.append(
            f"- **вклад графа**: mode={contribution.get('mode')}, "
            f"necessity={contribution.get('necessity')}, "
            f"delta_recall={contribution.get('delta_recall')}  "
        )
        if record.get("golden_graph_evidence"):
            lines.append("- **golden_graph_evidence**: true (вопрос из графового среза)  ")
        lines.append("\n**Источники:**\n")
        for source in record.get("retrieved") or []:
            lines.append(
                f"- `{source.get('axis')}` {source.get('source_url')} "
                f"(score={source.get('score')})"
            )
        if not (record.get("retrieved") or []):
            lines.append("- (пусто)")
        answer = (record.get("answer") or "").strip()
        lines.append("\n**Ответ:**\n")
        lines.append(answer if answer else "_(генерация не выполнялась: --retrieval-only)_")
        lines.append("\n---\n")
    path = out_dir / "qa_review.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def build_config_state(
    reports: dict[str, Any],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Идентичность, момент применения, право на устаревание и фактически применённое.

    Собирается из ответов, а не из предположения о конфиге: отпечаток и момент закрепления
    ставит `QueryPipeline` при первом запросе, а отсюда берутся только имена.

    **Дрейф между режимами — разрушающий для пары.** Baseline и target обязаны быть измерены
    одним конфигом: иначе delta измеряет не «вклад графа», а «вклад графа плюс разницу между
    двумя настройками». Проверка по образцу `revision mismatch` — не тихо, а Invalid.
    Конфигурация закрепляется на первый запрос каждого режима, поэтому дрейф возможен, если
    файл изменили между режимами, — и это видно, а не предполагается.
    """
    fingerprints: dict[str, str] = {}
    pinned_at: dict[str, str] = {}
    fallbacks: dict[str, int] = {}
    effective: dict[str, Any] = {}
    for record in records:
        fp = record.get("profile_fingerprint")
        if isinstance(fp, str) and fp:
            fingerprints.setdefault(str(record.get("mode", "?")), fp)
        when = record.get("profile_pinned_at")
        if isinstance(when, str) and when:
            pinned_at.setdefault(str(record.get("mode", "?")), when)
        for name in record.get("config_fallbacks") or []:
            fallbacks[str(name)] = fallbacks.get(str(name), 0) + 1
        applied = record.get("effective_retrieval")
        if isinstance(applied, dict) and not effective:
            effective = applied
    distinct = sorted(set(fingerprints.values()))
    drift = len(distinct) > 1
    return {
        "profile_fingerprints": fingerprints,
        "profile_fingerprint": distinct[0] if distinct else None,
        "profile_pinned_at": sorted(pinned_at.values())[0] if pinned_at else None,
        # Право на устаревание объявлено явно, а не подразумевается: пока сессия идёт,
        # конфигурация неизменна, и «устаревшая» конфигурация в артефакте означает, что
        # правило нарушено, а не что кто-то не обновился.
        "profile_staleness": "immutable_for_session",
        "config_drift": drift,
        "config_fallbacks": fallbacks,
        "effective_retrieval": effective,
        "modes": len(reports),
    }


def _format_causes(causes: dict[str, int]) -> str:
    """Гистограмма причин поимённо, отсортированная по частоте.

    Порядок по частоте, а не по алфавиту: первым читается то, что чинить в первую
    очередь, а не то, что первым попалось в словарь.
    """
    if not causes:
        return "нет"
    return ", ".join(
        f"{name}={count}" for name, count in sorted(causes.items(), key=lambda kv: (-kv[1], kv[0]))
    )


def write_passport(
    manifest: dict[str, Any],
    report: dict[str, Any] | None,
    out_dir: Path,
    *,
    ingest: dict[str, Any] | None,
    failures: int,
    argv: list[str],
) -> Path:
    """Паспорт прогона: что мерили, при каких условиях и что здесь не интерпретируется."""
    out_dir.mkdir(parents=True, exist_ok=True)
    golden = manifest.get("golden_coverage") or {}
    projection = manifest.get("projection") or {}
    target = (report or {}).get("target") or {}
    gc = target.get("graph_contribution") or {}
    degraded = int((ingest or {}).get("enrichment_degraded_documents") or 0)
    dropped = int((ingest or {}).get("llm_layer_dropped_documents") or 0)
    lost_entities = int((ingest or {}).get("llm_layer_lost_entities") or 0)
    lost_edges = int((ingest or {}).get("llm_layer_lost_edges") or 0)
    causes = (ingest or {}).get("enrichment_causes") or {}

    lines = [
        "# Паспорт прогона",
        "",
        f"- **run_id**: `{manifest.get('run_id', 'n/a')}`  ",
        f"- **started_at**: {manifest.get('started_at', 'n/a')}  ",
        f"- **code_commit**: `{manifest.get('code_commit', 'n/a')}`  ",
        f"- **code_tree**: `{manifest.get('code_tree', 'unknown')}`  ",
        f"- **домен**: {manifest.get('domain', 'n/a')}  ",
        f"- **revision**: `{manifest.get('revision', 'n/a')}`  ",
        f"- **корпус**: {manifest.get('corpus', 'n/a')}  ",
        f"- **документов загружено**: {manifest.get('corpus_documents_count', 0)}  ",
        f"- **лимит документов**: {manifest.get('corpus_limit') or 'нет'}  ",
        f"- **датасеты**: {', '.join(manifest.get('datasets', []))}  ",
        f"- **вопросов**: {target.get('question_count', 'n/a')}  ",
        f"- **mode**: {manifest.get('mode', 'n/a')}  ",
        f"- **графическая ось**: {manifest.get('graph_enabled')}  ",
        (
            f"- **projection lease**: {projection.get('lease_seconds')} "
            f"(источник: {projection.get('lease_source', 'n/a')})  "
        ),
        f"- **упавших вопросов**: {failures}  ",
    ]
    if manifest.get("note"):
        lines.append(f"- **цель прогона**: {manifest['note']}  ")

    lines += [
        "",
        "## Что меряем",
        "",
        "| Метрика | Смысл |",
        "|---|---|",
        "| `recall@5`, `mrr`, `ndcg` | качество retrieval по `golden_sources` |",
        "| `necessity` | доля golden, найденная **только** графовой осью (срез `golden_graph_evidence`) |",
        "| `delta_recall` | recall(обе оси) − recall(vector-only) на том же срезе |",
        "| `graph_axis_active` | графическая ось фактически дала источники |",
        "| `degraded_questions` | вопросы, где readiness-gate не открыл граф |",
        "| `groundedness`/`coverage` | только при включённом судье |",
        "",
        "## Что здесь НЕ интерпретируется",
        "",
    ]
    ratio = golden.get("fully_covered_ratio")
    if ratio is not None and ratio < 0.5:
        lines.append(
            f"- ⚠️ **`golden_coverage` = {ratio}** ({golden.get('fully_covered')} из "
            f"{golden.get('questions_total')} вопросов имеют все эталонные источники "
            "в загруженном корпусе). Recall/nDCG по этому прогону **не** характеризуют "
            "качество системы — они отражают усечение корпуса."
        )
    if (report or {}).get("verdict") in {"n/a", "invalid"}:
        lines.append(f"- ⚠️ **verdict = `{report.get('verdict')}`** — не является приёмкой.")
    if failures:
        lines.append(f"- ⚠️ **{failures}** вопросов завершились ошибкой (см. `failures.jsonl`).")
    if not (report or {}).get("generation", {}).get("groundedness") is not None:
        lines.append("- Судья не запускался: groundedness/coverage не вычислены.")
    if not lines[-1].startswith("- "):
        lines.append("- Явных ограничений не обнаружено.")
    if not target.get("graph_axis_active") and manifest.get("graph_enabled"):
        lines.append(
            "- ⚠️ **`graph_axis_active` = false**: ни один вопрос не получил источник "
            "по оси `graph`. Граф был готов, но не дал результата — вклад графа "
            "на этом прогоне не измерен."
        )
    if (gc.get("degraded_questions") or 0) > 0:
        lines.append(
            f"- ⚠️ **degraded_questions = {gc.get('degraded_questions')}**: "
            "readiness-gate не открыл граф (см. `projection_status` в `qa_log.jsonl`)."
        )
    # Причина вердикта печатается здесь же: вердикт без причины в артефакте не
    # отличим от сбоя раннера, и следующий читатель примет прогон за измерение.
    for reason in (report or {}).get("verdict_reasons") or []:
        lines.append(f"- ⚠️ **вердикт непригоден**: {reason}")
    # Конфигурация — третье измерение прогона, и без неё отчёт неполон: версия кода
    # настройки запроса не определяет.
    cfg = (report or {}).get("config") or {}
    if cfg:
        lines.append(
            f"- **конфигурация**: `{cfg.get('profile_fingerprint') or 'n/a'}` "
            f"(закреплена {cfg.get('profile_pinned_at') or 'n/a'}, "
            f"устаревание: {cfg.get('profile_staleness') or 'n/a'})"
        )
        if cfg.get("config_drift"):
            lines.append(
                "- ⚠️ **`config_drift = true`**: режимы измерены разными настройками, "
                f"отпечатки: {cfg.get('profile_fingerprints')}"
            )
        if cfg.get("config_fallbacks"):
            applied = ", ".join(
                f"{name}×{count}" for name, count in sorted(cfg["config_fallbacks"].items())
            )
            lines.append(
                f"- **параметры, применённые с дефолтом** (значение нечитаемо): {applied}"
            )
        if cfg.get("effective_retrieval"):
            lines.append(f"- **применённые настройки**: `{cfg['effective_retrieval']}`")

    if ingest:
        lines += [
            "",
            "## Ingest",
            "",
            (
                f"- документов: {ingest.get('documents_total')} "
                f"(cold={ingest.get('cold_documents')}, "
                f"no-op={ingest.get('noop_documents')})  "
            ),
            f"- суммарно: {ingest.get('total_wall_time_s')} с  ",
            f"- cold mean: {ingest.get('cold_wall_time_mean_s')} с  ",
            (
                f"- документов с деградацией optional-ингеста: {degraded}  "
                if degraded
                else "- документов с деградацией optional-ингеста: 0  "
            ),
            (
                f"- документов, потерявших LLM-слой целиком: {dropped}  "
                if dropped
                else "- документов, потерявших LLM-слой целиком: 0  "
            ),
            (
                f"- записей сущностей потеряно: {lost_entities} (рёбер: {lost_edges})  "
                if dropped
                else "- записей сущностей потеряно: 0 (рёбер: 0)  "
            ),
            "",
            (
                "Попер-документные времена — в `ingest_report.json`; по ним оценивается "
                "стоимость полного корпуса (она зависит от размера документа, а не от числа)."
            ),
        ]
    if degraded:
        # Причины разведены поимённо: чинить профиль, промпт и модель — разные работы,
        # и «деградация 40» как одно число не говорит ни о какой из них.
        lines += [
            "",
            (
                f"- причины деградации: {_format_causes(causes)}  "
                if causes
                else "- причины деградации: не классифицированы  "
            ),
        ]
    if dropped:
        # Счётчик деградации — umbrella: он складывает «профиль не загрузился» (ничего
        # не потеряно) и «исключение в экстракции» (потерян весь слой). Читать его как
        # потерю нельзя, поэтому потеря считается отдельно. Слой роняется целиком при
        # негодной конфигурации и при сбое модели (два именованных типа; дефект нашего
        # кода падает, а не деградирует) вместе с `entity_edges`, то есть теряются и
        # узлы-якоря, и сами гипотезы связей.
        lines += [
            "",
            (
                f"> ⚠️ **{dropped} документов потеряли LLM-слой целиком** "
                f"({lost_entities} записей сущностей и {lost_edges} рёбер исчезли; "
                f"счётчик деградации: {degraded}). **Вердикт прогона при этом "
                "`invalid`** — смещение одностороннее, пропавшие факты нельзя ни найти, "
                "ни вспомнить, поэтому недооценка оси гарантирована (см. `docs/06` §2.2). "
                "Причины — в `ingest_report.json` "
                "(`enrichment_cause`, `enrichment_error`), в логах ingestion-service "
                "(сообщение стадии `EXTRACT`). "
                "Гранулярность сброса слоя — открытый вопрос, см. ADR-032; для "
                "проектирования реального ingest поведение неприемлемо, потеря необратима "
                "(повторная загрузка того же `content_hash` — no-op)."
            ),
        ]

    lines += [
        "",
        "## Какие логи снимаются",
        "",
        "| Артефакт | Содержимое |",
        "|---|---|",
        "| `run_manifest.json` | условия прогона (машиночитаемо) |",
        "| `ingest_report.json` | по каждому документу: время, статус, no-op, факты обогащения (деградация, причина, размер потери, объём LLM-слоя) |",
        "| `qa_log.jsonl` | по каждому вопросу: источники, метрики, тайминги |",
        "| `qa_review.md` | тот же разбор для чтения глазами |",
        "| `lift_report.json` / `.md` | агрегат и вердикт |",
        "| `failures.jsonl` | вопросы с ошибками |",
        "| `trace.jsonl` | события pipeline (только с `--trace`) |",
        "| `preflight.log` | доступность контуров |",
        "| `logs/*.log` | логи сервисов (снимает хостовый wrapper) |",
        "| `resources.json` | RAM/VRAM: старт, пик, финал (снимает wrapper) |",
        "",
        "## Воспроизведение",
        "",
        "```",
        " ".join(argv),
        "```",
    ]
    path = out_dir / "PASSPORT.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_lift_report(report: dict[str, Any], out_dir: Path, manifest: dict[str, Any] | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "lift_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Lift Report (ADR-015)\n",
        f"**Run ID:** {report.get('run_id', 'n/a')}  ",
        f"**Created:** {report.get('created_at', 'n/a')}  ",
        f"**Verdict:** `{report.get('verdict', 'n/a')}`  ",
        f"**Revision:** `{report.get('revision', 'n/a')}`\n",
    ]
    # Причина вердикта идёт сразу под ним, а не в конце отчёта: вердикт без причины
    # выглядит как результат, и его читают как результат.
    for reason in report.get("verdict_reasons") or []:
        lines.append(f"> ⚠️ Вердикт `{report.get('verdict', 'n/a')}` непригоден: {reason}  ")

    # Блок «Условия прогона» (design.md §5.1/5.9) — из манифеста.
    if manifest:
        lines.append("\n## Условия прогона\n")
        lines.append(f"- **run_id**: {manifest.get('run_id', 'n/a')}  ")
        lines.append(f"- **started_at**: {manifest.get('started_at', 'n/a')}  ")
        lines.append(f"- **code_commit**: {manifest.get('code_commit', 'n/a')}  ")
        lines.append(f"- **domain**: {manifest.get('domain', 'n/a')}  ")
        lines.append(f"- **revision**: {manifest.get('revision', 'n/a')}  ")
        lines.append(f"- **revision_fingerprint**: {manifest.get('revision_fingerprint', 'n/a')}  ")
        lines.append(f"- **datasets**: {', '.join(manifest.get('datasets', []))}  ")
        lines.append(f"- **k**: {manifest.get('k', 'n/a')}  ")
        lines.append(f"- **mode**: {manifest.get('mode', 'n/a')}  ")
        lines.append(f"- **graph_enabled**: {manifest.get('graph_enabled', [])}  ")
        projection = manifest.get("projection") or {}
        lines.append(
            f"- **projection**: config_fingerprint={projection.get('config_fingerprint', 'n/a')}, "
            f"state_db={projection.get('state_db', 'n/a')}  "
        )
        components = manifest.get("components") or {}
        lines.append(
            "- **components**: embedder={embedder}, dimensions={dimensions}, reranker={reranker}, "
            "chunker={chunker}({chunk_size}/{chunk_overlap})\n".format(**components)
        )
        generation = manifest.get("generation") or {}
        lines.append(
            "- **generation**: llm_adapter={llm_adapter}, model={model}, "
            "temperature={temperature}, judge_adapter={judge_adapter}\n".format(**generation)
        )
        lines.append(f"- **flags**: {manifest.get('flags', [])}  ")

    for mode_name in ("baseline", "target"):
        lines.append(f"\n## {mode_name.title()}\n")
        for section in ("retrieval", "generation"):
            lines.append(f"### {section.title()}\n")
            for k, v in report.get(mode_name, {}).get(section, {}).items():
                lines.append(f"- **{k}**: {v}\n")
        gc = report.get(mode_name, {}).get("graph_contribution") or {}
        if gc:
            lines.append("### Graph Contribution\n")
            for k, v in gc.items():
                lines.append(f"- **{k}**: {v}\n")

    lines.append("\n## Delta\n")
    for k, v in report.get("delta", {}).items():
        # Валютное правило: неизмеренная метрика — это n/a, а не 0.0 (docs/test_plan.md §3).
        lines.append(f"- **{k}**: {'n/a' if v is None else format(v, '+.4f')}\n")

    # Правило парности (design.md §5.1): расхождение > 1 поля фактора -> не парные.
    pair = report.get("pair")
    if pair is not None:
        lines.append("\n## Pair check\n")
        lines.append(f"- **paired**: {pair.get('paired')}  ")
        lines.append(f"- **differing_factors**: {pair.get('differing_factors', [])}  ")
        if not pair.get("paired"):
            lines.append("> **WARNING: прогоны не парные** — вердикт недействителен.\n")
    (out_dir / "lift_report.md").write_text("".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="M4 Eval Runner (ADR-015)")
    parser.add_argument("--domain", required=True, choices=["it", "library", "cinema"])
    parser.add_argument("--mode", required=True, choices=["baseline", "hybrid", "both"])
    parser.add_argument("--corpus", help="Path to corpus dir (docs/*.md); skips ingestion if omitted")
    parser.add_argument(
        "--documents",
        action="append",
        default=[],
        help="Явный список документов корпуса (путь относительно --corpus, можно "
        "указывать несколько раз). Нужен для усечённых прогонов, где полный "
        "ingest неподъёмен; попадает в манифест как corpus_documents.",
    )
    parser.add_argument(
        "--limit-docs",
        type=int,
        default=None,
        help="Взять только первые N документов корпуса после сортировки (smoke-прогон).",
    )
    parser.add_argument(
        "--note",
        default="",
        help="Цель прогона human-readable строкой; попадает в манифест и PASSPORT.md.",
    )
    parser.add_argument("--dataset", required=True, help="Path to questions.jsonl")
    parser.add_argument(
        "--extra-dataset",
        action="append",
        default=[],
        help="Дополнительный набор вопросов (JSONL), мержится с --dataset; id сохраняются "
        "и обязаны быть уникальными в объединении. Можно указывать несколько раз.",
    )
    parser.add_argument("--out", default="reports/", help="Output directory")
    parser.add_argument("--source-prefix", default="", help="Optional prefix for source_url (e.g. docs)")
    parser.add_argument(
        "--unreachable-timeout",
        type=float,
        default=DEFAULT_UNREACHABLE_TIMEOUT_S,
        help="Сколько секунд опросы могут не отвечать, сек (по умолч. 300): после этого "
        "стенд считается недоступным. Считается по неудачным опросам, а не по "
        "отсутствию прогресса: «мы не видим джоб» и «джобы не двигаются» — разные факты "
        "с разными причинами",
    )
    parser.add_argument(
        "--job-timeout",
        type=float,
        default=DEFAULT_JOB_TIMEOUT_S,
        help="Сколько секунд конкретная джоба может не двигаться при живом стенде, сек "
        "(по умолч. 900). Ловит и зависшую среди живых, и единственную не завершённую в "
        "хвосте батча. Независим от --unreachable-timeout: условия взаимоисключающие, "
        "поэтому ни одна конфигурация не делает одно из окон мёртвым",
    )
    parser.add_argument(
        "--wait-ceiling",
        type=float,
        default=DEFAULT_WAIT_CEILING_S,
        help="Жёсткий кап ожидания всей пачки джоб, сек (по умолч. 7200). Страховка для "
        "«медленно, но живо». Не путать с бюджетами подачи: --slot-timeout и "
        "--submit-budget ограничивают фазу POST /documents",
    )
    parser.add_argument(
        "--slot-timeout",
        type=float,
        default=DEFAULT_SLOT_TIMEOUT_S,
        help="Сколько ждать свободного слота исполнителя для одного документа при 429, "
        "сек (по умолч. 300). 429 = «жив и занят», поэтому это норма на полном корпусе, а "
        "не авария; отличать надо от --submit-budget, который ограничивает всю фазу",
    )
    parser.add_argument(
        "--submit-budget",
        type=float,
        default=DEFAULT_SUBMIT_BUDGET_S,
        help="Бюджет всей фазы подачи корпуса, сек (по умолч. 7200). Существует отдельно от "
        "--slot-timeout, иначе лимит умножается на число документов и перестаёт быть капом",
    )
    parser.add_argument(
        "--no-preflight",
        action="store_true",
        help="Пропустить fail-fast проверку доступности контуров",
    )
    parser.add_argument(
        "--no-judge",
        action="store_true",
        help="Без судьи (эквивалент EVAL_LLM_ADAPTER=none): groundedness/coverage "
        "не вычисляются, generation.mode='n/a', вердикт Lift = n/a (design.md §5.2/§6)",
    )
    parser.add_argument(
        "--retrieval-only",
        action="store_true",
        help="Retrieval-only (design.md §6): без генерации и судьи; LLM-контур генерации "
        "в preflight не требуется, но ingestion с --corpus и EXTRACT_LLM=true требует LLM; "
        "generation.mode='skipped'",
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Писать диагностический слой 4 (trace.jsonl) с событиями этапов "
        "pipeline (embedding/cache/graph/vector/rerank/llm/done)",
    )
    parser.add_argument(
        "--compare-with",
        metavar="PATH",
        help="Сравнить с парным прогоном: файл run_manifest.json либо директория. "
        "Различие больше чем в одном поле фактора (mode/graph_enabled) → "
        "'прогоны не парные' и вердикт invalid",
    )
    args = parser.parse_args()

    # Единый лимит ожидания сети: если LLM_TIMEOUT_S не задан явно — 5 минут, не 600с.
    os.environ.setdefault("LLM_TIMEOUT_S", str(DEFAULT_LLM_TIMEOUT_S))

    dataset_path = Path(args.dataset)
    datasets = [load_dataset(dataset_path)]
    for extra_path in args.extra_dataset:
        datasets.append(load_dataset(Path(extra_path)))
    questions = merge_datasets(datasets)
    if not questions:
        raise ValueError("eval dataset is empty")
    out_dir = Path(args.out)

    if not args.no_preflight and not preflight(
        out_dir,
        include_generation=not args.retrieval_only,
        include_ingestion=bool(args.corpus),
    ):
        print("preflight FAILED: обязательный контур недоступен — прогон прерван")
        raise SystemExit(1)

    generate = not args.retrieval_only
    if args.retrieval_only:
        print("mode: retrieval-only (без генерации и судьи)")
    elif args.no_judge:
        print("mode: без судьи (--no-judge)")
    judge = None if (args.no_judge or args.retrieval_only) else build_judge()

    # 1. Ingest корпуса (async) и ждём завершения — ревизия знаний до прогона.
    submitted: list[dict[str, Any]] = []
    resolved_timeouts = {
        "unreachable_timeout_s": args.unreachable_timeout,
        "job_timeout_s": args.job_timeout,
        "wait_ceiling_s": args.wait_ceiling,
        "slot_timeout_s": args.slot_timeout,
        "submit_budget_s": args.submit_budget,
    }
    if args.corpus:
        corpus_dir = Path(args.corpus)
        documents = [str(item) for item in (args.documents or [])]
        submitted = ingest_corpus(
            corpus_dir,
            args.domain,
            args.source_prefix,
            slot_timeout_s=args.slot_timeout,
            submit_budget_s=args.submit_budget,
            documents=documents or None,
            limit=args.limit_docs,
        )
        print(f"ingest: {len(submitted)} документов поставлено, ждём завершения ...")
        statuses = wait_jobs(
            [record["job_id"] for record in submitted],
            unreachable_timeout_s=args.unreachable_timeout,
            job_timeout_s=args.job_timeout,
            ceiling_s=args.wait_ceiling,
        )
        # Попер-документное wall-time: ждём завершения пачкой, поэтому время
        # считаем от подачи заявки до последнего опроса — это включает очередь.
        finished_at = time.time()
        for record in submitted:
            record["waited_s"] = finished_at - record["submitted_at"]
        print(f"ingest: завершено ({len(statuses)}/{len(submitted)})")
    ingested_urls = {record["source_url"] for record in submitted}
    revision = fetch_revision(args.domain)
    print(f"revision: {revision}")

    # 2. Один run_id на прогон; манифест условий (design.md §5.1) пишется один раз.
    run_id = f"eval_{int(time.time())}"
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    flags = ["--retrieval-only"] if args.retrieval_only else (["--no-judge"] if args.no_judge else [])
    if args.trace:
        flags.append("--trace")
    if args.compare_with:
        flags.append(f"--compare-with={args.compare_with}")
    manifest = build_run_manifest(
        run_id=run_id,
        started_at=started_at,
        domain=args.domain,
        revision=revision,
        datasets=[args.dataset] + args.extra_dataset,
        mode_arg=args.mode,
        corpus=args.corpus,
        source_prefix=args.source_prefix,
        k=EVAL_K,
        judge=judge,
        flags=flags,
        corpus_documents=[record["source_url"] for record in submitted],
        corpus_limit=args.limit_docs,
        golden=golden_coverage(questions, ingested_urls),
        timeouts=resolved_timeouts,
        note=args.note,
    )
    manifest_path = out_dir / "run_manifest.json"
    print(f"manifest: {manifest_path}")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 3. qa_log переживает прерывание (append по вопросу); trace — только с флагом.
    qa_log_path = out_dir / "qa_log.jsonl"
    qa_log_path.write_text("", encoding="utf-8")
    failures_path = out_dir / "failures.jsonl"
    failures_path.write_text("", encoding="utf-8")
    failures: list[str] = []
    trace_path = out_dir / "trace.jsonl" if args.trace else None
    if trace_path is not None:
        trace_path.write_text("", encoding="utf-8")
    else:
        # Без флага артефакт не создаётся: устаревший trace прошлого прогона удаляем
        # (как и пересоздание qa_log строкой выше), чтобы out-dir отражал текущий прогон.
        stale_trace = out_dir / "trace.jsonl"
        if stale_trace.exists():
            stale_trace.unlink()
            print("artifact: удалён устаревший trace.jsonl (--trace не задан)")

    def _run_mode(mode: str, sink: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        # UC12-01 fix: в режиме ``both`` baseline выключает граф, target — включает.
        # Было: graph_enabled = mode == "hybrid"  →  в both оба режима выключали граф.
        graph_enabled = mode in ("hybrid", "target")
        pipeline = build_eval_pipeline()
        with graph_toggle(graph_enabled):
            records = []
            for question in questions:
                try:
                    record = eval_question(
                        question,
                        pipeline,
                        args.domain,
                        revision,
                        judge,
                        mode=mode,
                        graph_enabled=graph_enabled,
                        run_id=run_id,
                        generate=generate,
                        trace_enabled=args.trace,
                    )
                except RuntimeError as exc:
                    # Расхождение revision несовместимо с корректным измерением
                    # (данные меняются под ногами) — это валит прогон целиком.
                    if "revision mismatch" in str(exc):
                        raise
                    # Остальное (таймаут LLM, сбой адаптера) уходит в failures.jsonl:
                    # усечённый прогон не должен теряться из-за одного вопроса.
                    _append_jsonl(
                        failures_path,
                        {
                            "run_id": run_id,
                            "mode": mode,
                            "id": question.get("id", ""),
                            "query": question.get("query", ""),
                            "error": str(exc)[:512],
                            "error_type": type(exc).__name__,
                            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        },
                    )
                    failures.append(question.get("id", ""))
                    continue
                trace_events = record.pop("trace_events", [])
                _append_jsonl(qa_log_path, record)
                if trace_path is not None and trace_events:
                    _append_jsonl(trace_path, {**record, "events": trace_events})
                records.append(record)
                if sink is not None:
                    sink.append(record)
        aggregate_r = aggregate(records)
        print(f"run: {mode} → retrieval.recall@5={aggregate_r['retrieval'].get('recall_at_k')}")
        return aggregate_r

    modes = ["baseline", "target"] if args.mode == "both" else [args.mode]
    reports: dict[str, Any] = {}
    all_records: list[dict[str, Any]] = []
    for mode in modes:
        key = "baseline" if mode == "baseline" else "target"
        print(f"run: {key} ({len(questions)} вопросов) ...")
        reports[key] = _run_mode(mode, all_records)

    config_state = build_config_state(reports, all_records)
    for mode_key in reports:
        reports[mode_key]["config"] = config_state

    judge_active = judge is not None and generate
    # Отчёт ingest строится ДО подсчёта вердикта: потеря LLM-слоя инвалидирует
    # прогон, и вердикт обязан знать о ней, а не узнавать о ней постфактум в паспорте.
    ingest_summary = build_ingest_report(submitted, statuses) if submitted else None
    ingest_quality = build_ingest_quality(ingest_summary)
    for mode_key in reports:
        reports[mode_key]["ingest_quality"] = ingest_quality
    report = lift_report(
        baseline=reports.get("baseline", {}),
        target=reports.get("target", {}),
        revision=revision,
        judge_active=judge_active,
    )
    report["ingest_quality"] = ingest_quality
    report["config"] = config_state
    report["run_id"] = run_id
    report["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report["manifest"] = manifest
    manifest["projection_observed"] = {
        mode: {
            "statuses": sorted(
                {str(status) for status in aggregate_report.get("projection_statuses", [])}
            ),
            "revisions": sorted(
                {
                    str(projection_revision)
                    for projection_revision in aggregate_report.get("projection_revisions", [])
                }
            ),
        }
        for mode, aggregate_report in reports.items()
    }
    # Конфигурация дописывается после прогона: она известна только из ответов, а манифест
    # перезаписывается тут же — тот же приём, что с наблюдённой проекцией.
    manifest["config"] = config_state
    write_run_manifest(manifest, out_dir)

    # 4. Сравнение с парным прогоном (design.md §5.1): расхождение факторов → invalid.
    if args.compare_with:
        try:
            other = load_compare_manifest(args.compare_with)
        except (ValueError, TypeError, OSError) as exc:
            print(f"compare-with: {exc}")
            report["pair"] = {
                "paired": False,
                "differing_factors": [],
                "message": str(exc),
                "compare_with": args.compare_with,
            }
            # Парность не подтверждена → verdict не может быть pass/fail (валидность
            # результата зависит от пары), иначе JSON и lift_report.md противоречат друг другу.
            report["verdict"] = "invalid"
        else:
            pair = compare_run_manifests(manifest, other)
            pair["compare_with"] = args.compare_with
            report["pair"] = pair
            if not pair["paired"]:
                report["verdict"] = "invalid"
            print(f"pair: {'paired' if pair['paired'] else 'NOT paired: ' + (pair['message'] or '')}")

    write_lift_report(report, out_dir, manifest)

    # 5. Сопутствующие артефакты прогона: паспорт, разбор пар, отчёт ingest.
    argv = ["python", "/app/infra/eval/run_eval.py", *sys.argv[1:]]
    write_command_file(out_dir, argv)
    if ingest_summary is not None:
        write_ingest_report(ingest_summary, out_dir)
    write_qa_review(all_records, out_dir)
    write_passport(
        manifest,
        report,
        out_dir,
        ingest=ingest_summary,
        failures=len(failures),
        argv=argv,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
