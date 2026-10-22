from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .aggregate import compute_complete, summarize_results
from .manifest import ReportError, load_manifest, load_results, load_summary
from .render import (
    render_red_team_md,
    render_run_md,
    write_text_atomic,
)


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(3, f"{self.prog}: error: {message}\n")


def build_parser() -> Parser:
    parser = Parser(prog="python -m report_builder")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="build summary and latest reports")
    build.add_argument("--run", type=Path, required=True, help="run directory")
    return parser


def _scan_runs(reports_dir: Path) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for manifest_path in sorted(reports_dir.glob("*/manifest.json")):
        run_dir = manifest_path.parent
        try:
            manifest = load_manifest(run_dir)
            summaries = load_summary(run_dir)
            if summaries is None:
                summaries = summarize_results(load_results(run_dir))
        except ReportError as exc:
            print(f"warning: skipping {run_dir}: {exc}", file=sys.stderr)
            continue
        runs.append({"manifest": manifest, "summaries": summaries})
    return runs


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "build":
        parser.error("a command is required")

    run_dir = args.run.resolve()
    reports_dir = run_dir.parent
    try:
        manifest = load_manifest(run_dir)
        results = load_results(run_dir)
        summaries = summarize_results(results)
        complete = bool(manifest.get("complete")) and compute_complete(
            manifest, results
        )
        manifest["complete"] = complete
        write_text_atomic(
            run_dir / "summary.json",
            json.dumps(summaries, ensure_ascii=False, indent=2) + "\n",
        )
        write_text_atomic(
            run_dir / "summary.md",
            render_run_md(manifest, summaries, title="Run Summary", complete=complete),
        )
        if complete:
            write_text_atomic(
                reports_dir / "latest.md",
                render_run_md(
                    manifest,
                    summaries,
                    title="Latest Complete Run",
                    complete=True,
                ),
            )
        else:
            print(
                "warning: incomplete run; latest.md was not updated",
                file=sys.stderr,
            )
        runs = _scan_runs(reports_dir)
        write_text_atomic(reports_dir / "red-team.md", render_red_team_md(runs))
    except ReportError as exc:
        print(f"report error: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"filesystem error: {exc}", file=sys.stderr)
        return 2

    print(run_dir / "summary.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
