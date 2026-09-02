"""Spoken-form text normalization.

This is the single biggest lever on perceived audiobook quality, and it is
deliberately deterministic (no ML) and small-transform-per-function so each
piece is unit-testable. Feeds into the segment content hash, so improving a rule
re-renders only the affected audio.

Order matters: strip artifacts -> fix unicode punctuation -> expand
abbreviations -> speak numbers -> collapse whitespace.

Each step takes the narration language. What differs per language — the
punctuation map, the abbreviations, how numbers are read — lives in
``pipeline/lang/<code>.py``; the steps themselves, and their order, are shared.
English is the default everywhere, and behaves exactly as it did before the
language existed (``tests/data/golden_en.json`` holds it to that).
"""

from __future__ import annotations

import re
from functools import lru_cache

from .lang import Rules, rules_for

# --- 1. artifact removal -----------------------------------------------------

# Footnote/endnote markers: [12], superscript digits, and {3}. Conservative:
# only bracketed pure-digit runs, so "[chapter 3]" style prose is untouched.
_FOOTNOTE_BRACKET = re.compile(r"\[\d{1,3}\]|\{\d{1,3}\}")
_SUPERSCRIPT_RUN = re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+")


def strip_artifacts(text: str) -> str:
    text = _FOOTNOTE_BRACKET.sub("", text)
    text = _SUPERSCRIPT_RUN.sub("", text)
    return text


# --- 2. unicode punctuation --------------------------------------------------

@lru_cache(maxsize=None)
def _punct_re(lang: str) -> re.Pattern:
    keys = sorted(rules_for(lang).punct_map, key=len, reverse=True)
    return re.compile("|".join(re.escape(k) for k in keys))


def fix_punctuation(text: str, lang: str = "en") -> str:
    table = rules_for(lang).punct_map
    return _punct_re(lang).sub(lambda m: table[m.group(0)], text)


# --- 3. abbreviations --------------------------------------------------------

def expand_abbreviations(text: str, lang: str = "en") -> str:
    for pat, repl in rules_for(lang).abbreviations:
        text = pat.sub(repl, text)
    return text


# --- 4. numbers --------------------------------------------------------------

def speak_numbers(text: str, lang: str = "en") -> str:
    return rules_for(lang).speak_numbers(text)


# --- 5. whitespace -----------------------------------------------------------

_MULTISPACE = re.compile(r"[ \t]+")
_MULTINEWLINE = re.compile(r"\n{2,}")
# Also the French no-break space before ; : ! ?, which fix_punctuation has
# turned into a plain space by now: a narrator wants "Bonjour!", and the
# sentence splitter needs the mark against the word.
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?])")


def collapse_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _MULTISPACE.sub(" ", text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _MULTINEWLINE.sub("\n\n", text)
    return text.strip()


# --- pipeline ----------------------------------------------------------------

def normalize_text(text: str, lang: str = "en") -> str:
    text = strip_artifacts(text)
    text = fix_punctuation(text, lang)
    text = expand_abbreviations(text, lang)
    text = speak_numbers(text, lang)
    text = collapse_whitespace(text)
    return text


def rules(lang: str = "en") -> Rules:
    """The language's rule bundle, for callers that need the strings or hints."""
    return rules_for(lang)


# --- pronunciation fixes (user-supplied) -------------------------------------

def apply_pronunciation(text: str, overrides: dict[str, str]) -> str:
    """Replace whole-word, case-sensitive tokens with a spoken spelling the TTS
    reads correctly — e.g. ``{"LOG": "log"}`` so Chatterbox says "log" instead of
    spelling out "el oh gee". Case sensitivity is deliberate: it lets a fix target
    the ALL-CAPS form ("LOG") without touching ordinary words ("log", "catalog").

    Applied at segment-build time, after normalization, so each fix is folded into
    the segment content hash: changing one re-renders only the affected segments.
    Language-neutral: the fix is the user's own spelling.
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


def normalize_title(title: str, lang: str = "en") -> str:
    """Chapter titles: speak roman numerals ('Chapter IV' -> 'Chapter 4') then
    run the standard number expansion."""
    title = _roman_in_headings(title)
    return normalize_text(title, lang)


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
