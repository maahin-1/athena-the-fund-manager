"""`python -m athena.dashboard` opens the dashboard in the browser; extra arguments go to Streamlit,
for example `--server.port 8600` or `--server.headless true`."""
from __future__ import annotations

import sys
from pathlib import Path

APP = Path(__file__).with_name("app.py")


def main(extra: list[str] | None = None) -> int:
    from streamlit.web import cli

    sys.argv = ["streamlit", "run", str(APP), *(sys.argv[1:] if extra is None else extra)]
    return cli.main()


if __name__ == "__main__":
    raise SystemExit(main())
