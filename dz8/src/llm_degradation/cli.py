"""Command-line interface for llm-degradation-lab."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, NoReturn

from ai_testgen.models import ArtifactType

from .config import ConfigError, LabConfig, load_config
from .degradation import DegradationError, run_degradation
from .models import (
    EnvironmentInfo,
    ErrorInfo,
    ExperimentReport,
    InputInfo,
    PromptInfo,
    VerdictInfo,
)
from .normalizer_bridge import (
    BridgeError,
    build_prompt,
    load_normalized_from_config,
    normalizer_snapshot,
    prompt_sha256,
)
from .ollama import (
    LabOllamaClient,
    ModelMissing,
    OllamaResponseError,
    OllamaUnavailable,
)
from .probes.api_contract import api_verdict, run_api_checks
from .probes.canary import canary_prompt, canary_system, run_canary
from .probes.consistency import run_consistency
from .probes.hallucinations import (
    DEFAULT_PROBES_PATH,
    HallucinationResult,
    load_probes,
    run_hallucinations,
)
from .report import (
    REPORT_JSON,
    ReportError,
    create_run,
    list_reports,
    load_report,
    report_envelope,
    write_inputs,
    write_report,
)
from .runner import SeriesOptions
from .util import iso_utc, write_json_atomic, write_text_atomic

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_OLLAMA = 3
EXIT_CHECK = 4
EXIT_REGRESSION = 5

ARTIFACT_TYPES = tuple(item.value for item in ArtifactType)


class CliUsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise CliUsageError(message)


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parse(argv)
    except CliUsageError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    try:
        return _dispatch(args)
    except ConfigError as exc:
        return _emit_error(args, EXIT_USAGE, str(exc))
    except BridgeError as exc:
        return _emit_error(args, EXIT_USAGE, str(exc))
    except ModelMissing as exc:
        return _emit_error(args, EXIT_OLLAMA, str(exc), _pull_hint(args))
    except OllamaUnavailable as exc:
        return _emit_error(args, EXIT_OLLAMA, str(exc), _stand_hint())
    except OllamaResponseError as exc:
        return _emit_error(args, EXIT_CHECK, str(exc))
    except DegradationError as exc:
        return _emit_error(args, EXIT_CHECK, str(exc))
    except ReportError as exc:
        return _emit_error(args, EXIT_USAGE, str(exc))
    except ValueError as exc:
        return _emit_error(args, EXIT_USAGE, str(exc))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = _Parser(prog="llm-lab", description="LLM degradation lab")
    parser.add_argument("--version", action="version", version="llm-degradation-lab 0.1.0")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = _common_parser()
    for name in (
        "health",
        "normalize",
        "api-check",
        "hallucinations",
        "consistency",
        "canary",
        "degradation",
        "report",
    ):
        subparsers.add_parser(name, parents=[common])

    parse = subparsers.choices["normalize"]
    parse.add_argument("--url", default=None)
    parse.add_argument("--write", default=None, help="write canonical text to this path")

    parse = subparsers.choices["hallucinations"]
    parse.add_argument("--probes", default=None)
    parse.add_argument("--strict", action="store_true")
    parse.add_argument("--artifact-type", default=None, choices=ARTIFACT_TYPES)
    parse.add_argument("--count", default=None, type=int)
    parse.add_argument("--runs", default=None, type=int)

    parse = subparsers.choices["consistency"]
    parse.add_argument("--artifact-type", default=None, choices=ARTIFACT_TYPES)
    parse.add_argument("--count", default=None, type=int)
    parse.add_argument("--runs", default=None, type=int)

    parse = subparsers.choices["canary"]
    parse.add_argument("--runs", default=None, type=int)

    parse = subparsers.choices["degradation"]
    parse.add_argument("--artifact-type", default=None, choices=ARTIFACT_TYPES)
    parse.add_argument("--count", default=None, type=int)
    parse.add_argument("--runs", default=None, type=int)
    parse.add_argument("--baseline-model", default=None)
    parse.add_argument("--candidate-model", default=None)
    parse.add_argument("--baseline-report", default=None)
    parse.add_argument("--fail-on-regression", action="store_true")

    parse = subparsers.choices["report"]
    parse.add_argument("--run-id", default=None)
    parse.add_argument("--report", default=None)
    parse.add_argument("--format", choices=("md", "json"), default="md")
    parse.add_argument("--list", action="store_true", dest="list_reports")

    return parser.parse_args(argv)


def _common_parser() -> _Parser:
    common = _Parser(add_help=False)
    common.add_argument("--config", default=None)
    common.add_argument("--base-url", default=None)
    common.add_argument("--model", default=None)
    common.add_argument("--timeout", default=None, type=float)
    common.add_argument("--input", default=None)
    common.add_argument("--temperature", default=None, type=float)
    common.add_argument("--seed", default=None, type=int)
    common.add_argument("--num-ctx", default=None, type=int)
    common.add_argument("--num-predict", default=None, type=int)
    common.add_argument("--max-retries", default=None, type=int)
    common.add_argument("--keep-runs", default=None, type=int)
    common.add_argument("--preset", action="append", default=None)
    common.add_argument("--output-dir", default=None)
    common.add_argument("--json", action="store_true")
    common.add_argument("--quiet", action="store_true")
    return common


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "report":
        return _command_report(args)

    config = _config_from_args(args)
    if args.command == "normalize":
        return _command_normalize(args, config)

    client = LabOllamaClient(config.base_url, config.model, config.timeout)
    try:
        if args.command == "health":
            return _command_health(args, config, client)
        if args.command == "api-check":
            return _command_api(args, config, client)
        if args.command == "hallucinations":
            return _command_hallucinations(args, config, client)
        if args.command == "consistency":
            return _command_consistency(args, config, client)
        if args.command == "canary":
            return _command_canary(args, config, client)
        if args.command == "degradation":
            return _command_degradation(args, config, client)
    finally:
        client.close()
    raise CliUsageError(f"unknown command: {args.command}")


def _config_from_args(args: argparse.Namespace) -> LabConfig:
    overrides: dict[str, Any] = {}
    for key in (
        "base_url",
        "model",
        "timeout",
        "temperature",
        "seed",
        "num_ctx",
        "num_predict",
        "max_retries",
        "keep_runs",
        "output_dir",
    ):
        if hasattr(args, key):
            value = getattr(args, key)
            if value is not None:
                overrides[key] = value
    return load_config(args.config, overrides=overrides)


def _series_options(
    args: argparse.Namespace, config: LabConfig, default_runs: int
) -> SeriesOptions:
    runs = args.runs if getattr(args, "runs", None) is not None else default_runs
    if runs < 1:
        raise ConfigError("runs must be >= 1")
    return SeriesOptions(
        runs=runs,
        temperature=config.temperature,
        seed=config.seed,
        num_ctx=config.num_ctx,
        num_predict=config.num_predict,
        max_retries=config.max_retries,
    )


def _command_health(
    args: argparse.Namespace,
    config: LabConfig,
    client: LabOllamaClient,
) -> int:
    model = args.model or config.model
    health = client.health(model)
    payload = {
        "schema_version": 1,
        "ok": bool(health.get("ok")),
        "kind": "health",
        "base_url": config.base_url,
        "ollama_version": health.get("ollama_version", ""),
        "model": model,
        "digest": health.get("digest"),
        "models": health.get("models", []),
        "command_hint": health.get("command_hint"),
        "error": None if health.get("ok") else health.get("error", "health check failed"),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    elif health.get("ok"):
        print(
            f"ok: Ollama {payload['ollama_version']} at {config.base_url}; "
            f"model {model}; digest {payload['digest']}"
        )
    else:
        print(f"error: {payload['error']}", file=sys.stderr)
        if payload["command_hint"]:
            print(f"hint: {payload['command_hint']}", file=sys.stderr)
    return EXIT_OK if health.get("ok") else EXIT_OLLAMA


def _command_normalize(args: argparse.Namespace, config: LabConfig) -> int:
    input_path = Path(args.input) if args.input else config.input_dir
    normalized = load_normalized_from_config(
        input_path, url=args.url, normalizer_config=config.normalizer_config
    )
    artifact_type = _artifact_type(args)
    count = args.count if getattr(args, "count", None) is not None else 5
    prompt = build_prompt(
        normalized,
        artifact_type,
        count,
        presets=args.preset or (),
        prompt_dir=config.prompt_dir,
        presets_dir=config.presets_dir,
    )
    if args.write:
        Path(args.write).write_text(normalized.text, encoding="utf-8")
    payload = {
        "schema_version": 1,
        "ok": True,
        "kind": "normalize",
        "source": str(normalized.source),
        "encoding": normalized.encoding,
        "source_sha256": normalized.source_sha256,
        "text_sha256": normalized.text_sha256,
        "title": normalized.title,
        "url": normalized.url,
        "br_ids": list(normalized.br_ids),
        "warnings": list(normalized.warnings),
        "prompt_sha256": prompt.prompt_sha256,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(normalized.text)
    return EXIT_OK


def _command_api(
    args: argparse.Namespace,
    config: LabConfig,
    client: LabOllamaClient,
) -> int:
    context = create_run(config.output_dir, "api")
    checks = run_api_checks(client, config.model, prompt_dir=config.probe_prompt_dir)
    verdict = api_verdict(checks)
    report = _empty_report(config, client, "api", context, runs=0)
    report = report.model_copy(
        update={
            "finished_at": iso_utc(),
            "summary": {"checks": [item.model_dump(mode="json") for item in checks]},
            "verdict": verdict,
        }
    )
    if all(item.status == "fail" and item.http_status is None for item in checks):
        return _finish(args, config, context, report, EXIT_OLLAMA)
    return _finish(
        args,
        config,
        context,
        report,
        EXIT_CHECK if verdict.status == "fail" else EXIT_OK,
    )


def _command_hallucinations(
    args: argparse.Namespace,
    config: LabConfig,
    client: LabOllamaClient,
) -> int:
    probes_path = Path(args.probes) if args.probes else config.golden_dir / DEFAULT_PROBES_PATH
    probes = load_probes(probes_path)
    options = _series_options(args, config, default_runs=3)

    input_path = Path(args.input) if args.input else config.input_dir
    if args.input is None:
        # The probe file owns its inputs; use the first one for the report header.
        input_path = _probe_input(config, probes[0].input)

    normalized = load_normalized_from_config(input_path, normalizer_config=config.normalizer_config)
    artifact_type = _artifact_type(args)
    count = args.count if args.count is not None else 5
    prompt = build_prompt(
        normalized,
        artifact_type,
        count,
        presets=args.preset or (),
        prompt_dir=config.prompt_dir,
        presets_dir=config.presets_dir,
    )
    context = create_run(config.output_dir, "hallucinations")
    write_inputs(context, normalized.text, prompt.user)

    def progress(index: int, total: int, probe_id: str) -> None:
        _log(args, f"probe {index}/{total}: {probe_id}")

    result: HallucinationResult = run_hallucinations(
        client,
        probes,
        model=config.model,
        root=config.root,
        golden_dir=config.golden_dir,
        options=options,
        runs=options.runs,
        strict=args.strict,
        thresholds=config.thresholds,
        normalizer_config=config.normalizer_config,
        prompt_dir=config.prompt_dir,
        probe_prompt_dir=config.probe_prompt_dir,
        runs_dir=context.directory,
        progress=progress,
    )
    report = _report(
        config,
        client,
        context,
        kind="hallucinations",
        normalized=normalized,
        prompt=prompt,
        options=options,
    )
    summary = dict(result.summary)
    summary["probes"] = [item.model_dump(mode="json") for item in result.probes]
    inputs = sorted({probe.input for probe in probes})
    warnings = list(normalized.warnings)
    if len(inputs) > 1:
        warnings.append("probe inputs: " + ", ".join(inputs))
    report = report.model_copy(
        update={
            "finished_at": iso_utc(),
            "summary": summary,
            "verdict": result.verdict,
            "warnings": warnings,
        }
    )
    if result.verdict.status == "fail" and args.strict:
        code = EXIT_REGRESSION
    elif result.verdict.status == "fail":
        code = EXIT_CHECK
    else:
        code = EXIT_OK
    return _finish(args, config, context, report, code)


def _command_consistency(
    args: argparse.Namespace,
    config: LabConfig,
    client: LabOllamaClient,
) -> int:
    options = _series_options(args, config, default_runs=10)
    normalized, prompt = _prepare(args, config)
    context = create_run(config.output_dir, "consistency")
    write_inputs(context, normalized.text, prompt.user)

    def progress(index: int, total: int, record: Any) -> None:
        _log(args, f"run {index}/{total}: validator_pass={record.validator_pass}")

    result = run_consistency(
        client,
        normalized,
        prompt,
        options=options,
        thresholds=config.thresholds,
        prompt_dir=config.prompt_dir,
        runs_dir=context.directory / "runs",
        progress=progress,
    )
    report = _report(
        config,
        client,
        context,
        kind="consistency",
        normalized=normalized,
        prompt=prompt,
        options=options,
    )
    report = report.model_copy(
        update={
            "finished_at": iso_utc(),
            "runs": result.runs,
            "summary": result.summary,
            "verdict": result.verdict,
            "warnings": list(normalized.warnings),
        }
    )
    return _finish(
        args,
        config,
        context,
        report,
        EXIT_CHECK if result.verdict.status == "fail" else EXIT_OK,
    )


def _command_canary(
    args: argparse.Namespace,
    config: LabConfig,
    client: LabOllamaClient,
) -> int:
    options = _series_options(args, config, default_runs=1)
    normalized = load_normalized_from_config(
        Path(args.input) if args.input else config.input_dir,
        normalizer_config=config.normalizer_config,
    )
    prompt_text = canary_prompt(normalized, config.probe_prompt_dir)
    context = create_run(config.output_dir, "canary")
    write_inputs(context, normalized.text, prompt_text)

    def progress(index: int, total: int, result: Any) -> None:
        _log(args, f"canary run {index}/{total}: {result.verdict}")

    result = run_canary(
        client,
        normalized,
        model=config.model,
        runs=options.runs,
        options=options,
        thresholds=config.thresholds,
        on_run=progress,
        probe_prompt_dir=config.probe_prompt_dir,
    )
    canary_payload = [item.model_dump(mode="json") for item in result.results]
    raw_dir = context.directory / "raw" / "canary"
    write_json_atomic(
        raw_dir / "request.json",
        {
            "model": config.model,
            "temperature": 0.0,
            "seed": options.seed,
            "num_ctx": options.num_ctx,
            "num_predict": options.num_predict,
            "messages": [
                {"role": "system", "content": canary_system(config.probe_prompt_dir)},
                {"role": "user", "content": prompt_text},
            ],
        },
    )
    for index, item in enumerate(result.results, start=1):
        write_text_atomic(raw_dir / f"run-{index:02d}.txt", item.raw)
    summary = {
        "runs": len(result.results),
        "canary": canary_payload[0] if len(canary_payload) == 1 else canary_payload,
        "results": canary_payload,
    }
    prompt_info = PromptInfo(
        artifact_type="canary",
        count=0,
        presets=[],
        prompt_sha256=prompt_sha256(prompt_text),
    )
    report = _report(
        config,
        client,
        context,
        kind="canary",
        normalized=normalized,
        prompt_info=prompt_info,
        options=options,
    )
    verdict = VerdictInfo(
        status=result.verdict,
        reasons=result.reasons,
        thresholds={
            "canary_title_overlap_min": config.thresholds.canary_title_overlap_min,
            "canary_body_overlap_mean_min": config.thresholds.canary_body_overlap_mean_min,
            "canary_body_overlap_min": config.thresholds.canary_body_overlap_min,
        },
    )
    report = report.model_copy(
        update={
            "finished_at": iso_utc(),
            "runs": [],
            "summary": summary,
            "verdict": verdict,
            "warnings": list(normalized.warnings),
        }
    )
    return _finish(
        args,
        config,
        context,
        report,
        EXIT_CHECK if result.verdict == "fail" else EXIT_OK,
    )


def _command_degradation(
    args: argparse.Namespace,
    config: LabConfig,
    client: LabOllamaClient,
) -> int:
    options = _series_options(args, config, default_runs=10)
    if options.runs < 1:
        raise ConfigError("runs must be >= 1")
    normalized, prompt = _prepare(args, config)

    baseline_report = None
    if args.baseline_report:
        baseline_report = load_report(Path(args.baseline_report))
    if (args.baseline_model is None) == (baseline_report is None):
        raise DegradationError(
            "exactly one baseline source is required: --baseline-model or --baseline-report"
        )
    context = create_run(config.output_dir, "degradation")
    write_inputs(context, normalized.text, prompt.user)

    def progress(cell: str, index: int, total: int, record: Any) -> None:
        _log(args, f"{cell} run {index}/{total}: validator_pass={record.validator_pass}")

    result = run_degradation(
        client,
        normalized,
        prompt,
        baseline_model=args.baseline_model,
        candidate_model=args.candidate_model or config.model,
        options=options,
        thresholds=config.thresholds,
        prompt_dir=config.prompt_dir,
        runs_dir=context.directory / "runs",
        baseline_report=baseline_report,
        progress=progress,
    )
    report = _report(
        config,
        client,
        context,
        kind="degradation",
        normalized=normalized,
        prompt=prompt,
        options=options,
    )
    report = report.model_copy(
        update={
            "finished_at": iso_utc(),
            "runs": result.runs,
            "summary": result.candidate_summary,
            "verdict": result.verdict,
            "warnings": list(normalized.warnings),
            "baseline": result.baseline,
            "candidate": result.candidate,
            "comparison": result.comparison,
            "environment": report.environment.model_copy(update={"model": result.candidate_model}),
        }
    )

    if result.verdict.status == "insufficient_data":
        code = EXIT_CHECK
    elif result.verdict.status == "regressed" and args.fail_on_regression:
        code = EXIT_REGRESSION
    else:
        code = EXIT_OK
    return _finish(args, config, context, report, code)


def _command_report(args: argparse.Namespace) -> int:
    config = _config_from_args(args)
    if args.list_reports:
        items = list_reports(config.output_dir)
        if args.json:
            print(
                json.dumps(
                    {
                        "schema_version": 1,
                        "ok": True,
                        "kind": "report",
                        "reports": items,
                    },
                    ensure_ascii=False,
                )
            )
        else:
            for item in items:
                print(
                    f"{item['run_id']}  {item['kind']:<14} {item['verdict']:<8} "
                    f"{item['model']}  runs={item['runs']}"
                )
        return EXIT_OK

    path = Path(args.report) if args.report else None
    if path is None and args.run_id:
        path = config.output_dir / args.run_id / REPORT_JSON
    if path is None:
        raise ReportError("report path or run id is required")
    report = load_report(path)
    if args.format == "json":
        print(json.dumps(report.model_dump(mode="json"), ensure_ascii=False))
    else:
        markdown = path.with_name("report.md")
        if markdown.is_file():
            print(markdown.read_text(encoding="utf-8"))
        else:
            print(report.model_dump_json(indent=2))
    return EXIT_OK


def _prepare(args: argparse.Namespace, config: LabConfig) -> tuple[Any, Any]:
    normalized = load_normalized_from_config(
        Path(args.input) if args.input else config.input_dir,
        normalizer_config=config.normalizer_config,
    )
    artifact_type = _artifact_type(args)
    count = args.count if getattr(args, "count", None) is not None else 5
    prompt = build_prompt(
        normalized,
        artifact_type,
        count,
        presets=args.preset or (),
        prompt_dir=config.prompt_dir,
        presets_dir=config.presets_dir,
    )
    return normalized, prompt


def _artifact_type(args: argparse.Namespace) -> ArtifactType:
    raw = getattr(args, "artifact_type", None) or "testcases"
    try:
        return ArtifactType(raw)
    except ValueError as exc:
        raise ConfigError(f"unknown artifact type: {raw!r}") from exc


def _report(
    config: LabConfig,
    client: LabOllamaClient,
    context: Any,
    *,
    kind: str,
    normalized: Any,
    options: SeriesOptions,
    prompt: Any = None,
    prompt_info: PromptInfo | None = None,
) -> ExperimentReport:
    report = _empty_report(config, client, kind, context, runs=options.runs)
    input_info = InputInfo(
        path=str(normalized.source),
        source_sha256=normalized.source_sha256,
        text_sha256=normalized.text_sha256,
        encoding=normalized.encoding,
        title=normalized.title,
        url=normalized.url,
        br_ids=list(normalized.br_ids),
        normalizer=normalizer_snapshot(normalized.settings),
    )
    if prompt_info is None and prompt is None:
        raise ReportError("prompt or prompt_info is required")
    if prompt_info is None:
        prompt_info = PromptInfo(
            artifact_type=prompt.artifact_type.value,
            count=prompt.count,
            presets=list(prompt.presets),
            prompt_sha256=prompt.prompt_sha256,
        )
    return report.model_copy(
        update={"input": input_info, "prompt": prompt_info, "params": {"runs": options.runs}}
    )


def _empty_report(
    config: LabConfig,
    client: LabOllamaClient,
    kind: str,
    context: Any,
    *,
    runs: int,
) -> ExperimentReport:
    health: dict[str, Any] = {}
    try:
        health = client.health(config.model)
    except OllamaUnavailable:
        health = {}
    environment = EnvironmentInfo(
        base_url=config.base_url,
        ollama_version=str(health.get("ollama_version", "")),
        model=config.model,
        digest=health.get("digest"),
        temperature=config.temperature,
        seed=config.seed,
        num_ctx=config.num_ctx,
        num_predict=config.num_predict,
        max_retries=config.max_retries,
    )
    return ExperimentReport(
        run_id=context.run_id,
        kind=kind,
        started_at=context.started_at,
        finished_at=iso_utc(),
        environment=environment,
        params={"runs": runs},
        warnings=list(config.warnings),
    )


def _finish(
    args: argparse.Namespace,
    config: LabConfig,
    context: Any,
    report: ExperimentReport,
    code: int,
) -> int:
    report_path, markdown_path = write_report(
        context, report, root=config.root, keep_runs=config.keep_runs
    )
    if args.json:
        print(json.dumps(report_envelope(report, report_path), ensure_ascii=False))
    else:
        print(f"verdict: {report.verdict.status}")
        for reason in report.verdict.reasons:
            print(f"- {reason}")
        print(f"report: {report_path}")
        print(f"markdown: {markdown_path}")
    return code


def _emit_error(
    args: argparse.Namespace,
    code: int,
    message: str,
    hint: str | None = None,
) -> int:
    payload = {
        "schema_version": 1,
        "ok": False,
        "kind": getattr(args, "command", "error") or "error",
        "run_id": None,
        "report_path": None,
        "verdict": "fail",
        "error": ErrorInfo(code=code, message=message, hint=hint).model_dump(mode="json"),
        "report": None,
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"error: {message}", file=sys.stderr)
        if hint:
            print(f"hint: {hint}", file=sys.stderr)
    return code


def _log(args: argparse.Namespace, message: str) -> None:
    if not getattr(args, "quiet", False):
        print(message, file=sys.stderr)


def _stand_hint() -> str:
    return "Start the stand: cd dz2 && .\\scripts\\up-cpu.ps1 (or up-gpu.ps1); check: docker ps"


def _pull_hint(args: argparse.Namespace) -> str:
    model = getattr(args, "model", None) or "llama3"
    return f"docker exec ohw-ollama ollama pull {model}"


def _probe_input(config: LabConfig, raw: str) -> Path:
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else config.root / candidate


if __name__ == "__main__":
    raise SystemExit(main())
