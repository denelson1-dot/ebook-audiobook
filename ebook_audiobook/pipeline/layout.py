"""Plex-compatible on-disk layout for finished audiobooks.

Plex's audiobook conventions (via the Audnexus agent) want one chapterized
``.m4b`` per book, filed as::

    {root}/{Author}/{Title (Year)}/{Title}.m4b
    {root}/{Author}/{Series}/{NN} - {Title (Year)}/{Title}.m4b   # when in a series

Names are sanitized to be safe on Windows/macOS/Linux while staying
human-readable (spaces preserved, only truly illegal characters removed) — the
*original* strings still live in the embedded tags (see ``audio.tag``).
"""

from __future__ import annotations

import re
from pathlib import Path

from ..jobs.models import Book

# Characters illegal in a path component on at least one major OS.
_ILLEGAL = re.compile(r'[/\\*?:"<>|\x00-\x1f]')

# Names Windows reserves for DOS devices. A file or folder called any of these —
# with or without an extension, in any case — cannot be created on Windows, and
# a book legitimately titled "Nul" or "Aux" would otherwise fail at the very end
# of a multi-hour render. Suffixing an underscore keeps it readable and legal.
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

# Per-component length cap. Every mainstream filesystem allows 255 *bytes*, and
# non-ASCII titles cost several bytes per character in UTF-8, so cap well under
# it.
MAX_COMPONENT_CHARS = 60

# Total path budget. Windows still enforces a 260-character MAX_PATH for programs
# that haven't opted into long paths — and Plex, Explorer, and whatever the user
# syncs their library with are all such programs. Staying under it is much
# friendlier than emitting a path only some tools can open. The margin leaves
# room for the ".m4b"/"cover.jpg" leaf names we append.
MAX_PATH_CHARS = 240


def _truncate(s: str, limit: int) -> str:
    """Shorten to ``limit`` characters, preferring a word boundary."""
    if len(s) <= limit:
        return s
    cut = s[:limit].rsplit(" ", 1)[0]
    if len(cut) < limit // 2:  # one enormous word: hard cut rather than gut it
        cut = s[:limit]
    return cut.strip(" .,;")


def sanitize_component(name: str, fallback: str = "Unknown",
                       max_chars: int = MAX_COMPONENT_CHARS) -> str:
    """Make one folder/file-name component safe on every OS but still readable.

    Handles the ways a perfectly ordinary book title can produce a path the OS
    rejects: illegal characters, a Windows reserved device name, excessive
    length, and trailing dots or spaces (which Windows silently drops, so the
    path we think we wrote would differ from the one on disk).
    """
    s = (name or "").strip().replace(":", " - ")
    s = _ILLEGAL.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip(" .,;")
    s = _truncate(s, max_chars)

    # A title made only of punctuation sanitizes down to noise like "- - -".
    # Nothing meaningful survived, so use the caller's fallback instead.
    if not re.search(r"[^\W_]", s, flags=re.UNICODE):
        return fallback

    # Escape a reserved device name by suffixing the *stem*, not the whole
    # string: "nul.txt_" is still the forbidden device "nul", "nul_.txt" isn't.
    stem, dot, rest = s.partition(".")
    if stem.lower() in _WINDOWS_RESERVED:
        s = f"{stem}_{dot}{rest}"
    return s or fallback


def _titled(book: Book) -> str:
    """``Title (Year)`` — the year is dropped entirely when unknown (never
    written as ``(Unknown)``)."""
    title = sanitize_component(book.title, "Untitled")
    year = (book.year or "").strip()
    return f"{title} ({year})" if year else title


def output_stem(book: Book) -> str:
    """Base name (no extension) for the .m4b and its cover sidecar."""
    return sanitize_component(book.title, "Untitled")


def _fit_to_path_budget(root: Path, parts: list[str], leaf_name: str) -> list[str]:
    """Shorten path components until the whole thing fits ``MAX_PATH_CHARS``.

    A deep library root plus a long author, a long series, and a long title can
    exceed Windows' MAX_PATH even when every individual component is legal. The
    components are shrunk together (longest first, so one runaway field doesn't
    force the others down to nothing) until the full file path fits. A very deep
    root can make this impossible; in that case we stop at a sane floor rather
    than producing single-letter folders, and the OS reports the real error.
    """
    floor = 12  # below this, names stop being recognisable

    def total(current: list[str]) -> int:
        return len(str(root.joinpath(*current, leaf_name)))

    parts = list(parts)
    while total(parts) > MAX_PATH_CHARS:
        longest = max(range(len(parts)), key=lambda i: len(parts[i]))
        if len(parts[longest]) <= floor:
            break  # nothing left to give
        over = total(parts) - MAX_PATH_CHARS
        target = max(floor, len(parts[longest]) - max(over, 1))
        parts[longest] = _truncate(parts[longest], target) or parts[longest][:target]
    return parts


def library_dir(root: Path, book: Book) -> Path:
    """Folder the book's .m4b + cover.jpg are written into, under ``root``."""
    author = sanitize_component(book.author, "Unknown Author")
    leaf = _titled(book)
    parts = [author]
    if book.series:
        series = sanitize_component(book.series)
        idx = (book.series_index or "").strip()
        if idx:
            # zero-pad the integer part so 2 sorts before 10.
            try:
                leaf = f"{int(float(idx)):02d} - {leaf}"
            except ValueError:
                leaf = f"{idx} - {leaf}"
        parts.append(series)
    parts.append(leaf)
    parts = _fit_to_path_budget(root, parts, f"{output_stem(book)}.m4b")
    return root.joinpath(*parts)


def library_m4b_path(root: Path, book: Book) -> Path:
    return library_dir(root, book) / f"{output_stem(book)}.m4b"
