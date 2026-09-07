"""Chunk normalized chapter text into TTS-safe segments.

Chatterbox degrades on long inputs, so we group whole sentences up to a target
character budget and never exceed a hard max in one generation. A single
over-long sentence is split on clause boundaries, then hard-wrapped as a last
resort. Order is always preserved.
"""

from __future__ import annotations

import re

from .. import config
from .lang import rules_for

# Sentence boundary: end punctuation + closing quote/paren, followed by space
# and a likely sentence start. Kept simple on purpose (no NLP dependency).
# The start class includes the accented capitals of the Latin-1 range (À, É,
# Ç…) and an opening guillemet; the closers include the closing one — so a
# French sentence ends where an English one does. Spanish adds the inverted
# marks that open a question or exclamation. Nothing else changed, so English
# text splits exactly as before.
_SENTENCE_END = re.compile(r'(?<=[.!?])["\'”’»)\]]*\s+(?=[A-ZÀ-ÖØ-ÞŒŸ0-9"\'“‘«(¿¡])')
_CLAUSE = re.compile(r'(?<=[,;:])\s+')

# Characters that may not open a line, for scripts wrapped by character rather
# than by word: closing brackets, the small kana that belong to the syllable
# before them, and the marks that trail a word. Breaking before one of these is
# the visible mistake a reader notices first (kinsoku shori).
_NO_BREAK_BEFORE = "」』）】〕》〉”’、。，．！？ぁぃぅぇぉっゃゅょァィゥェォッャュョーヽヾ々"


def split_sentences(text: str, lang: str = "en") -> list[str]:
    text = text.strip()
    if not text:
        return []
    pattern = rules_for(lang).sentence_end or _SENTENCE_END
    # Split on blank-line paragraph breaks first so we never merge across them.
    sentences: list[str] = []
    for para in re.split(r"\n{2,}", text):
        para = para.strip()
        if not para:
            continue
        parts = pattern.split(para)
        sentences.extend(p.strip() for p in parts if p.strip())
    return sentences


def _hard_wrap(text: str, max_chars: int, joiner: str = " ") -> list[str]:
    """Last resort for a clause longer than the hard max: split on word
    boundaries near the limit. A single word longer than the max (rare — URLs,
    concatenated text) is sliced so no chunk ever exceeds the model limit.

    With an empty joiner there are no word boundaries to use — the whole clause
    is one "word" — so the slice point is walked back off any character that may
    not start a line instead.
    """
    if joiner == "":
        return _wrap_by_character(text, max_chars)
    out, cur = [], ""
    for w in text.split():
        while len(w) > max_chars:  # word itself too long: slice it
            if cur:
                out.append(cur)
                cur = ""
            out.append(w[:max_chars])
            w = w[max_chars:]
        if cur and len(cur) + 1 + len(w) > max_chars:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur}{joiner}{w}" if cur else w
    if cur:
        out.append(cur)
    return out


def _wrap_by_character(text: str, max_chars: int) -> list[str]:
    out = []
    while len(text) > max_chars:
        cut = max_chars
        # Never leave a character that cannot open a line at the start of the
        # next piece. Give up after a few steps rather than walking the whole
        # chunk back on a pathological run of them.
        limit = max(1, max_chars - 8)
        while cut > limit and text[cut] in _NO_BREAK_BEFORE:
            cut -= 1
        out.append(text[:cut])
        text = text[cut:]
    if text:
        out.append(text)
    return out


def _split_long_sentence(sentence: str, max_chars: int, lang: str = "en") -> list[str]:
    rules = rules_for(lang)
    pattern = rules.clause or _CLAUSE
    pieces: list[str] = []
    for clause in pattern.split(sentence):
        clause = clause.strip()
        if not clause:
            continue
        if len(clause) <= max_chars:
            pieces.append(clause)
        else:
            pieces.extend(_hard_wrap(clause, max_chars, rules.joiner))
    return pieces


def chunk_text(
    text: str,
    target_chars: int | None = None,
    max_chars: int | None = None,
    lang: str = "en",
) -> list[str]:
    rules = rules_for(lang)
    target = target_chars or rules.chunk_target_chars or config.CHUNK_TARGET_CHARS
    hard_max = max_chars or rules.chunk_max_chars or config.CHUNK_MAX_CHARS
    joiner = rules.joiner
    join_len = len(joiner)

    units: list[str] = []
    for sentence in split_sentences(text, lang):
        if len(sentence) <= hard_max:
            units.append(sentence)
        else:
            units.extend(_split_long_sentence(sentence, hard_max, lang))

    # Greedily pack whole units up to the target without crossing the hard max.
    chunks: list[str] = []
    cur = ""
    for unit in units:
        if not cur:
            cur = unit
        elif len(cur) + join_len + len(unit) <= target or (
            len(cur) < target and len(cur) + join_len + len(unit) <= hard_max
        ):
            cur = f"{cur}{joiner}{unit}"
        else:
            chunks.append(cur)
            cur = unit
    if cur:
        chunks.append(cur)
    return chunks


# --- structure-aware chunking ------------------------------------------------

# A scene break is a short paragraph made only of separator glyphs, e.g.
# "* * *", "***", "⁂", "###", "· · ·". Dashes are deliberately excluded to avoid
# misreading dialogue/em-dash lines.
_SCENE_CHARS = r"*#⁂·~"
_SCENE_RE = re.compile(rf"^[{re.escape(_SCENE_CHARS)}]+(?:\s+[{re.escape(_SCENE_CHARS)}]+)*$")


def is_scene_break(paragraph: str) -> bool:
    s = paragraph.strip()
    return bool(s) and len(s) <= 12 and bool(_SCENE_RE.match(s))


def chunk_structured(
    text: str,
    target_chars: int | None = None,
    max_chars: int | None = None,
    lang: str = "en",
) -> list[tuple[str, str]]:
    """Chunk a chapter body into ``(text, boundary)`` pairs.

    Chunking is done per paragraph (never merging across a paragraph break), so
    boundaries always align with the book's structure. ``boundary`` is the pause
    that should follow the chunk: ``"sentence"`` between chunks inside a
    paragraph, ``"paragraph"`` at a paragraph end, and ``"scene"`` when the next
    paragraph is a scene-break marker (which is itself not spoken).
    """
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    out: list[list] = []  # mutable [text, boundary] pairs
    for para in paragraphs:
        if is_scene_break(para):
            if out:
                out[-1][1] = "scene"  # upgrade the preceding chunk's pause
            continue
        pieces = chunk_text(para, target_chars, max_chars, lang)
        for j, piece in enumerate(pieces):
            boundary = "paragraph" if j == len(pieces) - 1 else "sentence"
            out.append([piece, boundary])
    return [(t, b) for t, b in out]
