"""End-to-end pipeline tests using the fake engine (no GPU).

The render+assemble+package tests need ffmpeg; the full convert test also needs
Calibre. Both tools are required for real use, so exercising them here is the
point — but they're marked so they can be skipped in a tool-less environment.
"""

import json
import pathlib
import shutil
import subprocess

import pytest

from app import config
from app.config import VoiceSettings
from app.jobs.models import Book, Chapter, JobState
from app.jobs.store import JobStore
from app.pipeline import assemble, extract
from app import worker


def test_intro_chapter_announces_title_and_author():
    intro = worker._intro_chapter(Book(job_id="j", source_path="", source_hash="j",
                                       title="The Great Book", author="Jane Doe"))
    assert intro is not None
    assert intro.chapter_id == "intro" and intro.include is True
    assert intro.title == "The Great Book"   # spoken as the title card + m4b marker
    assert intro.text == "By Jane Doe."

    # Title only -> no author line; missing metadata -> no intro at all.
    t_only = worker._intro_chapter(Book(job_id="j", source_path="", source_hash="j",
                                        title="Solo", author="Unknown Author"))
    assert t_only.title == "Solo" and t_only.text == ""
    assert worker._intro_chapter(Book(job_id="j", source_path="", source_hash="j")) is None


def test_outro_chapter_signs_off_without_speaking_marker():
    outro = worker._outro_chapter(Book(job_id="j", source_path="", source_hash="j",
                                       title="The Great Book", author="Jane Doe"))
    assert outro is not None
    assert outro.chapter_id == "outro" and outro.include is True
    # The marker is a display-only nav label; the body is the spoken sign-off.
    assert outro.title == "The End" and outro.speak_title is False
    assert outro.text == "This concludes The Great Book, by Jane Doe."

    # Title only, and no metadata at all.
    assert worker._outro_chapter(Book(job_id="j", source_path="", source_hash="j",
                                      title="Solo", author="Unknown Author")).text == "This concludes Solo."
    assert worker._outro_chapter(Book(job_id="j", source_path="", source_hash="j")) is None


def test_outro_marker_is_not_narrated():
    ch = Chapter(chapter_id="outro", sequence=0, title="The End",
                 text="This concludes the book.", speak_title=False)
    segs = worker.build_segments([ch], "fake-1", "vk")
    # Only the body is spoken — the "The End" marker never becomes a segment.
    assert all("The End" != s.text for s in segs)
    assert any("concludes the book" in s.text for s in segs)


def test_default_included_skips_front_and_back_matter():
    skip = ["Copyright", "Ebook ISBN", "Table of Contents", "Contents",
            "Dedication", "Acknowledgements", "About the Author", "Also by Jane Doe",
            "Index", "Bibliography", "Other Titles",
            "What's next on your reading list?", "Up Next", "More from Jane Doe"]
    keep = ["Chapter One", "Prologue", "Epilogue", "Part I", "The Journey Home", ""]
    for t in skip:
        assert worker._default_included(t) is False, t
    for t in keep:
        assert worker._default_included(t) is True, t


def test_render_load_failure_surfaces_error(monkeypatch):
    store = _seed_job("loadfail")

    class BoomAdapter:
        engine_version = "boom"
        def load(self):
            raise RuntimeError("model load exploded")
        def unload(self):
            pass

    monkeypatch.setattr(worker, "get_adapter", lambda *a, **k: BoomAdapter())
    with pytest.raises(RuntimeError, match="model load exploded"):
        worker.render_job("loadfail")
    # A failure during model load must land on ERROR with the reason — never
    # leave the job stuck on "preparing" (the web runner swallows the exception).
    st = store.load_state()
    assert st.stage == "error" and "exploded" in (st.error or "")


def test_render_emits_initial_and_final_progress():
    _seed_job("progjob")
    seen = []
    worker.render_job("progjob", progress=lambda st: seen.append((st.stage, st.rendered_segments, st.total_segments)))
    rendering = [x for x in seen if x[0] == "rendering"]
    assert rendering, "no rendering progress callbacks fired"
    assert rendering[0][1] == 0            # initial 0/N emitted before first segment
    assert rendering[-1][1] == rendering[-1][2]  # ended at N/N


def test_render_raises_when_nothing_selected():
    store = _seed_job("emptyselect")
    chapters = store.load_chapters()
    for c in chapters:
        c.include = False
    store.save_chapters(chapters)
    with pytest.raises(RuntimeError, match="no sections selected"):
        worker.render_job("emptyselect")


def test_build_segments_pronunciation_rerenders_only_affected():
    ch = Chapter(chapter_id="ch0", sequence=0, title="LOG ENTRY",
                 text="The LOG says hi.\n\nAll quiet now.", char_count=30)
    base = worker.build_segments([ch], "fake-1", "vk")
    fixed = worker.build_segments([ch], "fake-1", "vk", {"LOG": "log"})
    # Segments with "LOG" get new ids (re-render); the untouched one is cached.
    assert base[0].segment_id != fixed[0].segment_id  # title
    assert base[1].segment_id != fixed[1].segment_id  # body w/ LOG
    assert base[2].segment_id == fixed[2].segment_id  # unaffected paragraph
    assert "log" in fixed[1].text and "LOG" not in fixed[1].text


def test_import_rejects_unsupported_formats_with_guidance(tmp_path):
    kfx = tmp_path / "novel.kfx"
    kfx.write_bytes(b"x")
    with pytest.raises(ValueError, match="KFX"):
        worker.import_ebook(str(kfx))

    unknown = tmp_path / "novel.xyz"
    unknown.write_bytes(b"x")
    with pytest.raises(ValueError, match="convert your book to EPUB"):
        worker.import_ebook(str(unknown))


def test_convert_error_message_flags_drm():
    from types import SimpleNamespace
    proc = SimpleNamespace(stdout="", stderr="calibre: DRMError: book is encrypted", returncode=1)
    msg = extract._convert_error_message(pathlib.Path("Purchased.azw3"), proc)
    assert "DRM-protected" in msg and "Purchased.azw3" in msg


def test_extract_job_records_error_on_failure(monkeypatch):
    store = JobStore("extractfail").ensure()
    store.save_book(Book(job_id="extractfail", source_path="/nope.epub", source_hash="extractfail"))
    store.save_state(JobState(job_id="extractfail"))

    def boom(*a, **k):
        raise extract.ExtractionError("This book appears to be DRM-protected.")
    monkeypatch.setattr(worker.extract, "run_ebook_convert", boom)

    with pytest.raises(extract.ExtractionError):
        worker.extract_job("extractfail")
    st = store.load_state()
    assert st.stage == "error" and "DRM-protected" in (st.error or "")


def test_gap_for_maps_boundaries():
    assert assemble.gap_for("sentence") == config.PAUSE_SENTENCE
    assert assemble.gap_for("paragraph") == config.PAUSE_PARAGRAPH
    assert assemble.gap_for("scene") == config.PAUSE_SCENE
    assert assemble.gap_for("chapter_title") == config.PAUSE_CHAPTER_TITLE
    assert assemble.gap_for("???") == config.PAUSE_SENTENCE  # safe default


def test_assemble_chapter_inserts_graduated_gaps(tmp_path):
    import numpy as np

    from app.audio.wav import duration_seconds, write_wav

    sr = 24000
    seg = np.zeros(int(0.5 * sr), dtype=np.float32)  # two 0.5s segments
    p1, p2 = tmp_path / "a.wav", tmp_path / "b.wav"
    write_wav(p1, seg, sr)
    write_wav(p2, seg, sr)
    out = tmp_path / "chap.wav"
    # gap after the last segment (0.3) is ignored; chapter_pause (1.0) applies.
    dur = assemble.assemble_chapter(out, [(p1, 0.7), (p2, 0.3)], sample_rate=sr, chapter_pause=1.0)
    expected = 0.5 + 0.7 + 0.5 + 1.0  # = 2.7s
    assert abs(dur - expected) < 0.02
    assert abs(duration_seconds(out) - expected) < 0.02

    # A lead pause (used for the outro) prepends silence before the first segment.
    out2 = tmp_path / "chap_lead.wav"
    dur2 = assemble.assemble_chapter(out2, [(p1, 0.3)], sample_rate=sr,
                                     chapter_pause=1.0, lead_pause=5.0)
    assert abs(dur2 - (5.0 + 0.5 + 1.0)) < 0.02


def test_build_segments_prepends_title_and_carries_boundaries():
    ch = Chapter(chapter_id="ch0000", sequence=0, title="Chapter One",
                 text="Para one here.\n\n* * *\n\nPara two here.", char_count=40)
    segs = worker.build_segments([ch], "fake-1", "vk")
    # B: title spoken first, with a chapter-title pause after
    assert segs[0].text == "Chapter One" and segs[0].boundary == "chapter_title"
    # C: the scene break is not spoken and upgrades the prior chunk's pause
    assert all("*" not in s.text for s in segs)
    assert segs[1].boundary == "scene"
    # boundary is NOT part of the content hash
    from app.hashing import segment_id
    assert segs[1].segment_id == segment_id(segs[1].text, "fake-1", "vk")


HAVE_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
HAVE_CALIBRE = shutil.which("ebook-convert") is not None


def _seed_job(job_id="fakejob"):
    store = JobStore(job_id).ensure()
    store.save_book(Book(job_id=job_id, source_path="/none.epub", source_hash=job_id,
                         title="Fake Book", author="Nobody"))
    store.save_chapters([
        Chapter(chapter_id="ch0000", sequence=0, title="Chapter One",
                text="This is the first chapter. It has two sentences.", char_count=48),
        Chapter(chapter_id="ch0001", sequence=1, title="Chapter Two",
                text="Second chapter here. Another sentence follows.", char_count=46),
    ])
    store.save_voice(VoiceSettings(engine="fake"))
    return store


def _ffprobe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_chapters", str(path)],
        capture_output=True, text=True,
    )
    return json.loads(out.stdout)


@pytest.mark.ffmpeg
@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_render_assemble_package_fake_produces_valid_m4b():
    _seed_job()
    state = worker.render_job("fakejob")
    assert state.stage == "done"
    out = state.output_path
    assert out and out.endswith(".m4b")

    probe = _ffprobe(out)
    # Real, non-empty audio with two chapter markers.
    assert float(probe["format"]["duration"]) > 1.0
    assert len(probe["chapters"]) == 2
    assert probe["chapters"][0]["tags"]["title"] == "Chapter One"


@pytest.mark.ffmpeg
@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe not installed")
def test_render_excludes_deselected_chapters():
    store = _seed_job("excludejob")
    chapters = store.load_chapters()
    chapters[0].include = False  # drop "Chapter One"
    store.save_chapters(chapters)

    state = worker.render_job("excludejob")
    assert state.stage == "done"
    # Only the kept chapter is rendered as work and packaged into the m4b.
    kept_segs = [s for s in store.load_segments() if s.chapter_id == "ch0001"]
    assert state.total_segments == len(kept_segs)
    probe = _ffprobe(state.output_path)
    titles = [c["tags"]["title"] for c in probe["chapters"]]
    assert titles == ["Chapter Two"]


@pytest.mark.ffmpeg
@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_resume_skips_already_rendered_segments():
    store = _seed_job("resumejob")
    worker.render_job("resumejob")
    seg = store.load_segments()[0]
    seg_path = store.segment_audio_path(seg.segment_id)
    first_mtime = seg_path.stat().st_mtime_ns

    # Re-run: content-addressed segments already exist, so they must be reused.
    worker.render_job("resumejob")
    assert seg_path.stat().st_mtime_ns == first_mtime


@pytest.mark.ffmpeg
@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_preview_produces_wav():
    _seed_job("previewjob")
    state = worker.render_job("previewjob", preview_max_seconds=1.0)
    # Preview is tracked separately and must NOT flip the lifecycle to "done"
    # or set the final output path.
    assert state.preview_output and state.preview_output.endswith("_preview.wav")
    assert state.preview_at
    assert state.stage != "done"
    assert state.output_path is None
    # Preview progress is measured by audio produced, not chapter segments, so
    # a completed preview fills the bar rather than stalling at a few percent.
    assert state.preview_progress > 0.0


@pytest.mark.calibre
@pytest.mark.ffmpeg
@pytest.mark.skipif(not (HAVE_FFMPEG and HAVE_CALIBRE), reason="need calibre + ffmpeg")
def test_full_convert_from_epub(synthetic_epub):
    job_id = worker.import_ebook(str(synthetic_epub), engine="fake")
    chapters = worker.extract_job(job_id)
    # A synthetic intro is prepended and a closing outro appended to the two
    # real chapters.
    assert len(chapters) == 4
    assert chapters[0].chapter_id == "intro" and chapters[0].title == "Test Book"
    assert chapters[-1].chapter_id == "outro" and chapters[-1].title == "The End"
    # Extraction should have normalized "$5" and "1999" to spoken form.
    joined = " ".join(c.text for c in chapters)
    assert "dollar" in joined and "[" not in joined

    state = worker.render_job(job_id)
    assert state.stage == "done"
    assert state.output_path.endswith(".m4b")
    probe = _ffprobe(state.output_path)
    # Intro marker, the two chapters, then the closing outro.
    titles = [c["tags"]["title"] for c in probe["chapters"]]
    assert titles == ["Test Book", "The First Chapter", "The Second Chapter", "The End"]
    # The outro is set apart by a long lead-in pause (>= ~4s of the 5s lead).
    outro = probe["chapters"][-1]
    assert float(outro["end_time"]) - float(outro["start_time"]) > 4.0
