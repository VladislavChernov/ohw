"""Пробник извлечения: посмотреть, что модель делает на настоящем документе.

Задача узкая и важная: не «прогнать eval», а посмотреть сырой вывод модели на реальном
тексте и посчитать, во сколько токенов обходится разбор. Ответ на три вопроса, которые
сейчас не имеют ответа:

  * обрывается ли ответ по max_tokens на настоящем документе, или это было свойство
    крошечного документа из E2E-фикстуры;
  * что модель пишет в ответ, если ответ не приходит вовсе;
  * во сколько токенов обходится промпт и во сколько - ответ, то есть кто ест бюджет.

Журнал берётся из того же тома `ingestion_data`, куда его пишет ingestion-api
(`LLM_EXCHANGE_LOG_FILE`), поэтому этот скрипт не перехватывает обмен, а только читает
уже записанное. Проверяется и то, что запись вообще идёт: пустой журнал при
`EXTRACT_LLM=true` - это тоже результат, и о нём нужно сказать, а не промолчать.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TERMINAL = frozenset({"succeeded", "failed", "cancelled"})


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} не задан: пробник не угадывает адреса и домен")
    return value


def _log(message: str) -> None:
    print(f"[{datetime.now(UTC).strftime('%H:%M:%S')}] {message}", flush=True)


def _opt(name: str) -> str | None:
    """Необязательная переменная: пустое значение и отсутствие равнозначны."""
    value = os.environ.get(name, "").strip()
    return value or None


def _request(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    api_key: str | None = None,
    timeout: float = 60.0,
) -> tuple[int, str]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    if api_key:
        request.add_header("X-API-Key", api_key)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read().decode("utf-8", "replace")


def _ingestion(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    url = _required("INGESTION_URL").rstrip("/") + path
    status, text = _request(
        method,
        url,
        payload=payload,
        api_key=os.environ.get("API_KEY") or os.environ.get("AUTH_API_KEY") or "",
    )
    if status >= 400:
        raise RuntimeError(f"{method} {path} -> {status}: {text[:300]}")
    return json.loads(text or "{}")


def ingest(source_url: str, domain: str, content: str) -> dict[str, Any]:
    body = {
        "source_url": source_url,
        "domain": domain,
        "doc_type": "md",
        "content": content,
        "tags": [],
        "links": [],
        "metadata": {"scenario": "llm-probe"},
    }
    started = _ingestion("POST", "/api/v1/ingestion/documents", body)
    job_id = str(started["job_id"])
    _log(f"POST {source_url} -> {job_id}")
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        job = _ingestion("GET", f"/api/v1/ingestion/jobs/{job_id}")
        if str(job.get("status")) in TERMINAL:
            return dict(job)
        time.sleep(3)
    raise RuntimeError(f"джоба {job_id} не завершилась за 30 минут")


def read_exchange(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                _log(f"ПРОПУЩЕНА нечитаемая строка журнала: {line[:120]}")
    return records


def summarize(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Сводит журнал в числа: кто ел бюджет и чем кончилось."""
    items = list(records)
    prompt_tokens = sum(int((r.get("usage") or {}).get("prompt_tokens") or 0) for r in items)
    completion_tokens = sum(int((r.get("usage") or {}).get("completion_tokens") or 0) for r in items)
    errors: dict[str, int] = {}
    for record in items:
        name = str(record.get("error") or "")
        if name:
            errors[name] = errors.get(name, 0) + 1
    lengths = sorted(int(record.get("response_chars") or 0) for record in items)
    return {
        "calls": len(items),
        "prompt_tokens_total": prompt_tokens,
        "completion_tokens_total": completion_tokens,
        "prompt_tokens_avg": round(prompt_tokens / len(items), 1) if items else 0,
        "completion_tokens_avg": round(completion_tokens / len(items), 1) if items else 0,
        "errors": errors,
        "response_chars_min": lengths[0] if lengths else 0,
        "response_chars_max": lengths[-1] if lengths else 0,
        "response_chars_median": lengths[len(lengths) // 2] if lengths else 0,
        "labels_present": sum(1 for r in items if r.get("labels")),
        # Считаем ИСТИННЫЕ признаки усечения, а не наличие ключа: словарь
        # {"response": false} в Python истинен, и подсчёт по нему врал бы.
        "truncated_any": sum(
            1 for r in items if any(bool(v) for v in (r.get("truncated") or {}).values())
        ),
    }


def main() -> int:
    domain = os.environ.get("DOMAIN", "it").strip() or "it"
    out = Path(_required("ARTIFACTS_DIR"))
    exchange_path = Path(_required("EXCHANGE_LOG"))
    corpus = Path(_required("CORPUS_DIR"))
    sources = [
        item.strip()
        for item in os.environ.get("PROBE_SOURCES", "").split(",")
        if item.strip()
    ]
    if not sources:
        raise RuntimeError("PROBE_SOURCES не задан: список документов через запятую")
    out.mkdir(parents=True, exist_ok=True)

    # Снимок ДО: журнал накапливается между прогонами, и без снимка нельзя отличить
    # старые записи от новых.
    before = read_exchange(exchange_path)
    _log(f"журнал до прогона: {len(before)} записей")

    jobs: list[dict[str, Any]] = []
    for relative in sources:
        path = corpus / relative
        if not path.is_file():
            raise RuntimeError(f"документ не найден: {path}")
        text = path.read_text(encoding="utf-8")
        _log(f"документ {relative}: {len(text)} символов")
        job = ingest(relative, domain, text)
        enrichment = job.get("enrichment") or {}
        _log(
            "  {status}, cause={cause}, сущностей={entities}, рёбер={edges}".format(
                status=job.get("status"),
                cause=enrichment.get("cause"),
                entities=enrichment.get("llm_records"),
                edges=enrichment.get("llm_edges"),
            )
        )
        jobs.append(
            {
                "source_url": relative,
                "chars": len(text),
                "job_id": job.get("job_id"),
                "status": job.get("status"),
                "error": job.get("error"),
                "enrichment": enrichment,
                "degraded": "enrichment_degraded" in (job.get("signals") or {}),
            }
        )

    records = read_exchange(exchange_path)
    fresh = records[len(before) :] if len(records) >= len(before) else records
    summary = summarize(fresh)

    (out / "jobs.json").write_text(
        json.dumps(jobs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out / "exchange_fresh.json").write_text(
        json.dumps(fresh, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # Артефакт без критерия — это лог. Манифест делает прогон самодостаточным: без него
    # через месяц нельзя понять, что проверялось, на каком коде и почему verdict такой.
    manifest = {
        "variant": _opt("PROBE_VARIANT"),
        "hypothesis": _opt("PROBE_HYPOTHESIS"),
        "criteria": [
            line.strip()
            for line in (_opt("PROBE_CRITERION") or "").splitlines()
            if line.strip()
        ],
        "code_version": _opt("PROBE_CODE_VERSION"),
        "profile_variant": _opt("PROBE_PROFILE_NOTE"),
        "sources": _opt("PROBE_SOURCES"),
        "ingestion_url": _opt("INGESTION_URL"),
        "documents": [
            {
                "source_url": job["source_url"],
                "status": job["status"],
                "cause": (job["enrichment"] or {}).get("cause"),
                "degraded": job["degraded"],
            }
            for job in jobs
        ],
        "verdict": "degraded" if any(job["degraded"] for job in jobs) else "clean",
        "metrics": summary,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    _log("=== СВОДКА ПО СВЕЖИМ ЗАПИСЯМ ===")
    for key, value in summary.items():
        _log(f"  {key}: {value}")
    degraded = [job for job in jobs if job["degraded"]]
    _log(f"документов с деградацией извлечения: {len(degraded)} из {len(jobs)}")
    for job in degraded:
        _log(f"  {job['source_url']}: cause={(job['enrichment'] or {}).get('cause')}")
    if not fresh:
        _log("ВНИМАНИЕ: свежих записей журнала нет - либо EXTRACT_LLM выключен, либо "
             "журнал не включён. Это результат, а не пустота.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())