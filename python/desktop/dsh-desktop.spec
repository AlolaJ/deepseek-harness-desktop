# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec for the DeepSeek Harness desktop shell.
#
# Produces a windowed onedir bundle:
#   .build/dist/dsh-desktop/DeepSeekHarness.exe
#   .build/dist/dsh-desktop/_internal/           # python + pywebview/pythonnet
#   .build/dist/dsh-desktop/_internal/resources/ # transient: see below
#
# PyInstaller forces data files under _internal/, but the bundled runtime
# (node.exe + cli closure + icon.ico) MUST end up as SIBLINGS of the EXE
# (`{app}\node.exe`, `{app}\cli`, `{app}\icon.ico`) so installed paths stay
# far below MAX_PATH for Inno/ISCC. The spec maps them into `_internal/resources`
# (the only place PyInstaller will accept), then scripts/build_desktop.ps1
# moves them out to the bundle root after collection (Relayout-Resources).
#
# The bundled runtime comes from scripts/build_desktop.ps1 step 2 (closure
# deploy); the spec refuses to run until it exists.

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

# SPECPATH is set by PyInstaller to the directory containing this spec.
ROOT = Path(SPECPATH) if "SPECPATH" in globals() else Path(os.getcwd())
BUILD = ROOT / ".build"
RES = BUILD / "resources"

_MISSING = []
if not (RES / "cli").is_dir():
    _MISSING.append(f"{RES}/cli (backend closure)")
if not (RES / "node.exe").is_file():
    _MISSING.append(f"{RES}/node.exe (bundled node)")
if not (ROOT / "assets" / "icon.ico").is_file():
    _MISSING.append("assets/icon.ico")
if _MISSING:
    sys.exit("missing bundled artefacts: " + "; ".join(_MISSING))

# pywebview ships its own PyInstaller hook (`pyinstaller40` entry point →
# `hook-webview.py`) which places `webview/lib/` (WebView2 .NET assemblies:
# Microsoft.Web.WebView2.*.dll + runtimes/WebView2Loader.dll) at the right spot
# under _MEIPASS, so we do NOT collect_data_files('webview') again — that would
# duplicate the lib tree. pythonnet also ships an official hook (hook-clr.py)
# that is NOT auto-discovered → add it explicitly via hookspath.
_PYTHONNET_HOOKS = ROOT / ".venv" / "Lib" / "site-packages" / "pythonnet" / "_pyinstaller"
if not _PYTHONNET_HOOKS.is_dir():
    sys.exit(f"pythonnet hook dir missing at {_PYTHONNET_HOOKS} (check venv layout)")

datas = [(str(RES), "resources")]
binaries = []
hiddenimports = []

for pkg in ("webview", "clr_loader", "pythonnet", "cffi"):
    hiddenimports += collect_submodules(pkg)
datas += collect_data_files("clr_loader")
datas += collect_data_files("pythonnet")
binaries += collect_dynamic_libs("clr_loader")
binaries += collect_dynamic_libs("pythonnet")

# Explicitly collect our own package (belt-and-suspenders on top of the
# absolute import in __main__.py): if the entry import is ever made relative
# again, modulegraph silently drops it and the frozen EXE loses the whole
# package — pin it here so that regression can never break a build again.
hiddenimports += ["dsh_shell", "dsh_shell.app", "dsh_shell.backend", "dsh_shell.config", "dsh_shell.log", "dsh_shell.tray"]

a = Analysis(
    [str(ROOT / "dsh_shell" / "__main__.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(_PYTHONNET_HOOKS)],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PySide6", "numpy", "PIL"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DeepSeekHarness",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # windowed: double-click opens no console
    icon=str(ROOT / "assets" / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="dsh-desktop",
)