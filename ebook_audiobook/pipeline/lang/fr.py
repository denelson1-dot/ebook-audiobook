"""French: how the text of a French book is prepared for the narrator.

The multilingual model reads what it is given, letter by letter: "1999" is
four digits to it, "M. Dupont" is an em and a full stop. So numbers are
written out (``num2words`` knows French), the usual abbreviations are
expanded, French typography — guillemets, the no-break space before ``;``,
``:``, ``!`` and ``?`` — is folded into plain punctuation, and the words the
app itself says are French.
"""

from __future__ import annotations

import re

from . import Rules
from .en import PUNCT_MAP as _EN_PUNCT

# French typography, on top of the shared map. Longest keys first: a
# guillemet with its no-break space is one thing, and becomes one quote.
PUNCT_MAP = {
    # A guillemet with the space that goes inside it is one thing, and
    # becomes one quote: no-break, narrow no-break, or plain.
    "\u00ab\u00a0": '"', "\u00a0\u00bb": '"', "\u00ab\u202f": '"', "\u202f\u00bb": '"',
    "\u00ab ": '"', " \u00bb": '"', "\u00ab": '"', "\u00bb": '"',
    # "n° 4" is a number, not a temperature; said here, before ° is.
    "n\u00b0": "num\u00e9ro ", "N\u00b0": "num\u00e9ro ", "n\u00ba": "num\u00e9ro ", "N\u00ba": "num\u00e9ro ",
    "\u202f": " ",                 # narrow no-break space (before ; : ! ?)
    **{k: v for k, v in _EN_PUNCT.items() if k not in ("\u00b0", "&")},
    "\u00b0": " degr\u00e9s ",
    "&": " et ",
}

# (pattern, replacement), in order. "M." is Monsieur only before a capital,
# which is also what keeps the sentence splitter from ending a sentence at it.
_ABBREVIATIONS = [
    (r"\bMM\.\s*(?=[A-ZÀ-Ý])", "Messieurs "),
    (r"\bM\.\s*(?=[A-ZÀ-Ý])", "Monsieur "),
    (r"\bMmes\b\.?", "Mesdames"),
    (r"\bMme\b\.?", "Madame"),
    (r"\bMlles\b\.?", "Mesdemoiselles"),
    (r"\bMlle\b\.?", "Mademoiselle"),
    (r"\bDr\b\.?\s*(?=[A-ZÀ-Ý])", "Docteur "),
    (r"\bPr\b\.?\s*(?=[A-ZÀ-Ý])", "Professeur "),
    (r"\bMe\s+(?=[A-ZÀ-Ý])", "Maître "),
    (r"\bSte[\s-](?=[A-ZÀ-Ý])", "Sainte-"),
    (r"\bSt[\s-](?=[A-ZÀ-Ý])", "Saint-"),
    (r"\bp\.\s?ex\.", "par exemple"),
    (r"\bc\.-à-d\.", "c'est-à-dire"),
    (r"\betc\.", "et cætera"),
    (r"\bav\.\s?J\.-C\.", "avant Jésus-Christ"),
    (r"\bapr\.\s?J\.-C\.", "après Jésus-Christ"),
    (r"\bcf\.", "voir"),
    (r"\bboul\.", "boulevard"),
    (r"\bav\.\s+(?=[A-ZÀ-Ý])", "avenue "),
]
ABBREVIATIONS = [(re.compile(p), r) for p, r in _ABBREVIATIONS]

# Digits, with the French thousands separators (a space of any width) and a
# comma for decimals. Patterns are tried in this order: a time before the
# integer it contains, currency before the number it holds.
_SEP = "[   ]"
_DIGITS = rf"\d{{1,3}}(?:{_SEP}\d{{3}})+|\d+"
_TIME = re.compile(rf"\b(\d{{1,2}})\s?h(?:\s?(\d{{2}}))?\b")
_EURO = re.compile(rf"({_DIGITS})(?:,(\d{{1,2}}))?\s?€")
_DOLLAR = re.compile(rf"\$\s?({_DIGITS})(?:[.,](\d{{1,2}}))?|({_DIGITS})(?:,(\d{{1,2}}))?\s?\$")
_PERCENT = re.compile(rf"({_DIGITS})(?:,(\d+))?\s?%")
_ORDINAL = re.compile(r"\b(\d+)(ers|er|res|re|ères|ère|èmes|ème|es|e)\b")
# A Roman ordinal needs a real numeral: two letters or more, or a lone I, V or
# X. A lone L or C is a word — "Le", "Ce" — not the fiftieth of anything.
_ROMAN_ORDINAL = re.compile(r"\b((?:[IVXLC]{2,7})|[IVX])(ers|er|res|re|ères|ère|èmes|ème|es|e)\b")
# A comma after a number is a decimal only when digits follow it: "1625, le
# bourg" is a year and a pause.
_DECIMAL = re.compile(rf"(?<![\w,.])({_DIGITS}),(\d+)(?![\w.]|,\d)")
_INTEGER = re.compile(rf"(?<![\w,.])({_DIGITS})(?![\w.]|,\d)")


def _n(value, **kw) -> str:
    from num2words import num2words

    return num2words(value, lang="fr", **kw)


def _int(s: str) -> int:
    return int(re.sub(_SEP, "", s))


def _roman(s: str) -> int:
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total, prev = 0, 0
    for ch in reversed(s):
        v = vals[ch]
        total += -v if v < prev else v
        prev = max(prev, v)
    return total


def _ordinal(n: int, suffix: str) -> str:
    feminine = suffix.startswith(("re", "ère"))
    if n == 1:
        word = "première" if feminine else "premier"
    else:
        word = _n(n, to="ordinal")
    if suffix.endswith("s") and n != 1:
        word += "s"
    elif suffix.endswith("s"):
        word += "s"
    return word


def _time(m: re.Match) -> str:
    h, mins = int(m.group(1)), m.group(2)
    if h > 24:
        return m.group(0)
    words = _n(h) + (" heure" if h == 1 else " heures")
    if mins and int(mins):
        words += " " + _n(int(mins))
    return words


def _money(whole: int, cents: int, currency: str, unit: str) -> str:
    if not cents:  # "cinq euros", not "cinq euros et zéro centimes"
        return _n(whole) + " " + unit + ("" if whole == 1 else "s")
    return _n(whole + cents / 100, to="currency", currency=currency)


def _euro(m: re.Match) -> str:
    return _money(_int(m.group(1)), int((m.group(2) or "0").ljust(2, "0")), "EUR", "euro")


def _dollar(m: re.Match) -> str:
    whole = _int(m.group(1) or m.group(3))
    cents = int((m.group(2) or m.group(4) or "0").ljust(2, "0"))
    return _money(whole, cents, "USD", "dollar")


def speak_numbers(text: str) -> str:
    text = _TIME.sub(_time, text)
    text = _EURO.sub(_euro, text)
    text = _DOLLAR.sub(_dollar, text)
    text = _PERCENT.sub(lambda m: _n(float(f"{_int(m.group(1))}.{m.group(2) or 0}")
                                     if m.group(2) else _int(m.group(1))) + " pour cent", text)
    text = _ROMAN_ORDINAL.sub(lambda m: _ordinal(_roman(m.group(1)), m.group(2)), text)
    text = _ORDINAL.sub(lambda m: _ordinal(int(m.group(1)), m.group(2)), text)
    text = _DECIMAL.sub(lambda m: _n(_int(m.group(1))) + " virgule " + _n(int(m.group(2))), text)
    text = _INTEGER.sub(lambda m: _n(_int(m.group(1))), text)
    return text


# Front and back matter, as French publishers title it. Matched against an
# accent-stripped lowercase title, so "TABLE DES MATIERES" is caught too.
SKIP_TITLE_HINTS = (
    "table des matieres", "sommaire", "droits d'auteur", "tous droits reserves",
    "mentions legales", "depot legal", "remerciements", "a propos de l'auteur",
    "a propos de l'autrice", "du meme auteur", "de la meme autrice",
    "dans la meme collection", "dedicace", "colophon", "glossaire", "bibliographie",
    "epigraphe", "exergue", "acheve d'imprimer", "page de titre", "faux-titre",
    "notes", "index", "isbn", "copyright",
)

RULES = Rules(
    code="fr",
    punct_map=PUNCT_MAP,
    abbreviations=ABBREVIATIONS,
    speak_numbers=speak_numbers,
    skip_title_hints=SKIP_TITLE_HINTS,
    strings={
        "chapter_n": "Chapitre %(n)s",
        "by_author": "De %(author)s.",
        "concludes": "Ici se termine %(subject)s",
        "by_author_tail": ", de %(author)s.",
        "this_book": "ce livre",
        "the_end": "Fin",
    },
)
