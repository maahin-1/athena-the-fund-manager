from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


def read_setting(name: str, env_file: Path | str = DEFAULT_ENV_FILE, environ: Mapping[str, str] | None = None) -> str:
    """A setting from the process environment, else from the env file; "" if neither has a value.

    The value is returned to the caller and never logged or printed here."""
    value = (os.environ if environ is None else environ).get(name, "")
    if value:
        return value
    path = Path(env_file)
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        if key.strip() == name:
            return raw.strip().strip('"').strip("'")
    return ""
