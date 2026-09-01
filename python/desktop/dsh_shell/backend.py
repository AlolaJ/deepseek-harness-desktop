"""BackendSupervisor: spawns and supervises the local `dsh web` backend process.

Launch form (dev):   node <repo>/apps/cli/lib/bin.js web --port 0 --no-open
  --port 0   -> OS-assigned ephemeral port, printed to stdout as an HTTP URL.
  --no-open  -> don't pop a browser (confirmed flag in packages/bundle/web-app).

Pure-Python threading supervisor (no Qt): the WebView shell owns no GUI-thread
marshaling, so callbacks fire on the reader/watchdog threads and the UI thread
only reads `url` / waits on a Condition. Logic is transplanted from the old
PySide BackendRunner (QTimer+signals -> threads+callbacks).

On Windows the child runs detached from any new console (CREATE_NO_WINDOW), so
no terminal window flashes behind the GUI window.
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time

from . import config

# The URL line is `dsh web: http://127.0.0.1:<port>/?token=<launch-token> (LAN: ...)`.
# The launch token is REQUIRED since the 0.1.2 browser-auth flow (token mints
# an authority-bound cookie, then 303 -> clean `/`); a bare-origin URL gets
# 401 "dsh web authentication required", so the match must carry the query.
# `\S*` stops at the space before "(LAN: ...)" — the LAN URL is a different
# host (not 127.0.0.1) and can never match.
_URL_RE = re.compile(r"http://127\.0\.0\.1:\d+\S*")

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_STDERR_TAIL = 24

# Backoff for auto-restarting a backend that died unexpectedly (0 -> 2 -> 4 -> 8s).
_RESTART_DELAY = 2.0
_RESTART_MAX = 8.0
# Exit detection fallback: a hard kill (TerminateProcess) can leave the stdout
# pipe write-end open in a descendant, wedging the reader thread — so a loop
# polls the child directly. `Popen.poll()` is authoritative.
_WATCHDOG_INTERVAL = 1.0


class BackendStartError(RuntimeError):
    """The backend could not be spawned (missing node / CLI / bad args)."""


class BackendSupervisor:
    """Lifecycle supervision for the node backend.

    Callbacks (all invoked on background threads):
      on_url(str)        first stdout line carrying http://127.0.0.1:<port> of the
                         current process (fires again after every auto-restart)
      on_failed(int, str)  unexpected exit: (exit code, tail of stderr)
      on_exited(int)     any handled exit (code; None-safe int)
    """

    def __init__(self, *, on_url=None, on_failed=None, on_exited=None) -> None:
        self.on_url = on_url
        self.on_failed = on_failed
        self.on_exited = on_exited

        self._lock = threading.Lock()
        self._url_cond = threading.Condition(self._lock)
        self._proc: subprocess.Popen[str] | None = None
        self._final_url: str | None = None
        self._stderr_tail: list[str] = []
        self._stopping = False
        self._exit_handled = False
        self._restart_delay = _RESTART_DELAY
        self._watchdog_stop = threading.Event()

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Spawn the backend (no-op if one is already running)."""
        with self._lock:
            if self._proc is not None:
                return
            bin_js = config.cli_bin_js()
            node = config.node_executable()
            args = [node, str(bin_js), "web", "--port", "0", "--no-open"]
            cwd = config.backend_cwd()
            cwd.mkdir(parents=True, exist_ok=True)
            self._final_url = None
            self._stderr_tail.clear()
            self._stopping = False
            self._exit_handled = False
            try:
                self._proc = subprocess.Popen(
                    args,
                    cwd=str(cwd),
                    env=os.environ.copy(),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=_CREATE_NO_WINDOW,
                )
            except OSError as exc:
                self._proc = None
                raise BackendStartError(f"failed to spawn {args[0]}: {exc}") from exc
            proc = self._proc
        assert proc is not None and proc.stdout is not None and proc.stderr is not None
        threading.Thread(target=self._read_stdout, args=(proc,), daemon=True, name="dsh-backend-stdout").start()
        threading.Thread(target=self._read_stderr, args=(proc,), daemon=True, name="dsh-backend-stderr").start()
        threading.Thread(target=self._watchdog, daemon=True, name="dsh-backend-watchdog").start()

    def stop(self, timeout: float = 5.0) -> None:
        """Terminate the backend and stop supervising (no auto-restart)."""
        with self._lock:
            self._stopping = True
            proc = self._proc
            # Wake wait_for_url() so a _boot blocked on it returns at once —
            # otherwise a WM_CLOSE during the splash phase (backend still
            # booting) would hang process exit for up to the full timeout.
            self._url_cond.notify_all()
        if proc is None:
            self._watchdog_stop.set()
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        if proc.poll() is None:
            try:
                proc.terminate()
            except (OSError, subprocess.SubprocessError):
                pass
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except (OSError, subprocess.SubprocessError):
                pass
            proc.wait()
        self._watchdog_stop.set()

    # -- URL wait ------------------------------------------------------------

    def wait_for_url(self, timeout: float = 30.0) -> str | None:
        """Block until the current backend prints an HTTP URL (or timeout)."""
        deadline = time.monotonic() + timeout
        with self._url_cond:
            while self._final_url is None:
                if self._stopping:
                    return None  # supervisor is shutting down — stop waiting
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._url_cond.wait(remaining)
            return self._final_url

    @property
    def url(self) -> str | None:
        with self._lock:
            return self._final_url

    @property
    def stderr_tail_text(self) -> str:
        """Last stderr lines, for a user-facing boot-failure message."""
        with self._lock:
            return "\n".join(self._stderr_tail[-_STDERR_TAIL:])

    # -- readers / watchdog --------------------------------------------------

    def _read_stdout(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            text = line.rstrip("\r\n")
            if not text:
                continue
            match = _URL_RE.search(text)
            if match is not None:
                with self._url_cond:
                    if self._final_url is None:
                        self._final_url = match.group(0)
                        url = self._final_url
                    else:
                        url = None
                    self._url_cond.notify_all()
                if url is not None and self.on_url is not None:
                    self._notify(self.on_url, (url,))
        # EOF on stdout == process ended.
        code = proc.poll()
        if code is not None:
            self._handle_exit(code)

    def _read_stderr(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            text = line.rstrip("\r\n")
            if text:
                self._stderr_tail.append(text)
                if len(self._stderr_tail) > 400:
                    del self._stderr_tail[:-200]

    def _watchdog(self) -> None:
        """Watchdog fallback: poll for liveness; a hard kill normally shows up
        as EOF on stdout, but a descendant holding the pipe write-end can wedge
        it, so poll the process directly to know when it truly died."""
        while not self._watchdog_stop.wait(_WATCHDOG_INTERVAL):
            with self._lock:
                proc = self._proc
                stopping = self._stopping
                handled = self._exit_handled
            if proc is None or stopping or handled:
                continue
            code = proc.poll()
            if code is not None:
                self._handle_exit(code)

    def _handle_exit(self, code: int) -> None:
        """One owner handles each exit ({reader EOF, watchdog} race-free). If the
        death was unexpected the backend is respawned (it is a local dependency,
        not user-scoped state); the web view re-binds to the new URL."""
        with self._lock:
            if self._stopping or self._exit_handled:
                return
            self._exit_handled = True
            proc = self._proc
            self._proc = None
        if proc is None:
            return
        if code != 0 and self.on_failed is not None:
            tail = "\n".join(self._stderr_tail[-_STDERR_TAIL:])
            self._notify(self.on_failed, (code, tail))
        if self.on_exited is not None:
            self._notify(self.on_exited, (code,))
        if not self._stopping:
            self._schedule_restart()

    def _schedule_restart(self) -> None:
        threading.Thread(target=self._respawn_after, daemon=True, name="dsh-backend-restart").start()

    def _respawn_after(self) -> None:
        time.sleep(self._restart_delay)
        with self._lock:
            if self._stopping or self._proc is not None:
                return
        try:
            self.start()
            self._restart_delay = _RESTART_DELAY
        except Exception:  # noqa: BLE001 — back off and retry rather than dying
            self._restart_delay = min(self._restart_delay * 2, _RESTART_MAX)
            self._schedule_restart()

    @staticmethod
    def _notify(callback, args) -> None:
        try:
            callback(*args)
        except Exception:  # noqa: BLE001 — a bad listener must not kill supervision
            import traceback

            traceback.print_exc()