"""Small file logger for the windowed exe (a console=False build has no stdout).

Writes a line-per-event log into the user data dir (`<data>\\shell.log`) so the
app is diagnosable when launched by double-click. Also mirrors to a real
console when one exists (dev mode), where prints are useful.

Must never raise: `log` is on the crash-reporting path.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def log_dir() -> Path:
    """Directory holding shell.log (always under the writable user data dir)."""
    override = os.environ.get("DSH_DESKTOP_DATA_DIR")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "DeepSeekHarness"


def log(message: str) -> None:
    """Append ONE timestamped line to shell.log; mirror to console if any."""
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
    try:
        if sys.stderr is not None:
            print(line, file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001 — logging must never crash the app
        pass
    try:
        target = log_dir()
        target.mkdir(parents=True, exist_ok=True)
        with open(target / "shell.log", "a", encoding="utf-8", errors="replace") as fh:
            fh.write(line + "\n")
    except Exception:  # noqa: BLE001
        pass