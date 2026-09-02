"""Interface language: which one is in force, and how a string is looked up in it.

Standard-library ``gettext`` over the compiled catalogs in ``locale/``, with no
runtime dependency on Babel or Flask-Babel. Babel is a development tool here
(see ``tools/i18n.py``), used to extract strings and compile the ``.mo`` files
that this module reads.

Two things are deliberately separate:

* the **interface** language, which is what this module is about — the words on
  the pages, in the tray menu, in error replies; and
* the language a book is **narrated** in, which belongs to the book and the
  narrator, not to whoever is looking at the screen. Nothing here decides that.

How the language is chosen, in order: the ``EBAB_LANG`` environment variable,
then the saved setting, then whatever the browser asks for (or the desktop's
locale, for the tray), then English. The environment override exists for the
same reason ``EBAB_DATA_ROOT`` does — it pins the test suite, the CI smoke test
and a screenshot pass regardless of the machine they run on.

Nothing here imports Flask at module level, and nothing calls
``locale.setlocale``: that is process-global and thread-unsafe, and a render
thread must never change the language of the page a request is building.
"""

from __future__ import annotations

import gettext as _gettext
import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

LOCALE_DIR = Path(__file__).resolve().parent / "locale"
DOMAIN = "messages"
DEFAULT = "en"


@dataclass(frozen=True)
class Language:
    code: str                       # "fr"
    native: str                     # "Français" — shown untranslated, always
    # gettext plural *index* for a count, mirroring the Plural-Forms header of
    # the language's .po file. Not CLDR categories: French has "many" for a
    # million and up, which would index past a two-form msgstr[].
    plural: Callable[[int], int]
    decimal: str                    # "," for French
    thousands: str                  # U+202F narrow no-break space for French


SUPPORTED: dict[str, Language] = {
    "en": Language("en", "English", lambda n: int(n != 1), ".", ","),
    "fr": Language("fr", "Français", lambda n: int(n > 1), ",", " "),
}


# --- choosing the language --------------------------------------------------

def normalize(code: str | None) -> str:
    """``"fr-CA"`` -> ``"fr"``; anything unsupported -> ``""`` (meaning automatic)."""
    if not code:
        return ""
    primary = str(code).strip().replace("_", "-").split("-", 1)[0].lower()
    return primary if primary in SUPPORTED else ""


def env_language() -> str | None:
    return normalize(os.environ.get("EBAB_LANG")) or None


def detect_os_language() -> str | None:
    """The desktop's language, for the tray and anything else with no browser.

    Best effort on every platform, and never an error: the answer is only ever a
    default that Settings can override.
    """
    if sys.platform == "darwin":
        # A Finder launch has no LANG in its environment; ask the system.
        try:
            from Foundation import NSLocale  # type: ignore[import-not-found]

            preferred = NSLocale.preferredLanguages()
            if preferred:
                found = normalize(str(preferred[0]))
                if found:
                    return found
        except Exception:  # noqa: BLE001 - no pyobjc, or no window server
            pass
    if sys.platform == "win32":
        try:
            import ctypes

            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()  # type: ignore[attr-defined]
            found = _WINDOWS_PRIMARY_LANGUAGES.get(lang_id & 0xFF)
            if found:
                return found
        except Exception:  # noqa: BLE001
            pass
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        raw = os.environ.get(var)
        if raw:
            # LANGUAGE is a colon-separated preference list; the rest are
            # "fr_FR.UTF-8"-style.
            for candidate in raw.split(":"):
                found = normalize(candidate.split(".", 1)[0])
                if found:
                    return found
    return None


# Windows primary-language identifiers (the low byte of an LCID), for the
# languages we ship. Extend when a language is added.
_WINDOWS_PRIMARY_LANGUAGES = {0x09: "en", 0x0C: "fr"}


def resolve(setting: str | None, negotiated: str | None = None) -> str:
    """The language to use: environment, then the setting, then what the
    browser or desktop asked for, then English."""
    return env_language() or normalize(setting) or normalize(negotiated) or DEFAULT


_process_language: str | None = None


def set_process_language(lang: str | None) -> None:
    """The language for anything that runs outside a web request — the tray
    menu, the window title. ``serve()`` sets it once; the Settings page updates
    it. Nothing else should, and never at import time."""
    global _process_language
    _process_language = normalize(lang) or None


def current_language() -> str:
    """Inside a request, the request's language; otherwise the process's.

    The CLI never sets a process language, so it stays English without any
    special casing — the shared label tables it prints are the same ones the
    web UI translates.
    """
    try:
        from flask import g, has_request_context

        if has_request_context():
            lang = getattr(g, "lang", None)
            if lang:
                return lang
    except Exception:  # noqa: BLE001 - Flask not importable here; fine
        pass
    return _process_language or DEFAULT


# --- looking strings up ------------------------------------------------------

@lru_cache(maxsize=None)
def translations(lang: str) -> _gettext.NullTranslations:
    """The catalog for a language; a no-op catalog for English or a missing file."""
    if lang == DEFAULT or lang not in SUPPORTED:
        return _gettext.NullTranslations()
    return _gettext.translation(DOMAIN, str(LOCALE_DIR), languages=[lang], fallback=True)


def gettext(message: str, **params) -> str:
    """Translate, then format — always, as Jinja's newstyle gettext does.

    ``_("No folder at %(root)s.", root=path)`` in Python is the same call as
    ``_("No folder at %(root)s.", root=path)`` in a template and
    ``_("No folder at %(root)s.", {root: path})`` in JavaScript, and one rule
    covers all three: a literal percent sign is written ``%%``.
    """
    return translations(current_language()).gettext(message) % params


def ngettext(singular: str, plural: str, n: int, **params) -> str:
    params.setdefault("n", n)
    params.setdefault("num", n)
    return translations(current_language()).ngettext(singular, plural, n) % params


def pgettext(context: str, message: str, **params) -> str:
    return translations(current_language()).pgettext(context, message) % params


def npgettext(context: str, singular: str, plural: str, n: int, **params) -> str:
    params.setdefault("n", n)
    params.setdefault("num", n)
    return translations(current_language()).npgettext(context, singular, plural, n) % params


# Unformatted lookups, for Jinja: its newstyle gettext calls these with the
# message alone and does the %(name)s formatting itself.
def translate(message: str) -> str:
    return translations(current_language()).gettext(message)


def translate_plural(singular: str, plural: str, n: int) -> str:
    return translations(current_language()).ngettext(singular, plural, n)


def translate_context(context: str, message: str) -> str:
    return translations(current_language()).pgettext(context, message)


def translate_context_plural(context: str, singular: str, plural: str, n: int) -> str:
    return translations(current_language()).npgettext(context, singular, plural, n)


def N_(message: str) -> str:
    """Mark a string for extraction without translating it yet.

    For the shared label tables (``STAGE_LABELS`` and friends): the table keeps
    its English values so the CLI and any stored copy stay stable, and whoever
    renders a value calls ``_()`` on it at that moment, in that request's
    language.
    """
    return message


_ = gettext


@lru_cache(maxsize=None)
def js_catalog(lang: str) -> dict:
    """The catalog as the browser wants it: ``{msgid: text}`` for singular
    entries and ``{msgid: [form0, form1, …]}`` for plural ones. Empty for
    English, so the page carries nothing it does not need.

    Read from ``GNUTranslations._catalog``, which is private but has had the
    same shape for twenty years (Django's JavaScriptCatalog reads it too); the
    round-trip test in ``tests/test_i18n.py`` is what would notice a change.
    """
    raw = getattr(translations(lang), "_catalog", None) or {}
    out: dict = {}
    plural_forms: dict[str, dict[int, str]] = {}
    for key, value in raw.items():
        if isinstance(key, tuple):
            msgid, index = key
            plural_forms.setdefault(msgid, {})[index] = value
        elif key:  # "" is the header
            out[key] = value
    for msgid, forms in plural_forms.items():
        out[msgid] = [forms[i] for i in sorted(forms)]
    return out


def language_choices() -> list[dict]:
    """For the Settings select: code and native name, in a stable order."""
    return [{"code": code, "native": lang.native} for code, lang in SUPPORTED.items()]


# --- numbers, in the language's own habits -----------------------------------

def _lang(lang: str | None) -> Language:
    return SUPPORTED.get(lang or current_language(), SUPPORTED[DEFAULT])


def fmt_int(n: int, lang: str | None = None) -> str:
    """``1234`` -> ``"1,234"`` in English, ``"1 234"`` in French."""
    return f"{int(n):,}".replace(",", _lang(lang).thousands)


def fmt_number(x: float, digits: int, lang: str | None = None) -> str:
    """A decimal with the language's separators: ``"1.5"`` / ``"1,5"``."""
    language = _lang(lang)
    whole, _sep, frac = f"{float(x):,.{digits}f}".partition(".")
    whole = whole.replace(",", language.thousands)
    return f"{whole}{language.decimal}{frac}" if frac else whole


def human_bytes(n, lang: str | None = None) -> str:
    """``1.5 MB`` — or ``1,5 Mo``, which is what a French speaker reads."""
    if n is None:
        return "—"
    f = float(n)
    catalog = translations(lang or current_language())
    # NOTE: byte units — French says o, Ko, Mo, Go, To
    units = (N_("B"), N_("KB"), N_("MB"), N_("GB"), N_("TB"))
    for unit in units:
        if f < 1024 or unit == "TB":
            digits = 0 if unit == "B" else 1
            return f"{fmt_number(f, digits, lang)} {catalog.gettext(unit)}"
        f /= 1024
    return f"{fmt_number(f, 1, lang)} {catalog.gettext('TB')}"


def fmt_listening(output_bytes: int | None, kbps: int) -> str | None:
    """How long a finished audiobook plays, from the one thing we always have.

    The shelf shows this instead of a file size on its own: "8h 04m" is what
    somebody actually wants to know about an audiobook.
    """
    if not output_bytes or kbps <= 0:
        return None
    secs = output_bytes / (kbps * 125)  # kbps -> bytes per second
    h, m = int(secs // 3600), int(round((secs % 3600) / 60))
    if m == 60:
        h, m = h + 1, 0
    if h:
        # NOTE: hours and minutes of listening time, e.g. "8h 04m"
        return _("%(h)dh %(m)02dm", h=h, m=m)
    # NOTE: minutes of listening time, e.g. "12m"
    return _("%(m)dm", m=m)
