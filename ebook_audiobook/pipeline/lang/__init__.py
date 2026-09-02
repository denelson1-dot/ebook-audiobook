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
    strings: dict[str, str] = field(default_factory=dict)


def rules_for(lang: str | None) -> Rules:
    """The rules for a language code; English for anything unknown."""
    from . import en

    if lang == "fr":
        from . import fr

        return fr.RULES
    return en.RULES
