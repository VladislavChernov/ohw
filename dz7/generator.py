#!/usr/bin/env python3
"""
Dz7 — LLM Prompt Generator.

Reads prompt templates from ``templates/<type>.txt``, fills them with the
requirements text and produces prompts for Dz2 (the test stand) to run against
a local Ollama model.

Supported artifact types: testcases, checklist, testplan, qareport,
coveragematrix.

The module has no third-party dependencies and is testable with the standard
library only::

    python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import argparse
import math
import re
import shutil
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ARTIFACT_TYPES: tuple[str, ...] = (
    "testcases",
    "checklist",
    "testplan",
    "qareport",
    "coveragematrix",
)

# Backward-compatible aliases used by early Dz7 drafts and by the homework text.
ARTIFACT_ALIASES: dict[str, str] = {
    "testcase": "testcases",
    "testcases": "testcases",
    "checklist": "checklist",
    "testplan": "testplan",
    "qareport": "qareport",
    "qa_report": "qareport",
    "coveragematrix": "coveragematrix",
    "coverage_matrix": "coveragematrix",
}

DEFAULT_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
DEFAULT_PRESETS_DIR = Path(__file__).resolve().parent / "presets"
# Requirements live in the test stand's input directory, not in Dz7.
DEFAULT_DZ2_INPUT_DIR = Path(__file__).resolve().parent.parent / "dz2" / "docs"
DEFAULT_OUTPUT_DIR = Path("output")
CONFIG_FILE_NAME = "config.toml"

_PLACEHOLDER_PATTERN = re.compile(r"\{([a-z][a-z0-9_]*)\}", re.IGNORECASE)
_URL_LINE_PATTERN = re.compile(r"^Target site: \{url\}[ \t]*\n?", re.MULTILINE)
_BR_ID_PATTERN = re.compile(r"\bBR-\d+\b")
_URL_PATTERN = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)

EXIT_OK = 0
EXIT_USAGE = 2


class GeneratorError(Exception):
    """User-facing error: bad arguments, missing template, unreadable input."""


@dataclass(frozen=True)
class Preset:
    """Reusable block of extra instructions injected into ``{extra}``."""

    id: str
    label: str
    text: str
    types: tuple[str, ...]

    def applies_to(self, artifact_type: str) -> bool:
        return artifact_type in self.types


def normalize_artifact_type(value: str) -> str:
    normalized = value.strip().lower()
    try:
        return ARTIFACT_ALIASES[normalized]
    except KeyError:
        supported = ", ".join(ARTIFACT_TYPES)
        raise GeneratorError(
            f"unknown artifact type: {value!r} (supported: {supported})"
        ) from None


def extract_url(content: str) -> str | None:
    """Extract the first URL from the requirements text (same rule as Dz2)."""
    match = _URL_PATTERN.search(content)
    if match is None:
        return None
    return match.group(0).rstrip(".,;:")


def target_negative_count(count: int) -> int:
    """Number of negative test cases for the requested total (same rule as Dz2)."""
    if count < 2:
        return 0
    return max(1, min(count - 1, math.ceil(count * 0.4)))


def strip_template_comments(template: str) -> str:
    """Drop the leading ``#`` comment block and blank lines from a template."""
    lines = template.splitlines()
    index = 0
    while index < len(lines) and (
        not lines[index].strip() or lines[index].lstrip().startswith("#")
    ):
        index += 1
    return "\n".join(lines[index:])


def render_template(template: str, values: dict[str, str]) -> str:
    """Substitute ``{name}`` placeholders without touching literal JSON braces."""

    def replace(match: re.Match[str]) -> str:
        return values.get(match.group(1).lower(), match.group(0))

    return _PLACEHOLDER_PATTERN.sub(replace, template)


def load_template(
    artifact_type: str,
    templates_dir: Path = DEFAULT_TEMPLATES_DIR,
) -> str:
    """Load ``<templates_dir>/<type>.txt`` or raise a user-facing error."""
    artifact_type = normalize_artifact_type(artifact_type)
    path = templates_dir / f"{artifact_type}.txt"
    if not path.is_file():
        raise GeneratorError(
            f"prompt template not found: {path} "
            f"(expected one file per artifact type: {', '.join(ARTIFACT_TYPES)})"
        )
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GeneratorError(f"cannot read prompt template {path}: {exc}") from exc


def load_presets(presets_dir: Path = DEFAULT_PRESETS_DIR) -> dict[str, Preset]:
    """Load every ``*.toml`` preset from ``presets_dir`` keyed by ``id``."""
    if not presets_dir.is_dir():
        raise GeneratorError(f"presets directory does not exist: {presets_dir}")

    presets: dict[str, Preset] = {}
    for path in sorted(presets_dir.glob("*.toml")):
        preset = _load_preset(path)
        if preset.id in presets:
            raise GeneratorError(f"duplicate preset id: {preset.id!r}")
        presets[preset.id] = preset

    if not presets:
        raise GeneratorError(f"no *.toml presets found in: {presets_dir}")
    return presets


def _load_preset(path: Path) -> Preset:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise GeneratorError(f"invalid preset {path}: {exc}") from exc

    values: dict[str, Any] = {}
    for key in ("id", "label", "text"):
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise GeneratorError(f"{path}: '{key}' must be a non-empty string")
        values[key] = value.strip()

    if values["id"] != path.stem:
        raise GeneratorError(
            f"{path}: 'id' must match the file name ({path.stem!r}), got {values['id']!r}"
        )

    raw_types = raw.get("types")
    if raw_types is None:
        preset_types = ARTIFACT_TYPES
    else:
        if not isinstance(raw_types, list) or not raw_types:
            raise GeneratorError(f"{path}: 'types' must be a non-empty array of strings")
        preset_types = tuple(normalize_artifact_type(str(item)) for item in raw_types)

    return Preset(
        id=values["id"],
        label=values["label"],
        text=values["text"],
        types=preset_types,
    )


def select_extras(
    presets: Mapping[str, Preset],
    preset_ids: Sequence[str],
    artifact_type: str,
) -> list[str]:
    """Resolve preset ids into instruction texts for this artifact type."""
    extras: list[str] = []
    for preset_id in preset_ids:
        preset = presets.get(preset_id)
        if preset is None:
            available = ", ".join(sorted(presets)) or "-"
            raise GeneratorError(
                f"unknown preset: {preset_id!r} (available: {available})"
            )
        if not preset.applies_to(artifact_type):
            raise GeneratorError(
                f"preset {preset_id!r} does not apply to {artifact_type} "
                f"(types: {', '.join(preset.types)})"
            )
        extras.append(preset.text)
    return extras


def _extra_block(extras: Sequence[str]) -> str:
    if not extras:
        return ""
    lines = [
        "Additional instructions:",
        "- Apply them only to the requirements below; do not add pages, features,",
        "  products or data the requirements do not mention, and do not change",
        "  the required output format.",
    ]
    lines += [f"- {' '.join(text.split())}" for text in extras]
    return "\n".join(lines)


def build_prompt(
    artifact_type: str,
    content: str,
    url: str | None,
    count: int = 5,
    templates_dir: Path = DEFAULT_TEMPLATES_DIR,
    extras: Sequence[str] = (),
    testcases: str | None = None,
) -> str:
    """Build a ready LLM prompt for a single artifact type.

    ``testcases`` is a plain list of already generated test cases; the coverage
    matrix reuses their ids instead of inventing its own.
    """
    artifact_type = normalize_artifact_type(artifact_type)
    if count < 1:
        raise GeneratorError(f"count must be a positive integer, got {count}")

    resolved_url = url or extract_url(content)
    negative = target_negative_count(count)
    positive = count - negative

    values = {
        "content": content,
        "url": resolved_url or "",
        "count": str(count),
        "count_noun": "test case" if count == 1 else "test cases",
        "positive": str(positive),
        "negative": str(negative),
        "requirement_rule": _requirement_rule(content),
        "extra": _extra_block(extras),
        "testcases": _testcases_block(testcases),
    }

    template = strip_template_comments(load_template(artifact_type, templates_dir))
    if extras and "{extra}" not in template:
        raise GeneratorError(
            f"template {artifact_type}.txt has no {{extra}} placeholder, "
            "so presets cannot be applied"
        )
    if resolved_url is None:
        template = _URL_LINE_PATTERN.sub("", template)

    prompt = render_template(template, values)
    leftover = sorted(set(_PLACEHOLDER_PATTERN.findall(prompt)))
    if leftover:
        raise GeneratorError(
            f"unknown placeholder(s) in template {artifact_type}.txt: "
            + ", ".join(f"{{{name}}}" for name in leftover)
        )

    prompt = re.sub(r"\n{3,}", "\n\n", prompt).strip()
    return prompt + "\n"


def _requirement_rule(content: str) -> str:
    if not _BR_ID_PATTERN.search(content):
        return ""
    return (
        "Every test case MUST set its 'requirement' field to the business "
        "requirement id (BR-N) from the Requirements section that it verifies."
    )


def _testcases_block(testcases: str | None) -> str:
    """State plainly whether a test case list is available.

    The matrix may only reuse ids it was given, so the prompt must make the
    absence of that list explicit instead of leaving an empty section.
    """
    text = (testcases or "").strip()
    if not text:
        return "No test cases have been generated for these requirements yet."
    return "Test cases already generated for these requirements:\n" + text


def read_requirements(args: argparse.Namespace, input_dir: Path) -> str:
    if args.requirements is not None and args.requirements_file is not None:
        raise GeneratorError("use either --requirements or --requirements-file, not both")
    if args.requirements is not None:
        return args.requirements
    if args.requirements_file is not None:
        path = _resolve_requirements_file(args.requirements_file, input_dir)
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            raise GeneratorError(f"cannot read requirements file {path}: {exc}") from exc
    raise GeneratorError(
        "requirements are required: pass --requirements TEXT or "
        f"--requirements-file NAME (looked up in {input_dir})"
    )


def _resolve_requirements_file(value: Path, input_dir: Path) -> Path:
    """Accept either a direct path or a file name inside the Dz2 input dir."""
    if value.is_file():
        return value
    if not value.is_absolute():
        candidate = input_dir / value
        if candidate.is_file():
            return candidate
    raise GeneratorError(
        f"requirements file not found: {value} (input dir: {input_dir})"
    )


def load_config(explicit_path: Path | None) -> dict[str, Any]:
    """Read Dz7 config; a missing default config is not an error."""
    if explicit_path is not None:
        if not explicit_path.is_file():
            raise GeneratorError(f"config file not found: {explicit_path}")
        path: Path | None = explicit_path
    else:
        candidate = Path.cwd() / CONFIG_FILE_NAME
        path = candidate if candidate.is_file() else None

    config: dict[str, Any] = {
        "default_count": 5,
        "default_type": "testcases",
        "templates_dir": DEFAULT_TEMPLATES_DIR,
        "presets_dir": DEFAULT_PRESETS_DIR,
        "input_dir": DEFAULT_DZ2_INPUT_DIR,
        "output_dir": DEFAULT_OUTPUT_DIR,
    }
    if path is None:
        return config

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise GeneratorError(f"invalid config {path}: {exc}") from exc

    generation = raw.get("generation", {})
    paths = raw.get("paths", {})
    if not isinstance(generation, dict) or not isinstance(paths, dict):
        raise GeneratorError(f"{path}: [generation] and [paths] must be tables")

    config["default_count"] = _config_int(generation, "default_count", 5, path)
    default_type = generation.get("default_type", "testcases")
    if not isinstance(default_type, str):
        raise GeneratorError(f"{path}: 'default_type' must be a string")
    config["default_type"] = normalize_artifact_type(default_type)

    templates_dir = paths.get("templates_dir")
    if templates_dir is not None:
        if not isinstance(templates_dir, str):
            raise GeneratorError(f"{path}: 'templates_dir' must be a string")
        config["templates_dir"] = (path.parent / templates_dir).resolve()

    presets_dir = paths.get("presets_dir")
    if presets_dir is not None:
        if not isinstance(presets_dir, str):
            raise GeneratorError(f"{path}: 'presets_dir' must be a string")
        config["presets_dir"] = (path.parent / presets_dir).resolve()

    input_dir = paths.get("input_dir")
    if input_dir is not None:
        if not isinstance(input_dir, str):
            raise GeneratorError(f"{path}: 'input_dir' must be a string")
        config["input_dir"] = (path.parent / input_dir).resolve()

    output_dir = paths.get("output_dir")
    if output_dir is not None:
        if not isinstance(output_dir, str):
            raise GeneratorError(f"{path}: 'output_dir' must be a string")
        config["output_dir"] = Path(output_dir)

    return config


def _config_int(section: dict[str, Any], key: str, default: int, path: Path) -> int:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise GeneratorError(f"{path}: '{key}' must be an integer")
    if value < 1:
        raise GeneratorError(f"{path}: '{key}' must be a positive integer")
    return value


def emit_templates(output_dir: Path, templates_dir: Path = DEFAULT_TEMPLATES_DIR) -> list[Path]:
    """Copy raw templates into ``output_dir`` so Dz2 can use them via --prompt-dir."""
    missing = [name for name in ARTIFACT_TYPES if not (templates_dir / f"{name}.txt").is_file()]
    if missing:
        raise GeneratorError(
            f"templates directory {templates_dir} is missing: {', '.join(missing)}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name in ARTIFACT_TYPES:
        destination = output_dir / f"{name}.txt"
        shutil.copyfile(templates_dir / f"{name}.txt", destination)
        written.append(destination)
    return written


def emit_presets(output_dir: Path, presets_dir: Path = DEFAULT_PRESETS_DIR) -> list[Path]:
    """Copy preset TOML files into ``output_dir`` so Dz2 can use them."""
    presets = load_presets(presets_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for preset_id in sorted(presets):
        source = presets_dir / f"{preset_id}.toml"
        destination = output_dir / f"{preset_id}.toml"
        shutil.copyfile(source, destination)
        written.append(destination)
    return written


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="dz7-generator",
        description="Generate LLM prompts for Dz2 test documentation",
    )
    parser.add_argument("--requirements", default=None, help="requirements text")
    parser.add_argument(
        "--requirements-file",
        type=Path,
        default=None,
        help=(
            "path or file name from the Dz2 input directory "
            f"(default: {DEFAULT_DZ2_INPUT_DIR})"
        ),
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=None,
        help=f"Dz2 input directory with requirements (default: {DEFAULT_DZ2_INPUT_DIR})",
    )
    parser.add_argument("--url", default=None, help="site URL (default: extract from requirements)")
    parser.add_argument(
        "--type",
        default=None,
        help=f"artifact type: {', '.join(ARTIFACT_TYPES)} (default: from config)",
    )
    parser.add_argument("--count", type=int, default=None, help="number of test cases")
    parser.add_argument("--all-types", action="store_true", help="generate prompts for all types")
    parser.add_argument("--output", type=Path, default=None, help="output file for a single prompt")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="directory for --all-types / --emit-templates",
    )
    parser.add_argument(
        "--emit-templates",
        action="store_true",
        help="copy raw templates to --output-dir (for dz2 --prompt-dir)",
    )
    parser.add_argument(
        "--emit-presets",
        action="store_true",
        help="copy preset TOML files to --output-dir (for dz2 --presets-dir)",
    )
    parser.add_argument(
        "--preset",
        action="append",
        default=[],
        metavar="ID",
        help="preset id to append to the prompt; repeat for several presets",
    )
    parser.add_argument(
        "--presets-dir",
        type=Path,
        default=None,
        help=f"directory with preset TOML files (default: {DEFAULT_PRESETS_DIR})",
    )
    parser.add_argument(
        "--list-presets",
        action="store_true",
        help="print available presets and exit",
    )
    parser.add_argument(
        "--templates-dir",
        type=Path,
        default=None,
        help="alternative directory with <type>.txt templates",
    )
    parser.add_argument("--config", type=Path, default=None, help="path to TOML config")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        config = load_config(args.config)
        templates_dir: Path = args.templates_dir or config["templates_dir"]
        presets_dir: Path = args.presets_dir or config["presets_dir"]
        input_dir: Path = args.input_dir or config["input_dir"]
        output_dir: Path = args.output_dir or config["output_dir"]

        if args.emit_templates:
            written = emit_templates(output_dir, templates_dir)
            for path in written:
                print(f"template saved to: {path}")
            return EXIT_OK

        if args.emit_presets:
            written = emit_presets(output_dir, presets_dir)
            for path in written:
                print(f"preset saved to: {path}")
            return EXIT_OK

        if args.list_presets:
            for preset in load_presets(presets_dir).values():
                print(f"{preset.id}: {preset.label} [{', '.join(preset.types)}]")
            return EXIT_OK

        content = read_requirements(args, input_dir)
        count = args.count if args.count is not None else int(config["default_count"])
        presets = load_presets(presets_dir) if args.preset else {}

        if args.all_types:
            artifact_types = list(ARTIFACT_TYPES)
        else:
            artifact_types = [
                normalize_artifact_type(args.type or str(config["default_type"]))
            ]

        for artifact_type in artifact_types:
            extras = select_extras(presets, args.preset, artifact_type)
            prompt = build_prompt(
                artifact_type, content, args.url, count, templates_dir, extras
            )
            if len(artifact_types) == 1 and args.output is None:
                print(prompt, end="")
                continue

            output_path = args.output if args.output is not None else output_dir / f"{artifact_type}.txt"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(prompt, encoding="utf-8")
            print(f"prompt saved to: {output_path}")

    except GeneratorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
