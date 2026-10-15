"""Разложить изменённые файлы по областям селектора.

Git в контейнере нет и быть не должно: source control принадлежит хосту. Список
изменённых файлов приходит аргументами — его вычисляет вызывающая сторона.

Возвращает области, слитые через запятую, либо завершается с кодом 3, если какой-то
файл не попал ни в одну область. Молча пропустить такой файл нельзя: зелёный селектор
при незакрытой правке читается как «всё прошло», а проверялось меньше, чем кажется.
"""

from __future__ import annotations

import argparse
import fnmatch
import sys
from pathlib import Path

import yaml

UNKNOWN_EXIT = 3


def classify(path: str, areas: dict[str, dict[str, object]]) -> list[str]:
    hits = []
    for name, spec in areas.items():
        patterns = spec.get("match") or []
        if any(fnmatch.fnmatch(path, str(pattern)) for pattern in patterns):
            hits.append(name)
    return hits


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selectors", type=Path, required=True)
    parser.add_argument("--prefix", default="", help="префикс, добавляемый к путям (корень git)")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()

    spec = yaml.safe_load(args.selectors.read_text(encoding="utf-8")) or {}
    areas = spec.get("areas") or {}

    if not args.paths:
        print(
            "список изменённых файлов пуст. Git считает хост, контейнер его не видит",
            file=sys.stderr,
        )
        return UNKNOWN_EXIT

    found: list[str] = []
    unknown: list[str] = []
    for raw in args.paths:
        path = raw.strip().strip('"').replace("\\", "/")
        if not path:
            continue
        if args.prefix:
            path = f"{args.prefix.rstrip('/')}/{path}"
        hits = classify(path, areas)
        if not hits:
            unknown.append(path)
            continue
        for hit in hits:
            if hit not in found:
                found.append(hit)

    if unknown:
        print("изменённые файлы вне описанных областей:", file=sys.stderr)
        for path in unknown:
            print(f"  {path}", file=sys.stderr)
        print("добавь область в gate_selectors.yaml или запусти gate.sh full", file=sys.stderr)
        return UNKNOWN_EXIT

    print(",".join(found))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())