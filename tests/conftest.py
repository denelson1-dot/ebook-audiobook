"""Point every test's data root at an isolated temp dir so nothing touches the
real local-data/ and tests can run in parallel/repeatably."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path, monkeypatch):
    monkeypatch.setenv("EBAB_DATA_ROOT", str(tmp_path / "data"))
    yield


def _xhtml(title: str, paragraphs: list[str]) -> bytes:
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (
        f'<?xml version="1.0" encoding="utf-8"?>'
        f'<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{title}</title></head>'
        f"<body><h1>{title}</h1>{body}</body></html>"
    ).encode("utf-8")


@pytest.fixture
def synthetic_epub(tmp_path) -> Path:
    """A minimal, valid EPUB2 with two chapters — no copyrighted content."""
    path = tmp_path / "sample.epub"
    ch1 = _xhtml("Chapter I", ["It was the year 1999.", "He paid $5 to Mr. Smith."])
    ch2 = _xhtml("Chapter II", ["The 2nd day arrived.", "She walked 3 miles home."])

    opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Test Book</dc:title>
    <dc:creator>Test Author</dc:creator>
    <dc:identifier id="id">urn:uuid:test</dc:identifier>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="c1"/>
    <itemref idref="c2"/>
  </spine>
</package>"""

    ncx = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <navMap>
    <navPoint id="n1" playOrder="1"><navLabel><text>The First Chapter</text></navLabel><content src="ch1.xhtml"/></navPoint>
    <navPoint id="n2" playOrder="2"><navLabel><text>The Second Chapter</text></navLabel><content src="ch2.xhtml"/></navPoint>
  </navMap>
</ncx>"""

    with zipfile.ZipFile(path, "w") as z:
        # mimetype must be first and stored (uncompressed) per EPUB spec.
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>',
        )
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/toc.ncx", ncx)
        z.writestr("OEBPS/ch1.xhtml", ch1)
        z.writestr("OEBPS/ch2.xhtml", ch2)
    return path
