"""Desk shell orchestration: spawn the backend, embed its web UI, supervise.

Performance design (deliberate, no bridge):
  * The web UI talks to the backend over localhost HTTP/WebSocket directly —
    no pywebview js_api, no polling loop, no Python-side per-frame cost.
  * `webview.start(debug=False)` runs the WebView2 production rendering path.
  * A persistent `WEBVIEW2_USER_DATA_FOLDER` keeps the HTTP index / asset /
    GPU shader cache across launches (biggest repeat-startup win).
  * A crashed backend is auto-restarted and the window re-binds to the new
    URL; conversations live server-side (sqlite/jsonl), so reload restores
    them without restarting the shell.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

import webview  # type: ignore

from . import config
from . import tray as tray_mod
from .backend import BackendSupervisor
from .log import log

APP_NAME = "DeepSeek Harness"
_WINDOW = (1280, 800)
_MIN_WINDOW = (960, 600)
_BOOT_TIMEOUT = 30.0


def _prepare_webview2_data_dir() -> None:
    root = config.webview2_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("WEBVIEW2_USER_DATA_FOLDER", str(root))


def _hold_mutex() -> object | None:
    """Create the named instance mutex so an installer / second launch can
    tell a running copy (handles are process-lifetime; kernel closes on exit)."""
    if os.name != "nt":
        return None
    try:
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, config.app_mutex_name())
        if handle:
            return handle
    except Exception:  # noqa: BLE001 — single-instance is best-effort
        return None
    return None


def _set_window_titlebar_icon(icon: Path) -> None:
    """Give the WebView2 window a real titlebar icon.

    pywebview's create_window() has no `icon=` on Windows, so the titlebar
    otherwise shows the generic form icon even though the EXE / shortcut /
    taskbar all carry the app icon (embedded PE resource). Best-effort: after
    the native window exists, load an HICON from the bundled .ico and post
    WM_SETICON (big + small). Icon handle is intentionally not destroyed — the
    window keeps owning it for its lifetime.
    """
    import ctypes

    user32 = ctypes.windll.user32
    # Prototypes spelled with primitives: ctypes.wintypes on the Python 3.13.14
    # build has no LRESULT (and dropping an argtype on 64-bit truncates the
    # pointer, so be explicit). WPARAM/LPARAM/HANDLE are pointer-sized.
    _HWND, _WPARAM, _LPARAM = ctypes.c_void_p, ctypes.c_size_t, ctypes.c_ssize_t
    user32.FindWindowW.restype = _HWND
    user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.LoadImageW.restype = _HWND
    user32.LoadImageW.argtypes = [
        ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
        ctypes.c_int, ctypes.c_int, ctypes.c_uint,
    ]
    user32.SendMessageW.restype = ctypes.c_ssize_t  # LRESULT
    user32.SendMessageW.argtypes = [_HWND, ctypes.c_uint, _WPARAM, _LPARAM]

    hwnd = user32.FindWindowW(None, APP_NAME)
    if not hwnd:
        return
    IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x00000010, 0x00000040
    # cx/cy=0 + LR_DEFAULTSIZE -> system-metric sizes from the .ico's frames
    hicon = user32.LoadImageW(None, str(icon), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
    if not hicon:
        return
    WM_SETICON, ICON_BIG, ICON_SMALL = 0x0080, 1, 0
    user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon)
    user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon)


def run() -> int:
    _prepare_webview2_data_dir()
    _hold_mutex()

    supervisor = BackendSupervisor()
    failures: list[str] = []

    def on_failed(code: int, tail: str) -> None:
        failures.append(f"backend exited with code {code}\n{tail}")

    supervisor.on_failed = on_failed

    try:
        supervisor.start()
    except Exception as exc:  # missing node / CLI not built
        log(f"==> failed to start backend: {exc}")
        return 1

    log("==> starting backend ...")
    url = supervisor.wait_for_url(_BOOT_TIMEOUT)
    if url is None:
        log("==> backend did not report a URL within the timeout")
        supervisor.stop()
        return 1
    log(f"==> backend URL: {url}")

    window = webview.create_window(
        APP_NAME,
        url,
        width=_WINDOW[0],
        height=_WINDOW[1],
        min_size=_MIN_WINDOW,
    )

    # pywebview 6.x has no icon= param on Windows — once the native window
    # exists (shown event), set its titlebar icon via WM_SETICON from the
    # bundled icon.ico (frozen). Dev mode uses assets/icon.png (not an ICO),
    # so there the generic form icon remains — acceptable for a dev launch.
    _icon = config.app_icon()
    if _icon.suffix.lower() == ".ico":
        def _apply_titlebar_icon() -> None:
            # runs on the webview event thread — a cosmetic failure must never
            # take the window/shell down with it
            try:
                _set_window_titlebar_icon(_icon)
            except Exception as exc:  # noqa: BLE001 — best-effort, cosmetic
                log(f"==> titlebar icon failed (ignoring): {exc}")
        window.events.shown += _apply_titlebar_icon

    def rebind(new_url: str) -> None:
        # Backend restarted (crash) — point the WebView at the fresh port; the
        # page re-bootstraps and its sessions restore from the server side.
        log(f"==> backend restarted, rebinding to {new_url}")
        try:
            window.load_url(new_url)
        except Exception as exc:  # noqa: BLE001 — window may already be gone
            log(f"==> rebind failed: {exc}")

    supervisor.on_url = rebind

    # ---- close-to-tray --------------------------------------------------
    # Clicking the window's X hides it to the system tray (the backend keeps
    # running in the background); only the tray's 退出/Exit actually quits.
    # pywebview 6.x has no tray support, so the tray is a WinForms NotifyIcon
    # on its own STA thread (dsh_shell/tray.py). `DSH_DESKTOP_E2E_CLOSE_EXIT`
    # (config.close_exits) is the probe escape hatch: the build/installer boot
    # probes drive the app with WM_CLOSE and need it to exit for their
    # zero-leftovers assertion.
    exiting = {"flag": False}

    def _show_from_tray() -> None:
        try:
            window.show()
        except Exception as exc:  # noqa: BLE001 — best-effort
            log(f"==> show from tray failed: {exc}")

    def _exit_from_tray() -> None:
        if exiting["flag"]:
            return
        exiting["flag"] = True
        log("==> tray: exit requested")
        try:
            # destroy() marshals to the pywebview UI thread and runs the normal
            # close flow; on_closing sees the flag and lets the close proceed.
            window.destroy()
        except Exception as exc:  # noqa: BLE001 — never leave an inert app
            log(f"==> tray: window destroy failed, forcing exit: {exc}")
            os._exit(1)

    tray = tray_mod.create(config.tray_icon(), _show_from_tray, _exit_from_tray) if not config.close_exits() else None

    def _on_closing(**_kw) -> bool | None:
        if exiting["flag"] or config.close_exits():
            return None  # allow the close -> normal full shutdown
        # Cancel the close and park the app in the tray instead.
        try:
            window.hide()
        except Exception as exc:  # noqa: BLE001 — best-effort
            log(f"==> hide-to-tray failed: {exc}")
        if tray is not None:
            tray.notify_balloon("仍在后台运行 · still running in the background")
        return False

    if tray is not None:
        window.events.closing += _on_closing

    try:
        # debug=False is the production WebView2 path (GPU + optimized rendering).
        webview.start(debug=False)
    except Exception as exc:  # noqa: BLE001
        log(f"==> webview start failed: {exc}")
        supervisor.stop()
        return 1
    finally:
        supervisor.stop()

    if failures:
        log("==> backend errors during the session:")
        for line in failures:
            log(line)
        return 2
    return 0