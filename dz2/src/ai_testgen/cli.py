import argparse
import json
import os
import sys
from dataclasses import replace
from functools import partial
from pathlib import Path

from ai_testgen.config import Config, ConfigError, load_config
from ai_testgen.evaluation import (
    EvaluationError,
    artifact_reference,
    evaluate_prompt,
    quality_filename,
    quality_record,
    utc_run_id,
)
from ai_testgen.handlers import ArtifactHandler, get_handler
from ai_testgen.input_doc import (
    InputError,
    RequirementDoc,
    load_docs,
    parse_artifact_type,
)
from ai_testgen.models import ArtifactType
from ai_testgen.ollama_client import (
    OllamaClient,
    OllamaConnectionError,
    OllamaResponseError,
)
from ai_testgen.presets import (
    Preset,
    PresetError,
    load_presets,
    resolve_presets_dir,
    select_extras,
)
from ai_testgen.prompt import PromptError, resolve_prompt_dir
from ai_testgen.service import (
    GeneratedArtifact,
    GenerationError,
    GenerationOptions,
    RetryNotice,
    generate_artifact,
    testcase_index,
)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_OLLAMA = 3
EXIT_GENERATION = 4
EXIT_EVALUATION = 5


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ai-testgen",
        description="Generate test documentation for a website via local ollama LLM",
    )
    parser.add_argument(
        "-n", "--count", type=int, required=True, help="number of test cases per input file"
    )
    parser.add_argument("--config", type=Path, default=None, help="path to TOML config (default: ./ai-testgen.toml)")
    parser.add_argument(
        "--input-dir", type=Path, default=None, help="directory with .md/.txt requirement files"
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None, help="directory for generated markdown reports"
    )
    parser.add_argument(
        "--prompt-dir",
        type=Path,
        default=None,
        help="directory with per-artifact prompt templates (default: ./prompts)",
    )
    type_group = parser.add_mutually_exclusive_group()
    type_group.add_argument(
        "--type",
        dest="artifact_type",
        default=None,
        help="generate this artifact type for every input file (overrides front-matter)",
    )
    type_group.add_argument(
        "--all-types",
        action="store_true",
        help="generate all five artifact types for every input file",
    )
    parser.add_argument(
        "--preset",
        action="append",
        default=[],
        metavar="ID",
        help="prompt preset id from the presets directory; repeat for several presets",
    )
    parser.add_argument(
        "--presets-dir",
        type=Path,
        default=None,
        help="directory with preset TOML files (default: ./presets)",
    )
    parser.add_argument("--url", default=None, help="site URL overriding the one found in files")
    parser.add_argument("--max-retries", type=int, default=None, help="extra attempts per file after a failed validation")
    parser.add_argument("--timeout", type=float, default=None, help="Ollama request timeout in seconds (default: 180)")
    parser.add_argument("--temperature", type=float, default=None, help="LLM sampling temperature")
    parser.add_argument(
        "--testcases-file",
        type=Path,
        default=None,
        help=(
            "text file with already generated test cases (TC-id lines); the "
            "coverage matrix prompt reuses these ids instead of inventing them"
        ),
    )
    parser.add_argument(
        "--evaluate",
        action="store_true",
        help=(
            "do not generate artifacts: ask the LLM to score each built prompt "
            "on the four Dz7 criteria and write output/quality/<type>.json"
        ),
    )
    args = parser.parse_args(argv)
    if args.count < 1:
        parser.error(f"--count must be a positive integer, got {args.count}")
    return args


def _resolve_settings(args: argparse.Namespace) -> Config:
    config = load_config(args.config)

    max_retries = args.max_retries if args.max_retries is not None else config.max_retries
    temperature = args.temperature if args.temperature is not None else config.temperature
    timeout = args.timeout if args.timeout is not None else config.timeout
    if max_retries < 0:
        raise ConfigError(f"--max-retries must not be negative, got {max_retries}")
    if not 0.0 <= temperature <= 2.0:
        raise ConfigError(f"--temperature must be within [0.0, 2.0], got {temperature}")
    if timeout <= 0:
        raise ConfigError(f"--timeout must be positive, got {timeout}")

    resolved = Config(
        base_url=os.environ.get("OLLAMA_BASE_URL") or config.base_url,
        model=os.environ.get("OLLAMA_MODEL") or config.model,
        timeout=timeout,
        input_dir=args.input_dir if args.input_dir is not None else config.input_dir,
        output_dir=args.output_dir if args.output_dir is not None else config.output_dir,
        prompt_dir=args.prompt_dir if args.prompt_dir is not None else config.prompt_dir,
        presets_dir=args.presets_dir if args.presets_dir is not None else config.presets_dir,
        temperature=temperature,
        max_retries=max_retries,
    )
    return resolved


def _load_requested_presets(args: argparse.Namespace, settings: Config) -> dict[str, Preset]:
    if not args.preset:
        return {}
    directory = resolve_presets_dir(settings.presets_dir)
    if directory is None:
        raise PresetError(
            "presets directory not found: pass --presets-dir or set DZ2_PRESETS_DIR"
        )
    return load_presets(directory)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        settings = _resolve_settings(args)
        type_override = (
            parse_artifact_type(args.artifact_type) if args.artifact_type else None
        )
    except (ConfigError, InputError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if not settings.model:
        print("error: model is not set: provide OLLAMA_MODEL or 'model' in [ollama] config section", file=sys.stderr)
        return EXIT_USAGE

    try:
        docs = load_docs(settings.input_dir, args.url)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    try:
        prompt_dir = resolve_prompt_dir(settings.prompt_dir)
        presets = _load_requested_presets(args, settings)
    except (PromptError, PresetError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    testcases_file: str | None = None
    if args.testcases_file is not None:
        try:
            testcases_file = args.testcases_file.read_text(encoding="utf-8")
        except OSError as exc:
            print(
                f"error: cannot read testcases file {args.testcases_file}: {exc}",
                file=sys.stderr,
            )
            return EXIT_USAGE

    client = OllamaClient(base_url=settings.base_url, model=settings.model, timeout=settings.timeout)
    options = GenerationOptions(
        count=args.count,
        temperature=settings.temperature,
        max_retries=settings.max_retries,
    )
    explicit_type = args.all_types or type_override is not None

    for doc in docs:
        # The coverage matrix may only reference test cases that exist: keep the
        # artifact generated earlier in this run and hand its ids to the matrix.
        generated_cases: GeneratedArtifact | None = None
        for artifact_type in _artifact_types(doc, type_override, args.all_types):
            scoped = replace(doc, artifact_type=artifact_type)
            try:
                handler = get_handler(artifact_type, prompt_dir)
                extras = select_extras(presets, args.preset, artifact_type)
                user_prompt = handler.build_user(
                    scoped,
                    options.count,
                    extras,
                    _testcases_for(artifact_type, generated_cases) or testcases_file,
                )
            except (PromptError, PresetError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return EXIT_USAGE

            if args.evaluate:
                try:
                    evaluation = evaluate_prompt(
                        client,
                        user_prompt,
                        artifact_type=artifact_type,
                        presets=args.preset,
                        on_criterion=partial(_print_criterion, doc=doc),
                    )
                except (OllamaConnectionError, OllamaResponseError) as exc:
                    print(f"error: {exc}", file=sys.stderr)
                    return EXIT_OLLAMA
                except EvaluationError as exc:
                    print(f"error: {exc}", file=sys.stderr)
                    return EXIT_EVALUATION
                run_id = utc_run_id()
                payload = quality_record(
                    artifact_type=artifact_type,
                    model=settings.model,
                    prompt=user_prompt,
                    result=evaluation,
                    presets=args.preset,
                    count=options.count,
                    run_id=run_id,
                    generated_artifact=artifact_reference(
                        settings.output_dir
                        / _output_name(doc, artifact_type, explicit_type),
                        settings.output_dir,
                    ),
                )
                output_path = (
                    settings.output_dir
                    / "quality"
                    / quality_filename(artifact_type, args.preset, run_id)
                )
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                print(
                    f"evaluated: {output_path} "
                    f"(average {evaluation.average:.1f})"
                )
                continue

            try:
                generated = generate_artifact(
                    client,
                    handler,
                    scoped,
                    options,
                    user_prompt=user_prompt,
                    on_retry=partial(_print_retry, doc=doc),
                )
            except (OllamaConnectionError, OllamaResponseError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return EXIT_OLLAMA
            except GenerationError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return EXIT_GENERATION

            output_path = settings.output_dir / _output_name(
                doc, artifact_type, explicit_type
            )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                generated.render(doc.url, doc.path.name), encoding="utf-8"
            )
            print(f"written: {output_path} ({_summary(generated, handler)})")
            if artifact_type is ArtifactType.TESTCASES:
                generated_cases = generated

    return EXIT_OK


def _testcases_for(
    artifact_type: ArtifactType,
    generated: GeneratedArtifact | None,
) -> str | None:
    """Test case ids for the matrix, or ``None`` when there is nothing to reuse."""
    if artifact_type is not ArtifactType.COVERAGEMATRIX or generated is None:
        return None
    return testcase_index(generated.result)


def _artifact_types(
    doc: RequirementDoc,
    type_override: ArtifactType | None,
    all_types: bool,
) -> list[ArtifactType]:
    if all_types:
        return list(ArtifactType)
    if type_override is not None:
        return [type_override]
    return [doc.artifact_type]


def _output_name(
    doc: RequirementDoc,
    artifact_type: ArtifactType,
    explicit_type: bool,
) -> str:
    if explicit_type:
        return f"{doc.path.stem}.{artifact_type.value}.md"
    return f"{doc.path.stem}.md"


def _summary(generated: GeneratedArtifact, handler: ArtifactHandler) -> str:
    if generated.result.document is not None:
        return handler.artifact_type.value
    return f"{len(generated.result.cases)} test cases"


def _print_retry(notice: RetryNotice, doc: RequirementDoc) -> None:
    print(
        f"attempt {notice.attempt}/{notice.total_attempts} for '{doc.path.name}' failed validation:",
        file=sys.stderr,
    )
    for issue in notice.issues:
        print(f"  - {issue}", file=sys.stderr)


def _print_criterion(criterion: str, score: int, doc: RequirementDoc) -> None:
    print(f"  {criterion}: {score}/5 ({doc.path.name})")
