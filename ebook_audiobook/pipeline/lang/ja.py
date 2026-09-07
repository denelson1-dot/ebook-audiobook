"""Japanese: how the text of a Japanese book is prepared for the narrator.

Japanese is the first language here that the shared pipeline could not simply
be pointed at, for two reasons.

**Nothing separates the words.** The shared sentence splitter looks for a full
stop followed by a space, and the hard-wrapper splits on whitespace. Given a
Japanese paragraph both find nothing, so the paragraph arrives as a single
"sentence" and is then sliced at whatever character happens to sit at the
budget — mid-word, mid-name. This module supplies its own sentence and clause
patterns, an empty joiner, and a smaller chunk budget, because a character of
Japanese carries far more speech than a character of English.

**The engine reads kanji but not digits.** ``chatterbox`` runs Japanese through
``pykakasi`` (a hard dependency of the engine, not an optional one), which turns
kanji into the hiragana the model was trained on — and it is *good* at the
counter irregularities that make Japanese numbers hard: 四時 → よじ, 一日 →
ついたち, 二十日 → はつか, 一人 → ひとり, 六本 → ろっぽん. What it does not touch
is Arabic digits: "1999年" reaches the model as the four characters "1999".

So digits are converted here, and the split between the two ways of doing it is
drawn where measurement put it, not where it looked tidy:

* **Kanji numerals for almost everything**, letting pykakasi apply the counter
  reading. It is better at this than a table of our own would be.
* **Hiragana written out here for the three cases pykakasi gets wrong**: 4-digit
  years (千九百九十九年 comes back as せんきゅうひゃく*つくも*ねん — it reads 九十九
  as the poetic name for ninety-nine — and 千八百 as せん*ぱち*ひゃく), minutes
  (一分 → いち*ぶ* rather than いっぷん; the 分 counter is genuinely ambiguous),
  and the irregular days of the month above ten (十四日 → じゅうよん*にち* rather
  than じゅうよっか).
"""

from __future__ import annotations

import re

from . import Rules, keep_on_error

# Japanese punctuation the model is trained on — 。、！？「」 — is left alone.
# What is folded away is the typographic furniture around it: the wave dash and
# the long horizontal bars used as pauses, which are not in the grapheme set.
PUNCT_MAP = {
    "〜": "、", "～": "、", "―": "、", "─": "、", "‥": "、", "…": "、",
    "・": "、",
    # An ideographic space is usually paragraph indentation, but in a heading
    # it is the gap between a chapter number and its title — "第三章　旅の始まり".
    # Deleting it runs the two together; a plain space keeps the boundary, and
    # the engine collapses runs of whitespace before tokenising anyway.
    "　": " ",
    " ": " ", " ": " ",
    "­": "",                  # soft hyphen
    "＆": "、",
}

# Japanese has no abbreviations of the "Mr." kind that need saying differently.
ABBREVIATIONS: list[tuple[re.Pattern, str]] = []

_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")

# Minutes. The reading of 分 changes with the digit before it and pykakasi
# consistently picks the "part/portion" reading instead of the "minute" one.
_MIN_ONES = {1: "いっぷん", 2: "にふん", 3: "さんぷん", 4: "よんぷん", 5: "ごふん",
             6: "ろっぷん", 7: "ななふん", 8: "はっぷん", 9: "きゅうふん"}
# Days of the month. One to ten are their own words, and 14/20/24 keep the old
# reading; everything else is regular.
_DAYS = {1: "ついたち", 2: "ふつか", 3: "みっか", 4: "よっか", 5: "いつか",
         6: "むいか", 7: "なのか", 8: "ようか", 9: "ここのか", 10: "とおか",
         14: "じゅうよっか", 20: "はつか", 24: "にじゅうよっか"}

_YEAR = re.compile(r"(?<!\d)(\d{3,4})年")
_MONTH_DAY = re.compile(r"(?<!\d)(\d{1,2})月(?:(\d{1,2})日)?")
# 1日 is ついたち on a calendar but いちにち as a span, and only the day-of-month
# reading is irregular — so a following 中/で/かけて marks it as a duration.
_DAY = re.compile(r"(?<!\d)(\d{1,2})日(?!間|中)")
_DAYS_SPAN = re.compile(r"(?<!\d)(\d{1,3})日(?=間|中)")
# 2時 is "two o'clock"; 2時間 is "two hours". Without the guard the 時 was
# eaten and the 間 left stranded, so pykakasi could no longer read the compound.
_TIME = re.compile(r"(?<!\d)(\d{1,2})時(?!間)(?:(\d{1,2})分)?(?:(\d{1,2})秒)?")
_HOURS = re.compile(r"(?<!\d)(\d{1,3})時間")
# 分 is minutes, except in 3分の1 (a third), where it is the denominator.
_MINUTE = re.compile(r"(?<!\d)(\d{1,3})分(?!の\d)")
_PERCENT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*[%％]")
_YEN = re.compile(r"[¥￥]\s?(\d[\d,]*)|(\d[\d,]*)\s?円")
_DOLLAR = re.compile(r"[$＄]\s?(\d[\d,]*(?:\.\d+)?)")
# The Latin guards cannot be reused here. Python's \w matches kanji and kana, so
# "(?<![\w.])" rejects every digit that touches Japanese text — and with no
# spaces between words, that is nearly all of them: "第3章" would keep its digit.
# Only digits and a decimal point are worth guarding against.
_DECIMAL = re.compile(r"(?<![\d.])(\d[\d,]*)\.(\d+)")
_INTEGER = re.compile(r"(?<![\d.])(\d[\d,]*\d|\d)(?!\.?\d)")


def _n(value: int, reading: bool = False) -> str:
    from num2words import num2words

    return num2words(value, lang="ja", reading=reading)


def _hira(value: int) -> str:
    return _n(value, reading=True)


def _kanji(value: int) -> str:
    return _n(value)


def _int(s: str) -> int:
    return int(s.replace(",", ""))


def _minutes(n: int) -> str:
    """1 → いっぷん, 10 → じゅっぷん, 30 → さんじゅっぷん, 45 → よんじゅうごふん."""
    if n == 0:
        return "れいふん"          # "0 minutes"; without this it was a bare っぷん
    ones, tens = n % 10, n - (n % 10)
    prefix = _hira(tens) if tens else ""
    if ones == 0:
        # じゅう → じゅっ, にじゅう → にじゅっ
        return (prefix[:-1] if prefix else "") + "っぷん"
    return prefix + _MIN_ONES[ones]


def _hours(n: int) -> str:
    """4時 → よじ, 9時 → くじ, 14時 → じゅうよじ."""
    if n == 0:
        return "れいじ"            # midnight on a 24-hour clock
    ones, tens = n % 10, n - (n % 10)
    special = {4: "よじ", 7: "しちじ", 9: "くじ"}
    prefix = _hira(tens) if tens else ""
    if ones in special:
        return prefix + special[ones]
    return (prefix + _hira(ones) if ones else prefix) + "じ"


def _day(n: int) -> str:
    if n in _DAYS:
        return _DAYS[n]
    return _hira(n) + "にち"


def _year(m: re.Match) -> str:
    # Written out here: pykakasi misreads 九十九 as つくも and 八百 as ぱひゃく.
    return _hira(int(m.group(1))) + "ねん"


def _month_day(m: re.Match) -> str:
    out = _kanji(int(m.group(1))) + "月"
    if m.group(2):
        out += _day(int(m.group(2)))
    return out


def _time(m: re.Match) -> str:
    h, mins, secs = m.group(1), m.group(2), m.group(3)
    out = _hours(int(h))
    # "10時00分" is ten o'clock, not ten o'clock and zero minutes.
    if mins and int(mins):
        out += _minutes(int(mins))
    if secs:
        out += _hira(int(secs)) + "びょう"
    return out


def _decimal_number(raw: str) -> str:
    """A figure that may carry a fractional part, read whole then digit by
    digit — "3.5" as 三てんご, not truncated to 三."""
    whole, _, frac = raw.replace(",", "").partition(".")
    out = _kanji(int(whole or 0))
    if frac:
        out += "てん" + "".join(_hira(int(d)) for d in frac)
    return out


def _dollars(m: re.Match) -> str:
    """Money is dollars and cents, not a decimal read digit by digit: $3.50 is
    三ドル五十セント, never 三てんごゼロドル."""
    whole, _, frac = m.group(1).replace(",", "").partition(".")
    out = _kanji(int(whole or 0)) + "ドル"
    cents = int(frac.ljust(2, "0")[:2]) if frac else 0
    if cents:
        out += _kanji(cents) + "セント"
    return out


def _percent(m: re.Match) -> str:
    return _decimal_number(m.group(1)) + "パーセント"


def speak_numbers(text: str) -> str:
    text = text.translate(_FULLWIDTH_DIGITS)
    # Order matters: the more specific counter wins before the digits inside it
    # are read as a plain number.
    text = _HOURS.sub(keep_on_error(lambda m: _hira(int(m.group(1))) + "じかん"), text)
    text = _DAYS_SPAN.sub(keep_on_error(lambda m: _hira(int(m.group(1))) + "にち"), text)
    text = _YEAR.sub(keep_on_error(_year), text)
    text = _MONTH_DAY.sub(keep_on_error(_month_day), text)
    text = _DAY.sub(keep_on_error(lambda m: _day(int(m.group(1)))), text)
    text = _TIME.sub(keep_on_error(_time), text)
    text = _MINUTE.sub(keep_on_error(lambda m: _minutes(int(m.group(1)))), text)
    text = _PERCENT.sub(keep_on_error(_percent), text)
    text = _YEN.sub(keep_on_error(lambda m: _kanji(_int(m.group(1) or m.group(2))) + "円"), text)
    text = _DOLLAR.sub(keep_on_error(_dollars), text)
    # The digits after the point are read one at a time, and in hiragana: left
    # as kanji, 四 comes back as し where a decimal wants よん.
    text = _DECIMAL.sub(keep_on_error(
        lambda m: _kanji(_int(m.group(1))) + "てん" + "".join(_hira(int(d)) for d in m.group(2))),
        text)
    # Everything else: kanji numerals, and pykakasi supplies the counter reading.
    text = _INTEGER.sub(keep_on_error(lambda m: _kanji(_int(m.group(1)))), text)
    return text


# --- how a Japanese sentence is cut up ---------------------------------------

# Zero-width: there is no whitespace to consume. A sentence ends after 。！？,
# and after any closing bracket that follows one — so 「……。」 ends where the
# bracket closes, not inside it.
SENTENCE_END = re.compile(
    r'(?<=[。！？][」』）】〕》〉”’])|(?<=[。！？])(?![。！？」』）】〕》〉”’])')
# The reading comma, again with no space after it.
CLAUSE = re.compile(r'(?<=[、，])')

# Front and back matter, as Japanese publishers title it. Deliberately absent:
# まえがき, あとがき, 序文, 解説 — those are the book, not its wrapper.
SKIP_TITLE_HINTS = (
    "目次", "奥付", "著作権", "版権", "謝辞", "参考文献", "索引", "用語集",
    "献辞", "著者について", "著者紹介", "訳者紹介", "中扉", "凡例",
    "初出一覧", "装丁", "図版一覧", "isbn", "copyright",
)

RULES = Rules(
    code="ja",
    punct_map=PUNCT_MAP,
    abbreviations=ABBREVIATIONS,
    speak_numbers=speak_numbers,
    skip_title_hints=SKIP_TITLE_HINTS,
    strings={
        "chapter_n": "第%(n)s章",
        "by_author": "%(author)s、著。",
        "concludes": "%(subject)sは、これで終わりです",
        "by_author_tail": "。%(author)s、著。",
        "this_book": "この本",
        "the_end": "おわり",
        "voice_sample": "これは選ばれた語り手の声の見本です。静かな町は広く無関心な空の下で眠り、どこかで鐘が二度鳴りました。",
    },
    sentence_end=SENTENCE_END,
    clause=CLAUSE,
    # No spaces between words, so none between chunks either.
    joiner="",
    # Measured, not guessed: rendering the same passage in each language gives
    # 163 ms of speech per Japanese character against 67 ms per English one — a
    # ratio of 2.44. English's 350/500 budget therefore lands at 143/205 here,
    # and over-long utterances are exactly what this engine degrades on.
    chunk_target_chars=140,
    chunk_max_chars=200,
)
