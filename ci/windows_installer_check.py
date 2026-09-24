"""Install, upgrade and uninstall the Windows installer, for real, on CI.

    python ci/windows_installer_check.py dist/ebook-audiobook-setup-X.exe

Every step a user would take, in order, checking what Windows ends up with
rather than what the script meant to do:

1. A copy from the PowerShell one-liner is faked first, with a settings file
   beside it: the installer must remove that program and keep the data.
2. Install silently: the Add/Remove Programs entry, the Start-menu shortcut,
   PATH, the command line, and the bundled Python.
3. Start the app the way the shortcut does and talk to it.
4. Upgrade over the running app: it must be closed, and the app's package
   replaced whole (a stale module planted in it must be gone).
5. Uninstall silently: every trace of the program gone, the data kept.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
import winreg
from pathlib import Path

APP_ID = "{C2B6260A-7FC8-4A3F-945D-47C4FFB734FE}_is1"
UNINSTALL_KEY = rf"Software\Microsoft\Windows\CurrentVersion\Uninstall\{APP_ID}"
LOCAL = Path(os.environ["LOCALAPPDATA"])
APP = LOCAL / "Programs" / "ebook-audiobook"
DATA = LOCAL / "ebook-audiobook"
START_MENU = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
SHORTCUT = START_MENU / "ebook-audiobook.lnk"


def check(ok: bool, what: str) -> None:
    print(("  ok    " if ok else "  FAIL  ") + what, flush=True)
    if not ok:
        raise SystemExit(f"installer check failed: {what}")


def user_path() -> list[str]:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
        try:
            value, _ = winreg.QueryValueEx(k, "Path")
        except FileNotFoundError:
            return []
    return [p for p in value.split(";") if p]


def set_user_path(parts: list[str]) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "Path", 0, winreg.REG_EXPAND_SZ, ";".join(parts))


def on_path(folder: Path) -> bool:
    return any(Path(p).resolve() == folder.resolve() for p in user_path() if p)


def uninstall_entry() -> dict | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as k:
            return {name: winreg.QueryValueEx(k, name)[0]
                    for name in ("DisplayName", "DisplayVersion", "UninstallString")}
    except FileNotFoundError:
        return None


def run_setup(setup: Path, log: str) -> None:
    rc = subprocess.run([str(setup), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                         f"/LOG={log}"], timeout=900).returncode
    if rc != 0:
        print(Path(log).read_text(errors="replace")[-4000:])
    check(rc == 0, f"setup exited 0 (got {rc})")


def app_python(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(APP / "python" / "python.exe"), "-m", "ebook_audiobook", *args],
                          capture_output=True, text=True, timeout=300)


def start_app() -> tuple[int, str]:
    """Start it as the shortcut does and wait until it answers."""
    runtime = DATA / "runtime.json"
    runtime.unlink(missing_ok=True)
    env = dict(os.environ, EBAB_NO_BROWSER="1")
    proc = subprocess.Popen([str(APP / "python" / "pythonw.exe"), "-m", "ebook_audiobook", "--gui"],
                            env=env, cwd=str(APP))
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if runtime.is_file():
            try:
                record = json.loads(runtime.read_text())
                url = f"http://127.0.0.1:{record['port']}"
                with urllib.request.urlopen(f"{url}/api/status", timeout=5) as r:
                    if json.loads(r.read()).get("app") == "ebook-audiobook":
                        return proc.pid, url
            except (OSError, ValueError, KeyError):
                pass
        time.sleep(1)
    check(False, "the installed app started and answered within two minutes")
    raise AssertionError  # unreachable


def uninstaller_running() -> bool:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq _unins.tmp", "/NH"],
                         capture_output=True, text=True).stdout
    return "_unins.tmp" in out.lower()


def running(pid: int) -> bool:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                         capture_output=True, text=True).stdout
    return str(pid) in out


def main(setup_path: str) -> int:
    setup = Path(setup_path).resolve()
    check(setup.is_file(), f"the installer exists: {setup.name} ({setup.stat().st_size / 1e6:.0f} MB)")

    print("1. a PowerShell-installed copy, with a book library beside it", flush=True)
    (DATA / "venv" / "Scripts").mkdir(parents=True, exist_ok=True)
    (DATA / "venv" / "Scripts" / "python.exe").write_bytes(b"not really python")
    (DATA / "bin").mkdir(exist_ok=True)
    (DATA / "bin" / "ebook-audiobook.cmd").write_text("@echo old")
    settings = DATA / "settings.json"
    settings.write_text('{"power_mode": "full", "left_by": "the installer check"}')
    set_user_path(user_path() + [str(DATA / "bin")])
    START_MENU.mkdir(parents=True, exist_ok=True)
    SHORTCUT.write_bytes(b"old shortcut")
    # Standing in for the old app: a venv's real Python runs from the base
    # install, with the venv only on its command line.
    old_app = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(900)",
                                str(DATA / "venv" / "Scripts" / "pythonw.exe")])

    print("2. install", flush=True)
    run_setup(setup, "install.log")
    check(not running(old_app.pid), "the old program's running copy was closed")
    check(not (DATA / "venv").exists(), "the old program's venv is gone")
    check(not (DATA / "bin").exists(), "the old program's bin folder is gone")
    check(not on_path(DATA / "bin"), "the old program is off PATH")
    check(settings.is_file() and "the installer check" in settings.read_text(),
          "the data folder was kept")
    entry = uninstall_entry()
    check(entry is not None and entry["DisplayName"] == "ebook-audiobook",
          f"Add/Remove Programs lists it: {entry}")
    check((APP / "python" / "pythonw.exe").is_file(), "the bundled Python is installed")
    check(SHORTCUT.is_file() and SHORTCUT.read_bytes() != b"old shortcut",
          "the Start-menu shortcut is the new one")
    check(on_path(APP / "bin"), "the command line is on PATH")
    r = app_python("paths")
    check(r.returncode == 0 and str(DATA).lower() in r.stdout.lower(),
          f"`python -m ebook_audiobook paths` names the data folder: {r.stdout.strip()[:200]} {r.stderr[-400:]}")
    r = subprocess.run(["cmd", "/c", str(APP / "bin" / "ebook-audiobook.cmd"), "paths"],
                       capture_output=True, text=True, timeout=300)
    check(r.returncode == 0, f"ebook-audiobook.cmd runs: {r.stderr[-400:]}")
    r = app_python("diagnose")
    check(r.returncode == 0, f"`diagnose` runs without the speech engine: {r.stderr[-400:]}")

    print("3. start it as the shortcut does", flush=True)
    pid, url = start_app()
    check(running(pid), f"the app is running at {url}")

    print("4. upgrade over the running app", flush=True)
    stale = APP / "python" / "Lib" / "site-packages" / "ebook_audiobook" / "stale_module.py"
    stale.write_text("raise RuntimeError('left over from an older version')")
    run_setup(setup, "upgrade.log")
    check(not running(pid), "the running app was closed for the upgrade")
    check(not stale.exists(), "the app's package was replaced whole")
    check(app_python("paths").returncode == 0, "the upgraded app runs")
    check(settings.is_file(), "the data folder survived the upgrade")
    pid, url = start_app()

    print("5. uninstall", flush=True)
    uninstaller = APP / "unins000.exe"
    check(uninstaller.is_file(), "the uninstaller is there")
    log = Path("uninstall.log").resolve()
    subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                    f"/LOG={log}"], timeout=300)
    # The uninstaller copies itself to %TEMP% (as _unins.tmp) and runs from
    # there, so the process above returns at once. Wait for that copy to exit:
    # its last step (PATH, the browser cache) runs after the files and the
    # Add/Remove Programs entry are already gone.
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline and (uninstall_entry() or (APP / "python").exists()
                                           or uninstaller_running()):
        time.sleep(2)
    if uninstall_entry() is not None:
        print(subprocess.run(["tasklist", "/V"], capture_output=True, text=True).stdout[-3000:])
        if log.is_file():
            print(log.read_text(errors="replace")[-6000:])
    check(uninstall_entry() is None, "Add/Remove Programs no longer lists it")
    check(not (APP / "python").exists(), "the bundled Python is gone")
    check(not SHORTCUT.exists(), "the Start-menu shortcut is gone")
    check(not on_path(APP / "bin"), f"the command line is off PATH: {user_path()}")
    check(not running(pid), "the running app was closed")
    check(settings.is_file(), "the data folder was kept (a silent uninstall never deletes it)")
    check(not (DATA / "browser-profile").exists(), "the app window's browser cache is gone")
    print("installer check passed", flush=True)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    raise SystemExit(main(sys.argv[1]))
