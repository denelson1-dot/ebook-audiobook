"""Checking for, and installing, a newer release.

This is the one part of the app that talks to the network for its own sake, so
it is the one part that has to be careful about it. The promise on the tin is
that nothing leaves your machine; a background version check would quietly make
that untrue, and a version check is a ping to GitHub carrying your IP and rough
usage pattern.

So: **nothing here ever runs on its own.** There is no timer and no start-up
poll. A check happens when someone runs ``ebook-audiobook update``, or presses
the button in Settings after opting in (``check_for_updates``, off by default).
:func:`check` is the only function that opens a socket, and it is never called
from import time or from a request handler that the user did not trigger.

Applying an update re-runs the official installer, which is the same code path a
new user gets — so an upgrade is never a second, less-tested install route.
"""

from __future__ import annotations

from .i18n import _
import json
import platform
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

REPO = "denelson1-dot/ebook-audiobook"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
INSTALL_SH = f"https://github.com/{REPO}/releases/latest/download/install-macos-linux.sh"
INSTALL_PS1 = f"https://github.com/{REPO}/releases/latest/download/install-windows.ps1"

TIMEOUT_SECONDS = 10


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Release:
    version: str
    tag: str
    url: str

    @property
    def notes_url(self) -> str:
        return self.url or RELEASES_PAGE


def parse_version(text: str) -> tuple[int, ...]:
    """``"v1.2.3"`` -> ``(1, 2, 3)``.

    Trailing suffixes (``1.2.3.dev0``, ``1.2.3+local``) are cut at the first
    non-numeric part, so a locally-built copy compares as its base version
    rather than sorting unpredictably.
    """
    cleaned = (text or "").strip().lstrip("vV")
    parts: list[int] = []
    for chunk in re.split(r"[.\-+]", cleaned):
        if chunk.isdigit():
            parts.append(int(chunk))
        else:
            break
    return tuple(parts) or (0,)


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def current_version() -> str:
    from . import __version__

    return __version__


def check(timeout: float = TIMEOUT_SECONDS) -> Release:
    """Ask GitHub for the latest release. Opens a network connection.

    Only ever called in response to something the user did.
    """
    req = urllib.request.Request(
        LATEST_API,
        headers={
            "Accept": "application/vnd.github+json",
            # GitHub rejects requests without one, and an honest agent is
            # better than pretending to be a browser.
            "User-Agent": f"ebook-audiobook/{current_version()}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=ssl.create_default_context()) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError(_("No published releases yet.")) from e
        if e.code in (403, 429):
            raise UpdateError(_("GitHub rate-limited the version check. Try again later, or see %(url)s", url=RELEASES_PAGE)) from e
        raise UpdateError(_("GitHub returned HTTP %(code)s for the version check.", code=e.code)) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError(_("Couldn't reach GitHub to check for updates (%(e)s). "
                            "You're offline, or a firewall is in the way.", e=e)) from e
    except ValueError as e:
        raise UpdateError(_("GitHub's reply wasn't valid JSON.")) from e

    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        raise UpdateError(_("GitHub's reply had no release tag."))
    return Release(version=tag.lstrip("vV"), tag=tag,
                   url=str(payload.get("html_url") or RELEASES_PAGE))


def install_command() -> str:
    """The command that upgrades this machine, for showing to the user."""
    if sys.platform.startswith("win"):
        return f'irm {INSTALL_PS1} | iex'
    return f"curl -fsSL {INSTALL_SH} | bash"


def apply_update(yes: bool = False, timeout: float = 3_600) -> int:
    """Download and run the official installer, in place.

    Deliberately the same script a new user runs: an upgrade path that isn't the
    install path is an upgrade path nobody tests. Returns the installer's exit
    code. Requires curl (macOS/Linux) or PowerShell (Windows), both of which
    were needed to install in the first place.
    """
    if sys.platform.startswith("win"):
        args = ["-Yes"] if yes else []
        script = (
            f"$ErrorActionPreference='Stop'; "
            f"& ([scriptblock]::Create((irm {INSTALL_PS1}))) {' '.join(args)}"
        )
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
    else:
        flags = " -- --yes" if yes else ""
        cmd = ["bash", "-c", f"curl -fsSL {INSTALL_SH} | bash{flags}"]

    try:
        # Inherits stdout/stderr on purpose: the installer's progress is the
        # only feedback during a multi-gigabyte download.
        return subprocess.run(cmd, timeout=timeout).returncode
    except FileNotFoundError as e:
        raise UpdateError(
            "Couldn't find the tool needed to run the installer "
            f"({'PowerShell' if sys.platform.startswith('win') else 'bash/curl'})."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise UpdateError("The installer took too long and was stopped.") from e


def status(timeout: float = TIMEOUT_SECONDS) -> tuple[bool, Release | None, str]:
    """``(update_available, release, human_message)``. Opens a connection."""
    current = current_version()
    try:
        latest = check(timeout=timeout)
    except UpdateError as e:
        return False, None, str(e)
    if is_newer(latest.version, current):
        return True, latest, (
            _("%(latest)s is available (you have %(current)s).", latest=latest.version, current=current))
    if is_newer(current, latest.version):
        # A source checkout mid-release, or a locally-built wheel. Claiming
        # "you're on the latest" would be a lie in the one situation where the
        # person reading it is most likely to be checking something specific.
        return False, latest, (
            _("You're on %(current)s, ahead of the latest release (%(latest)s) — an unreleased build.", current=current, latest=latest.version))
    return False, latest, _("You're on the latest version (%(current)s).", current=current)


def platform_hint() -> str:
    """A short description of what this machine would install, for `check`."""
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin":
        if machine in ("arm64", "aarch64"):
            return "macOS on Apple Silicon — PyTorch with Metal (MPS) acceleration"
        return "macOS on Intel — CPU only (PyTorch stopped building for Intel Macs)"
    if system == "Windows":
        return "Windows — CUDA build if an NVIDIA GPU is present, otherwise CPU"
    return "Linux — CUDA or ROCm build if a supported GPU is present, otherwise CPU"
