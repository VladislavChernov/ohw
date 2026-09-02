"""Advanced CLI pipeline: docs → prompt → LLM JSON-plan → core → report.

Bundles the steps into a single deterministic pipeline, mirroring simple but
producing and executing a JSON test-plan (no pytest, no LLM code).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ohw_kit.io import InputError, load_input
from ohw_kit.ollama_client import OllamaClient

import json_testgen_advanced.openapi_reader  # noqa: F401  (register readers)
from json_testgen_advanced.config import AdvancedConfig
from json_testgen_advanced.core import execute_plan
from json_testgen_advanced.docs import DocBundle, build_bundle, build_prompt
from json_testgen_advanced.generator import generate_plan
from json_testgen_advanced.plan import TestPlan, schema_version
from json_testgen_advanced.prompt import load_template
from json_testgen_advanced.report import build_report

_CONTRACT_EXTS = (".json", ".yaml", ".yml")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate API tests as a JSON plan and execute it (advanced: no pytest)"
    )
    parser.add_argument("--service", default=None, help="Base URL of the target API")
    parser.add_argument("--input-dir", default=None, help="dir with prompt template (default: ./input)")
    parser.add_argument("--contracts-dir", default=None, help="dir with contracts base/supplements")
    parser.add_argument("--output-dir", default=None, help="dir for the report (default: ./output)")
    parser.add_argument("--prompt-file", default=None, help="explicit path to the prompt template")
    parser.add_argument("--template-file", default=None, help="alias of --prompt-file")
    parser.add_argument("--max-retries", type=int, default=None, help="LLM retry budget")
    parser.add_argument("--temperature", type=float, default=None, help="sampling temperature")
    parser.add_argument("--required-resources", default=None, help="comma-separated required resources")
    parser.add_argument("--no-run", action="store_true", help="generate plan but do not execute it")
    parser.add_argument("--save-prompt", action="store_true", help="save the filled prompt to output/")
    args = parser.parse_args(argv)

    cfg = AdvancedConfig.from_env()
    if args.service:
        cfg.service = args.service
    if args.input_dir:
        cfg.input_dir = args.input_dir
    if args.contracts_dir:
        cfg.contracts_dir = args.contracts_dir
    if args.output_dir:
        cfg.output_dir = args.output_dir
    if args.max_retries:
        cfg.max_retries = args.max_retries
    if args.temperature is not None:
        cfg.temperature = args.temperature
    if args.required_resources is not None:
        cfg.required_resources = [r.strip() for r in args.required_resources.split(",") if r.strip()]

    output_dir = Path(cfg.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    template_file = args.prompt_file or args.template_file
    if template_file is None:
        template_file = str(Path(cfg.input_dir) / "prompt.txt")
    print(f"[1/4] Читаю шаблон промпта из {template_file}...")
    template = load_template(template_file)

    print(f"[2/4] Собираю документацию из {cfg.contracts_dir}...")
    bundle = load_documentation(cfg)
    for warning in bundle.warnings:
        print(f"  ! {warning}")
    prompt = build_prompt(
        template,
        context=bundle.context,
        base_content=bundle.base.content if bundle.base else "",
        supplements_content="\n\n".join(s.content for s in bundle.supplements),
        service=cfg.service,
        schema_version=schema_version(),
    )
    if args.save_prompt:
        (output_dir / "prompt.txt").write_text(prompt, encoding="utf-8")
        print(f"  Сохранён промпт в {output_dir / 'prompt.txt'}")

    print(f"[3/4] Запрашиваю JSON-план у Ollama ({cfg.ollama_model}, retries={cfg.max_retries})...")
    client = OllamaClient(
        base_url=cfg.ollama_base_url,
        model=cfg.ollama_model,
        timeout=cfg.timeout,
        json_mode=cfg.json_mode,
    )
    plan = generate_plan(
        client,
        prompt,
        max_retries=cfg.max_retries,
        required_resources=cfg.required_resources,
    )

    # Persist the generated plan as a deliverable.
    (output_dir / "plan.json").write_text(_plan_to_json(plan), encoding="utf-8")

    if args.no_run:
        print("[4/4] Пропускаю исполнение (--no-run)")
        return 0

    print("[4/4] Исполняю JSON-план (ядро HTTP)...")
    execution = execute_plan(plan, base_url=cfg.service)
    report = build_report(
        execution,
        model=cfg.ollama_model,
        schema_version=schema_version(),
    )
    report_path = output_dir / "report.md"
    report_path.write_text(report, encoding="utf-8")
    print(report)
    return 0 if execution.failed == 0 else 1


def load_documentation(cfg: AdvancedConfig) -> DocBundle:
    """Load contracts base/supplements + optional page into a DocBundle."""
    contracts = Path(cfg.contracts_dir)
    base_text: str | None = None
    supplements: list[str] = []
    api_page: str = ""

    try:
        files = load_input(
            contracts / "base", extensions=_CONTRACT_EXTS, recursively=True
        )
        if files:
            base_text = files[0].content
    except InputError:
        base_text = None

    try:
        for f in load_input(contracts / "supplements", extensions=_CONTRACT_EXTS, recursively=True):
            supplements.append(f.content)
    except InputError:
        supplements = []

    page_path = contracts / "page" / "api.md"
    if page_path.exists():
        api_page = page_path.read_text(encoding="utf-8")
    elif contracts.joinpath("api.md").exists():
        api_page = contracts.joinpath("api.md").read_text(encoding="utf-8")

    return build_bundle(api_page=api_page, base_text=base_text, supplement_texts=supplements)


def _plan_to_json(plan: TestPlan) -> str:
    import json

    from json_testgen_advanced.plan import StepSpec

    def step(s: StepSpec) -> dict[str, object]:
        req: dict[str, object] = {
            "method": s.request.method,
            "path": s.request.path,
            "headers": s.request.headers or None,
            "body": s.request.body,
        }
        d: dict[str, object] = {"name": s.name, "request": req, "extract": s.extract or None}
        ex: dict[str, object] = {}
        if s.expect.status_code is not None:
            ex["status_code"] = s.expect.status_code
        if s.expect.checks:
            ex["checks"] = s.expect.checks
        if ex:
            d["expect"] = ex
        if s.on_fail != "abort":
            d["on_fail"] = s.on_fail
        return d

    tests: list[dict[str, object]] = []
    for t in plan.tests:
        d: dict[str, object] = {"name": t.name, "steps": [step(s) for s in t.steps]}
        if t.description:
            d["description"] = t.description
        if t.vars:
            d["vars"] = t.vars
        if t.cleanup:
            d["cleanup"] = [step(s) for s in t.cleanup]
        if t.provisional:
            d["provisional"] = True
        tests.append(d)
    return json.dumps({"service": plan.service, "tests": tests}, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    sys.exit(main())
