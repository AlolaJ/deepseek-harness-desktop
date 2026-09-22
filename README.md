# DeepSeek Harness Desktop

English | [中文](README.zh.md)

DeepSeek Harness Desktop (`dsh-desktop`) is a Windows desktop build of the open-source [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) agent. It is **ready to use out of the box**（即开即用）: the packaged EXE and bilingual installer bundle the whole backend — the `dsh` CLI, the `dsh web` web UI and a Node.js runtime — so anyone, including newcomers, can download, install, and start using it without installing Node.js or Python first.

The project keeps the **entire official codebase as a full copy**: the backend logic is a complete copy of the upstream `deepseek-harness` repository, and the desktop shell, installer, and their build scripts live on top of it under `python/desktop/`. Official updates are continuously merged in, so this desktop build stays in sync with the upstream project and keeps receiving its fixes and features.

## Quick start (Windows)

1. Download the newest installer — `DeepSeekHarness-*-Setup.exe` — from the [Releases](https://github.com/deepseek-ai/deepseek-harness/releases) page.
2. Run it and follow the bilingual (中文 / English) wizard; it offers a per-user or all-users install.
3. Launch **DeepSeek Harness** from the Start menu or the desktop shortcut. The app starts its own bundled backend — no Node.js or Python required.
4. Clicking the window's **X** keeps it running in the system tray; right-click the tray icon and choose **退出 / Exit** to quit completely.

## Features

- **Self-contained** — bundles `node.exe`, the full `dsh` CLI closure, and the web UI; nothing else to install.
- **Windows-native window** — the UI runs in **WebView2** (the Edge engine, preinstalled on Windows 11 / shipped with Edge).
- **Bilingual installer** — 中文 / English, update-aware: reinstalling over an existing install keeps your conversations and settings.
- **Close to tray** — closing the window parks it in the tray; the tray menu (退出 / Exit) is the only way to fully quit.
- **Sessions survive** — conversations live under `%LOCALAPPDATA%\DeepSeekHarness` and are preserved across update and uninstall.
- **Stays in sync** — official upstream changes are continuously merged into this repository.

## What the desktop build adds

On top of the untouched upstream copy, `python/desktop/` adds the pywebview/WebView2 window host, a system-tray icon, and the packaging pipeline.

| Directory / file | Role |
| --- | --- |
| `python/desktop/` | Desktop shell (pywebview + WebView2), tray, configuration, build scripts |
| `python/desktop/inno/` | Bilingual Inno Setup script that produces the installer |
| `apps`, `packages`, `docs`, … | Full, unchanged copy of the official `deepseek-harness` source |
| `scripts/` | Repository-wide build and bilingual-pairing gates |

## Build from source

```sh
git clone https://github.com/deepseek-ai/deepseek-harness.git
cd deepseek-harness
pnpm install
pnpm run build
```

The desktop shell, the double-clickable EXE, and the installer can then be produced with `python/desktop/scripts/build_desktop.ps1`, `python/desktop/scripts/build_installer.ps1`, and `python/desktop/scripts/verify_installer.ps1` respectively (`pnpm run build` first is only needed to prepare the UI and CLI artifacts).

## Syncing with upstream

This repository mirrors the official project by design: upstream `deepseek-ai/deepseek-harness` changes are merged into this tree continuously, so the desktop build inherits every fix and feature. The bilingual pairing gates under `scripts/` keep the 中文 / English documentation consistent.

## Community and support

- Submit feedback or bug reports through [GitHub Discussions](https://github.com/deepseek-ai/deepseek-harness/discussions).
- Add the [`dsh-plugin`](https://github.com/topics/dsh-plugin) topic to your plugin repository for discoverability.
- Follow the official project for roadmap and updates: [DeepSeek AI](https://deepseek.com).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Development

Start with the [development guide](docs/development.md) and [architecture documentation](docs/architecture.md).

`pnpm run dev:web` builds, serves, and rebuilds client bundles on source edits in one terminal, and `make help` lists the matching Make targets for Web and Desktop; the guide's application commands section owns the full table.

For agents, follow [AGENTS.md](AGENTS.md).

## Citation

```bibtex
@misc{deepseek-harness2026,
  title={DeepSeek Harness: Everything is a Plugin},
  author={DeepSeek-AI},
  year={2026},
  publisher={GitHub},
  howpublished={\url{https://github.com/deepseek-ai/deepseek-harness}},
}
```

## License

[MIT](LICENSE)

Third-party dependencies and their licenses are disclosed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).