"""Spoken-form text normalization.

This is the single biggest lever on perceived audiobook quality, and it is
deliberately deterministic (no ML) and small-transform-per-function so each
piece is unit-testable. Feeds into the segment content hash, so improving a rule
re-renders only the affected audio.

Order matters: strip artifacts -> fix unicode punctuation -> expand
abbreviations -> speak numbers -> collapse whitespace.
"""

from __future__ import annotations

import re

import inflect

_p = inflect.engine()

# --- 1. artifact removal -----------------------------------------------------

# Footnote/endnote markers: [12], superscript digits, and {3}. Conservative:
# only bracketed pure-digit runs, so "[chapter 3]" style prose is untouched.
_FOOTNOTE_BRACKET = re.compile(r"\[\d{1,3}\]|\{\d{1,3}\}")
_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0000000000")  # drop, not read
_SUPERSCRIPT_RUN = re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+")


def strip_artifacts(text: str) -> str:
    text = _FOOTNOTE_BRACKET.sub("", text)
    text = _SUPERSCRIPT_RUN.sub("", text)
    return text


# --- 2. unicode punctuation --------------------------------------------------

_PUNCT_MAP = {
    "“": '"', "”": '"',      # curly double quotes
    "‘": "'", "’": "'",      # curly single quotes / apostrophe
    "–": ", ", "—": ", ",    # en/em dash -> comma pause
    "…": "...",                    # ellipsis
    " ": " ",                      # non-breaking space
    "­": "",                       # soft hyphen
    "‐": "-", "‑": "-", "‒": "-",  # various hyphens
    "•": ", ",                     # bullet -> pause
    "°": " degrees ",
    "&": " and ",                  # ampersand
}
_PUNCT_RE = re.compile("|".join(re.escape(k) for k in _PUNCT_MAP))


def fix_punctuation(text: str) -> str:
    return _PUNCT_RE.sub(lambda m: _PUNCT_MAP[m.group(0)], text)


# --- 3. abbreviations --------------------------------------------------------

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
_TITLE_RES = [(re.compile(k), v) for k, v in _TITLES.items()]


def expand_abbreviations(text: str) -> str:
    for pat, repl in _TITLE_RES:
        text = pat.sub(repl, text)
    return text


# --- 4. numbers --------------------------------------------------------------

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


# --- 5. whitespace -----------------------------------------------------------

_MULTISPACE = re.compile(r"[ \t]+")
_MULTINEWLINE = re.compile(r"\n{2,}")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?])")


def collapse_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _MULTISPACE.sub(" ", text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _MULTINEWLINE.sub("\n\n", text)
    return text.strip()


# --- pipeline ----------------------------------------------------------------

def normalize_text(text: str) -> str:
    text = strip_artifacts(text)
    text = fix_punctuation(text)
    text = expand_abbreviations(text)
    text = speak_numbers(text)
    text = collapse_whitespace(text)
    return text


# --- pronunciation fixes (user-supplied) -------------------------------------

def apply_pronunciation(text: str, overrides: dict[str, str]) -> str:
    """Replace whole-word, case-sensitive tokens with a spoken spelling the TTS
    reads correctly — e.g. ``{"LOG": "log"}`` so Chatterbox says "log" instead of
    spelling out "el oh gee". Case sensitivity is deliberate: it lets a fix target
    the ALL-CAPS form ("LOG") without touching ordinary words ("log", "catalog").

    Applied at segment-build time, after normalization, so each fix is folded into
    the segment content hash: changing one re-renders only the affected segments.
    """
    if not overrides:
        return text
    for src, dst in overrides.items():
        src = (src or "").strip()
        if not src:
            continue
        # Lambda replacement -> dst is treated literally (no backslash/group refs).
        text = re.sub(rf"\b{re.escape(src)}\b", lambda _m, d=dst: d, text)
    return text


def normalize_title(title: str) -> str:
    """Chapter titles: speak roman numerals ('Chapter IV' -> 'Chapter 4') then
    run the standard number expansion."""
    title = _roman_in_headings(title)
    return normalize_text(title)


_ROMAN_RE = re.compile(r"\b(?=[MDCLXVI])(M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3}))\b")


def _roman_to_int(s: str) -> int:
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total, prev = 0, 0
    for ch in reversed(s.upper()):
        v = vals[ch]
        total += -v if v < prev else v
        prev = max(prev, v)
    return total


def _roman_in_headings(title: str) -> str:
    def repl(m: re.Match) -> str:
        s = m.group(0)
        if not s:
            return s
        # Skip single 'I'/'V'/... that are more likely the pronoun/letter.
        if len(s) == 1 and s.upper() in {"I", "V", "X"}:
            return s
        return str(_roman_to_int(s))

    return _ROMAN_RE.sub(repl, title)
