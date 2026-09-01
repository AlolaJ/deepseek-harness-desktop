"""dsh_shell — a thin Python + WebView2 desktop shell for the DeepSeek Harness web UI.

The shell spawns the local `dsh web` backend and embeds the served web UI in a
WebView2 window. There is no Qt widget tree and no Python<->JS bridge: the
browser talks to the backend over localhost HTTP/WebSocket directly.
"""

__version__ = "0.1.0"