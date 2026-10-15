"""Развернуть список областей селектора в пути тестов.

Путь, которого нет, — ошибка, а не пустой результат: селектор обязан либо запустить
тесты, либо упасть. Пустой список при непустых областях означает опечатку в YAML.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selectors", type=Path, required=True)
    parser.add_argument("--areas", required=True, help="области через запятую")
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args()

    spec = yaml.safe_load(args.selectors.read_text(encoding="utf-8")) or {}
    areas = spec.get("areas") or {}
    wanted = [name.strip() for name in args.areas.split(",") if name.strip()]

    root = args.root or args.selectors.resolve().parent.parent
    targets: list[str] = []
    missing: list[str] = []

    for name in wanted:
        area = areas.get(name)
        if area is None:
            print(f"область не описана в селекторе: {name}", file=sys.stderr)
            return 2
        for rel in area.get("tests") or []:
            rel = str(rel)
            if rel not in targets:
                targets.append(rel)
            if not (root / rel).is_file():
                missing.append(rel)

    if missing:
        print("в селекторе есть пути, которых нет на диске:", file=sys.stderr)
        for rel in missing:
            print(f"  {rel}", file=sys.stderr)
        return 2
    if not targets:
        print(f"области {wanted} не содержат ни одного теста", file=sys.stderr)
        return 2

    print(" ".join(targets))
    return 0


if __name__ == "__main__":
    sys.exit(main())