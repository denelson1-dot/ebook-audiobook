"""Cover art: the failure here is silent, which is what makes it worth testing.

The MP4 `covr` atom carries a format flag that mutagen and ffmpeg both take on
trust. Hand them WebP bytes labelled JPEG and you get a file that passes every
validation check, reports a cover, and shows a blank square in Plex. So the
format is decided by the bytes, never the filename.
"""

from __future__ import annotations

import subprocess

import pytest

from ebook_audiobook import tools
from ebook_audiobook.pipeline import cover

HAVE_FFMPEG = tools.ffmpeg_path() is not None

# Smallest real headers of each format — enough for the sniffer, which only ever
# looks at the first few bytes.
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 16
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
GIF_BYTES = b"GIF89a" + b"\x00" * 16
WEBP_BYTES = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 16
BMP_BYTES = b"BM" + b"\x00" * 20
TIFF_BYTES = b"II*\x00" + b"\x00" * 16
SVG_BYTES = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>'


@pytest.mark.parametrize("data,expected", [
    (JPEG_BYTES, ".jpg"),
    (PNG_BYTES, ".png"),
    (GIF_BYTES, ".gif"),
    (WEBP_BYTES, ".webp"),
    (BMP_BYTES, ".bmp"),
    (TIFF_BYTES, ".tiff"),
    (SVG_BYTES, ".svg"),
])
def test_sniffs_each_format(data, expected):
    assert cover.sniff_image(data) == expected


@pytest.mark.parametrize("data", [b"", b"short", b"not an image at all, really"])
def test_unrecognisable_data_sniffs_as_nothing(data):
    assert cover.sniff_image(data) is None


def test_a_png_named_jpg_is_identified_by_its_bytes():
    """The exact case that produces a blank cover: the extension lies."""
    assert cover.sniff_image(PNG_BYTES) == ".png"


def test_jpeg_and_png_pass_through_untouched():
    """No re-encoding: the publisher's cover should reach Plex as-is."""
    assert cover.normalize_cover(JPEG_BYTES) == (JPEG_BYTES, ".jpg")
    assert cover.normalize_cover(PNG_BYTES) == (PNG_BYTES, ".png")


def test_nothing_is_returned_for_no_cover():
    assert cover.normalize_cover(b"") is None


def test_an_undecodable_cover_is_dropped_not_mislabelled():
    """No cover is honest; a broken one that reports success is not."""
    assert cover.normalize_cover(b"definitely not an image") is None


def test_conversion_is_skipped_when_ffmpeg_is_missing(monkeypatch):
    """Users on the bundled ffmpeg-less path must not crash on an odd cover."""
    monkeypatch.setattr(cover.tools, "ffmpeg_path", lambda: None)
    assert cover.normalize_cover(WEBP_BYTES) is None


def _render(tmp_path, ext):
    """A real, decodable image in the requested format, via ffmpeg."""
    p = tmp_path / f"cover.{ext}"
    subprocess.run(
        [str(tools.ffmpeg_path()), "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "color=c=red:s=64x64", "-frames:v", "1", str(p)],
        check=True,
    )
    return p.read_bytes()


@pytest.mark.ffmpeg
@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
@pytest.mark.parametrize("ext", ["gif", "webp", "bmp"])
def test_formats_players_cannot_show_are_converted_to_jpeg(tmp_path, ext):
    """WebP covers are increasingly common and render nowhere in this chain."""
    result = cover.normalize_cover(_render(tmp_path, ext))
    assert result is not None, f"a real {ext} cover was dropped"
    data, out_ext = result
    assert out_ext == ".jpg"
    assert cover.sniff_image(data) == ".jpg", "conversion produced non-JPEG bytes"


@pytest.mark.ffmpeg
@pytest.mark.skipif(not HAVE_FFMPEG, reason="needs ffmpeg")
def test_a_real_png_cover_is_left_alone(tmp_path):
    original = _render(tmp_path, "png")
    assert cover.normalize_cover(original) == (original, ".png")
