"""What changes in the text pipeline from one narration language to the next.

A ``Rules`` bundle per language: how punctuation is spoken, which abbreviations
are expanded, how numbers are read, which section titles mark front and back
matter, and the few sentences the app itself narrates (the fallback chapter
title, the opening and closing announcements). Everything else in
``normalize.py`` and ``chunk.py`` is shared.

English is the reference: its rules are the ones the app has always applied,
moved here unchanged, and a golden test holds them to that. A new language is
one more module in this package.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class Rules:
    code: str
    # Unicode punctuation -> what the narrator should see. Matched longest
    # key first, so "« " and "«" can both be listed.
    punct_map: dict[str, str]
    # (compiled pattern, replacement) pairs, applied in order.
    abbreviations: list[tuple[re.Pattern, str]]
    # Digits, currency, percentages, ordinals -> words.
    speak_numbers: Callable[[str], str]
    # Lowercase fragments of section titles that mark front or back matter —
    # copyright pages, tables of contents, acknowledgements — off by default.
    skip_title_hints: tuple[str, ...]
    # The sentences the app narrates itself, with %(name)s placeholders:
    #   chapter_n        a section with no title of its own
    #   by_author        the opening announcement's body
    #   concludes        the closing announcement, before the author
    #   by_author_tail   its ending when there is an author
    #   this_book        what "concludes" says when the title is unknown
    #   the_end          the closing section's display-only marker
    #   voice_sample     the sentence the Voices page auditions a narrator with
    strings: dict[str, str] = field(default_factory=dict)

    # --- how this language's sentences are cut up -----------------------------
    #
    # All five default to None/" ", which is exactly what chunk.py did before
    # any of this existed, so English and French are untouched. They exist for
    # languages that do not put spaces between words: for those, the shared
    # Latin patterns match nothing at all, a whole paragraph arrives as one
    # "sentence", and the hard-wrapper — which splits on whitespace — slices it
    # at arbitrary character positions, mid-word.
    #
    # None means "use the shared Latin pattern / the global budget".
    sentence_end: re.Pattern | None = None
    clause: re.Pattern | None = None
    # What goes between two units packed into one chunk. "" for scripts with no
    # word spacing, where a joining space would be a visible error.
    joiner: str = " "
    # Per-language chunk budgets, in characters. A character carries far more
    # speech in a logographic script than in a Latin one, so a budget tuned for
    # English produces over-long utterances — precisely what the engine degrades
    # on. None means config.CHUNK_TARGET_CHARS / CHUNK_MAX_CHARS.
    chunk_target_chars: int | None = None
    chunk_max_chars: int | None = None


def keep_on_error(fn):
    """Wrap a re.sub callback so it leaves the text alone instead of raising.

    ``inflect`` and ``num2words`` both have hard range limits — 37 digits for
    English, 28 for Spanish, 52 for Japanese — and raise past them. A digit run
    that long is not a number anyone wants read aloud, but it does occur: OCR
    noise, an identifier, a hash in a technical book. Unguarded it propagates
    out of ``normalize_text`` and aborts the whole extraction, so one such run
    anywhere makes a book unimportable.

    Per callback rather than per document, so one impossible number leaves the
    rest of the page's numbers spoken.
    """
    from functools import wraps

    @wraps(fn)
    def guarded(m):
        try:
            return fn(m)
        except Exception:  # noqa: BLE001 - any library limit, not our business
            return m.group(0)

    return guarded


# Languages with a rules module of their own. Everything else — including the
# narration languages the engine speaks but we have written no rules for — falls
# back to English, which is why this maps to a module name rather than gating on
# membership somewhere else.
_MODULES = {"en": "en", "fr": "fr", "es": "es", "ja": "ja"}


def rules_for(lang: str | None) -> Rules:
    """The rules for a language code; English for anything unknown."""
    from importlib import import_module

    name = _MODULES.get(lang or "", "en")
    return import_module(f"{__name__}.{name}").RULES
