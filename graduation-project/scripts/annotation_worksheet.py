#!/usr/bin/env python3
"""Render and read a human-readable clarity annotation worksheet."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.quality.clarity_kit import (  # noqa: E402
    parse_worksheet,
    read_jsonl,
    render_worksheet,
    write_jsonl,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="annotation-worksheet",
        description=(
            "Подготовить читаемый лист разметки или собрать из него JSONL."
        ),
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    prepare = subparsers.add_parser(
        "prepare", help="сделать Markdown-лист из выборки фрагментов"
    )
    prepare.add_argument("--sample", required=True, metavar="JSONL")
    prepare.add_argument("--annotator", required=True)
    prepare.add_argument("--output", required=True, metavar="MD")

    parse = subparsers.add_parser(
        "parse", help="собрать JSONL из заполненного Markdown-листа"
    )
    parse.add_argument("--worksheet", required=True, metavar="MD")
    parse.add_argument("--annotator", required=True)
    parse.add_argument("--output", required=True, metavar="JSONL")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.mode == "prepare":
            records = read_jsonl(Path(args.sample))
            text = render_worksheet(records, args.annotator)
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding="utf-8")
            print(f"worksheet prepared: {len(records)} -> {output}", flush=True)
        else:
            text = Path(args.worksheet).read_text(encoding="utf-8")
            records = parse_worksheet(text, args.annotator)
            write_jsonl(Path(args.output), records)
            print(f"annotations collected: {len(records)} -> {args.output}", flush=True)
    except (OSError, ValueError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr, flush=True)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
