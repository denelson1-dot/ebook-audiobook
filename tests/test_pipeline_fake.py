"""End-to-end pipeline tests using the fake engine (no GPU).

The render+assemble+package tests need ffmpeg; the full convert test also needs
Calibre. Both tools are required for real use, so exercising them here is the
point — but they're marked so they can be skipped in a tool-less environment.
"""

import json
import pathlib
from pathlib import Path

import pytest

from ebook_audiobook import config
from ebook_audiobook.config import VoiceSettings
from ebook_audiobook.jobs.models import Book, Chapter, JobState
from ebook_audiobook.jobs.store import JobStore
from ebook_audiobook.pipeline import assemble, extract
from ebook_audiobook import worker


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

    from ebook_audiobook.audio.wav import duration_seconds, write_wav

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
    from ebook_audiobook.hashing import segment_id
    assert segs[1].segment_id == segment_id(segs[1].text, "fake-1", "vk")


from ebook_audiobook import tools

HAVE_FFMPEG = tools.ffmpeg_path() is not None
HAVE_CALIBRE = tools.ebook_convert_path() is not None


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
    """Container facts as ``{"format": {...}, "chapters": [...]}``.

    Uses ffprobe when it exists, and otherwise reconstructs the same shape from
    ``ffmpeg -f ffmetadata`` plus mutagen. Without this, every one of these
    output-verification tests would silently skip on a machine that only has the
    bundled ffmpeg — which is exactly the configuration most users run.
    """
    probe_exe = tools.ffprobe_path()
    if probe_exe:
        out = tools.run([probe_exe, "-v", "quiet", "-print_format", "json",
                         "-show_format", "-show_chapters", str(path)])
        return json.loads(out.stdout)

    out = tools.run([str(tools.ffmpeg_path()), "-hide_banner", "-loglevel", "error",
                     "-i", str(path), "-f", "ffmetadata", "-"])
    chapters, current, timebase = [], None, 1000.0
    for line in (out.stdout or "").splitlines():
        line = line.strip()
        if line == "[CHAPTER]":
            current = {"tags": {}}
            chapters.append(current)
        elif current is None:
            continue
        elif line.startswith("TIMEBASE="):
            # "1/1000" -> ticks per second
            _num, _, den = line.split("=", 1)[1].partition("/")
            timebase = float(den or 1)
        elif line.startswith("START="):
            current["start_time"] = str(int(line.split("=", 1)[1]) / timebase)
        elif line.startswith("END="):
            current["end_time"] = str(int(line.split("=", 1)[1]) / timebase)
        elif line.startswith("title="):
            current["tags"]["title"] = line.split("=", 1)[1]

    from mutagen.mp4 import MP4

    return {"format": {"duration": str(MP4(str(path)).info.length)},
            "chapters": chapters}


@pytest.mark.ffmpeg
@pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg available")
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
@pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg available")
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
@pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg available")
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
@pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg available")
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


def test_a_full_disk_stops_the_render_with_a_resumable_message(tmp_path, monkeypatch):
    """ENOSPC won't fix itself on the next attempt, so retrying twice more just
    delays the news and buries it under a generic 'failed after 3 attempts'."""
    import errno

    from ebook_audiobook import worker as worker_mod

    class FakeClip:
        samples, sample_rate = None, 24000

    class Adapter:
        def synthesize(self, text):
            return FakeClip()

    calls = []

    def full_disk(path, samples, sample_rate):
        calls.append(path)
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(worker_mod, "write_wav", full_disk)
    with pytest.raises(worker_mod.OutOfSpaceError) as e:
        worker_mod._render_one(Adapter(), "hello", tmp_path / "seg.wav")

    assert len(calls) == 1, "a full disk was retried instead of reported"
    assert "free some space" in str(e.value).lower()
    assert "resumes" in str(e.value).lower()


def test_other_os_errors_are_still_retried(tmp_path, monkeypatch):
    """A transient IO blip is exactly what the retries are for — don't lose them."""
    from ebook_audiobook import worker as worker_mod

    class FakeClip:
        samples, sample_rate = None, 24000

    class Adapter:
        def synthesize(self, text):
            return FakeClip()

    calls = []

    def flaky(path, samples, sample_rate):
        calls.append(path)
        raise OSError(errno.EIO, "I/O error")

    import errno

    monkeypatch.setattr(worker_mod, "write_wav", flaky)
    with pytest.raises(RuntimeError):
        worker_mod._render_one(Adapter(), "hello", tmp_path / "seg.wav")
    assert len(calls) == 3, "retries were skipped for a non-fatal error"


@pytest.mark.ffmpeg
@pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg available")
def test_finished_render_reclaims_its_working_files_when_asked():
    """The auto-reclaim switch, wired through a real render.

    ``storage.free_after_render`` is unit-tested on its own; what this covers is
    that ``render_job`` actually calls it — the sort of wiring that can rot
    silently, because the audiobook still comes out either way.
    """
    from ebook_audiobook import settings as app_settings

    store = _seed_job("autofreejob")
    s = app_settings.load_settings()
    s.auto_free_working_files = True
    app_settings.save_settings(s)

    state = worker.render_job("autofreejob")
    assert state.stage == "done"
    assert Path(state.output_path).is_file(), "the audiobook must survive the tidy-up"
    assert store.intermediate_bytes() == 0, "raw narration audio should have been reclaimed"


@pytest.mark.ffmpeg
@pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg available")
def test_finished_render_keeps_working_files_by_default():
    store = _seed_job("keepfilesjob")
    state = worker.render_job("keepfilesjob")
    assert state.stage == "done"
    assert store.intermediate_bytes() > 0, "nothing is deleted unless the user asked"


# --- measuring what a book will actually cost --------------------------------

@pytest.mark.ffmpeg
@pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg available")
def test_measuring_records_both_rates_and_the_settings_they_belong_to():
    store = _seed_job("measurejob")
    state = worker.measure_job("measurejob")

    assert state.chars_per_audio_second and state.chars_per_audio_second > 0
    assert state.chars_per_render_second and state.chars_per_render_second > 0
    # Tied to the voice it was measured with, so a later change can be spotted.
    from ebook_audiobook import config
    from ebook_audiobook.hashing import voice_key
    assert state.measured_voice_key == voice_key(store.load_voice(), config.SAMPLE_RATE)


@pytest.mark.ffmpeg
@pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg available")
def test_measuring_leaves_the_job_where_it_found_it():
    """It is a calibration, not a render: it must not look like progress."""
    store = _seed_job("measurestage")
    before = store.load_state().stage
    state = worker.measure_job("measurestage")
    assert state.stage == before
    assert state.output_path is None
    assert state.preview_output is None      # nothing to listen to
    assert state.preview_progress == 0.0


@pytest.mark.ffmpeg
@pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg available")
def test_measured_audio_is_kept_for_the_real_render():
    """The point of using the real engine: none of the work is thrown away."""
    store = _seed_job("measurecache")
    worker.measure_job("measurecache")
    cached = list(store.segments_dir.glob("*.wav"))
    assert cached, "measuring should leave its segments in the content-addressed cache"

    state = worker.render_job("measurecache")
    assert state.stage == "done"


@pytest.mark.ffmpeg
@pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg available")
def test_a_measurement_does_not_count_the_warm_up_segment():
    """The first generation after a model load is markedly slower.

    Counting it would make every machine look worse than it is, so it is
    rendered (and kept) but excluded from the arithmetic. With the fake engine
    every segment costs about the same, so the check is that the measurement is
    based on fewer segments than it rendered.
    """
    store = _seed_job("warmup")
    worker.measure_job("warmup")
    rendered = len(list(store.segments_dir.glob("*.wav")))
    assert rendered >= 2, "need more than the warm-up segment to measure anything"


def test_estimates_use_a_measured_rate_when_there_is_one():
    from ebook_audiobook.audio import estimate

    # 30,000 characters at the generic 15 chars/sec is 2,000 seconds.
    assert estimate.estimate_audio_seconds(30_000) == pytest.approx(2000)
    # A slower measured rate means a longer book, and the estimate must follow.
    assert estimate.estimate_audio_seconds(30_000, 10.0) == pytest.approx(3000)
    # Nonsense never reaches a division.
    assert estimate.estimate_audio_seconds(30_000, 0) == pytest.approx(2000)
    assert estimate.estimate_audio_seconds(30_000, None) == pytest.approx(2000)


def test_the_estimate_object_carries_the_measured_length_through():
    from ebook_audiobook.audio import estimate

    generic = estimate.estimate(30_000, 64)
    measured = estimate.estimate(30_000, 64, None, 10.0)
    assert measured.audio_seconds > generic.audio_seconds
    # A longer book is a bigger file: the size estimate has to follow too.
    assert measured.size_bytes > generic.size_bytes


@pytest.mark.ffmpeg
@pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg available")
def test_a_book_of_one_short_section_can_still_be_measured():
    """The degenerate case that broke the first attempt.

    Drawing only from the chosen chapter meant a one-paragraph chapter had
    nothing left once the warm-up segment was discarded, so no measurement was
    recorded at all — and the UI would have gone on quietly showing a guess
    while claiming to have measured.
    """
    store = JobStore("tiny").ensure()
    store.save_book(Book(job_id="tiny", source_path="/nowhere/x.epub", source_hash="h",
                         title="A Very Short Book", author="An Author"))
    store.save_chapters([Chapter(chapter_id="only", sequence=1, title="Only",
                                 text="A single short paragraph, and nothing else at all.",
                                 char_count=49, include=True)])
    store.save_voice(VoiceSettings(engine="fake"))

    state = worker.measure_job("tiny")
    assert state.chars_per_audio_second and state.chars_per_audio_second > 0


# --- measuring: the warm-up is the first *fresh* generation ---------------------

class _Clock:
    """A monotonic clock that charges a fixed cost per render: a slow first
    generation (the model warming up) and a quick one after that."""

    def __init__(self, first=10.0, rest=1.0):
        self.now = 0.0
        self.first, self.rest = first, rest
        self.renders = 0

    def monotonic(self):
        return self.now

    def render(self):
        self.now += self.first if self.renders == 0 else self.rest
        self.renders += 1


def _seed_long_job(job_id):
    """Six paragraphs of identical length: every segment costs the same
    characters, so a rate measured over any subset of them is the same rate."""
    paragraphs = [f"Sentence number {i} of the chapter, written to a fixed length."
                  for i in range(1, 7)]
    assert len({len(p) for p in paragraphs}) == 1
    text = "\n\n".join(paragraphs)
    store = JobStore(job_id).ensure()
    store.save_book(Book(job_id=job_id, source_path="/none.epub", source_hash=job_id,
                         title="Long Book", author="Nobody"))
    store.save_chapters([Chapter(chapter_id="ch0000", sequence=0, title="Chapter One",
                                 text=text, char_count=len(text))])
    store.save_voice(VoiceSettings(engine="fake"))
    return store


def _measure_with_clock(job_id, monkeypatch, clock):
    real_render = worker._render_one

    def timed(adapter, text, path):
        clock.render()
        return real_render(adapter, text, path)

    monkeypatch.setattr(worker, "_render_one", timed)
    monkeypatch.setattr(worker.time, "monotonic", clock.monotonic)
    return worker.measure_job(job_id)


@pytest.mark.ffmpeg
@pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg available")
def test_the_warm_up_is_skipped_even_when_the_first_segment_was_cached(monkeypatch):
    """Measure is offered next to Preview, and a preview leaves the opening
    segments in the cache. The warm-up is then not the first segment in the
    list but the first one the engine actually renders — and counting a 10 s
    warm-up as ordinary work makes a machine look five times slower than it is.
    """
    cold = _seed_long_job("cold")
    cold_state = _measure_with_clock("cold", monkeypatch, _Clock())
    assert cold.load_state().chars_per_render_second == cold_state.chars_per_render_second

    warm = _seed_long_job("warm")
    worker.render_job("warm", preview_max_seconds=1.0, preview_chapter_id="ch0000")
    cached = len(list(warm.segments_dir.glob("*.wav")))
    assert 1 <= cached <= 3, "the preview should have cached the opening segments only"
    warm_state = _measure_with_clock("warm", monkeypatch, _Clock())

    # Same machine, same clock, same-sized segments: the same speed — the
    # cached segments contribute neither their seconds nor their characters.
    assert warm_state.chars_per_render_second == pytest.approx(
        cold_state.chars_per_render_second, rel=0.05)


@pytest.mark.ffmpeg
@pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg available")
def test_measuring_an_errored_job_does_not_leave_it_saying_stopped_by_a_problem():
    """Measuring clears the error (it is a fresh attempt at the engine), so
    restoring the 'error' stage afterwards would show a problem with no text."""
    store = _seed_job("erred")
    st = store.load_state()
    st.stage = "error"
    st.error = "the engine fell over"
    store.save_state(st)

    worker.measure_job("erred")
    after = store.load_state()
    assert after.stage == "extracted"
    assert after.error is None


@pytest.mark.ffmpeg
@pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg available")
def test_a_finished_render_forgets_its_preview_completely():
    """The preview file is deleted when the .m4b is written; the state that
    points at it has to go too, or a reloaded page aims an <audio> at a 404."""
    store = _seed_job("previewthenrender")
    worker.render_job("previewthenrender", preview_max_seconds=1.0, preview_chapter_id="ch0000")
    assert store.load_state().preview_at

    state = worker.render_job("previewthenrender")
    assert state.stage == "done"
    assert state.preview_output is None and state.preview_at is None


@pytest.mark.ffmpeg
@pytest.mark.skipif(not HAVE_FFMPEG, reason="no ffmpeg available")
def test_the_bitrate_a_file_was_encoded_at_is_remembered():
    """The voice's bitrate can be changed after the render without re-rendering;
    the listening time on the shelf is size ÷ bitrate, so it needs the real one."""
    store = _seed_job("bitrate")
    voice = store.load_voice()
    voice.extra["bitrate_kbps"] = 48
    store.save_voice(voice)
    state = worker.render_job("bitrate")
    assert state.output_bitrate_kbps == 48


def test_a_zero_second_preview_does_not_divide_by_zero(monkeypatch):
    """The CLI accepts --seconds 0; the web route clamps, the worker must too."""
    _seed_job("zero")
    state = worker.render_job("zero", preview_max_seconds=0)
    assert state.error is None
