"""CLI entry point for API test generator."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from api_testgen.config import Config
from api_testgen.extractor import extract_code, save_code
from api_testgen.ollama import generate_code
from api_testgen.prompt import load_prompt, resolve_prompt_path
from api_testgen.runner import format_report, run_pytest


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Generate API autotests via local LLM (simple: pytest output)",
    )
    parser.add_argument(
        "--output-dir",
        default="./output",
        help="Directory for generated test files (default: ./output)",
    )
    parser.add_argument(
        "--input-dir",
        default="./input",
        help="Directory with prompt files (default: ./input)",
    )
    parser.add_argument(
        "--prompt-file",
        default=None,
        help="Path to the prompt file (default: <input-dir>/prompt.txt)",
    )
    parser.add_argument(
        "--max-retries", type=int, default=None, help="Max LLM retry attempts (overrides env)"
    )
    parser.add_argument("--no-run", action="store_true", help="Generate code but don't run pytest")
    parser.add_argument(
        "--save-prompt", action="store_true", help="Save prompt to file for debugging"
    )
    args = parser.parse_args()

    config = Config.from_env()
    if args.max_retries:
        config.max_retries = args.max_retries
    config.output_dir = args.output_dir

    prompt_path = resolve_prompt_path(args.prompt_file, Path(args.input_dir))
    print(f"[1/4] Loading prompt from {prompt_path}...")
    prompt = load_prompt(prompt_path)

    if args.save_prompt:
        saved = Path(config.output_dir) / "prompt.txt"
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_text(prompt, encoding="utf-8")
        print(f"  Saved to {saved}")

    print(
        f"[2/4] Sending prompt to Ollama ({config.ollama_model}, max {config.max_retries} attempts)..."
    )
    code = asyncio.run(
        generate_code(
            config.ollama_base_url,
            config.ollama_model,
            prompt,
            config.max_retries,
            config.timeout,
        )
    )

    print("[3/4] Extracting and saving code...")
    clean_code = extract_code(code)
    test_file = save_code(clean_code, Path(config.output_dir))
    print(f"  Saved to {test_file}")

    if args.no_run:
        print("[4/4] Skipping pytest (--no-run)")
        return

    print("[4/4] Running pytest...")
    results = run_pytest(test_file)
    report = format_report(results)
    print(report)

    if results["failed"] > 0 or results["errors"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
