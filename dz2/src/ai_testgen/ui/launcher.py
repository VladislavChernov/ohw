"""Console entry point for the workbench: ``ai-testgen-ui``."""

import sys
from pathlib import Path


def main() -> None:
    from streamlit.web import cli as stcli

    app = Path(__file__).with_name("app.py")
    sys.argv = [
        "streamlit",
        "run",
        str(app),
        "--server.address",
        "0.0.0.0",
        "--server.port",
        "8501",
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]
    raise SystemExit(stcli.main())


if __name__ == "__main__":
    main()
