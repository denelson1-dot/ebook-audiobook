from app.pipeline import extract


def test_parse_epub_metadata_and_chapters(synthetic_epub):
    book = extract.parse_epub(synthetic_epub)
    assert book.title == "Test Book"
    assert book.author == "Test Author"
    assert len(book.chapters) == 2


def test_parse_epub_uses_toc_titles(synthetic_epub):
    book = extract.parse_epub(synthetic_epub)
    titles = [c.title for c in book.chapters]
    assert titles == ["The First Chapter", "The Second Chapter"]


def test_parse_epub_extracts_text(synthetic_epub):
    book = extract.parse_epub(synthetic_epub)
    assert "1999" in book.chapters[0].text
    assert "Smith" in book.chapters[0].text


def test_parse_epub_strips_heading_from_body(synthetic_epub):
    # The body <h1> ("Chapter I") is pulled out so it isn't read twice; the
    # spoken title comes from the TOC instead.
    book = extract.parse_epub(synthetic_epub)
    assert book.chapters[0].title == "The First Chapter"
    assert "Chapter I" not in book.chapters[0].text


def test_extract_chapter_strips_heading_and_converts_hr():
    html = b"<html><body><h1>The Title</h1><p>First.</p><hr/><p>Second.</p></body></html>"
    heading, text = extract._extract_chapter(html)
    assert heading == "The Title"
    assert "The Title" not in text        # heading removed from the body
    assert "* * *" in text                # <hr> becomes a scene-break marker
    assert "First." in text and "Second." in text
