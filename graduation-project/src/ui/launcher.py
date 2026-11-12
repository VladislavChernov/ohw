"""Console launcher for the workbench: ``python -m src.ui``."""

import os
import sys
from pathlib import Path


def main() -> None:
    from streamlit.web import cli as stcli

    host = os.environ.get("GRADUATION_UI_HOST", "0.0.0.0")
    port = os.environ.get("GRADUATION_UI_PORT", "8501")
    app = Path(__file__).with_name("app.py")
    sys.argv = [
        "streamlit",
        "run",
        str(app),
        "--server.address",
        host,
        "--server.port",
        str(port),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]
    raise SystemExit(stcli.main())


if __name__ == "__main__":
    main()
