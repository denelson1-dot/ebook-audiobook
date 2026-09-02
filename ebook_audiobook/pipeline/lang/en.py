"""English: the rules the app has always applied, moved here unchanged.

``tests/data/golden_en.json`` holds the output these produced before they
moved; the segment hashes of every existing render depend on it staying so.
"""

from __future__ import annotations

import re

import inflect

from . import Rules

_p = inflect.engine()

PUNCT_MAP = {
    "“": '"', "”": '"',      # curly double quotes
    "‘": "'", "’": "'",      # curly single quotes / apostrophe
    "–": ", ", "—": ", ",    # en/em dash -> comma pause
    "…": "...",                    # ellipsis
    " ": " ",                      # non-breaking space
    "­": "",                       # soft hyphen
    "‐": "-", "‑": "-", "‒": "-",  # various hyphens
    "•": ", ",                     # bullet -> pause
    "°": " degrees ",
    "&": " and ",                  # ampersand
}

# Conservative title expansions where the spoken form is unambiguous.
_TITLES = {
    r"\bMr\.": "Mister",
    r"\bMrs\.": "Missus",
    r"\bMs\.": "Miss",
    r"\bDr\.": "Doctor",
    r"\bProf\.": "Professor",
    r"\bSt\.": "Saint",
    r"\bMt\.": "Mount",
    r"\bvs\.": "versus",
    r"\be\.g\.": "for example",
    r"\bi\.e\.": "that is",
    r"\betc\.": "et cetera",
}
ABBREVIATIONS = [(re.compile(k), v) for k, v in _TITLES.items()]

_YEAR = re.compile(r"(?<!\d)(1[0-9]{3}|20[0-9]{2})(?!\d)")
_ORDINAL = re.compile(r"\b(\d+)(st|nd|rd|th)\b", re.IGNORECASE)
_CURRENCY = re.compile(r"\$(\d[\d,]*)(\.\d{1,2})?")
_PERCENT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*%")
_INTEGER = re.compile(r"(?<![\w.])(\d[\d,]*)(?![\w.])")


def _say_year(m: re.Match) -> str:
    y = int(m.group(1))
    # 2000-2009 read naturally as "two thousand and N"; others as pairs.
    if 2000 <= y <= 2009:
        return _p.number_to_words(y, andword="")
    hi, lo = divmod(y, 100)
    if lo == 0:
        return _p.number_to_words(hi, andword="") + " hundred"
    lo_words = _p.number_to_words(lo, andword="") if lo >= 10 else "oh " + _p.number_to_words(lo, andword="")
    return f"{_p.number_to_words(hi, andword='')} {lo_words}"


def _say_currency(m: re.Match) -> str:
    dollars = int(m.group(1).replace(",", ""))
    cents = m.group(2)
    words = _p.number_to_words(dollars, andword="") + (" dollar" if dollars == 1 else " dollars")
    if cents:
        c = int(cents[1:].ljust(2, "0"))
        if c:
            words += " and " + _p.number_to_words(c, andword="") + (" cent" if c == 1 else " cents")
    return words


def speak_numbers(text: str) -> str:
    text = _CURRENCY.sub(_say_currency, text)
    text = _PERCENT.sub(lambda m: _p.number_to_words(m.group(1).replace(",", ""), andword="") + " percent", text)
    text = _YEAR.sub(_say_year, text)
    text = _ORDINAL.sub(lambda m: _p.ordinal(_p.number_to_words(int(m.group(1)), andword="")), text)
    text = _INTEGER.sub(lambda m: _p.number_to_words(m.group(1).replace(",", ""), andword=""), text)
    return text


# Title fragments that mark front/back matter a listener usually wants to skip.
# Matched case-insensitively as whole-ish phrases against the normalized title.
SKIP_TITLE_HINTS = (
    "copyright", "isbn", "all rights reserved", "title page", "half title",
    "table of contents", "contents", "colophon", "dedication",
    "acknowledgement", "acknowledgment", "about the author", "about the publisher",
    "also by", "praise for", "advance praise", "other books", "other titles",
    "by the same author", "more from", "more by",
    "index", "bibliography", "notes", "footnotes", "endnotes", "glossary",
    "newsletter", "sign up", "sign-up", "imprint", "frontispiece", "epigraph",
    # Back-matter promos: "What's next on your reading list?", "Up next", etc.
    "reading list", "what's next", "whats next", "up next", "keep reading",
    "recommended reading",
)

RULES = Rules(
    code="en",
    punct_map=PUNCT_MAP,
    abbreviations=ABBREVIATIONS,
    speak_numbers=speak_numbers,
    skip_title_hints=SKIP_TITLE_HINTS,
    strings={
        "chapter_n": "Chapter %(n)s",
        "by_author": "By %(author)s.",
        "concludes": "This concludes %(subject)s",
        "by_author_tail": ", by %(author)s.",
        "this_book": "this book",
        "the_end": "The End",
    },
)
