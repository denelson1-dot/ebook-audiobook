"""The path a freshly-installed user is actually on: bundled ffmpeg, no ffprobe.

Most users will never install ffmpeg themselves — they'll get the static build
that ships with the ``imageio-ffmpeg`` wheel, which contains ffmpeg but *not*
ffprobe. Validation therefore has to reach its verdict with ffmpeg alone, and a
missing ffprobe must never be reported as a broken audiobook.
"""

from __future__ import annotations

import shutil

import pytest

from ebook_audiobook import tools, worker
from ebook_audiobook.audio import validate
from ebook_audiobook.config import VoiceSettings
from ebook_audiobook.jobs.models import Book, Chapter
from ebook_audiobook.jobs.store import JobStore

HAVE_FFMPEG = tools.ffmpeg_path() is not None

pytestmark = pytest.mark.skipif(not HAVE_FFMPEG, reason="needs some ffmpeg")


@pytest.fixture
def no_ffprobe(monkeypatch):
    """Pretend ffprobe isn't installed, however it's looked up."""
    tools.reset_cache()
    real_which = shutil.which
    monkeypatch.setattr(tools.shutil, "which",
                        lambda n: None if n == "ffprobe" else real_which(n))
    monkeypatch.setattr(tools, "ffprobe_path", lambda: None)
    yield
    tools.reset_cache()


def _make_job(job_id="probeless") -> tuple[JobStore, Book]:
    store = JobStore(job_id).ensure()
    book = Book(job_id=job_id, source_path="x.epub", source_hash="h",
                title="Probeless Book", author="Test Author", year="2024")
    store.save_book(book)
    store.save_chapters([
        Chapter(chapter_id="ch0001", sequence=0, title="Chapter One",
                text="Words in the first chapter. " * 8, char_count=224),
        Chapter(chapter_id="ch0002", sequence=1, title="Chapter Two",
                text="Words in the second chapter. " * 8, char_count=232),
    ])
    store.save_voice(VoiceSettings(engine="fake"))
    return store, book


@pytest.mark.ffmpeg
def test_full_render_and_validation_without_ffprobe(no_ffprobe, tmp_path):
    assert tools.ffprobe_path() is None

    _store, book = _make_job()
    state = worker.render_job("probeless", output_dir=str(tmp_path / "out"),
                              output_mode=worker.MODE_FOLDER)

    from pathlib import Path

    out = Path(state.output_path)
    assert out.exists() and out.stat().st_size > 1000

    # The render only reaches DONE if validate_m4b found no problems, so getting
    # here already proves ffprobe-free validation passed. Assert the substance
    # directly too, so a regression names the real cause.
    probe = validate.probe_container(out)
    assert probe is not None, "ffmpeg-only probe returned nothing"
    assert probe["chapters"] == 2
    assert probe["duration"] and probe["duration"] > 0
    assert validate.validate_m4b(out, book) == []


@pytest.mark.ffmpeg
def test_ffmpeg_only_probe_matches_ffprobe(tmp_path):
    """When both are available they must agree, or the fallback is misleading."""
    if shutil.which("ffprobe") is None:
        pytest.skip("no system ffprobe to compare against")

    _store, book = _make_job("probecompare")
    state = worker.render_job("probecompare", output_dir=str(tmp_path / "out"),
                              output_mode=worker.MODE_FOLDER)

    from pathlib import Path

    out = Path(state.output_path)
    with_probe = validate._probe_with_ffprobe(out)
    without = validate._probe_with_ffmpeg(out)

    assert with_probe and without
    assert with_probe["chapters"] == without["chapters"]
    assert abs(with_probe["duration"] - without["duration"]) < 0.5
