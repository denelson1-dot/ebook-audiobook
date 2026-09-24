#!/usr/bin/env python3
"""Build the Windows installer.

    python packaging/windows/build.py --wheel dist/ebook_audiobook-X-py3-none-any.whl --out dist

Runs on Windows (CI's windows-latest runner). Three things go into the
installer, staged under packaging/windows/stage and packaged by
ebook-audiobook.iss:

``python/``
    A standalone CPython from python-build-standalone (the builds uv uses).
    Relocatable, so it works from wherever the installer puts it, and it has
    pip, which the in-app speech-engine install needs. The app and every
    dependency are installed into it here, so installing the app needs no
    network and no Python of the user's own; the 2-5 GB speech engine is the
    one thing the app fetches later.
``bin/ebook-audiobook.cmd``
    The command line, put on PATH by the installer.
``app.ico``
    The icon for the shortcuts and Add/Remove Programs.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

# Pinned by hash: a build must never pick up a Python nobody checked.
PYTHON_URL = ("https://github.com/astral-sh/python-build-standalone/releases/download/"
              "20260924/cpython-3.12.14+20260924-x86_64-pc-windows-msvc-install_only.tar.gz")
PYTHON_SHA256 = "c5303174bc29f5205decf6721ac549d4eb41c448f9b8c46cbc562d00348865bb"

CMD = """@echo off
rem The ebook-audiobook command line, on PATH. Runs the Python the installer
rem put next to it, whatever other Python is installed.
"%~dp0..\\python\\python.exe" -m ebook_audiobook %*
"""


def say(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def download(url: str, dest: Path, sha256: str) -> Path:
    if dest.is_file() and hashlib.sha256(dest.read_bytes()).hexdigest() == sha256:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    say(f"downloading {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
    if digest != sha256:
        tmp.unlink()
        raise SystemExit(f"checksum mismatch for {url}: got {digest}, expected {sha256}")
    tmp.replace(dest)
    return dest


def stage_python(stage: Path, cache: Path) -> Path:
    archive = download(PYTHON_URL, cache / PYTHON_URL.rsplit("/", 1)[1], PYTHON_SHA256)
    say("unpacking the standalone Python")
    with tarfile.open(archive) as tar:
        # Everything in the archive is under python/.
        tar.extractall(stage, filter="data")
    py = stage / "python" / "python.exe"
    if not py.is_file():
        raise SystemExit(f"no python.exe in {archive}")
    return py


def pip(py: Path, *args: str) -> None:
    subprocess.run([str(py), "-m", "pip", "--disable-pip-version-check", *args], check=True)


def install_app(py: Path, wheel: Path) -> None:
    say(f"installing {wheel.name}")
    pip(py, "install", "--upgrade", "pip")
    # --no-compile: compiled files would record this staging path, so a
    # traceback on the user's machine would point at files that don't exist.
    # Python compiles them there on first import instead; the install folder
    # is the user's own, so it can.
    pip(py, "install", "--no-compile", "--no-warn-script-location", str(wheel))
    # The speech engine is installed later by pip inside the running app, and
    # Windows won't let pip replace a compiled module the app has loaded. The
    # engine pins numpy (see torchbuild._numpy_requirement), so it gets that
    # numpy now, while nothing is running.
    numpy = subprocess.run(
        [str(py), "-c", "from ebook_audiobook.torchbuild import _numpy_requirement; "
                        "print(_numpy_requirement())"],
        check=True, capture_output=True, text=True).stdout.strip()
    pip(py, "install", "--no-compile", "--no-warn-script-location", numpy)


def drop_stale_launchers(stage: Path) -> None:
    """pip's .exe launchers carry the absolute path of the Python that made
    them, which is this staging folder. After installation they would point at
    nothing, so they go; the shortcuts and ebook-audiobook.cmd use
    ``python -m`` instead, and pip writes fresh ones when the engine installs."""
    for exe in (stage / "python" / "Scripts").glob("*.exe"):
        exe.unlink()


def version_of(wheel: Path) -> str:
    m = re.match(r"ebook_audiobook-([^-]+)-", wheel.name)
    if not m:
        raise SystemExit(f"can't read a version from {wheel.name}")
    return m.group(1)


def find_iscc() -> str:
    for candidate in (os.environ.get("ISCC"),
                      shutil.which("iscc"),
                      os.path.join(os.environ.get("ProgramFiles(x86)", ""), "Inno Setup 6", "ISCC.exe"),
                      os.path.join(os.environ.get("ProgramFiles", ""), "Inno Setup 6", "ISCC.exe"),
                      os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe")):
        if candidate and Path(candidate).is_file():
            return candidate
    raise SystemExit("Inno Setup 6 (ISCC.exe) not found; install it or set ISCC")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--wheel", required=True, type=Path)
    p.add_argument("--out", default=ROOT / "dist", type=Path)
    p.add_argument("--stage", default=HERE / "stage", type=Path)
    p.add_argument("--cache", default=HERE / "cache", type=Path)
    a = p.parse_args(argv)
    if sys.platform != "win32":
        raise SystemExit("build.py stages a Windows Python and runs Inno Setup: run it on Windows")

    wheel = a.wheel.resolve()
    version = version_of(wheel)
    stage = a.stage.resolve()
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)

    py = stage_python(stage, a.cache.resolve())
    install_app(py, wheel)
    drop_stale_launchers(stage)
    (stage / "bin").mkdir()
    (stage / "bin" / "ebook-audiobook.cmd").write_text(CMD.replace("\n", "\r\n"), encoding="ascii")
    shutil.copyfile(ROOT / "ebook_audiobook" / "assets" / "icon.ico", stage / "app.ico")

    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    say(f"packaging ebook-audiobook {version}")
    subprocess.run([find_iscc(), f"/DAppVersion={version}", f"/DStage={stage}",
                    f"/DRoot={ROOT}", f"/O{out}", str(HERE / "ebook-audiobook.iss")],
                   check=True)
    setup = out / f"ebook-audiobook-setup-{version}.exe"
    say(f"built {setup} ({setup.stat().st_size / 1e6:.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
