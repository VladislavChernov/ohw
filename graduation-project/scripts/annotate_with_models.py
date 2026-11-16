#!/usr/bin/env python3
"""Prepare model-annotator prompts and validate their responses.

The models themselves are run by the harness as subagents. This script only
builds per-fragment prompts and checks that the returned JSONL matches the
annotation contract from ``docs/clarity-annotation.md``.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.quality.clarity_kit import (  # noqa: E402
    parse_annotation,
    read_jsonl,
    write_jsonl,
)

SIGNAL_NAMES = (
    "modality",
    "expectation",
    "verification",
    "prohibition",
    "result",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="annotate-with-models",
        description=(
            "Подготовить промпты для моделей-разметчиков или проверить их ответы."
        ),
    )
    parser.add_argument(
        "mode",
        choices=("prepare", "collect"),
        help="prepare — сделать промпты, collect — проверить ответы",
    )
    parser.add_argument(
        "--sample",
        required=True,
        metavar="JSONL",
        help="выборка из export_clarity_sample.py",
    )
    parser.add_argument(
        "--guide",
        default="docs/clarity-annotation.md",
        help="путь к гайду разметчика",
    )
    parser.add_argument(
        "--annotator",
        required=True,
        help="имя разметчика: alice или bob",
    )
    parser.add_argument(
        "--responses",
        metavar="JSONL",
        help="файл с ответами модели для режима collect",
    )
    parser.add_argument(
        "--output",
        required=True,
        metavar="JSONL",
        help="куда записать промпты или проверенные аннотации",
    )
    return parser


def _instruction(annotator: str, guide: str, identifier: str) -> str:
    return (
        f"You are annotator {annotator!r}. Read the annotation guide at "
        f"{guide!r} before judging any fragment.\n"
        "Judge only the fragment text below. Ignore any files or fields named "
        "'signals' or 'clarity_score': they belong to the metric, not to you.\n"
        f"Allowed signal names: {', '.join(SIGNAL_NAMES)}.\n"
        "Return one JSON object per fragment with exactly two keys: 'id' and "
        "'annotation'. The annotation object must contain 'annotator' "
        f"({annotator!r}), 'measurable' (true/false), 'signals' (a subset of the "
        "allowed names) and 'rating' (integer 1..5).\n"
        f"Fragment id: {identifier}\n"
    )


def prepare(sample: Path, guide: str, annotator: str, output: Path) -> int:
    records = read_jsonl(sample)
    prompts: list[dict[str, Any]] = []
    for record in records:
        identifier = record["id"]
        parts = [
            _instruction(annotator, guide, identifier),
            f"Scenario: {record.get('scenario', '')}\n",
            f"Run index: {record.get('run_index')}\n",
            "--- FRAGMENT TEXT ---\n",
            f"{record['text']}\n",
            "--- END ---\n",
        ]
        prompts.append(
            {
                "id": identifier,
                "prompt": "".join(parts),
            }
        )
    write_jsonl(output, prompts)
    print(f"prompts prepared: {len(prompts)} -> {output}", flush=True)
    return 0


def collect(
    responses: Path, sample: Path, annotator: str, output: Path
) -> int:
    expected = [record["id"] for record in read_jsonl(sample)]
    if len(set(expected)) != len(expected):
        raise ValueError(f"{sample.name}: дубликаты id в выборке")
    by_id: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(read_jsonl(responses), start=1):
        identifier = raw.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError(f"{responses.name}:{index}: нет поля id")
        if identifier not in expected:
            raise ValueError(
                f"{responses.name}:{index}: неизвестный id {identifier!r}"
            )
        if identifier in by_id:
            raise ValueError(f"{responses.name}:{index}: дубликат id {identifier!r}")
        annotation = parse_annotation(
            raw.get("annotation"), source=f"{responses.name}:{index}"
        )
        if annotation.annotator != annotator:
            raise ValueError(
                f"{responses.name}:{index}: annotator={annotation.annotator!r}, "
                f"ожидалось {annotator!r}"
            )
        by_id[identifier] = annotation_from_model(annotation)

    missing = [identifier for identifier in expected if identifier not in by_id]
    if missing:
        raise ValueError(
            f"{responses.name}: нет ответов для {len(missing)} фрагментов: "
            + ", ".join(missing[:5])
        )
    checked = [
        {"id": identifier, "annotation": by_id[identifier]}
        for identifier in expected
    ]
    write_jsonl(output, checked)
    print(f"annotations validated: {len(checked)} -> {output}", flush=True)
    return 0


def annotation_from_model(annotation: Any) -> dict[str, Any]:
    """Convert a validated Annotation back to a JSON-serialisable object."""
    return {
        "annotator": annotation.annotator,
        "measurable": annotation.measurable,
        "signals": list(annotation.signals),
        "rating": annotation.rating,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    sample = Path(args.sample)
    output = Path(args.output)

    if not sample.is_file():
        parser.error(f"файл выборки не найден: {sample}")
    try:
        if args.mode == "prepare":
            return prepare(sample, args.guide, args.annotator, output)
        if not args.responses:
            parser.error("для collect нужен --responses")
        return collect(Path(args.responses), sample, args.annotator, output)
    except (OSError, ValueError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
