"""Persistent, cross-session app settings.

Unlike per-job state (under ``jobs/<id>/``) these are user-wide preferences that
outlive any single conversion — chiefly the **audiobooks library root**: the
folder Plex points at, into which finished books are filed as a Plex-compatible
tree. Stored as one small JSON file at the data root so it survives restarts and
is trivial to back up or wipe with the rest of ``local-data/``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import paths
from .jobs.store import _atomic_write


@dataclass
class Settings:
    # Absolute path to the audiobooks library root (Plex's Music/Audiobooks
    # library folder). None until the user has chosen one.
    audiobooks_root: str | None = None
    # True once the user has either set a root or explicitly skipped setup, so
    # the first-run prompt stops nagging on every page.
    setup_dismissed: bool = False
    # Default render intensity for new jobs: "full", "balanced" or "quiet".
    # See ebook_audiobook.power. A job may override it.
    power_mode: str = "full"
    # Play a preview as soon as it finishes rendering.
    #
    # On by default: generating one is an explicit request to hear something, and
    # making someone press play again after a thirty-second wait they already
    # asked for is a step with no purpose. Off for anyone who would rather
    # decide when it starts — a shared office, headphones not in yet.
    autoplay_preview: bool = True

    # Which narrator a newly imported book starts with. Empty means the shipped
    # default. Existing books are never touched by this — their voice is part of
    # their own settings, and changing it would re-render them.
    default_voice_id: str = ""

    # Where the app window was last time, as {"x","y","width","height"}.
    #
    # Reported by the page itself rather than read off the desktop: this is a
    # browser window we spawned, so asking it where it is works identically on
    # Windows, macOS and Linux and needs no window-manager tooling. Chromium does
    # keep its own record, but it does not reliably re-apply it to an --app
    # window, which is why a relaunch kept landing on the default.
    window_geometry: dict | None = None

    # Reclaim a book's working files the moment it finishes narrating.
    #
    # Off by default. Those files are several gigabytes a book and are useless
    # once you are happy with the result, but deleting anything the user did not
    # ask to have deleted is not this app's habit — so this is a choice they
    # make, offered at the moment they hear the finished book.
    auto_free_working_files: bool = False

    # Whether the UI may ask GitHub whether a newer release exists.
    #
    # Off by default, and deliberately so: the app's promise is that nothing
    # leaves this machine, and a version check is a request to a third party
    # carrying your IP address. Opting in is a choice the user makes, not a
    # default they discover. `ebook-audiobook update` always works regardless —
    # running it *is* the consent.
    check_for_updates: bool = False

    # Interface language: "fr", or "" to follow the browser (and, for the tray,
    # the desktop). Precedence lives in ebook_audiobook.i18n.resolve.
    language: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})


def _settings_path() -> Path:
    return paths().root / "settings.json"


def load_settings() -> Settings:
    p = _settings_path()
    if not p.exists():
        return Settings()
    try:
        return Settings.from_dict(json.loads(p.read_text("utf-8")))
    except (ValueError, OSError):
        return Settings()


def save_settings(settings: Settings) -> Settings:
    paths().ensure()
    _atomic_write(_settings_path(), json.dumps(settings.to_dict(), indent=2))
    return settings


def audiobooks_root() -> str | None:
    """The configured library root, or None if setup hasn't chosen one yet."""
    return load_settings().audiobooks_root


def default_power_mode() -> str:
    """The user's default render intensity, validated.

    Read through power.normalize_mode so a hand-edited or stale settings file
    can never put a render into a mode that doesn't exist.
    """
    from .power import normalize_mode

    return normalize_mode(load_settings().power_mode)
