#!/usr/bin/env python3
"""Summarise human clarity annotations without changing the metric."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.quality.clarity_kit import (  # noqa: E402
    analyse,
    annotations_from_sample,
    collect_annotations,
    join_annotations,
    read_jsonl,
    render_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="analyze-clarity",
        description=(
            "Считать согласие разметчиков и разведочные связи категорий "
            "clarity с человеческой оценкой."
        ),
    )
    parser.add_argument(
        "--sample",
        required=True,
        metavar="JSONL",
        help="выборка из export_clarity_sample.py",
    )
    parser.add_argument(
        "--annotator-a",
        metavar="JSONL",
        help="разметка первого человека; без неё берётся annotation из выборки",
    )
    parser.add_argument(
        "--annotator-b",
        metavar="JSONL",
        help="разметка второго человека (для Cohen's kappa)",
    )
    parser.add_argument(
        "--output",
        metavar="MD",
        help="записать отчёт в файл вместо stdout",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        records = read_jsonl(Path(args.sample))
        if args.annotator_a:
            first = collect_annotations(Path(args.annotator_a))
            name_a = next(iter(first.values())).annotator if first else "—"
        else:
            first = annotations_from_sample(records)
            name_a = next(iter(first.values())).annotator if first else "—"
        second = (
            collect_annotations(Path(args.annotator_b))
            if args.annotator_b
            else None
        )
        if not first:
            raise ValueError("нет аннотаций: заполните annotation в выборке")
        name_b = (
            next(iter(second.values())).annotator if second else None
        )
        joined = join_annotations(records, first, second)
        analysis = analyse(joined, annotator_a=name_a, annotator_b=name_b)
        report = render_report(analysis)
    except (OSError, ValueError) as exc:
        print(f"ошибка анализа: {exc}", file=sys.stderr, flush=True)
        return 2

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
        print(f"отчёт записан: {output}", flush=True)
    else:
        print(report, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
