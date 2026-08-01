"""Packaging: the last step, where a failure costs the whole render."""

from __future__ import annotations

import subprocess
import types
from pathlib import Path

import pytest

from ebook_audiobook.pipeline import package


def test_encode_timeout_scales_with_book_length():
    """A fixed hour suited neither a novella nor a 60-hour doorstop."""
    short = package.encode_timeout(600)          # 10 minutes of audio
    doorstop = package.encode_timeout(60 * 3600)  # 60 hours
    assert short == package.MIN_ENCODE_TIMEOUT   # floor applies
    assert doorstop > 3600, "a very long book would be cut off by the old fixed cap"
    assert doorstop == int(60 * 3600 * package.ENCODE_TIMEOUT_PER_AUDIO_SECOND)


def test_a_locked_output_file_is_explained(tmp_path):
    """Windows and network shares lock a file that's open in a player."""
    proc = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="",
        stderr="Error opening output file: Permission denied",
    )
    msg = package._encode_error_message(tmp_path / "Book.m4b", proc)
    assert "open in a player" in msg
    assert "Permission denied" not in msg  # replaced, not merely appended


def test_a_full_disk_is_explained(tmp_path):
    proc = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="av_interleaved_write_frame(): No space left on device",
    )
    msg = package._encode_error_message(tmp_path / "Book.m4b", proc)
    assert "disk space" in msg
    assert "resumes" in msg  # the render isn't lost, so say so


def test_an_unknown_failure_still_shows_ffmpeg_output(tmp_path):
    proc = subprocess.CompletedProcess(args=[], returncode=69, stdout="",
                                       stderr="something exotic went wrong")
    msg = package._encode_error_message(tmp_path / "Book.m4b", proc)
    assert "something exotic went wrong" in msg
    assert "69" in msg


def test_unreadable_chapter_audio_names_the_chapter(tmp_path):
    """Muxing on regardless would ship an audiobook silently missing a chapter."""
    missing = tmp_path / "gone.wav"
    with pytest.raises(package.PackagingError) as e:
        package._chapter_seconds([package.ChapterAudio(title="Chapter Four", path=missing)])
    assert "Chapter Four" in str(e.value)


def test_a_timeout_removes_the_half_written_file(tmp_path, monkeypatch):
    """A truncated .m4b left behind would look like a finished audiobook."""
    out = tmp_path / "Book.m4b"
    out.write_bytes(b"partial")

    monkeypatch.setattr(package.tools, "require_ffmpeg", lambda: Path("/bin/true"))
    monkeypatch.setattr(package, "_chapter_seconds", lambda chapters: [1.0])

    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=900)

    monkeypatch.setattr(package.tools, "run", timeout)
    with pytest.raises(package.PackagingError) as e:
        package.package_m4b(out, [package.ChapterAudio("One", tmp_path / "a.wav")],
                            "T", "A", workdir=tmp_path)
    assert not out.exists(), "a truncated .m4b was left looking like a finished book"
    assert "resume" in str(e.value).lower()
