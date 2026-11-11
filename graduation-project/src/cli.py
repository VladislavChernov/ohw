"""Console entry point for the graduation project."""

from collections.abc import Sequence

from src.workflow.main import main as run


def main(argv: Sequence[str] | None = None) -> int:
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
