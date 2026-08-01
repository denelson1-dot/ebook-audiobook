"""Getting the book's cover into a format players will actually display.

An EPUB's cover is whatever the publisher (or a Calibre conversion) happened to
put in the manifest, and the manifest's file extension is not evidence: covers
turn up as ``.jpg`` files containing PNG data, and increasingly as WebP, which
nothing in the audiobook chain renders.

That matters because the failure is silent. The MP4 ``covr`` atom carries a
format flag, and both mutagen and ffmpeg believe whatever they are told — so
labelling WebP bytes as JPEG produces a file that validates perfectly, embeds a
cover, and shows a blank square in Plex. The only way to get this right is to
look at the bytes and convert anything that isn't already JPEG or PNG.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from .. import tools

# Formats every target (mutagen's covr atom, ffmpeg's attached_pic, Plex) shows.
JPEG = ".jpg"
PNG = ".png"


def sniff_image(data: bytes) -> str | None:
    """The real image format of these bytes, as an extension, or None.

    Magic numbers only — never the filename, which is routinely wrong.
    """
    if len(data) < 12:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return JPEG
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return PNG
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[:2] == b"BM":
        return ".bmp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return ".tiff"
    # SVG is text; a cover in vector form is rare but does happen.
    head = data[:256].lstrip()
    if head[:5] == b"<?xml" or head[:4] == b"<svg":
        return ".svg"
    return None


def normalize_cover(data: bytes) -> tuple[bytes, str] | None:
    """Return ``(bytes, extension)`` ready to embed, or None if unusable.

    JPEG and PNG pass through untouched. Anything else ffmpeg can decode is
    converted to JPEG. A cover that can't be converted is dropped rather than
    embedded in a format that would render as a blank square — no cover is an
    honest outcome, a broken one isn't.
    """
    if not data:
        return None
    kind = sniff_image(data)
    if kind in (JPEG, PNG):
        return data, kind
    converted = _to_jpeg(data, kind)
    return (converted, JPEG) if converted else None


def _to_jpeg(data: bytes, kind: str | None) -> bytes | None:
    """Transcode arbitrary image bytes to JPEG with ffmpeg, or None."""
    ffmpeg = tools.ffmpeg_path()
    if ffmpeg is None:
        return None
    # ffmpeg picks the demuxer from content, but a correct suffix helps it with
    # the formats where detection is weakest.
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / f"cover{kind or '.bin'}"
        dst = Path(td) / "cover.jpg"
        try:
            src.write_bytes(data)
            proc = tools.run(
                [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", src,
                 # Some sources decode with an alpha channel or an odd pixel
                 # format that the JPEG encoder rejects outright.
                 "-pix_fmt", "yuvj420p", "-frames:v", "1", dst],
                timeout=60,
            )
            if proc.returncode == 0 and dst.is_file() and dst.stat().st_size:
                return dst.read_bytes()
        except Exception:  # noqa: BLE001 - a cover is never worth failing over
            return None
    return None
