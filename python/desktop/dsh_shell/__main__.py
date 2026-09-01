"""Entry point: `python -m dsh_shell`.

Command-line overrides mirror the DSH_DESKTOP_* env vars (--node=node exe,
--cli-dir=CLI dir, --data-dir=data dir).
"""

from __future__ import annotations

import argparse
import os

# NOTE: absolute import, NOT `from .app import run`. PyInstaller bundles the app
# via static analysis; when __main__.py is treated as a top-level script (which
# it always is in the frozen EXE), a relative import in it is INVISIBLE to
# modulegraph — `dsh_shell.app` (and the whole package) never gets collected and
# the frozen EXE fails with `No module named 'dsh_shell'`. An absolute import is
# unambiguous and pulls in the package + its transitive imports automatically.
from dsh_shell.app import run


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="dsh_shell",
        description="DeepSeek Harness desktop shell — embeds the dsh web UI in a WebView2 window.",
    )
    parser.add_argument("--node", dest="node", help="node executable (default: DSH_DESKTOP_NODE or node on PATH)")
    parser.add_argument(
        "--cli-dir",
        dest="cli_dir",
        help="directory holding the dsh CLI (lib/bin.js); default: <repo>/apps/cli",
    )
    parser.add_argument(
        "--data-dir",
        dest="data_dir",
        help="writable data directory (backend cwd + WebView2 cache); default: %%LOCALAPPDATA%%/DeepSeekHarness",
    )
    args = parser.parse_args()

    if args.node:
        os.environ["DSH_DESKTOP_NODE"] = args.node
    if args.cli_dir:
        os.environ["DSH_DESKTOP_CLI_DIR"] = args.cli_dir
    if args.data_dir:
        os.environ["DSH_DESKTOP_DATA_DIR"] = args.data_dir

    raise SystemExit(run())


main()