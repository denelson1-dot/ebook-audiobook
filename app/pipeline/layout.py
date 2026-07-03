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


def sanitize_component(name: str, fallback: str = "Unknown") -> str:
    """Make one folder/file-name component safe on every OS but still readable."""
    s = (name or "").strip().replace(":", " - ")
    s = _ILLEGAL.sub("", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip(" .,;")  # trim trailing dots/spaces (Windows) and stray separators
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


def library_dir(root: Path, book: Book) -> Path:
    """Folder the book's .m4b + cover.jpg are written into, under ``root``."""
    author = sanitize_component(book.author, "Unknown Author")
    leaf = _titled(book)
    if book.series:
        series = sanitize_component(book.series)
        idx = (book.series_index or "").strip()
        if idx:
            # zero-pad the integer part so 2 sorts before 10.
            try:
                leaf = f"{int(float(idx)):02d} - {leaf}"
            except ValueError:
                leaf = f"{idx} - {leaf}"
        return root / author / series / leaf
    return root / author / leaf


def library_m4b_path(root: Path, book: Book) -> Path:
    return library_dir(root, book) / f"{output_stem(book)}.m4b"
