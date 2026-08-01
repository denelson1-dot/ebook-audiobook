"""Tests for choosing (and write-checking) the full-render output folder.

The point of the write-check is to fail *before* a multi-hour render, so these
cover the resolver's happy paths and each rejection, plus that a full render
actually lands in the chosen folder and the web route rejects a bad one.
"""

import os

import pytest

from ebook_audiobook import config, worker
from ebook_audiobook.config import VoiceSettings
from ebook_audiobook.jobs.models import Book, Chapter
from ebook_audiobook.jobs.store import JobStore
from ebook_audiobook.web import create_app

from ebook_audiobook import tools

HAVE_FFMPEG = tools.ffmpeg_path() is not None


def test_resolve_default_is_outputs_folder():
    out = worker.resolve_output_dir(None)
    assert out == config.paths().outputs
    assert out.is_dir()
    # Empty/whitespace also means "default".
    assert worker.resolve_output_dir("   ") == config.paths().outputs


def test_resolve_creates_missing_folder(tmp_path):
    target = tmp_path / "new" / "nested" / "out"
    assert not target.exists()
    out = worker.resolve_output_dir(str(target))
    assert out == target.resolve() and out.is_dir()


def test_resolve_expands_user(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    out = worker.resolve_output_dir("~/books")
    assert out == (tmp_path / "books").resolve() and out.is_dir()


def test_resolve_rejects_a_file(tmp_path):
    f = tmp_path / "not_a_dir.txt"
    f.write_text("x")
    with pytest.raises(worker.OutputDirError):
        worker.resolve_output_dir(str(f))


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file-mode permissions")
def test_resolve_rejects_unwritable_folder(tmp_path):
    ro = tmp_path / "readonly"
    ro.mkdir()
    ro.chmod(0o500)  # r-x: exists but not writable
    try:
        with pytest.raises(worker.OutputDirError):
            worker.resolve_output_dir(str(ro))
    finally:
        ro.chmod(0o700)  # let pytest clean up tmp_path


def _seed_job(job_id="outdirjob"):
    store = JobStore(job_id).ensure()
    store.save_book(Book(job_id=job_id, source_path="/none.epub", source_hash=job_id,
                         title="Fake Book", author="Nobody"))
    store.save_chapters([
        Chapter(chapter_id="ch0000", sequence=0, title="Chapter One",
                text="This is the first chapter. It has two sentences.", char_count=48),
    ])
    store.save_voice(VoiceSettings(engine="fake"))
    return store


@pytest.mark.ffmpeg
@pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg available")
def test_render_writes_to_chosen_folder(tmp_path):
    store = _seed_job()
    dest = tmp_path / "my audiobooks"
    state = worker.render_job("outdirjob", output_dir=str(dest))
    assert state.stage == "done"
    assert state.output_path.startswith(str(dest.resolve()))
    assert state.output_dir == str(dest.resolve())  # remembered for next time


def test_web_render_rejects_bad_output_dir(tmp_path):
    _seed_job("webbadjob")
    client = create_app().test_client()
    bad = tmp_path / "afile.txt"
    bad.write_text("x")
    r = client.post("/job/webbadjob/render", data={"output_dir": str(bad)})
    assert r.status_code == 400
    assert r.get_json()["error"]
    # A bad destination must not have queued any work.
    assert not client.get("/api/status").get_json()["busy"]
