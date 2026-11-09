"""Report writing, reading and Markdown rendering."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import SCHEMA_VERSION, ExperimentReport
from .util import iso_utc, new_run_id, utc_now, write_json_atomic, write_text_atomic

REPORT_JSON = "report.json"
REPORT_MD = "report.md"
MANIFEST_JSON = "manifest.json"
NORMALIZED_MD = "normalized.md"
PROMPT_TXT = "prompt.txt"
RAW_DIR = "raw"

KIND_TITLES = {
    "health": "Стенд",
    "api": "Корректность API",
    "api-check": "Корректность API",
    "hallucinations": "Галлюцинации",
    "consistency": "Согласованность",
    "canary": "Канарейка",
    "degradation": "Деградация",
    "report": "Отчёт",
}


class ReportError(Exception):
    """Report is missing, malformed or has an unsupported schema (code 2/4)."""


@dataclass(frozen=True)
class RunContext:
    run_id: str
    directory: Path
    kind: str
    started_at: str


def create_run(output_dir: Path, kind: str) -> RunContext:
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = new_run_id()
    directory = output_dir / run_id
    suffix = 0
    while directory.exists():
        suffix += 1
        directory = output_dir / f"{run_id}-{suffix}"
    directory.mkdir(parents=True)
    (directory / "runs").mkdir()
    return RunContext(
        run_id=directory.name,
        directory=directory,
        kind=kind,
        started_at=iso_utc(),
    )


def write_inputs(context: RunContext, normalized_text: str, prompt_text: str) -> None:
    write_text_atomic(context.directory / NORMALIZED_MD, normalized_text)
    write_text_atomic(context.directory / PROMPT_TXT, prompt_text)


def git_commit(root: Path) -> str | None:
    explicit = os.environ.get("LLM_LAB_GIT_COMMIT")
    if explicit:
        return explicit
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    return value or None


def build_manifest(report: ExperimentReport, *, root: Path) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": report.run_id,
        "kind": report.kind,
        "started_at": report.started_at,
        "finished_at": report.finished_at,
        "git_commit": git_commit(root),
        "environment": report.environment.model_dump(mode="json"),
        "input": report.input.model_dump(mode="json"),
        "prompt": report.prompt.model_dump(mode="json"),
        "params": report.params,
        "warnings": report.warnings,
    }


def write_report(
    context: RunContext,
    report: ExperimentReport,
    *,
    root: Path,
    keep_runs: int | None = None,
) -> tuple[Path, Path]:
    report_path = context.directory / REPORT_JSON
    markdown_path = context.directory / REPORT_MD
    manifest_path = context.directory / MANIFEST_JSON
    write_json_atomic(report_path, report.model_dump(mode="json"))
    write_text_atomic(markdown_path, render_markdown(report))
    write_json_atomic(manifest_path, build_manifest(report, root=root))
    export_raw_logs(context.directory)
    if keep_runs is not None:
        prune_reports(context.directory.parent, keep_runs)
    return report_path, markdown_path


def export_raw_logs(run_dir: Path) -> list[Path]:
    """Mirror every model request/response into ``<run_dir>/raw``.

    ``run_series`` stores all attempts (including retries and failed calls) in
    ``request.json``; text probes store the request and ``response.txt``. The
    raw folder is a flat, easy-to-read copy: one ``.txt`` with the model output
    and one ``.json`` with the full attempt metadata per call.
    """
    raw_root = run_dir / RAW_DIR
    written: list[Path] = []
    for request_path in sorted(run_dir.rglob("request.json")):
        relative = request_path.parent.relative_to(run_dir)
        try:
            payload = json.loads(request_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        attempts = payload.get("attempts")
        if isinstance(attempts, list) and attempts:
            for index, attempt in enumerate(attempts, start=1):
                stem = f"attempt-{index:02d}"
                text = attempt.get("raw") or attempt.get("error") or ""
                text_path = raw_root / relative / f"{stem}.txt"
                meta_path = raw_root / relative / f"{stem}.json"
                write_text_atomic(text_path, text)
                write_json_atomic(meta_path, attempt)
                written.extend([text_path, meta_path])
            continue
        response_path = request_path.parent / "response.txt"
        if not response_path.is_file():
            continue
        text_path = raw_root / relative / "response.txt"
        meta_path = raw_root / relative / "request.json"
        write_text_atomic(text_path, response_path.read_text(encoding="utf-8"))
        write_json_atomic(meta_path, payload)
        written.extend([text_path, meta_path])
    return written


def prune_reports(output_dir: Path, keep: int) -> list[Path]:
    """Delete the oldest run directories, keeping the newest ``keep`` reports.

    Only directories with a readable ``report.json`` are considered, so
    ``.gitkeep`` and any unrelated files are preserved.
    """
    if keep < 1 or not output_dir.is_dir():
        return []
    candidates: list[tuple[str, Path]] = []
    for path in output_dir.glob(f"*/{REPORT_JSON}"):
        try:
            report = load_report(path)
        except ReportError:
            continue
        candidates.append((report.run_id, path.parent))
    candidates.sort(key=lambda item: item[0], reverse=True)
    removed: list[Path] = []
    for _run_id, directory in candidates[keep:]:
        shutil.rmtree(directory, ignore_errors=True)
        removed.append(directory)
    return removed


def finalize_report(report: ExperimentReport, context: RunContext) -> ExperimentReport:
    return report.model_copy(
        update={
            "run_id": context.run_id,
            "started_at": report.started_at or context.started_at,
            "finished_at": report.finished_at or iso_utc(utc_now()),
        }
    )


def load_report(path: Path) -> ExperimentReport:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReportError(f"cannot read report {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ReportError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ReportError(f"report {path} is not a JSON object")
    version = raw.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ReportError(
            f"unsupported report schema_version in {path}: {version!r} (expected {SCHEMA_VERSION})"
        )
    try:
        return ExperimentReport.model_validate(raw)
    except ValidationError as exc:
        raise ReportError(f"report schema validation failed for {path}: {exc}") from exc


def list_reports(output_dir: Path, *, limit: int = 100) -> list[dict[str, Any]]:
    if not output_dir.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(output_dir.glob(f"*/{REPORT_JSON}"), reverse=True):
        try:
            report = load_report(path)
        except ReportError:
            continue
        items.append(
            {
                "run_id": report.run_id,
                "kind": report.kind,
                "started_at": report.started_at,
                "finished_at": report.finished_at,
                "model": report.environment.model,
                "verdict": report.verdict.status,
                "runs": len(report.runs),
                "path": str(path),
            }
        )
        if len(items) >= limit:
            break
    return items


def render_markdown(report: ExperimentReport) -> str:
    title = KIND_TITLES.get(report.kind, report.kind)
    lines: list[str] = [
        f"# {title}: {report.environment.model or '-'}",
        "",
        f"- run_id: `{report.run_id}`",
        f"- Начало: {report.started_at}",
        f"- Окончание: {report.finished_at}",
        f"- Вердикт: **{report.verdict.status}**",
        f"- Модель: `{report.environment.model}`, digest `{report.environment.digest or '-'}`",
        f"- Ollama: `{report.environment.ollama_version or '-'}`",
        f"- Параметры: temperature={report.environment.temperature}, "
        f"seed={report.environment.seed}, num_ctx={report.environment.num_ctx}, "
        f"num_predict={report.environment.num_predict}, "
        f"max_retries={report.environment.max_retries}",
        "",
        "## Вход",
        "",
        f"- Файл: `{report.input.path}`",
        f"- Кодировка: `{report.input.encoding}`",
        f"- URL: {report.input.url or '-'}",
        f"- BR: {', '.join(report.input.br_ids) or '-'}",
        f"- prompt_sha256: `{report.prompt.prompt_sha256}`",
        f"- Тип: `{report.prompt.artifact_type}`, count={report.prompt.count}",
        "",
    ]

    if report.verdict.reasons:
        lines.extend(["## Вердикт", ""])
        lines.extend(f"- {reason}" for reason in report.verdict.reasons)
        lines.append("")

    if report.runs:
        lines.extend(
            [
                "## Прогоны",
                "",
                "| # | cell | validator | attempts | cases | pos | neg | "
                "BR coverage | latency, ms |",
                "|---|---|---|---|---|---|---|---|---|",
            ]
        )
        for run in report.runs:
            metrics = run.metrics
            lines.append(
                f"| {run.index} | {run.cell or '-'} | "
                f"{'yes' if run.validator_pass else 'no'} | {run.attempts} | "
                f"{metrics.cases_total} | {metrics.positive} | {metrics.negative} | "
                f"{metrics.br_coverage:.2f} | {run.latency_ms:.0f} |"
            )
        lines.append("")

    if report.summary:
        lines.extend(["## Сводка", "", "| Метрика | Значение |", "|---|---|"])
        for key, value in report.summary.items():
            if isinstance(value, (dict, list)):
                rendered = "`" + json.dumps(value, ensure_ascii=False) + "`"
            else:
                rendered = str(value)
            lines.append(f"| {key} | {rendered} |")
        lines.append("")

    if report.kind == "degradation" and report.baseline and report.candidate:
        lines.extend(
            [
                "## Degradation",
                "",
                "| Метрика | Baseline | Candidate | Diff |",
                "|---|---|---|---|",
            ]
        )
        for key, value in (report.comparison or {}).items():
            if isinstance(value, dict) and {"baseline", "candidate", "diff"} <= set(value):
                lines.append(
                    f"| {key} | {value['baseline']} | {value['candidate']} | {value['diff']} |"
                )
        lines.append("")
        if report.verdict.reasons:
            lines.extend(["Причины:", ""])
            lines.extend(f"- {reason}" for reason in report.verdict.reasons)
            lines.append("")

    if report.kind == "canary":
        lines.extend(["## Канарейка", ""])
        canary = report.summary.get("canary")
        if isinstance(canary, dict):
            for key in (
                "ids_exact",
                "count_exact",
                "url_exact",
                "title_overlap",
                "body_overlap_mean",
                "body_overlap_min",
            ):
                lines.append(f"- {key}: {canary.get(key)}")
        lines.append("")

    if report.warnings:
        lines.extend(["## Предупреждения", ""])
        lines.extend(f"- {warning}" for warning in report.warnings)
        lines.append("")

    lines.extend(
        [
            "## Файлы",
            "",
            f"- `{REPORT_JSON}`",
            f"- `{NORMALIZED_MD}`",
            f"- `{PROMPT_TXT}`",
            f"- `{MANIFEST_JSON}`",
            "- `runs/`",
            f"- `{RAW_DIR}/` (полные ответы модели)",
            "",
        ]
    )
    return "\n".join(lines)


def report_envelope(report: ExperimentReport, report_path: Path) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": report.verdict.status != "fail",
        "kind": report.kind,
        "run_id": report.run_id,
        "report_path": str(report_path),
        "verdict": report.verdict.status,
        "error": None,
        "report": report.model_dump(mode="json"),
    }
