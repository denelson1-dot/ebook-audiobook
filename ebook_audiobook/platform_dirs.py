"""Where this app is allowed to put things on each operating system.

Deliberately dependency-free (no platformdirs) — the rules are short and we want
them auditable, because getting them wrong means a user's library ends up
somewhere they can't find or that gets wiped on upgrade.

Conventions followed:

===========  ===============================================================
Windows      ``%LOCALAPPDATA%\\ebook-audiobook``
macOS        ``~/Library/Application Support/ebook-audiobook``
Linux/BSD    ``$XDG_DATA_HOME/ebook-audiobook`` (default ``~/.local/share/…``)
===========  ===============================================================

These are *data* locations, not cache: the user's imported books, rendered
audio, voice clips, and settings live here and must survive an app upgrade or
reinstall.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "ebook-audiobook"


def _windows_local_appdata() -> Path:
    # LOCALAPPDATA is per-machine (not roamed), which is right for a data
    # directory that can hold many GB of audio.
    raw = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if raw:
        return Path(raw)
    return Path.home() / "AppData" / "Local"


def user_data_dir(app_name: str = APP_NAME) -> Path:
    """The per-user directory this app owns for persistent data."""
    if sys.platform == "win32":
        return _windows_local_appdata() / app_name
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / app_name


def user_bin_dir() -> Path:
    """Where a user-scoped launcher command should be installed.

    Only meaningful on macOS/Linux; the Windows installer writes a ``.cmd`` shim
    into the app directory and adds it to the user PATH instead.
    """
    return Path.home() / ".local" / "bin"


def user_desktop_dir() -> Path | None:
    """Best-effort location of the user's Desktop, or None if it isn't obvious.

    Used only to drop an optional launcher shortcut, so an unknown layout (a
    localized folder name, a headless box) simply means no shortcut.
    """
    candidates = [Path.home() / "Desktop"]
    xdg_desktop = os.environ.get("XDG_DESKTOP_DIR")
    if xdg_desktop:
        candidates.insert(0, Path(xdg_desktop))
    for c in candidates:
        if c.is_dir():
            return c
    return None
