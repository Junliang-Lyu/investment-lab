"""Load settings from investment-lab/.env without extra dependencies.

Values already present in the environment win, so a server can set them
directly and ignore the file.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


def load_env_file(path: str | Path | None = None) -> dict[str, str]:
    path = Path(path) if path else DEFAULT_ENV_FILE
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded
