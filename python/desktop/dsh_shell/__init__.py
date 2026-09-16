"""dsh_shell — a thin Python + WebView2 desktop shell for the DeepSeek Harness web UI.

The shell spawns the local `dsh web` backend and embeds the served web UI in a
WebView2 window. There is no Qt widget tree; the only Python<->JS surface is
the updater bridge (`update_ui.UpdateBridge`, injected via pywebview's js_api):
the browser talks to the backend over localhost HTTP/WebSocket directly.
"""

try:  # stamped by build_desktop.ps1 from apps/cli/package.json (gitignored)
    from ._version import __version__
except Exception:  # dev checkouts without a stamp
    __version__ = "0.0.0-dev"
