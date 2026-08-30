"""Plex-compatible output: settings, library foldering, metadata, tagging.

The tagging/validation integration test needs ffmpeg (to mux a real .m4b and a
real cover); the rest is pure-Python and always runs.
"""

import subprocess
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from ebook_audiobook import settings, worker
from ebook_audiobook.config import VoiceSettings
from ebook_audiobook.jobs.models import Book, Chapter
from ebook_audiobook.jobs.store import JobStore
from ebook_audiobook.pipeline import extract, layout
from ebook_audiobook.web import create_app

from ebook_audiobook import tools

HAVE_FFMPEG = tools.ffmpeg_path() is not None


# --- settings store ----------------------------------------------------------

def test_settings_roundtrip():
    assert settings.load_settings().audiobooks_root is None  # isolated data root
    s = settings.load_settings()
    s.audiobooks_root = "/tmp/books"
    s.setup_dismissed = True
    settings.save_settings(s)
    reloaded = settings.load_settings()
    assert reloaded.audiobooks_root == "/tmp/books" and reloaded.setup_dismissed
    assert settings.audiobooks_root() == "/tmp/books"


def test_default_output_mode_follows_root():
    assert worker.default_output_mode() == worker.MODE_FOLDER  # no root yet
    s = settings.load_settings()
    s.audiobooks_root = "/tmp/books"
    settings.save_settings(s)
    assert worker.default_output_mode() == worker.MODE_LIBRARY


# --- filename sanitization + library layout ---------------------------------

def test_sanitize_strips_illegal_but_stays_readable():
    out = layout.sanitize_component('Wolf: A Story?/\\<x>|y')
    for bad in '/\\:*?"<>|':
        assert bad not in out
    assert out.startswith("Wolf - A Story")  # colon became " - ", spaces kept
    assert layout.sanitize_component("   ") == "Unknown"  # fallback
    assert not layout.sanitize_component("name. ").endswith((".", " "))  # trailing trim


def test_library_path_with_and_without_year_and_series():
    book = Book(job_id="x", source_path="", source_hash="x",
                title="The Hobbit", author="J.R.R. Tolkien", year="1937")
    assert layout.library_m4b_path(Path("/lib"), book) == \
        Path("/lib/J.R.R. Tolkien/The Hobbit (1937)/The Hobbit.m4b")

    book.year = None
    assert layout.library_dir(Path("/lib"), book).name == "The Hobbit"  # no "(Unknown)"

    book.year = "1937"
    book.series = "Middle Earth"
    book.series_index = "2"
    assert layout.library_m4b_path(Path("/lib"), book) == \
        Path("/lib/J.R.R. Tolkien/Middle Earth/02 - The Hobbit (1937)/The Hobbit.m4b")


# --- OPF metadata extraction -------------------------------------------------

def test_metadata_parsing_from_opf():
    opf = """<?xml version="1.0"?>
    <package xmlns="http://www.idpf.org/2007/opf">
      <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf">
        <dc:title>T</dc:title>
        <dc:date>2019-05-01</dc:date>
        <dc:description>&lt;p&gt;A grand tale.&lt;/p&gt;</dc:description>
        <dc:identifier opf:scheme="ISBN">978-0-13-475759-9</dc:identifier>
        <meta name="calibre:series" content="My Series"/>
        <meta name="calibre:series_index" content="3.0"/>
      </metadata>
    </package>"""
    meta = extract._metadata(BeautifulSoup(opf, "xml"))
    assert meta["year"] == "2019"
    assert meta["isbn"] == "9780134757599"
    assert meta["description"] and "grand tale" in meta["description"].lower()
    assert "<p>" not in meta["description"]  # markup stripped
    assert meta["series"] == "My Series"
    assert meta["series_index"] == "3.0"


def test_creator_cleaning_strips_stray_semicolons():
    assert extract._clean_creator("Andy Weir;") == "Andy Weir"      # trailing separator
    assert extract._clean_creator("Andy Weir; Jane Doe") == "Andy Weir, Jane Doe"
    assert extract._clean_creator("  ; ; ") == ""                    # all blank
    assert extract._clean_meta("Project Hail Mary;") == "Project Hail Mary"


def test_sanitize_trims_trailing_separators():
    # Defensive: even an author already stored as "Andy Weir;" folders cleanly.
    assert layout.sanitize_component("Andy Weir;") == "Andy Weir"
    assert layout.library_dir(Path("/lib"),
        Book(job_id="x", source_path="", source_hash="x",
             title="T", author="Andy Weir;", year="2021")).parts[2] == "Andy Weir"


def test_metadata_absent_is_all_none():
    opf = '<package><metadata><dc:title xmlns:dc="http://purl.org/dc/elements/1.1/">T</dc:title></metadata></package>'
    meta = extract._metadata(BeautifulSoup(opf, "xml"))
    assert all(v is None for v in meta.values())


# --- web settings validation -------------------------------------------------

def test_web_settings_saves_and_rejects_bad_root(tmp_path):
    client = create_app().test_client()
    good = tmp_path / "library"
    r = client.post("/settings", data={"audiobooks_root": str(good)})
    assert r.status_code == 200 and r.get_json()["audiobooks_root"] == str(good.resolve())
    assert settings.audiobooks_root() == str(good.resolve())

    bad = tmp_path / "afile.txt"
    bad.write_text("x")
    r = client.post("/settings", data={"audiobooks_root": str(bad)})
    assert r.status_code == 400 and r.get_json()["error"]


def test_setup_banner_shows_until_dismissed():
    """The nag for a library that already has books in it.

    An *empty* library has no banner: its whole page is a setup checklist, and
    two prompts for the same folder would just be one too many. So this puts a
    book on the shelf first, which is also the case that matters — the person
    who imported something before choosing where finished books should go.
    """
    from ebook_audiobook.jobs.models import Book
    from ebook_audiobook.jobs.store import JobStore

    store = JobStore("bannerjob").ensure()
    store.save_book(Book(job_id="bannerjob", source_path="/nowhere/x.epub",
                         source_hash="h", title="A Book", author="An Author"))

    client = create_app().test_client()
    assert b"audiobooks library folder" in client.get("/").data
    client.post("/settings/dismiss-setup")
    assert b"audiobooks library folder" not in client.get("/").data


def test_empty_library_asks_for_the_folder_in_its_own_words():
    """Nothing imported yet: the folder step lives in the welcome checklist."""
    client = create_app().test_client()
    body = client.get("/").data
    assert b"Choose where finished books go" in body
    assert b"audiobooks library folder" not in body  # no duplicate banner


# --- library-mode render: tree + tags + sidecar (ffmpeg) ---------------------

def _make_jpeg(path: Path) -> None:
    subprocess.run([str(tools.ffmpeg_path()), "-y", "-f", "lavfi", "-i", "color=c=blue:s=32x32",
                    "-frames:v", "1", str(path)], capture_output=True, check=True)


@pytest.mark.ffmpeg
@pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg available")
def test_library_render_builds_tree_tags_and_sidecar(tmp_path):
    root = tmp_path / "Audiobooks"
    s = settings.load_settings()
    s.audiobooks_root = str(root)
    settings.save_settings(s)

    store = JobStore("libjob").ensure()
    cover = store.dir / "cover.jpg"
    _make_jpeg(cover)
    store.save_book(Book(job_id="libjob", source_path="/none.epub", source_hash="libjob",
                         title="Sky Over Water", author="Ada Lovelace", year="2021",
                         cover_path=str(cover)))
    store.save_chapters([Chapter(chapter_id="ch0000", sequence=0, title="One",
                                 text="First chapter. It has two sentences.", char_count=36)])
    store.save_voice(VoiceSettings(engine="fake"))

    state = worker.render_job("libjob")  # no args -> library mode (root is set)
    assert state.stage == "done"
    assert state.output_mode == worker.MODE_LIBRARY

    expect = root / "Ada Lovelace" / "Sky Over Water (2021)" / "Sky Over Water.m4b"
    assert Path(state.output_path) == expect.resolve() and expect.exists()
    assert (expect.parent / "cover.jpg").exists()  # sidecar

    from ebook_audiobook.audio.tag import read_tags
    tags = read_tags(expect)
    assert tags["stik"] == 2                       # marked as Audiobook
    assert tags["album_artist"] == "Ada Lovelace"  # author matching
    assert tags["album"] == "Sky Over Water"
    assert tags["has_cover"]


@pytest.mark.ffmpeg
@pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg available")
def test_delete_prunes_empty_library_dirs_but_keeps_siblings(tmp_path):
    root = tmp_path / "Audiobooks"
    s = settings.load_settings()
    s.audiobooks_root = str(root)
    settings.save_settings(s)

    def seed_and_render(job_id, title):
        store = JobStore(job_id).ensure()
        store.save_book(Book(job_id=job_id, source_path="/n.epub", source_hash=job_id,
                             title=title, author="Same Author", year="2000"))
        store.save_chapters([Chapter(chapter_id="ch0000", sequence=0, title="One",
                                     text="A chapter. Two sentences here.", char_count=30)])
        store.save_voice(VoiceSettings(engine="fake"))
        worker.render_job(job_id)
        return store

    a = seed_and_render("bookA", "Book A")
    b = seed_and_render("bookB", "Book B")
    author_dir = root / "Same Author"
    assert (author_dir / "Book A (2000)").exists()
    assert (author_dir / "Book B (2000)").exists()

    a.delete()
    # A's own folder is gone; the shared Author folder and B survive.
    assert not (author_dir / "Book A (2000)").exists()
    assert author_dir.exists()
    assert (author_dir / "Book B (2000)" / "Book B.m4b").exists()

    # Deleting the last book prunes the Author folder too, but never the root.
    b.delete()
    assert not author_dir.exists()
    assert root.exists()
