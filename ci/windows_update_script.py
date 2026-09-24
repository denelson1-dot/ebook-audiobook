"""Print the in-app update's PowerShell as ``-EncodedCommand`` takes it.

The same script ``update.start_windows_update`` launches when someone presses
"Install" on Windows, so CI can run it for real: wait for a process to exit,
run install.ps1 -Update, start the app again. Only the console window is left
out, since a runner has nobody to look at it.

Usage:
    python ci/windows_update_script.py --wait-for PID --installer PATH \\
        [--relaunch EXE] -- -Update -Version 1.5.0
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ebook_audiobook import update  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait-for", type=int, required=True)
    ap.add_argument("--installer", required=True)
    ap.add_argument("--relaunch", default="")
    ap.add_argument("installer_args", nargs="*")
    args = ap.parse_args()
    script = update.windows_update_script(
        wait_for=args.wait_for, installer=args.installer,
        installer_args=args.installer_args, relaunch=args.relaunch)
    print(update.encode_command(script))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
