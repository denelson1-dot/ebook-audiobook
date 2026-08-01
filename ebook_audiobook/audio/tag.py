"""Post-mux MP4/M4B tagging pass (mutagen).

ffmpeg already writes the container, chapters, cover video track, and the basic
title/artist/album/genre at package time. This pass adds the iTunes/Plex atoms
ffmpeg can't set well — most importantly ``stik=2`` (mark it an *Audiobook*, not
a song) and ``aART`` (album artist, which Audnexus matches the author on) — plus
a real ``covr`` cover atom and, when known, year/description/ISBN.

mutagen only rewrites the metadata (``ilst``) atoms; the audio and chapter
tracks are left untouched.
"""

from __future__ import annotations

from pathlib import Path

from ..jobs.models import Book

# iTunes "Media Type" (stik) value for an audiobook.
STIK_AUDIOBOOK = 2


def _series_sort_prefix(book: Book) -> str | None:
    idx = (book.series_index or "").strip()
    if not idx:
        return None
    try:
        return f"{int(float(idx)):02d}"
    except ValueError:
        return idx


def tag_m4b(path: Path, book: Book) -> None:
    """Apply Plex/Audnexus-friendly tags to an existing .m4b in place."""
    from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm

    audio = MP4(str(path))

    audio["\xa9nam"] = [book.title]
    audio["\xa9alb"] = [book.title]
    audio["\xa9ART"] = [book.author]
    audio["aART"] = [book.author]            # album artist — author matching
    audio["\xa9gen"] = ["Audiobook"]
    audio["stik"] = [STIK_AUDIOBOOK]         # 2 = Audiobook, the critical flag
    audio["trkn"] = [(1, 1)]                 # single-file book

    if book.year:
        audio["\xa9day"] = [str(book.year)]
    if book.description:
        audio["desc"] = [book.description]

    # Series ordering: sort album/name as "NN - Title" so Plex "By Name" orders
    # a series correctly. Falls back to the plain title otherwise.
    prefix = _series_sort_prefix(book)
    sort_val = f"{prefix} - {book.title}" if prefix else book.title
    audio["soal"] = [sort_val]
    audio["sonm"] = [sort_val]

    if book.cover_path and Path(book.cover_path).exists():
        # The format flag is taken from the bytes, not the filename. Covers are
        # normalized to JPEG/PNG on extraction, but a job created before that
        # (or edited by hand) can still have PNG data in a .jpg — and a wrong
        # flag here yields a file that looks perfect and renders as a blank
        # square. Anything unrecognisable is left off rather than mislabelled.
        from ..pipeline.cover import PNG, sniff_image

        data = Path(book.cover_path).read_bytes()
        kind = sniff_image(data)
        if kind is not None:
            fmt = MP4Cover.FORMAT_PNG if kind == PNG else MP4Cover.FORMAT_JPEG
            audio["covr"] = [MP4Cover(data, imageformat=fmt)]

    # Deterministic Audnexus matching when we have an identifier.
    if book.isbn:
        audio["----:com.apple.iTunes:ISBN"] = [MP4FreeForm(book.isbn.encode("utf-8"))]

    audio.save()


def read_tags(path: Path) -> dict:
    """Read back the tags we care about (for validation/inspection)."""
    from mutagen.mp4 import MP4

    audio = MP4(str(path))

    def first(key):
        v = audio.tags.get(key) if audio.tags else None
        return v[0] if v else None

    return {
        "title": first("\xa9nam"),
        "album": first("\xa9alb"),
        "artist": first("\xa9ART"),
        "album_artist": first("aART"),
        "stik": first("stik"),
        "has_cover": bool(audio.tags and audio.tags.get("covr")),
    }
