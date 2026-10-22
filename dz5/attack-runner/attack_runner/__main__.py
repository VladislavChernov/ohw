from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .runner import RunOptions, execute
from .scenarios import ScenarioError, load_scenarios, select_scenarios


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(3, f"{self.prog}: error: {message}\n")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _default_scenarios_dir() -> Path:
    configured = os.environ.get("SCENARIOS_DIR", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[1] / "scenarios"


def _default_db_scripts_dir() -> Path:
    configured = os.environ.get("DB_SCRIPTS_DIR", "").strip()
    if configured:
        return Path(configured)
    candidate = Path(__file__).resolve().parents[2] / "infra" / "db"
    return candidate if candidate.is_dir() else Path("/db")


def build_parser() -> Parser:
    parser = Parser(prog="python -m attack_runner")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run OWASP LLM scenarios")
    run.add_argument(
        "--target",
        default=os.environ.get("TARGET", "http://localhost:8080"),
        help="agent base URL",
    )
    run.add_argument(
        "--preset",
        type=int,
        default=_env_int("PRESET", 1),
        choices=(1, 2, 3, 4, 5),
        help="protection preset",
    )
    run.add_argument(
        "--runs",
        type=int,
        default=_env_int("RUNS", 3),
        help="repetitions per scenario; n<3 is reported as unknown",
    )
    run.add_argument("--out", type=Path, help="run output directory")
    run.add_argument(
        "--reports-dir",
        type=Path,
        default=Path(os.environ.get("REPORTS_DIR", "reports")),
        help="base directory for generated run ids",
    )
    run.add_argument(
        "--scenario",
        action="append",
        default=[],
        help="scenario id to run; may be repeated",
    )
    run.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("TIMEOUT", "60")),
        help="HTTP timeout in seconds",
    )
    run.add_argument(
        "--restore-db",
        choices=("on", "off"),
        default=os.environ.get("RESTORE_DB", "on"),
        help="restore SQLite before/after destructive scenarios",
    )
    run.add_argument(
        "--augustus",
        choices=("on", "off"),
        default=os.environ.get("AUGUSTUS", "off"),
        help="optional Augustus probe run",
    )
    run.add_argument(
        "--scenarios-dir",
        type=Path,
        default=_default_scenarios_dir(),
        help="directory with scenario YAML files",
    )
    run.add_argument(
        "--db-path",
        type=Path,
        default=Path(os.environ.get("DB_PATH", "db.sqlite")),
        help="SQLite database path",
    )
    run.add_argument(
        "--db-scripts-dir",
        type=Path,
        default=_default_db_scripts_dir(),
        help="directory with init.sql and seed.sql",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command != "run":
        parser.error("a command is required")
    if args.preset not in range(1, 6):
        parser.error("--preset must be between 1 and 5")
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    try:
        scenarios = load_scenarios(args.scenarios_dir)
        selected = select_scenarios(scenarios, args.scenario)
        options = RunOptions(
            target=args.target,
            preset=args.preset,
            runs=args.runs,
            out=args.out,
            reports_dir=args.reports_dir,
            timeout=args.timeout,
            restore_db=args.restore_db == "on",
            augustus=args.augustus == "on",
            scenario_ids=tuple(args.scenario),
            scenarios_dir=args.scenarios_dir,
            db_path=args.db_path,
            db_scripts_dir=args.db_scripts_dir,
        )
        out_dir = execute(options, selected)
    except ScenarioError as exc:
        print(f"scenario error: {exc}", file=sys.stderr)
        return 3
    except ValueError as exc:
        print(f"argument error: {exc}", file=sys.stderr)
        return 3
    except FileExistsError as exc:
        print(f"output error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
