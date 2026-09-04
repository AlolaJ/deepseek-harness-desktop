# dsh desktop shell (`python/desktop`)

English | [中文](README.zh.md)

A thin Python desktop shell for the DeepSeek Harness web UI. It spawns the
local `dsh web` backend and embeds the served web interface in a **WebView2**
window (via [`pywebview`](https://pywebview.flowrl.com/)). It replaces the
former PySide6 widget port, which rendered every screen in native Qt — this
shell hosts the same browser UI the `dsh web` command serves, so there is no
duplicate UI to maintain.

```
┌────────────────────────────────────────────┐
│ WebView2 window (Edge Chromium, GPU accel) │
│        ┌─────────────────────────┐         │
│        │  dsh web UI (SPA)        │         │
│        │  ──HTTP / WebSocket──┐   │         │
│        └─────────┬────────────┘   │         │
└──────────────────┼─────────────────────────┘
                   │ localhost
         ┌─────────▼──────────┐
         │ node …/bin.js web  │  spawned by BackendSupervisor
         │ --port 0 --no-open │  (CREATE_NO_WINDOW, auto-restart)
         └────────────────────┘
```

The Python side is a **window host only**: it spawns the backend, waits for the
printed URL, and opens a window at it. The browser talks to the backend
directly over localhost — there is no Python↔JS bridge, no JSON-RPC client in
Python, and no polling loop.

## Run (dev)

```powershell
# once: workspace build so apps/cli/lib/bin.js + apps/web/dist exist
pnpm run build

# launch the shell (creates python/desktop/.venv + installs deps on first run)
.\scripts\run_dev.ps1
```

Or manually:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python -m dsh_shell
```

### Overrides

| Flag           | Env var              | Effect                                   |
| -------------- | -------------------- | ---------------------------------------- |
| `--node`       | `DSH_DESKTOP_NODE`   | Node executable for the backend          |
| `--cli-dir`    | `DSH_DESKTOP_CLI_DIR`| Dir holding `lib/bin.js` (default `<repo>/apps/cli`) |
| `--data-dir`   | `DSH_DESKTOP_DATA_DIR`| Writable data dir (default `%LOCALAPPDATA%\DeepSeekHarness`) |

## What runs where

| Concern | Owner |
| ------- | ----- |
| UI rendering, markdown, tool cards, settings | Web UI in WebView2 (`dsh web`) |
| Backend HTTP/WebSocket API | the spawned `node` process (`BackendSupervisor` in [`dsh_shell/backend.py`](dsh_shell/backend.py)) |
| Window, backend spawn, crash supervision | `dsh_shell` (`app.py`) |

The backend crashes are self-healing: `BackendSupervisor` restarts `dsh web`
with exponential backoff and the window re-binds to the new port. Sessions are
persisted server-side (sqlite/jsonl), so a reload restores the conversation
without restarting the shell.

## Performance notes

1. **Zero Python bridge** — the browser hits the backend over localhost HTTP/
   WebSocket directly; no per-frame Python work, no IPC amplification.
2. **`debug=False`** — `webview.start(debug=False)` uses the WebView2
   production rendering path (hardware acceleration on by default).
3. **Persistent WebView2 cache** — `WEBVIEW2_USER_DATA_FOLDER` points at
   `%LOCALAPPDATA%\DeepSeekHarness\webview2`, reusing the HTTP index / asset /
   GPU shader caches across launches → visibly faster repeat startups.
4. **Localhost only** — no remote CDNs or mixed-content surprises; the SPA is
   served by the local backend.
5. **Crash self-healing with session retention** — backend restarts rebind the
   window in place instead of restarting the whole shell.
6. **`CREATE_NO_WINDOW`** — the node child never flashes a console window.

## Notes / limitations

- Windows-only window backend today (`pywebview` + WebView2 runtime, which is
  preinstalled on Windows 11 / ships with Edge).

## Packaging: EXE + installer

Build a double-clickable EXE and a bilingual installer (zh-CN / en), fully
self-contained (bundles its own node.exe + the `dsh` CLI closure + `apps/web`
dist):

```powershell
# 1) the PyInstaller windowed onedir EXE (boot-probes it standalone)
.\scripts\build_desktop.ps1

# 2) the Inno installer (reads the version from apps/cli/package.json)
#    requires: winget install JRSoftware.InnoSetup
.\scripts\build_installer.ps1

# 3) end-to-end installer verify: full install -> update branch -> uninstall
#    (independent scratch dirs; user data preservation asserted)
.\scripts\verify_installer.ps1
```

Pipeline (see `scripts/build_desktop.ps1` for flags like `-SkipDeploy`):

| Step | Produces | What happens |
| ---- | -------- | ------------ |
| preflight | — | verifies `apps/cli/lib/bin.js` + `apps/web/dist` (else `pnpm run build`) |
| closure deploy | `.build/resources/cli` | `pnpm --filter dsh-desktop-runtime deploy --prod --legacy` → a self-contained `node_modules`; three completeness fixes applied here: (1) link: vendor overrides de-linked into real copies, (2) the `link:`-overridden `@deepseek-ai/{cosmokit,schemastery}` materialized from `vendor/` (pnpm never ships them in the output tree), (3) peer-only `workspace:` packages (`@deepseek-ai/dsh-*`, `cordis-plugin-group`) installed because `deploy-root` declares them as **regular** deps — pnpm won't auto-install a `workspace:`-protocol peer in a deploy |
| closure smoke | `.build/closure-smoke/` | pre-EXE boot gate: launches `<closure>` `dsh web --port 0 --no-open` with node and asserts it prints an HTTP URL — a broken closure fails the build in seconds, before PyInstaller spends ~5 min |
| runtime resources | `.build/resources/{node.exe, icon.ico}` | bundled node + the app icon |
| PyInstaller | `.build/dist/dsh-desktop/` | windowed (`console=False`) onedir: `DeepSeekHarness.exe` + `_internal/` |
| relayout | `.build/dist/dsh-desktop/{node.exe, cli, icon.ico}` | after collection, `Relayout-Resources` moves the bundled runtime **out of `_internal\resources` to the bundle root** (siblings of the EXE). PyInstaller can only place them under `_internal/`, but Inno/ISCC (no `longPathAware`) and the installer's rename-over writes are capped at MAX_PATH (~260); keeping `node.exe`/`cli`/`icon.ico` siblings of the EXE keeps every installed path short even at the real per-user location |
| boot probe | `.build/probe/` (incl. `shell.log`) | launches the EXE standalone: window appears, bundled `<app>\node.exe` (bundle root) spawns `dsh web`, WM_CLOSE exits cleanly, no leftovers |

The EXE shell resolves everything frozen: `sys.frozen` makes `config.py` read
`node.exe`, the `cli` closure and `icon.ico` from the **bundle root** — the
directory containing `DeepSeekHarness.exe` (overridable via
`DSH_DESKTOP_NODE` / `DSH_DESKTOP_CLI_DIR` as before). Boot
diagnostics go to `%LOCALAPPDATA%\DeepSeekHarness\shell.log` (there is no
console). EXE / shortcut / taskbar icons come from the embedded PE icon
(`icon=assets/icon.ico`); the in-window titlebar icon (pywebview has no
`icon=` on Windows) is set post-show via `WM_SETICON` in `app.py`
(`icon.ico` only — dev launches keep the generic form icon). A named mutex
(`Global\…`) matches the installer's `AppMutex` so a second launch / install
can find a running copy.

The Inno installer (`inno/setup.iss`) is bilingual and follows the system
language; it offers per-user / all-users at install time; and it auto-detects
**full vs update**: a previous install (same `AppId`) makes it skip
license / directory / shortcut pages, keep the install dir and **all user data
(in `%LOCALAPPDATA%\DeepSeekHarness`, outside `{app}`)**, and warn on a
downgrade. Uninstall removes only `{app}` — conversations and settings survive.

### Deploy root

`deploy-root/` is a workspace member holding only a `package.json` that
declares `"@deepseek-ai/dsh": "workspace:^"` **plus every `workspace:`-protocol
peer/optional dep that pnpm deploy would otherwise silently drop** (the
`@deepseek-ai/dsh-*` feature packages, `cordis-plugin-group`,
`@deepseek-ai/{cosmokit,schemastery}` — the latter two still end up missing
after deploy because the root `overrides:` maps them to `link:vendor/*`, so
`build_desktop.ps1` materializes them from `vendor/` explicitly; the
Linux-only `node-addon-landlock-run-linux-*` packages are deliberately not
included). It is **not a build target** — it exists so `pnpm deploy` can
produce a physical, self-contained CLI closure. It is registered in the root
`pnpm-workspace.yaml` (`python/desktop/deploy-root`).