#!/usr/bin/env python3
"""Export section-level fragments for the clarity annotation corpus."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.quality.clarity_kit import export_snapshot, write_jsonl  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="export-clarity-sample",
        description=(
            "Выгрузить фрагменты сгенерированных артефактов для ручной "
            "разметки измеримости целей."
        ),
    )
    parser.add_argument(
        "--input",
        action="append",
        required=True,
        metavar="PATH",
        help="generation.json или папка с ним; можно указать несколько раз",
    )
    parser.add_argument(
        "--output",
        required=True,
        metavar="JSONL",
        help="куда записать выборку",
    )
    parser.add_argument(
        "--min-words",
        type=int,
        default=8,
        help="минимум слов во фрагменте (по умолчанию: 8)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.min_words < 0:
        parser.error("--min-words должен быть >= 0")

    paths: list[Path] = []
    for value in args.input:
        path = Path(value)
        if path.is_dir():
            path = path / "generation.json"
        if not path.is_file():
            parser.error(f"файл не найден: {path}")
        paths.append(path)

    records: list[dict] = []
    for path in paths:
        records.extend(export_snapshot(path, min_words=args.min_words))
    if not records:
        print("фрагменты не найдены: проверьте --input и --min-words", flush=True)
        return 2

    output = Path(args.output)
    write_jsonl(output, records)
    print(
        f"экспортировано фрагментов: {len(records)} -> {output}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
