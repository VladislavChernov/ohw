"""CLI entry point for API test generator."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from api_testgen.config import Config
from api_testgen.extractor import extract_code, save_code
from api_testgen.ollama import generate_code
from api_testgen.prompt import build_prompt, load_prompt_template
from api_testgen.runner import format_report, run_pytest
from api_testgen.swagger import fetch_swagger_spec, parse_endpoints


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
        "--target-url", default=None, help="Target API base URL (overrides TARGET_URL env)"
    )
    parser.add_argument(
        "--max-retries", type=int, default=None, help="Max LLM retry attempts (overrides env)"
    )
    parser.add_argument(
        "--input-dir",
        default="./input",
        help="Directory containing the prompt template (input/prompt.txt)",
    )
    parser.add_argument("--no-run", action="store_true", help="Generate code but don't run pytest")
    parser.add_argument(
        "--save-prompt", action="store_true", help="Save prompt to file for debugging"
    )
    args = parser.parse_args()

    config = Config.from_env()
    if args.target_url:
        config.target_url = args.target_url
    if args.max_retries:
        config.max_retries = args.max_retries
    config.output_dir = args.output_dir

    print(f"[1/5] Fetching OpenAPI spec from {config.target_url}...")
    spec = asyncio.run(fetch_swagger_spec(config.target_url))
    endpoints = parse_endpoints(spec)
    print(f"  Found {len(endpoints)} endpoints")

    print("[2/5] Building prompt...")
    template = load_prompt_template(Path(args.input_dir))
    prompt = build_prompt(template, endpoints, config.target_url)

    if args.save_prompt:
        prompt_path = Path(config.output_dir) / "prompt.txt"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")
        print(f"  Saved to {prompt_path}")

    print(
        f"[3/5] Generating tests via {config.ollama_model} (max {config.max_retries} attempts)..."
    )
    code = asyncio.run(
        generate_code(config.ollama_base_url, config.ollama_model, prompt, config.max_retries)
    )

    print("[4/5] Extracting and saving code...")
    clean_code = extract_code(code)
    test_file = save_code(clean_code, Path(config.output_dir))
    print(f"  Saved to {test_file}")

    if args.no_run:
        print("[5/5] Skipping pytest (--no-run)")
        return

    print("[5/5] Running pytest...")
    results = run_pytest(test_file)
    report = format_report(results, len(endpoints))
    print(report)

    if results["failed"] > 0 or results["errors"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
