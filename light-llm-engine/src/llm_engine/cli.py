"""CLI entry point: orchestrate docs -> ollama -> markdown output."""

import argparse
import os
import sys
from pathlib import Path

from llm_engine.config import Config, ConfigError, load_config
from llm_engine.input_doc import InputError, RequestDoc, load_docs
from llm_engine.ollama_client import (
    OllamaClient,
    OllamaConnectionError,
    OllamaResponseError,
)
from llm_engine.renderer import render

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_OLLAMA = 3
EXIT_GENERATION = 4


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="light-engine",
        description="Send request files to a local LLM (ollama) and write replies as markdown",
    )
    parser.add_argument("--config", type=Path, default=None, help="path to TOML config")
    parser.add_argument("--input-dir", type=Path, default=None, help="dir with .md/.txt request files")
    parser.add_argument("--output-dir", type=Path, default=None, help="dir for markdown replies")
    parser.add_argument("--model", default=None, help="ollama model name (overrides config/env)")
    parser.add_argument("--base-url", default=None, help="ollama base URL (overrides config/env)")
    parser.add_argument("--system", default=None, help="system prompt (overrides config)")
    parser.add_argument("--temperature", type=float, default=None, help="sampling temperature [0.0, 2.0]")
    parser.add_argument("--max-retries", type=int, default=None, help="extra attempts per file on empty reply")
    return parser.parse_args(argv)


def _resolve_settings(args: argparse.Namespace) -> Config:
    config = load_config(args.config)
    max_retries = args.max_retries if args.max_retries is not None else config.max_retries
    temperature = args.temperature if args.temperature is not None else config.temperature
    if max_retries < 0:
        raise ConfigError(f"--max-retries must not be negative, got {max_retries}")
    if not 0.0 <= temperature <= 2.0:
        raise ConfigError(f"--temperature must be within [0.0, 2.0], got {temperature}")
    return Config(
        base_url=os.environ.get("OLLAMA_BASE_URL") or args.base_url or config.base_url,
        model=os.environ.get("OLLAMA_MODEL") or args.model or config.model,
        timeout=config.timeout,
        system=args.system if args.system is not None else config.system,
        input_dir=args.input_dir if args.input_dir is not None else config.input_dir,
        output_dir=args.output_dir if args.output_dir is not None else config.output_dir,
        temperature=temperature,
        max_retries=max_retries,
    )


def _generate(client: OllamaClient, doc: RequestDoc, config: Config) -> str | None:
    for attempt in range(config.max_retries + 1):
        reply = client.chat(doc.content, system=config.system, temperature=config.temperature)
        if reply.strip():
            return reply
        print(
            f"attempt {attempt + 1}/{config.max_retries + 1} for '{doc.path.name}': empty reply",
            file=sys.stderr,
        )
    return None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        config = _resolve_settings(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if not config.model:
        print(
            "error: model is not set: provide OLLAMA_MODEL, --model, or 'model' in [ollama] config",
            file=sys.stderr,
        )
        return EXIT_USAGE

    try:
        docs = load_docs(config.input_dir)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    config.output_dir.mkdir(parents=True, exist_ok=True)
    client = OllamaClient(base_url=config.base_url, model=config.model, timeout=config.timeout)

    failures = 0
    for doc in docs:
        try:
            reply = _generate(client, doc, config)
        except OllamaConnectionError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_OLLAMA
        except OllamaResponseError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_OLLAMA
        if reply is None:
            failures += 1
            print(f"error: could not get a non-empty reply for '{doc.path.name}'", file=sys.stderr)
            continue
        out_path = config.output_dir / f"{doc.path.stem}.md"
        out_path.write_text(render(reply, doc.path.name), encoding="utf-8")
        print(f"written: {out_path}")

    if failures:
        return EXIT_GENERATION
    return EXIT_OK