"""Spanish: how the text of a Spanish book is prepared for the narrator.

One neutral Spanish, not a Peninsular and a Latin American variant: the
differences that matter here — how a number, a title or an abbreviation is
*read* — are shared, and the ones that do not are a matter of accent, which is
the narrator's job and not this file's. Four Spanish narrators ship, two per
accent region; they all read the text this module produces.

The multilingual model reads what it is given, so numbers are written out
(``num2words`` knows Spanish), the usual abbreviations are expanded, and the
words the app itself says are Spanish. Two Spanish-specific traps are handled
here and nowhere else:

* **A dot inside a number is a thousands separator, and a comma is the decimal
  point** — the exact opposite of English. "1.234" is one thousand two hundred
  and thirty-four; "3,5" is three point five.
* **``num2words`` says "punto" for the decimal mark**, where Spanish says
  "coma". Decimals are therefore composed here rather than handed to it whole.

Inverted ``¿`` and ``¡`` are deliberately left in place: the model is trained on
them, and they are what tells it a question has begun — which matters in a
language whose word order does not.
"""

from __future__ import annotations

import re

from . import Rules
from .en import PUNCT_MAP as _EN_PUNCT

PUNCT_MAP = {
    # Guillemets are used for dialogue in Spanish as well as French, with or
    # without the space that sits inside them. Longest keys are matched first.
    "« ": '"', " »": '"', "« ": '"', " »": '"',
    "« ": '"', " »": '"', "«": '"', "»": '"',
    # "n.º 4" is a number, not a temperature; said here, before ° is.
    "n.º": "número ", "N.º": "número ",
    "nº": "número ", "Nº": "número ",
    "n°": "número ", "N°": "número ",
    " ": " ",                 # narrow no-break space
    **{k: v for k, v in _EN_PUNCT.items() if k not in ("°", "&")},
    "°": " grados ",
    "&": " y ",
}

# (pattern, replacement), in order. A title is expanded only before a capital,
# which is also what keeps the sentence splitter from ending a sentence at the
# full stop of "Sr." — the same guard fr.py uses.
_CAP = r"(?=[A-ZÁÉÍÓÚÑÜ])"
_ABBREVIATIONS = [
    (rf"\bSrta\b\.?\s*{_CAP}", "Señorita "),
    (rf"\bSra\b\.?\s*{_CAP}", "Señora "),
    (rf"\bSr\b\.?\s*{_CAP}", "Señor "),
    (rf"\bDra\b\.?\s*{_CAP}", "Doctora "),
    (rf"\bDr\b\.?\s*{_CAP}", "Doctor "),
    (rf"\bProfa\b\.?\s*{_CAP}", "Profesora "),
    (rf"\bProf\b\.?\s*{_CAP}", "Profesor "),
    # "D.ª"/"Dña." before "D." so the longer form wins.
    (rf"\bD\.ª\s*{_CAP}", "Doña "),
    (rf"\bDña\b\.?\s*{_CAP}", "Doña "),
    (rf"\bD\.\s*{_CAP}", "Don "),
    (rf"\bSto\b\.?\s*{_CAP}", "Santo "),
    (rf"\bSta\b\.?\s*{_CAP}", "Santa "),
    # "EE. UU." with any spacing, including none.
    (r"\bEE\.\s?UU\.", "Estados Unidos"),
    (r"\bVds\b\.?", "ustedes"),
    (r"\bUds\b\.?", "ustedes"),
    (r"\bVd\b\.?", "usted"),
    (r"\bUd\b\.?", "usted"),
    (r"\betc\.", "etcétera"),
    (r"\bp\.\s?ej\.", "por ejemplo"),
    (r"\bes decir\b", "es decir"),
    # Two rules each, and the order matters. The abbreviation's own full stop is
    # also the sentence's full stop when a sentence ends on it, so it is kept
    # there and dropped everywhere else — keeping it always would put a spurious
    # pause in "44 a. C. y murió...", and dropping it always would run two
    # sentences together.
    (r"\ba\.\s?C\.(?=\s+[A-ZÁÉÍÓÚÑ¿¡]|\s*$)", "antes de Cristo."),
    (r"\ba\.\s?C\.", "antes de Cristo"),
    (r"\bd\.\s?C\.(?=\s+[A-ZÁÉÍÓÚÑ¿¡]|\s*$)", "después de Cristo."),
    (r"\bd\.\s?C\.", "después de Cristo"),
    (r"\bpágs?\b\.", "página"),
    (r"\bnúms?\b\.", "número"),
    (r"\bcap\b\.", "capítulo"),
    (r"\bvol\b\.", "volumen"),
    (r"\bart\b\.", "artículo"),
    (r"\bcf\b\.", "véase"),
    (r"\bapdo\b\.", "apartado"),
]
ABBREVIATIONS = [(re.compile(p), r) for p, r in _ABBREVIATIONS]

# A thousands separator in Spanish is a dot or a space of some width; the
# decimal mark is a comma. Requiring exactly three digits after each separator
# is what keeps "Llegó en 1999. 300 personas" from becoming one number.
_SEP = "[.   ]"
_DIGITS = rf"\d{{1,3}}(?:{_SEP}\d{{3}})+|\d+"
_TIME = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_EURO = re.compile(r"(\d[\d.,]*)\s?€")
_DOLLAR = re.compile(r"\$\s?(\d[\d.,]*)|(\d[\d.,]*)\s?\$")
_PERCENT = re.compile(rf"({_DIGITS})(?:,(\d+))?\s?%")
# 1.º 1.ª 1º 1ª 1er 1.er 2.os 3.as — the dot is optional, the marker is not.
_ORDINAL = re.compile(r"\b(\d+)\.?(º s|ºS|ºs|º|ªs|ª|er|os|as|o|a)\b")
# Roman numerals in running text are only safe with a word in front of them:
# MIL, CIVIL, VID, LID and CID are all ordinary Spanish words spelled entirely
# in Roman-numeral letters. Titles are handled elsewhere, by
# normalize._roman_in_headings, which runs before this.
_ROMAN_CONTEXT = re.compile(
    r"\b(siglos?|capítulos?|tomos?|libros?|partes?|actos?|escenas?|volúmenes|volumen|"
    r"números?)"
    r"(\s+)([IVXLCDM]{1,7})\b")
# A monarch's number is read as an ordinal — "Carlos tercero" — but only up to
# ten; from eleven on Spanish switches to the cardinal, "Alfonso trece".
_ROMAN_REGNAL = re.compile(
    r"\b(Juan Pablo|Juan|Carlos|Felipe|Fernando|Alfonso|Isabel|Luis|Enrique|Pedro|"
    r"Benedicto|Francisco|Pío|León|Gregorio|Clemente|Inocencio|Urbano|Alejandro)"
    r"(\s+)([IVXLCDM]{1,7})\b")
# The trailing guard rejects a dot or comma only when a digit follows it: that
# is what distinguishes "1.234" (one number, already matched whole by _DIGITS)
# from "en 1999." (a number, then the end of the sentence). Rejecting every
# following dot — the obvious spelling — silently leaves every sentence-final
# number unspoken.
_DECIMAL = re.compile(rf"(?<![\w,.])({_DIGITS}),(\d+)(?!\w|[.,]\d)")
_INTEGER = re.compile(rf"(?<![\w,.])({_DIGITS})(?!\w|[.,]\d)")


def _n(value, **kw) -> str:
    from num2words import num2words

    return num2words(value, lang="es", **kw)


def _int(s: str) -> int:
    return int(re.sub(_SEP, "", s))


def _roman(s: str) -> int:
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total, prev = 0, 0
    for ch in reversed(s):
        v = vals[ch]
        total += -v if v < prev else v
        prev = max(prev, v)
    return total


def _ordinal(n: int, suffix: str) -> str:
    """"1.º" -> primero, "1.ª" -> primera, "1.er" -> primer, "2.os" -> segundos.

    Spanish forms the feminine of an ordinal by swapping the final -o for -a,
    including in the compound forms ("décimo octavo" -> "décima octava" is the
    strict form, but "décimo octava" is what is read aloud), and apocopates
    "primero"/"tercero" to "primer"/"tercer" before a masculine noun — which is
    exactly what the "er" marker in the text is telling us.
    """
    word = _n(n, to="ordinal")
    plural = suffix.endswith("s")
    marker = suffix.rstrip("sS")
    if marker == "er":
        word = re.sub(r"o$", "", word)          # primero -> primer
    elif marker in ("ª", "a"):
        word = re.sub(r"o$", "a", word)         # segundo -> segunda
    if plural:
        word += "s"
    return word


def _regnal(m: re.Match) -> str:
    n = _roman(m.group(3))
    if n <= 10:
        word = _n(n, to="ordinal")
        # Isabel la Católica is "Isabel primera", not "primero".
        if m.group(1) in ("Isabel",):
            word = re.sub(r"o$", "a", word)
    else:
        word = _n(n)
    return f"{m.group(1)}{m.group(2)}{word}"


def _amount(s: str) -> tuple[int, int]:
    """Split a written money amount into whole units and hundredths.

    A Spanish book may print either convention next to a "$": "1.250,75" the
    Spanish way, or "1,250.75" the American way. Guessing per-separator gets
    both wrong half the time, so the rule is positional — whichever of "." or
    "," appears last is the decimal mark, and it only counts as one when it is
    not followed by exactly three digits (that shape is a thousands group).
    """
    last = max(s.rfind("."), s.rfind(","))
    if last == -1:
        return int(re.sub(r"\D", "", s) or 0), 0
    tail = s[last + 1:]
    if len(tail) == 3 or not tail.isdigit():   # a thousands group, not cents
        return int(re.sub(r"\D", "", s) or 0), 0
    whole = int(re.sub(r"\D", "", s[:last]) or 0)
    return whole, int(tail.ljust(2, "0")[:2])


def _time(m: re.Match) -> str:
    h, mins = int(m.group(1)), int(m.group(2))
    if h > 23 or mins > 59:
        return m.group(0)
    # "a las 9:05" -> "a las nueve y cinco": the conjunction is how Spanish
    # prose says a time. "nueve horas cinco" is announcement register and reads
    # as a machine talking.
    words = _n(h)
    if mins:
        words += " y " + _n(mins)
    return words


def _money(whole: int, cents: int, currency: str, unit: str) -> str:
    if not cents:  # "cinco euros", not "cinco euros con cero céntimos"
        return _n(whole) + " " + unit + ("" if whole == 1 else "s")
    return _n(whole + cents / 100, to="currency", currency=currency)


def _euro(m: re.Match) -> str:
    whole, cents = _amount(m.group(1).rstrip(".,"))
    return _money(whole, cents, "EUR", "euro")


def _dollar(m: re.Match) -> str:
    raw = (m.group(1) or m.group(2)).rstrip(".,")
    whole, cents = _amount(raw)
    return _money(whole, cents, "USD", "dólar")


def _percent(m: re.Match) -> str:
    # Composed rather than handed to num2words as a float, so the decimal mark
    # is read "coma" and not "punto".
    words = _n(_int(m.group(1)))
    if m.group(2):
        words += " coma " + _n(int(m.group(2)))
    return words + " por ciento"


def speak_numbers(text: str) -> str:
    text = _TIME.sub(_time, text)
    text = _EURO.sub(_euro, text)
    text = _DOLLAR.sub(_dollar, text)
    text = _PERCENT.sub(_percent, text)
    text = _ROMAN_REGNAL.sub(_regnal, text)
    text = _ROMAN_CONTEXT.sub(
        lambda m: f"{m.group(1)}{m.group(2)}{_n(_roman(m.group(3)))}", text)
    text = _ORDINAL.sub(lambda m: _ordinal(int(m.group(1)), m.group(2)), text)
    text = _DECIMAL.sub(lambda m: _n(_int(m.group(1))) + " coma " + _n(int(m.group(2))), text)
    text = _INTEGER.sub(lambda m: _n(_int(m.group(1))), text)
    return text


# Front and back matter, as Spanish publishers title it. Matched against an
# accent-stripped lowercase title, so "ÍNDICE" and "indice" both land.
# Deliberately absent: "prologo", "prefacio", "introduccion", "epilogo" — those
# are the book, not its wrapper.
SKIP_TITLE_HINTS = (
    "indice", "tabla de contenido", "tabla de contenidos", "contenido",
    "derechos de autor", "todos los derechos reservados", "aviso legal",
    "nota legal", "deposito legal", "agradecimientos", "sobre el autor",
    "sobre la autora", "acerca del autor", "acerca de la autora",
    "del mismo autor", "de la misma autora", "otros titulos", "otros libros",
    "dedicatoria", "colofon", "glosario", "bibliografia", "notas", "indice analitico",
    "epigrafe", "portada", "portadilla", "creditos", "isbn", "copyright",
    "pagina de titulo", "guarda",
)

RULES = Rules(
    code="es",
    punct_map=PUNCT_MAP,
    abbreviations=ABBREVIATIONS,
    speak_numbers=speak_numbers,
    skip_title_hints=SKIP_TITLE_HINTS,
    strings={
        "chapter_n": "Capítulo %(n)s",
        "by_author": "De %(author)s.",
        "concludes": "Aquí termina %(subject)s",
        "by_author_tail": ", de %(author)s.",
        "this_book": "este libro",
        "the_end": "Fin",
        "voice_sample": "Esta es una muestra de la voz elegida. El pueblo dormía bajo un cielo ancho e indiferente, y en algún lugar una campana sonó dos veces.",
    },
)
