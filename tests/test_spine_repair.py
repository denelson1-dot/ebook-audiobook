"""Repairing EPUBs whose spine lists something that isn't a document.

Some commercial EPUBs put the cover image in the reading order. Calibre walks
the spine expecting parsed documents, hits raw image bytes and dies with a bare
TypeError from its CSS flattener. We drop the offending entry first.

The risk to guard against is over-reach: dropping a genuine chapter would
silently truncate a book, which is much worse than the crash being avoided. So
these tests pin what is left alone as firmly as what is removed.
"""

from __future__ import annotations

import subprocess
import zipfile

import pytest

from ebook_audiobook.pipeline import extract


def _epub(path, manifest: str, spine: str) -> None:
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Spine Test</dc:title>
  </metadata>
  <manifest>{manifest}</manifest>
  <spine toc="ncx">{spine}</spine>
</package>"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/book.xhtml", "<html><body><p>text</p></body></html>")
        z.writestr("OEBPS/cover.jpeg", b"\xff\xd8\xff\xe0not-really-a-jpeg")


def _spine_ids(epub_path) -> list[str]:
    from lxml import etree

    with zipfile.ZipFile(epub_path) as z:
        root = etree.fromstring(z.read("OEBPS/content.opf"))
    return [r.get("idref") for r in root.iterfind(".//{*}spine/{*}itemref")]


COVER_IN_SPINE = (
    '<item id="cover" href="cover.jpeg" media-type="image/jpeg"/>'
    '<item id="book" href="book.xhtml" media-type="application/xhtml+xml"/>'
)


# --- what gets repaired ------------------------------------------------------

def test_drops_an_image_from_the_spine(tmp_path):
    """The exact shape that crashed Calibre on a real book."""
    src = tmp_path / "in.epub"
    _epub(src, COVER_IN_SPINE, '<itemref idref="cover"/><itemref idref="book"/>')

    result = extract.repair_epub_spine(src, tmp_path / "out.epub")
    assert result is not None
    dest, dropped = result
    assert dropped == ["cover"]
    assert _spine_ids(dest) == ["book"]


def test_the_repaired_file_is_still_a_valid_epub(tmp_path):
    src = tmp_path / "in.epub"
    _epub(src, COVER_IN_SPINE, '<itemref idref="cover"/><itemref idref="book"/>')
    dest, _ = extract.repair_epub_spine(src, tmp_path / "out.epub")

    with zipfile.ZipFile(dest) as z:
        first = z.infolist()[0]
        assert first.filename == "mimetype"          # must be first...
        assert first.compress_type == zipfile.ZIP_STORED  # ...and uncompressed
        assert z.read("mimetype") == b"application/epub+zip"
        # everything else survived the round trip
        assert z.read("OEBPS/book.xhtml")
        assert z.read("OEBPS/cover.jpeg").startswith(b"\xff\xd8")


def test_the_cover_stays_in_the_manifest(tmp_path):
    """Only the reading order is wrong; the cover itself must survive."""
    from lxml import etree

    src = tmp_path / "in.epub"
    _epub(src, COVER_IN_SPINE, '<itemref idref="cover"/><itemref idref="book"/>')
    dest, _ = extract.repair_epub_spine(src, tmp_path / "out.epub")

    with zipfile.ZipFile(dest) as z:
        root = etree.fromstring(z.read("OEBPS/content.opf"))
    ids = [i.get("id") for i in root.iterfind(".//{*}manifest/{*}item")]
    assert "cover" in ids


@pytest.mark.parametrize("media_type", [
    "image/jpeg", "image/png", "text/css", "application/x-dtbncx+xml",
    "font/woff2", "audio/mpeg", "video/mp4", "IMAGE/JPEG",
])
def test_drops_every_kind_of_non_document(tmp_path, media_type):
    src = tmp_path / "in.epub"
    _epub(
        src,
        f'<item id="junk" href="cover.jpeg" media-type="{media_type}"/>'
        '<item id="book" href="book.xhtml" media-type="application/xhtml+xml"/>',
        '<itemref idref="junk"/><itemref idref="book"/>',
    )
    result = extract.repair_epub_spine(src, tmp_path / "out.epub")
    assert result is not None, f"{media_type} should have been dropped"
    assert _spine_ids(result[0]) == ["book"]


# --- what is deliberately left alone -----------------------------------------

def test_a_healthy_book_is_not_touched(tmp_path):
    src = tmp_path / "in.epub"
    _epub(src, COVER_IN_SPINE, '<itemref idref="book"/>')
    assert extract.repair_epub_spine(src, tmp_path / "out.epub") is None


@pytest.mark.parametrize("media_type", [
    "application/xhtml+xml", "text/html", "application/x-dtbook+xml", "",
])
def test_documents_and_unknown_types_are_kept(tmp_path, media_type):
    """An unknown media type must be assumed to be a chapter.

    Dropping a real chapter would silently shorten the audiobook, which is a
    far worse failure than the crash this repair exists to avoid.
    """
    src = tmp_path / "in.epub"
    _epub(
        src,
        f'<item id="odd" href="book.xhtml" media-type="{media_type}"/>'
        '<item id="book" href="book.xhtml" media-type="application/xhtml+xml"/>',
        '<itemref idref="odd"/><itemref idref="book"/>',
    )
    assert extract.repair_epub_spine(src, tmp_path / "out.epub") is None


def test_an_unreadable_file_is_left_for_calibre_to_diagnose(tmp_path):
    """Calibre's own errors are better than anything we'd invent here."""
    src = tmp_path / "not.epub"
    src.write_bytes(b"this is not a zip file at all")
    assert extract.repair_epub_spine(src, tmp_path / "out.epub") is None


def test_a_missing_idref_target_is_left_alone(tmp_path):
    """A spine pointing at a manifest id that doesn't exist is a different bug."""
    src = tmp_path / "in.epub"
    _epub(src, COVER_IN_SPINE, '<itemref idref="ghost"/><itemref idref="book"/>')
    assert extract.repair_epub_spine(src, tmp_path / "out.epub") is None


# --- the error message -------------------------------------------------------

def _failure(stderr: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)


def test_epub_input_is_not_told_to_convert_to_epub(tmp_path):
    """The advice that sent us round in circles on a real book."""
    msg = extract._convert_error_message(tmp_path / "book.epub", _failure("boom"))
    assert "convert it to EPUB in Calibre first" not in msg
    assert "already an EPUB" in msg


def test_non_epub_input_still_gets_the_conversion_advice(tmp_path):
    msg = extract._convert_error_message(tmp_path / "book.pdf", _failure("boom"))
    assert "convert it to EPUB in Calibre first" in msg


def test_the_spine_crash_is_named_rather_than_dumped(tmp_path):
    trace = ("File \"/usr/lib/calibre/calibre/ebooks/oeb/transforms/flatcss.py\", "
             "line 278, in stylize_spine\n"
             "TypeError: argument should be integer or bytes-like object, not 'str'")
    msg = extract._convert_error_message(tmp_path / "book.epub", _failure(trace))
    assert "isn't a document" in msg
    assert "TypeError" not in msg
