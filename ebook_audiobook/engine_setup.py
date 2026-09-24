"""Installing the speech engine from inside the app.

The PowerShell and shell installers install PyTorch and Chatterbox as one of
their own steps. The Windows setup.exe cannot: 2-5 GB is too much to put in
an installer, and which build a machine needs depends on its graphics card. So
the app does it, from a button in Settings, with the same decision the
installers make (:func:`torchbuild.select`, fed by the same ``nvidia-smi``
probe) and the same three pip commands (:func:`torchbuild.install_commands`),
run by the Python the app itself is running in.

It runs through the app's one worker, like a language-model download, so it
never competes with a render for the disk, and Quit knows to warn about it.
Downloads pip finished are kept in its cache, so a cancelled or failed
install picks up where it stopped.
"""

from __future__ import annotations

import collections
import importlib
import importlib.metadata
import os
import platform
import re
import subprocess
import sys
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from . import device, torchbuild

_DOWNLOADING = re.compile(r"^\s*Downloading (\S+)")
_PROGRESS = re.compile(r"^Progress (\d+) of (\d+)")


@dataclass
class Status:
    state: str = "idle"        # idle | running | done | error | cancelled
    build: str = ""            # the Build's label, e.g. "NVIDIA ... (CUDA 12.8)"
    step: int = 0              # which of the pip commands is running
    steps: int = 0
    file: str = ""             # what pip is downloading right now
    done_bytes: int = 0
    total_bytes: int = 0
    error: str = ""


_lock = threading.Lock()
_status = Status()
# Bumped when an install finishes, so a cached "the engine is missing" knows
# it is out of date (see web.app's /api/prereqs).
generation = 0


def status() -> dict:
    with _lock:
        return asdict(_status)


def mark_queued() -> None:
    """Report it as started the moment it is asked for, before the worker
    picks it up, so the page never shows the Install button a second time."""
    _update(state="running", build="", step=0, steps=0, file="",
            done_bytes=0, total_bytes=0, error="")


def _update(**fields) -> None:
    with _lock:
        for k, v in fields.items():
            setattr(_status, k, v)


def installed_torch() -> str | None:
    """The installed torch's version, read without importing it."""
    try:
        return importlib.metadata.version("torch")
    except importlib.metadata.PackageNotFoundError:
        return None


def _platform() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def probe_gpu() -> tuple[str, str, list[tuple[int, int]]]:
    """``(vendor, name, compute capabilities)``, the way the installers ask.

    nvidia-smi names the card and each GPU's compute capability, which decides
    between the CUDA 12.8 and 12.6 builds. Well-formed values only: a broken
    NVML prints its error to stdout. AMD counts on Linux alone, where ROCm is.
    """
    name, caps = "", []
    try:
        kwargs = {"capture_output": True, "text": True, "timeout": 20}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,compute_cap",
                              "--format=csv,noheader"], **kwargs)
        if out.returncode == 0:
            for line in out.stdout.splitlines():
                gpu, _, cap = line.rpartition(",")
                if gpu.strip() and not name:
                    name = gpu.strip()
                m = re.fullmatch(r"\s*(\d+)\.(\d+)\s*", cap)
                if m:
                    caps.append((int(m.group(1)), int(m.group(2))))
    except (OSError, subprocess.SubprocessError):
        pass
    if name:
        return "nvidia", name, caps
    if sys.platform.startswith("linux") and device.amd_gpu_in_sysfs():
        return "amd", "", []
    return "", "", []


def plan() -> torchbuild.Build:
    """The build this machine should get."""
    vendor, name, caps = probe_gpu()
    return torchbuild.select(_platform(), platform.machine().lower(), vendor, "",
                             name, caps or None)


def _python() -> str:
    """The Python to run pip with: this one, but python.exe rather than
    pythonw.exe, whose output would go nowhere."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").is_file():
        return str(exe.with_name("python.exe"))
    return str(exe)


def _command(pip_args: list[str]) -> list[str]:
    return [_python(), "-m", "pip", "--disable-pip-version-check", *pip_args,
            "--progress-bar", "raw",
            # Gigabytes, often over hotel or conference Wi-Fi: pip's defaults
            # give up on a 15-second stall.
            "--timeout", "60", "--retries", "10"]


class Cancelled(Exception):
    pass


def install(should_cancel: Callable[[], bool] | None = None,
            build: torchbuild.Build | None = None) -> None:
    """Install the engine, reporting through :func:`status`. Raises on failure."""
    global generation
    build = build or plan()
    commands = torchbuild.install_commands(build)
    _update(state="running", build=build.label, step=0, steps=len(commands), file="",
            done_bytes=0, total_bytes=0, error="")
    try:
        for step, pip_args in enumerate(commands, 1):
            _update(step=step, file="", done_bytes=0, total_bytes=0)
            _run(_command(pip_args), should_cancel)
    except Cancelled:
        _update(state="cancelled", file="")
        raise
    except Exception as e:  # noqa: BLE001 - reported, then re-raised for the error log
        _update(state="error", error=str(e), file="")
        raise
    importlib.invalidate_caches()  # the new packages, for this very process
    generation += 1
    _update(state="done", file="")


def _run(cmd: list[str], should_cancel: Callable[[], bool] | None) -> None:
    kwargs = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "text": True,
              "encoding": "utf-8", "errors": "replace", "bufsize": 1,
              "env": dict(os.environ, PYTHONIOENCODING="utf-8")}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    tail: collections.deque = collections.deque(maxlen=30)
    proc = subprocess.Popen(cmd, **kwargs)
    try:
        for line in proc.stdout:
            if should_cancel and should_cancel():
                proc.terminate()
                raise Cancelled()
            line = line.rstrip()
            m = _PROGRESS.match(line)
            if m:
                _update(done_bytes=int(m.group(1)), total_bytes=int(m.group(2)))
                continue
            tail.append(line)
            m = _DOWNLOADING.match(line)
            if m:
                _update(file=m.group(1), done_bytes=0, total_bytes=0)
        rc = proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
    if rc != 0:
        detail = "\n".join(l for l in tail if l.strip())[-1500:]
        hint = ""
        if "WinError 5" in detail or "Access is denied" in detail:
            # A compiled module the running app has loaded can't be replaced.
            hint = ("Windows would not let pip replace a file the app is using. "
                    "Quit the app, start it again and install before opening a book.\n\n")
        raise RuntimeError(f"{hint}pip exited with code {rc}:\n{detail}")
