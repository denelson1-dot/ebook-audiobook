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
