"""Path and environment resolution for the desktop shell.

Two layouts:
  * Dev (source-run): resources live in the workspace build tree
    (`<repo>/apps/cli` + `apps/web/dist`) and the CLI is launched with
    whatever node is on PATH unless overridden.
  * Frozen (PyInstaller onedir): `node.exe`, a self-contained `cli/` closure
    and the app icon live AT THE BUNDLE ROOT, i.e. next to the EXE
    (`<app>/node.exe`, `<app>/cli`, `<app>/icon.ico`). They are NOT kept under
    `_internal/` because Inno Setup / ISCC are capped at MAX_PATH (~260) and a
    `_internal/resources/cli/node_modules/...` prefix would push the deepest
    node_modules paths past 260 at real install locations (per-user default dir
    is ~54 chars). Sibling-of-exe keeps every installed path comfortably short.

Every piece is overridable through an env var (kept from the old PySide port so
existing workflows carry over). Env overrides win over both layouts:
  DSH_DESKTOP_NODE      node executable
  DSH_DESKTOP_CLI_DIR   directory containing the dsh CLI (lib/bin.js)
  DSH_DESKTOP_DATA_DIR  writable user data directory
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "DeepSeek Harness"

# python/desktop/dsh_shell/config.py -> python/desktop
_DEV_BASE = Path(__file__).resolve().parents[1]
# python/desktop/dsh_shell/config.py -> repo root
_REPO_ROOT = Path(__file__).resolve().parents[3]

_APP_ID = "dsh-desktop-8f3c0b2a-1e2d-4b49-8a6f-2e5c3a9b0f11"


def _frozen_base() -> Path:
    """Bundle root under PyInstaller onedir = `<app>`, sibling of the EXE."""
    return Path(sys.executable).parent


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def user_data_dir() -> Path:
    """Writable, user-owned directory for the backend cwd and WebView2 cache."""
    override = os.environ.get("DSH_DESKTOP_DATA_DIR")
    if override:
        return Path(override)
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(local) / "DeepSeekHarness"


def webview2_data_dir() -> Path:
    """Persistent WebView2 user-data folder: keeps the HTTP/GPU shader cache
    across launches (the biggest repeat-startup win for an embedded SPA)."""
    return user_data_dir() / "webview2"


def node_executable() -> str:
    """Resolve the node binary that launches the CLI backend."""
    override = os.environ.get("DSH_DESKTOP_NODE")
    if override:
        return override
    if is_frozen():
        bundled = _frozen_base() / "node.exe"
        if bundled.is_file():
            return str(bundled)
        raise FileNotFoundError(f"bundled node not found at {bundled}")
    found = shutil.which("node")
    if found is None:
        raise FileNotFoundError("node is not on PATH; set DSH_DESKTOP_NODE to a node executable")
    return found


def cli_bin_js() -> Path:
    """The dsh CLI entry (lib/bin.js).

    Dev default is `<repo>/apps/cli` (the workspace build). Override with
    DSH_DESKTOP_CLI_DIR, or point it at a closure directory whose
    node_modules/@deepseek-ai/dsh/lib/bin.js exists (the packaged layout).
    """
    override = os.environ.get("DSH_DESKTOP_CLI_DIR")
    if not override and is_frozen():
        closure = _frozen_base() / "cli"
        if (closure / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js").is_file():
            override = str(closure)
    base = Path(override) if override else _REPO_ROOT / "apps" / "cli"
    entry = base / "lib" / "bin.js"
    if not entry.is_file():
        entry = base / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
    if not entry.is_file():
        raise FileNotFoundError(
            f"CLI is not built at {base}/lib/bin.js (or {base}/node_modules/@deepseek-ai/dsh/lib/bin.js); "
            "run `pnpm build` (or set DSH_DESKTOP_CLI_DIR)"
        )
    return entry


def app_icon() -> Path:
    """Application icon for the window title bar / taskbar.

    Frozen: the bundled `icon.ico` at the bundle root. Dev: `assets/icon.png`.
    """
    if is_frozen():
        ico = _frozen_base() / "icon.ico"
        if ico.is_file():
            return ico
    return _DEV_BASE / "assets" / "icon.png"


def tray_icon() -> Path:
    """An .ico for the system-tray NotifyIcon.

    Prefer an actual .ico in both layouts (`assets/icon.ico` is kept in the
    repo next to the .png). Falls back to `app_icon()` (which for a dev launch
    is a .png) — the tray then converts a bitmap on the fly, but the repo ships
    both formats so the .ico branch is what normally runs.
    """
    ico = _frozen_base() / "icon.ico" if is_frozen() else _DEV_BASE / "assets" / "icon.ico"
    if ico.is_file():
        return ico
    return app_icon()


def close_exits() -> bool:
    """E2E / boot-probe escape hatch for close-to-tray.

    When `DSH_DESKTOP_E2E_CLOSE_EXIT=1`, the window's X button fully quits the
    app instead of hiding to the system tray. The build/installer probe
    scripts (`build_desktop.ps1`, `verify_installer.ps1`) drive the app with
    WM_CLOSE and then assert zero leftover processes, so they must set this to
    keep the tray from absorbing the close.
    """
    return os.environ.get("DSH_DESKTOP_E2E_CLOSE_EXIT") == "1"


def app_mutex_name() -> str:
    """Named mutex for single-instance / installer 'is it running' detection."""
    return f"Global\\{APP_NAME}.{_APP_ID}"


def backend_cwd() -> Path:
    """Working directory for the spawned `dsh web`; must be writable by the user."""
    return user_data_dir() / "runtime"


def settings_file() -> Path:
    """User settings JSON (currently only update prefs; first persistent file)."""
    return user_data_dir() / "settings.json"


def updates_dir() -> Path:
    """Where downloaded setup EXEs land (`user_data_dir()/updates`)."""
    return user_data_dir() / "updates"


def updates_disabled() -> bool:
    """Kill switch for any update-check networking.

    `DSH_DESKTOP_UPDATE_DISABLE=1` is the explicit probe switch;
    `close_exits()` (E2E probe mode) implies it so probe boots never touch
    the network — the boot probes must stay offline deterministically.
    """
    return os.environ.get("DSH_DESKTOP_UPDATE_DISABLE") == "1" or close_exits()