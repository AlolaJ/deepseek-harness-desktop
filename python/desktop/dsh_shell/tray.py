"""System-tray icon for the desktop shell (Windows only).

pywebview 6.x has no tray support of its own, so the tray is a small WinForms
``NotifyIcon`` + right-click menu (打开/Show + 检查更新/Check for updates +
退出/Exit) running on its own STA message-pump thread. All user actions funnel
back to callbacks owned by ``dsh_shell.app`` — the tray never touches app
state directly.

Lifetime: the icon lives until the shell truly exits. When the app shuts down
(the tray "退出/Exit" requested a real quit, or the E2E close path ran),
pywebview's on-close shutdown calls ``Application.Exit()``, which also ends
this thread's message pump; the ``finally`` in ``_run`` then disposes the icon.
The thread is background so it can never hold the process open on its own.

pythonnet is imported lazily on the tray thread: the CLR/WinForms world is
entered once there, and a failure to load it degrades gracefully (``start()``
returns ``False`` and ``app.py`` falls back to the old close-quits behaviour).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from .log import log

# Held in a module global so garbage collection can never reap the NotifyIcon
# while its STA message loop is still running.
_TRAY: "Tray | None" = None


class Tray:
    """A WinForms NotifyIcon on its own background STA thread."""

    def __init__(
        self,
        icon_path: Path,
        on_show: Callable[[], None],
        on_exit: Callable[[], None],
        on_check_update: Callable[[], None] | None = None,
    ) -> None:
        self._icon_path = str(icon_path)
        self._on_show = on_show
        self._on_exit = on_exit
        self._on_check_update = on_check_update
        self._thread = None
        self._notify = None

    def start(self) -> bool:
        """Start the tray thread. Returns False if .NET/WinForms is unavailable
        (or not Windows) so the caller can degrade to close-quits."""
        if os.name != "nt":
            return False
        try:
            # `import clr` must come before any System.* import — pythonnet's
            # meta-path finder is only active once the runtime is entered (the
            # same ordering pywebview's own winforms backend relies on).
            import clr  # noqa: PLC0415, F401
            from System.Threading import ApartmentState, Thread, ThreadStart
        except Exception as exc:  # pythonnet not importable in this Python
            log(f"==> tray unavailable (no pythonnet/.NET): {exc}")
            return False
        self._thread = Thread(ThreadStart(self._run))
        # STA is required for WinForms controls.
        self._thread.SetApartmentState(ApartmentState.STA)
        # Background: never keep the process alive on the tray's own.
        self._thread.IsBackground = True
        self._thread.Start()
        return True

    def _run(self) -> None:
        """Tray-thread entry: enter the CLR, build the icon, run the pump."""
        try:
            import clr  # noqa: PLC0415 — intentionally lazy, on-thread

            clr.AddReference("System.Windows.Forms")
            clr.AddReference("System.Drawing")
            import System.Windows.Forms as WinForms  # noqa: PLC0415

            self._notify = self._build_icon(WinForms)
            log("==> tray: NotifyIcon created (open = 左键双击/菜单 打开, exit = 菜单 退出)")
            WinForms.Application.Run()  # blocks until Application.Exit()
        except Exception as exc:  # pragma: no cover - defensive
            log(f"==> tray thread failed: {exc}")
        finally:
            self._dispose()

    def _build_icon(self, WinForms: object) -> object:
        from System.Drawing import Bitmap, Icon  # noqa: PLC0415

        # .ico loads directly; a .png (dev fallback) gets a live HICON.
        if os.path.splitext(self._icon_path)[1].lower() != ".ico":
            _bmp = Bitmap(self._icon_path)
            _icon = Icon.FromHandle(_bmp.GetHicon())
        else:
            _icon = Icon(self._icon_path)

        notify = WinForms.NotifyIcon()
        notify.Icon = _icon
        notify.Text = "DeepSeek Harness"
        notify.Visible = True

        menu = WinForms.ContextMenuStrip()
        item_show = WinForms.ToolStripMenuItem("打开 DeepSeek Harness / Show")
        item_show.Click += self._menu_show
        menu.Items.Add(item_show)
        menu.Items.Add(WinForms.ToolStripSeparator())
        if self._on_check_update is not None:
            item_update = WinForms.ToolStripMenuItem("检查更新 / Check for updates")
            item_update.Click += self._menu_check_update
            menu.Items.Add(item_update)
        item_exit = WinForms.ToolStripMenuItem("退出 / Exit")
        item_exit.Click += self._menu_exit
        menu.Items.Add(item_exit)
        notify.ContextMenuStrip = menu

        # Double-click restores the window just like the menu Show item.
        notify.MouseDoubleClick += self._menu_show
        return notify

    # --- menu handlers (run on the tray STA thread) -----------------------

    def _menu_show(self, sender=None, event=None) -> None:  # noqa: ARG002
        self._dispatch(self._on_show)

    def _menu_check_update(self, sender=None, event=None) -> None:  # noqa: ARG002
        self._dispatch(self._on_check_update)

    def _menu_exit(self, sender=None, event=None) -> None:  # noqa: ARG002
        self._dispatch(self._on_exit)

    def _dispatch(self, fn: Callable[[], None]) -> None:
        """Run a user callback, never letting its failure take the tray down.
        The callbacks call pywebview window.show()/destroy(), which marshal to
        the pywebview UI thread via Control.Invoke — cross-thread safe."""
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 — cosmetic context
            log(f"==> tray action failed: {exc}")

    # --- cosmetic helpers --------------------------------------------------

    def notify_balloon(self, text: str, title: str = "DeepSeek Harness") -> None:
        """Show a taskbar balloon (best-effort). Called on hide-to-tray from
        the pywebview UI thread; ShowBalloonTip posts a message to the icon's
        own hidden window, so it safely resolves on the tray thread."""
        if self._notify is None:
            return
        try:
            self._notify.BalloonTipTitle = title
            self._notify.BalloonTipText = text
            self._notify.ShowBalloonTip(3000)
        except Exception as exc:  # noqa: BLE001
            log(f"==> tray balloon failed: {exc}")

    def _dispose(self) -> None:
        try:
            if self._notify is not None:
                self._notify.Visible = False
                self._notify.Dispose()
                self._notify = None
        except Exception as exc:  # pragma: no cover - defensive
            log(f"==> tray dispose failed: {exc}")


def create(
    icon_path: Path,
    on_show: Callable[[], None],
    on_exit: Callable[[], None],
    on_check_update: Callable[[], None] | None = None,
) -> "Tray | None":
    """Create and start the tray; returns None if unavailable so the caller
    keeps close-quits behaviour."""
    global _TRAY
    tray = Tray(icon_path, on_show, on_exit, on_check_update)
    if not tray.start():
        return None
    _TRAY = tray  # keep alive for the process lifetime
    return tray