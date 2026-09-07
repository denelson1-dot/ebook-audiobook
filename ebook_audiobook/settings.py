"""Persistent, cross-session app settings.

Unlike per-job state (under ``jobs/<id>/``) these are user-wide preferences that
outlive any single conversion — chiefly the **audiobooks library root**: the
folder Plex points at, into which finished books are filed as a Plex-compatible
tree. Stored as one small JSON file at the data root so it survives restarts and
is trivial to back up or wipe with the rest of ``local-data/``.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
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
    #
    # "full" here is the answer for a settings.json written before this key
    # existed, not the answer for a new machine — those installs have been
    # rendering at full speed all along and an upgrade must not quietly slow
    # them down. A genuinely fresh install starts on power.NEW_INSTALL_MODE
    # instead; load_settings() is the one place that knows the difference.
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
    #
    # Kept as the English entry of default_voice_ids below, and still written so
    # that an older build reading this file finds the default it expects. Note
    # the reverse leg does not hold: an older build rewrites the whole file
    # without the per-language key, so a downgrade-then-upgrade keeps the
    # English default and loses the others.
    default_voice_id: str = ""

    # The same choice, per narration language: {"en": "male-british", ...}.
    #
    # One global default cannot work once more than one language ships. Picking
    # a Spanish narrator for new books would otherwise hand that voice to the
    # next English book too — a voice that cannot speak its language, chosen by
    # nobody. from_dict migrates the single old value in as the English entry.
    default_voice_ids: dict = field(default_factory=dict)

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

    # The version this machine has already been told about and said "not now"
    # to — so the update banner doesn't nag about the same release on every
    # page for the rest of its life. Cleared implicitly the moment a newer tag
    # is published, since that no longer matches the latest release.
    updates_dismissed_version: str = ""

    # Which palette: "classic" (the original warm, amber-accented theme) or
    # "modern" (a cooler, neutral pair closer to what most 2026 desktop apps
    # ship). Two independent axes — see color_mode for light/dark — so either
    # can change without the other. Defaults to "classic" so an existing
    # install never re-skins itself out from under someone — also, not
    # coincidentally, the pre-selected answer the first-run onboarding modal
    # itself shows (see web/app.py), so "not chosen yet" reads the same way
    # whether you're looking at this fallback or at the modal.
    color_scheme: str = "classic"

    # "system" (follow the OS, the historical and still-default behaviour),
    # "light" or "dark" — an explicit override of prefers-color-scheme.
    color_mode: str = "system"

    # Has this machine been through the first-run onboarding modal (language,
    # automatic updates, appearance) yet? Defaults to True deliberately — see
    # load_settings(). The point is to show the modal exactly once, to a
    # person who has never touched Settings at all, never to someone upgrading
    # from a version that predates it.
    preferences_onboarded: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        s = cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})
        if not isinstance(s.default_voice_ids, dict):
            s.default_voice_ids = {}
        # A settings file written before narrators were per-language carries a
        # single default_voice_id that applied to every book. It belongs under
        # the language that voice actually speaks — filing it under "en"
        # regardless meant a user whose default was a French narrator silently
        # stopped getting it. Resolved lazily, because voices.py imports this
        # module and the library is not readable at import time.
        if s.default_voice_id and not s.default_voice_ids:
            s.default_voice_ids = {"": s.default_voice_id}
        return s

    def default_voice_for(self, language: str) -> str:
        """This machine's chosen narrator for a language, or "" for none.

        The empty key is the pre-per-language default, which applied to every
        book; it answers for any language until a real per-language choice
        replaces it. voices.default_voice_id still checks that the voice speaks
        the language asked for, so this cannot hand a Spanish book an English
        narrator — it only stops an upgrade from silently discarding a choice.
        """
        ids = self.default_voice_ids or {}
        return ids.get(language or "en") or ids.get("", "")

    def set_default_voice(self, language: str, voice_id: str) -> None:
        ids = dict(self.default_voice_ids or {})
        ids[language or "en"] = voice_id
        self.default_voice_ids = ids
        # Mirrored so an older build still finds a default it understands.
        self.default_voice_id = ids.get("en", "")


def _settings_path() -> Path:
    return paths().root / "settings.json"


def load_settings() -> Settings:
    """The current settings — or, the first time this ever runs on a machine,
    the defaults as a new install wants them.

    Two fields differ between "new machine" and "old settings file missing a
    key", and the dataclass can only carry one answer, so it carries the
    upgrade-safe one and this function overrides for the fresh case:

    ``preferences_onboarded`` defaults to True, so that reading an *existing*
    settings.json written before onboarding existed — which simply lacks the
    key — fills it in as "already done" and never nags someone who has been
    using the app for months.

    ``power_mode`` defaults to "full" for the same reason: the key postdates
    settings.json, and a machine that has been rendering at full speed since
    before render intensity existed must keep doing so across an upgrade. A new
    install has no such history and starts on power.NEW_INSTALL_MODE.

    Only the genuine absence of a settings file at all (a fresh install, or a
    corrupt one being reset to defaults) counts as new, so those are the two
    places that override.
    """
    from .power import NEW_INSTALL_MODE
    p = _settings_path()
    if not p.exists():
        return Settings(preferences_onboarded=False, power_mode=NEW_INSTALL_MODE)
    try:
        loaded = json.loads(p.read_text("utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("settings.json is not an object")
        return Settings.from_dict(loaded)
    except (ValueError, TypeError, OSError):
        # Unreadable. Falling back to defaults is right — the app has to start —
        # but the next save would write those defaults over whatever is there,
        # and the first-run modal would present the loss as a fresh install. So
        # the file is moved aside first: nothing is destroyed, and there is
        # something to hand back if someone asks what happened to their library
        # folder. Best effort; a read-only data dir must not stop the app.
        try:
            p.replace(p.with_suffix(".corrupt.json"))
        except OSError:
            pass
        return Settings(preferences_onboarded=False, power_mode=NEW_INSTALL_MODE)


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


COLOR_SCHEMES = ("classic", "modern")
COLOR_MODES = ("system", "light", "dark")


def normalize_color_scheme(value: str | None) -> str:
    """A hand-edited or outdated settings file must never hand the page a
    palette name its CSS doesn't define."""
    return value if value in COLOR_SCHEMES else "classic"


def normalize_color_mode(value: str | None) -> str:
    return value if value in COLOR_MODES else "system"
