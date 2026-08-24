import argparse
import os
import sys
from pathlib import Path

from ai_testgen.config import Config, ConfigError, load_config
from ai_testgen.handlers import ArtifactHandler, get_handler
from ai_testgen.input_doc import InputError, RequirementDoc, load_docs
from ai_testgen.models import GenerationResult
from ai_testgen.ollama_client import (
    OllamaClient,
    OllamaConnectionError,
    OllamaResponseError,
)
from ai_testgen.renderer import render, render_document

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_OLLAMA = 3
EXIT_GENERATION = 4


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ai-testgen",
        description="Generate positive and negative test cases for a website via local ollama LLM",
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
    parser.add_argument("--url", default=None, help="site URL overriding the one found in files")
    parser.add_argument("--max-retries", type=int, default=None, help="extra attempts per file after a failed validation")
    parser.add_argument("--temperature", type=float, default=None, help="LLM sampling temperature")
    args = parser.parse_args(argv)
    if args.count < 1:
        parser.error(f"--count must be a positive integer, got {args.count}")
    return args


def _resolve_settings(args: argparse.Namespace) -> Config:
    config = load_config(args.config)

    max_retries = args.max_retries if args.max_retries is not None else config.max_retries
    temperature = args.temperature if args.temperature is not None else config.temperature
    if max_retries < 0:
        raise ConfigError(f"--max-retries must not be negative, got {max_retries}")
    if not 0.0 <= temperature <= 2.0:
        raise ConfigError(f"--temperature must be within [0.0, 2.0], got {temperature}")

    resolved = Config(
        base_url=os.environ.get("OLLAMA_BASE_URL") or config.base_url,
        model=os.environ.get("OLLAMA_MODEL") or config.model,
        timeout=config.timeout,
        input_dir=args.input_dir if args.input_dir is not None else config.input_dir,
        output_dir=args.output_dir if args.output_dir is not None else config.output_dir,
        temperature=temperature,
        max_retries=max_retries,
    )
    return resolved


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        settings = _resolve_settings(args)
    except ConfigError as exc:
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

    client = OllamaClient(base_url=settings.base_url, model=settings.model, timeout=settings.timeout)

    for doc in docs:
        handler = get_handler(doc.artifact_type)
        result = _generate_for_doc(client, handler, doc, args.count, settings.temperature, settings.max_retries)
        if isinstance(result, int):
            return result
        output_dir = settings.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{doc.path.stem}.md"
        summary = _write_report(output_path, doc, handler, result)
        print(f"written: {output_path} ({summary})")

    return EXIT_OK


def _write_report(output_path: Path, doc: RequirementDoc, handler: ArtifactHandler, result: GenerationResult) -> str:
    if result.document is not None:
        output_path.write_text(
            render_document(handler.artifact_type, doc.url, doc.path.name, result.document),
            encoding="utf-8",
        )
        return handler.artifact_type.value
    output_path.write_text(render(result.cases, doc.url, doc.path.name), encoding="utf-8")
    return f"{len(result.cases)} test cases"


def _generate_for_doc(
    client: OllamaClient,
    handler: ArtifactHandler,
    doc: RequirementDoc,
    count: int,
    temperature: float,
    max_retries: int,
) -> GenerationResult | int:
    system = handler.build_system()
    user_prompt = handler.build_user(doc, count)
    use_feedback = False
    issues: list[str] = []

    for attempt in range(max_retries + 1):
        prompt = handler.build_retry(issues) if use_feedback else user_prompt
        try:
            raw = client.chat(system, prompt, temperature, json_mode=handler.wants_json)
        except OllamaConnectionError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_OLLAMA
        except OllamaResponseError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_OLLAMA
        result = handler.validate_response(raw, count)
        if result.ok:
            return result
        issues = result.issues
        use_feedback = True
        print(
            f"attempt {attempt + 1}/{max_retries + 1} for '{doc.path.name}' failed validation:",
            file=sys.stderr,
        )
        for issue in issues:
            print(f"  - {issue}", file=sys.stderr)

    print(
        f"error: could not produce valid {handler.artifact_type.value} for '{doc.path.name}'",
        file=sys.stderr,
    )
    return EXIT_GENERATION
