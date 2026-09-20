r"""Update checking + silent self-update for the desktop shell.

Source of truth: the GitHub Releases of the distribution fork
(`AlolaJ/deepseek-harness-desktop`). Each release carries the Inno setup EXE
as an asset named `DeepSeekHarness-<version>-Setup.exe`; the checker compares
the release tag against the shell's stamped version (`dsh_shell._version`,
written by `build_desktop.ps1` from `apps/cli/package.json`).

Apply flow (consented in the UI, see update_ui.py):
  1. download the setup EXE into `<data>/updates/` (`.part` -> `os.replace`)
  2. spawn a detached PowerShell helper (`-EncodedCommand`, no quoting traps)
     that waits for THIS process to exit, runs the installer silently
     (`/VERYSILENT ... /DIR=<app dir>` — the exact invocation
     `verify_installer.ps1` proves), logs the exit code to shell.log and
     relaunches the app
  3. the shell exits cleanly (tray-exit path) so `app.run`'s
     `finally: supervisor.stop()` kills the backend and the process's
     `Global\` mutex is released before Inno checks `AppMutex`

stdlib only (urllib) — the shell venv carries no HTTP client.

Test hooks (also see config.updates_disabled):
  DSH_DESKTOP_UPDATE_API_BASE    override the GitHub API base (fake server)
  DSH_DESKTOP_UPDATE_REPO        override `owner/repo`
  DSH_DESKTOP_VERSION_OVERRIDE   override the current version
  DSH_DESKTOP_E2E_UPDATE_AUTO    auto-answer the update prompt (E2E probes)
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import config
from .log import log
from . import __version__ as _PKG_VERSION

_CREATE_NO_WINDOW = 0x08000000  # same constant as backend.py

_UA = f"DeepSeekHarness-Desktop/{_PKG_VERSION}"
_ASSET_RE = re.compile(r"^deepseekharness-.*-setup\.exe$", re.IGNORECASE)


class UpdateError(RuntimeError):
    """One-line reason an update step failed (shown/logged verbatim)."""


# ---- version comparison (semver-ish, prerelease aware) ----------------------


def normalize_tag(tag: str) -> str:
    """`v0.1.6-alpha.1` -> `0.1.6-alpha.1`."""
    return (tag or "").strip().lstrip("vV")


def parse_version(text: str) -> tuple[tuple[int, ...], tuple] | None:
    """Parse `0.1.6-alpha.1` into ((0, 1, 6), ('alpha', 1)); None if malformed.

    Numeric prerelease identifiers stay ints (so alpha.9 < alpha.10), textual
    ones stay strings (alpha < beta < rc lexically). Build metadata (+x) is
    ignored per semver.
    """
    s = normalize_tag(text).split("+", 1)[0]
    if not s:
        return None
    core, _, pre = s.partition("-")
    parts = core.split(".")
    if not parts or not all(p.isdigit() for p in parts):
        return None
    nums = tuple(int(p) for p in parts)
    if not pre:
        return (nums, ())
    ids: list[int | str] = []
    for ident in pre.split("."):
        if ident.isdigit():
            ids.append(int(ident))
        elif ident and all(c.isalnum() or c == "-" for c in ident):
            ids.append(ident)
        else:
            return None
    return (nums, tuple(ids))


def _compare_version(a: tuple[tuple[int, ...], tuple], b: tuple[tuple[int, ...], tuple]) -> int:
    a_core, a_pre = a
    b_core, b_pre = b
    n = max(len(a_core), len(b_core))
    a_pad = a_core + (0,) * (n - len(a_core))
    b_pad = b_core + (0,) * (n - len(b_core))
    if a_pad != b_pad:
        return -1 if a_pad < b_pad else 1
    if a_pre == b_pre:
        return 0
    if not a_pre:
        return 1  # a release outranks any prerelease of the same core
    if not b_pre:
        return -1
    for x, y in zip(a_pre, b_pre):
        if x == y:
            continue
        x_num, y_num = isinstance(x, int), isinstance(y, int)
        if x_num and y_num:
            return -1 if x < y else 1
        if x_num:
            return -1  # numeric identifiers rank below alphanumeric ones
        if y_num:
            return 1
        return -1 if str(x) < str(y) else 1
    return -1 if len(a_pre) < len(b_pre) else 1


def is_newer(candidate: str, current: str) -> bool:
    """Strictly newer (equal or lower versions never prompt)."""
    a, b = parse_version(candidate), parse_version(current)
    if a is None or b is None:
        return False
    return _compare_version(a, b) > 0


def current_version() -> str:
    """The running shell's version (stamped at build time, overridable for tests)."""
    override = os.environ.get("DSH_DESKTOP_VERSION_OVERRIDE")
    if override:
        return override.strip()
    return _PKG_VERSION


# ---- GitHub releases --------------------------------------------------------


@dataclass(frozen=True)
class ReleaseOffer:
    tag: str
    version: str
    asset_name: str
    asset_url: str
    asset_size: int | None  # absent from some responses; download infers


def api_base() -> str:
    return os.environ.get("DSH_DESKTOP_UPDATE_API_BASE") or "https://api.github.com"


def repo_slug() -> str:
    return os.environ.get("DSH_DESKTOP_UPDATE_REPO") or "AlolaJ/deepseek-harness-desktop"


def release_page_url(tag: str | None = None) -> str:
    """Human-facing releases page (browser fallback when the app can't update)."""
    base = f"https://github.com/{repo_slug().strip('/')}/releases"
    return f"{base}/tag/{tag}" if tag else base


def check_github(timeout: float = 10.0) -> ReleaseOffer:
    """Ask the GitHub API for the latest release and its setup EXE asset.

    `/releases/latest` excludes prereleases, so for an alpha/rc version line we
    list all releases and pick the newest by `is_newer`. Raises UpdateError
    with a one-line reason.
    """
    slug = repo_slug().strip("/")
    request = urllib.request.Request(
        f"{api_base().rstrip('/')}/repos/{slug}/releases",
        headers={"User-Agent": _UA, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"GitHub HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise UpdateError(f"GitHub unreachable: {exc}") from exc
    try:
        releases = json.loads(body.decode("utf-8", "replace"))
    except Exception as exc:
        raise UpdateError("GitHub returned an unreadable response") from exc
    if not isinstance(releases, list) or not releases:
        raise UpdateError("no releases found")
    best: ReleaseOffer | None = None
    for rel in releases:
        tag = str(rel.get("tag_name") or "").strip()
        if not tag:
            continue
        for asset in rel.get("assets") or []:
            name = str(asset.get("name") or "")
            download = str(asset.get("browser_download_url") or "")
            if _ASSET_RE.match(name) and download:
                size = asset.get("size")
                offer = ReleaseOffer(
                    tag=tag,
                    version=normalize_tag(tag),
                    asset_name=name,
                    asset_url=download,
                    asset_size=int(size) if isinstance(size, (int, float)) and size > 0 else None,
                )
                if best is None or is_newer(offer.version, best.version):
                    best = offer
                break  # one matching asset per release
    if best is None:
        raise UpdateError("no DeepSeekHarness-*-Setup.exe asset in any release")
    return best


# ---- settings persistence (first file the shell writes besides shell.log) ----


class UpdateSettings:
    """`<data>/settings.json` -> `{"update": {"skipped_version": <tag|null>}}`.

    `skipped_version` is the FULL tag behind a 不再提醒 choice; newer releases
    prompt again. Load is tolerant (missing/corrupt -> defaults), save is
    atomic (tmp in the same dir + os.replace) and never raises.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.path = config.settings_file()
        self.skipped_version: str | None = None
        self.auto_check: bool = True
        self.load()

    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            update = data.get("update") or {}
            skipped = update.get("skipped_version")
            self.skipped_version = str(skipped) if skipped else None
            self.auto_check = bool(update.get("auto_check", True))
        except FileNotFoundError:
            pass
        except Exception as exc:  # noqa: BLE001 — settings must never break boot
            log(f"==> updater: settings unreadable, using defaults: {exc}")

    def save(self) -> None:
        with self._lock:
            data = {
                "update": {
                    "skipped_version": self.skipped_version,
                    "auto_check": self.auto_check,
                }
            }
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_name(self.path.name + ".tmp")
                tmp.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                os.replace(tmp, self.path)
            except Exception as exc:  # noqa: BLE001
                log(f"==> updater: settings save failed: {exc}")


# ---- download ----------------------------------------------------------------


def download_release(
    offer: ReleaseOffer,
    dest_dir: Path,
    progress: Callable[[int, int | None], None] | None = None,
    chunk_size: int = 256 * 1024,
    timeout: float = 30.0,
) -> Path:
    """Stream the setup EXE into `dest_dir` (`.part` then `os.replace`).

    No resume: GitHub's CDN redirect target may not honour Range; an aborted
    download leaves a `.part` that cleanup_partials() sweeps at next launch.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(offer.asset_name).name
    if not _ASSET_RE.match(safe_name):
        raise UpdateError(f"unexpected asset name: {offer.asset_name!r}")
    final = dest_dir / safe_name
    part = dest_dir / (safe_name + ".part")
    request = urllib.request.Request(offer.asset_url, headers={"User-Agent": _UA})
    received = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp, open(part, "wb") as fh:
            total = offer.asset_size
            length = resp.headers.get("Content-Length")
            if length and length.isdigit():
                total = int(length)
            last_push = 0.0
            last_bytes = -1
            while True:
                block = resp.read(chunk_size)
                if not block:
                    break
                fh.write(block)
                received += len(block)
                now = time.monotonic()
                # Throttle progress pushes (each one marshals into the webview).
                if progress and (now - last_push >= 0.4 or received - last_bytes >= 2_000_000):
                    last_push, last_bytes = now, received
                    progress(received, total)
        if offer.asset_size is not None and received != offer.asset_size:
            raise UpdateError(f"downloaded {received} bytes, expected {offer.asset_size}")
    except UpdateError:
        part.unlink(missing_ok=True)
        raise
    except urllib.error.HTTPError as exc:
        part.unlink(missing_ok=True)
        raise UpdateError(f"download HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        part.unlink(missing_ok=True)
        raise UpdateError(f"download failed: {exc}") from exc
    if progress:
        progress(received, received)
    os.replace(part, final)
    return final


# ---- PowerShell helper (waits for exit -> silent install -> relaunch) --------


def _ps_quote(value: object) -> str:
    """A PowerShell single-quoted literal (only `'` needs doubling)."""
    return "'" + str(value).replace("'", "''") + "'"


def build_helper_script(
    installer: Path,
    app_dir: Path,
    exe_path: Path,
    pid: int,
    log_path: Path,
    settle_seconds: int = 3,
) -> str:
    r"""The updater's afterlife, as a PowerShell script.

    Runs detached (its image is powershell.exe, NOT the exe Inno must replace).
    Waits for the shell to exit (releasing the `Global\` mutex Inno's
    AppMutex checks), settles, installs silently, logs the exit code into
    shell.log (same timestamp format as log.py) and relaunches — unconditionally:
    if Inno failed before touching files the old exe relaunches fine, and a
    mid-flight failure is broken either way, the log line is the diagnostic.
    """
    installer_arg = ",".join(
        [
            _ps_quote("/VERYSILENT"),
            _ps_quote("/SUPPRESSMSGBOXES"),
            _ps_quote("/NORESTART"),
            _ps_quote("/CURRENTUSER"),
            _ps_quote(f'/DIR="{app_dir}"'),
        ]
    )
    return f"""$ErrorActionPreference = 'Continue'
$Log = {_ps_quote(log_path)}
function Write-Log([string]$Msg) {{
  $line = '{{0}} {{1}}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Msg
  try {{ [System.IO.File]::AppendAllText($Log, $line + "`r`n", [System.Text.UTF8Encoding]::new($false)) }} catch {{}}
}}
Write-Log '==> updater: helper started, waiting for shell (pid {pid}) to exit'
try {{ Wait-Process -Id {int(pid)} -Timeout 120 -ErrorAction SilentlyContinue }} catch {{}}
Start-Sleep -Seconds {int(settle_seconds)}
Write-Log '==> updater: running silent installer'
$code = -1
try {{
  $p = Start-Process -FilePath {_ps_quote(installer)} -PassThru -Wait -ArgumentList {installer_arg}
  $code = $p.ExitCode
}} catch {{ Write-Log ('==> updater: installer launch failed: ' + $_.Exception.Message) }}
Write-Log ('==> updater: installer exit code ' + $code)
if ($code -eq 0) {{
  try {{
    Remove-Item -LiteralPath {_ps_quote(installer)} -Force -ErrorAction SilentlyContinue
    Write-Log '==> updater: removed downloaded installer'
  }} catch {{}}
}}
Write-Log '==> updater: relaunching app'
try {{ Start-Process -FilePath {_ps_quote(exe_path)} -WorkingDirectory {_ps_quote(app_dir)} }}
catch {{ Write-Log ('==> updater: relaunch failed: ' + $_.Exception.Message) }}
"""


def encode_powershell(script: str) -> str:
    """-EncodedCommand payload (UTF-16LE base64) — immune to quoting traps."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def _spawn_helper(script: str) -> None:
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-EncodedCommand",
            encode_powershell(script),
        ],
        creationflags=_CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )


# ---- orchestrator ------------------------------------------------------------


class Updater:
    """State machine behind the update UI; one instance, owned by app.run.

    phases: idle -> checking -> available -> downloading -> applying
            (side exits: uptodate / error). Rendering is push-only: the page
            never polls, `on_state_change` -> update_ui.push_state carries
            each snapshot; a single applyState() render entry heals any state
            lost across a page navigation.
    """

    def __init__(self) -> None:
        self.settings = UpdateSettings()
        self.on_state_change: Callable[[], None] | None = None
        self.on_apply_exit: Callable[[], None] | None = None
        self.is_window_hidden: Callable[[], bool] | None = None
        self.notify_balloon: Callable[[str], None] | None = None
        self._lock = threading.RLock()
        self._state: dict = {"phase": "idle"}
        self._offer: ReleaseOffer | None = None

    # -- snapshots -----------------------------------------------------------

    def state(self) -> dict:
        with self._lock:
            return dict(self._state)

    def _push(self) -> None:
        callback = self.on_state_change
        if callback is not None:
            try:
                callback()
            except Exception as exc:  # noqa: BLE001 — push is best-effort
                log(f"==> updater: state push failed: {exc}")

    # -- lifecycle -----------------------------------------------------------

    def cleanup_partials(self) -> None:
        """Sweep .part leftovers from an interrupted download (no resume)."""
        updates = config.updates_dir()
        try:
            if updates.is_dir():
                for part in updates.glob("*.part"):
                    part.unlink(missing_ok=True)
                    log(f"==> updater: removed stale {part.name}")
        except Exception as exc:  # noqa: BLE001
            log(f"==> updater: partial cleanup failed: {exc}")

    def auto_check_allowed(self) -> bool:
        """Startup check: frozen builds only (dev shells have no stamped
        version), not opted out, and not in a probe/E2E context — the build
        and installer boot probes must never touch the network."""
        if not config.is_frozen():
            return False
        if current_version() == "0.0.0-dev":
            return False
        if not self.settings.auto_check:
            return False
        return not config.updates_disabled()

    # -- check ---------------------------------------------------------------

    def check(self, manual: bool) -> dict:
        with self._lock:
            if self._state.get("phase") in ("checking", "downloading"):
                return {**self._state, "reason": "busy"}
            self._state = {"phase": "checking", "manual": manual}
        self._push()
        threading.Thread(
            target=self._check_worker, args=(manual,), daemon=True, name="dsh-update-check"
        ).start()
        return self.state()

    def _check_worker(self, manual: bool) -> None:
        try:
            offer = check_github()
        except UpdateError as exc:
            log(f"==> updater: check failed: {exc}")
            with self._lock:
                # auto failures stay silent (log-only); manual ones surface a toast
                self._state = {"phase": "error" if manual else "idle", "message": str(exc)}
            self._push()
            return
        current = current_version()
        newer = is_newer(offer.version, current)
        log(f"==> updater: latest release {offer.tag} (current {current}, newer={newer})")
        if not newer:
            with self._lock:
                self._state = {"phase": "uptodate" if manual else "idle"}
            self._push()
            return
        suppressed = offer.tag == self.settings.skipped_version
        if suppressed and not manual:
            # The user said 不再提醒 for this exact release; a NEWER one re-prompts.
            log(f"==> updater: {offer.tag} suppressed by user choice")
            with self._lock:
                self._state = {"phase": "idle"}
            self._push()
            return
        with self._lock:
            self._offer = offer
            self._state = {
                "phase": "available",
                "manual": manual,
                "tag": offer.tag,
                "version": offer.version,
                "current": current,
                "asset_name": offer.asset_name,
                "asset_size": offer.asset_size,
                "suppressed": suppressed,
            }
        self._push()
        self._maybe_e2e_auto_choice()

    def _maybe_e2e_auto_choice(self) -> None:
        """E2E hook (mirrors DSH_DESKTOP_E2E_CLOSE_EXIT): let probe scripts
        drive the update flow without a human choosing in the modal."""
        auto = os.environ.get("DSH_DESKTOP_E2E_UPDATE_AUTO")
        if auto in ("install", "later", "never"):
            log(f"==> updater: E2E auto-choice: {auto}")
            threading.Thread(
                target=self.choose, args=(auto,), daemon=True, name="dsh-update-e2e"
            ).start()

    # -- user choice ---------------------------------------------------------

    def choose(self, action: str) -> dict:
        action = (action or "").strip().lower()
        with self._lock:
            offer = self._offer
            if action in ("later", "never"):
                if offer is None:
                    return {**self._state, "reason": "no-offer"}
                if action == "never":
                    self.settings.skipped_version = offer.tag
                    self.settings.save()
                    log(f"==> updater: will not remind about {offer.tag} again")
                else:
                    log(f"==> updater: user deferred {offer.tag}")
                self._offer = None
                self._state = {"phase": "idle"}
            elif action == "install":
                if offer is None or self._state.get("phase") not in ("available", "error"):
                    return {**self._state, "reason": "no-offer"}
                self._state = {
                    "phase": "downloading",
                    "tag": offer.tag,
                    "received": 0,
                    "total": offer.asset_size,
                }
            else:
                return {**self._state, "reason": "unknown-action"}
        self._push()
        if action == "install":
            threading.Thread(
                target=self._download_worker, args=(offer,), daemon=True, name="dsh-update-dl"
            ).start()
        return self.state()

    def _download_worker(self, offer: ReleaseOffer) -> None:
        def progress(received: int, total: int | None) -> None:
            with self._lock:
                if self._state.get("phase") != "downloading":
                    return  # superseded (new check/choice) — drop stale progress
                self._state["received"] = received
                self._state["total"] = total
            self._push()

        try:
            installer = download_release(offer, config.updates_dir(), progress)
        except UpdateError as exc:
            log(f"==> updater: download failed: {exc}")
            with self._lock:
                self._state = {"phase": "error", "message": str(exc), "tag": offer.tag}
            self._push()
            return
        log(f"==> updater: downloaded {installer.name} ({installer.stat().st_size} bytes)")
        if self.is_window_hidden and self.is_window_hidden() and self.notify_balloon:
            # The user parked the app in the tray mid-download; the install
            # they consented to proceeds, the balloon documents the restart.
            self.notify_balloon(f"更新已下载 · update ready, restarting to apply ({offer.tag})")
        try:
            self._spawn_apply(installer)
        except UpdateError as exc:
            log(f"==> updater: apply failed: {exc}")
            with self._lock:
                self._state = {"phase": "error", "message": str(exc), "tag": offer.tag}
            self._push()
            return
        with self._lock:
            self._state = {"phase": "applying", "tag": offer.tag}
        self._push()
        if self.on_apply_exit is not None:
            self.on_apply_exit()

    def _spawn_apply(self, installer: Path) -> None:
        if os.name != "nt":
            raise UpdateError("self-update is Windows-only")
        exe_path = Path(sys.executable)
        app_dir = exe_path.parent
        script = build_helper_script(
            installer, app_dir, exe_path, os.getpid(), config.settings_file().parent / "shell.log"
        )
        log(f"==> updater: helper script:\n{script}")
        _spawn_helper(script)
