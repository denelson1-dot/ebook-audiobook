"""Web-layer integration tests. The heavier ones use Calibre + ffmpeg via the
fake engine (no GPU), driving the same routes the browser hits."""

import time

import pytest

from ebook_audiobook.jobs.models import Book, Chapter, JobState, Stage
from ebook_audiobook.jobs.store import JobStore
from ebook_audiobook.web import create_app

from ebook_audiobook import tools

HAVE_FFMPEG = tools.ffmpeg_path() is not None
HAVE_CALIBRE = tools.ebook_convert_path() is not None


@pytest.fixture
def client():
    return create_app().test_client()


def test_pages_render(client):
    for url in ["/", "/new", "/voices", "/api/voices", "/api/status"]:
        assert client.get(url).status_code == 200


def test_fs_browser_lists_home(client):
    d = client.get("/api/fs").get_json()
    assert "cwd" in d and isinstance(d["dirs"], list)


def test_fs_browser_flags_unsupported_ebook(client, tmp_path):
    (tmp_path / "good.epub").write_bytes(b"x")
    (tmp_path / "novel.kfx").write_bytes(b"x")
    d = client.get(f"/api/fs?kind=ebook&path={tmp_path}").get_json()
    by_name = {f["name"]: f for f in d["files"]}
    assert by_name["good.epub"].get("disabled") is not True
    assert by_name["novel.kfx"]["disabled"] is True
    assert "KFX" in by_name["novel.kfx"]["reason"]


def test_import_unsupported_format_shows_inline_error(client, tmp_path):
    f = tmp_path / "book.kfx"
    f.write_bytes(b"x")
    r = client.post("/import", data={"path": str(f), "engine": "fake"})
    assert r.status_code == 400
    assert b"alert-error" in r.data and b"KFX" in r.data


def _wait_idle(client, timeout=60):
    for _ in range(int(timeout * 2)):
        if not client.get("/api/status").get_json()["busy"]:
            return
        time.sleep(0.5)
    raise AssertionError("worker never went idle")


@pytest.mark.calibre
@pytest.mark.skipif(not HAVE_CALIBRE, reason="need calibre")
def test_chapters_endpoint_populates_after_extract(client, synthetic_epub):
    r = client.post("/import", data={"path": str(synthetic_epub), "engine": "fake"})
    job = r.headers["Location"].rstrip("/").split("/")[-1]
    # The job page must render even before extraction has finished (no chapters yet).
    assert client.get(f"/job/{job}").status_code == 200
    _wait_idle(client)
    d = client.get(f"/job/{job}/chapters").get_json()
    # Two real chapters, plus the prepended intro and appended closing outro.
    assert len(d["chapters"]) == 4
    assert d["chapters"][0]["chapter_id"] == "intro"
    assert d["chapters"][-1]["chapter_id"] == "outro"
    assert d["default_chapter_id"]  # a content chapter is chosen


def test_pronunciation_fixes_persist(client):
    from ebook_audiobook.config import VoiceSettings
    store = JobStore("pron").ensure()
    store.save_book(Book(job_id="pron", source_path="", source_hash="pron", title="T", author="A"))
    store.save_voice(VoiceSettings(engine="fake"))

    client.post("/job/pron/settings", data={
        "engine": "fake", "voice_id": "default", "bitrate": "64",
        "pron": "LOG=log\nJPL=J P L\n\nbadline\n=empty",
    })
    pron = JobStore("pron").load_voice().extra["pron"]
    assert pron == {"LOG": "log", "JPL": "J P L"}  # blank/no-key lines dropped


def _seed_stale(job_id, stage, with_chapters=True):
    store = JobStore(job_id).ensure()
    store.save_book(Book(job_id=job_id, source_path="", source_hash=job_id, title="T", author="A"))
    if with_chapters:
        store.save_chapters([Chapter(chapter_id="ch0000", sequence=0, title="One", text="hi", char_count=2)])
    store.save_state(JobState(job_id=job_id, stage=stage))
    return store


def test_status_reconciles_stale_working_stage(client):
    # A process killed mid-render leaves state frozen at a transient stage with
    # no worker behind it; the status read must reset it to something resumable.
    _seed_stale("stale1", Stage.PREPARING.value)
    d = client.get("/job/stale1/status").get_json()
    assert d["stage"] == "extracted" and d["busy"] is False

    # No chapters (extraction interrupted) -> back to imported.
    _seed_stale("stale2", Stage.EXTRACTING.value, with_chapters=False)
    assert client.get("/job/stale2/status").get_json()["stage"] == "imported"

    # A finished/idle stage is left untouched.
    _seed_stale("stale3", Stage.DONE.value)
    assert client.get("/job/stale3/status").get_json()["stage"] == "done"


def test_startup_sweep_resets_stranded_jobs():
    _seed_stale("boot1", Stage.RENDERING.value)
    create_app()  # boot-time reconciliation sweep runs here
    assert JobStore("boot1").load_state().stage == "extracted"


def test_chapter_include_toggle_persists(client):
    store = JobStore("inc").ensure()
    store.save_book(Book(job_id="inc", source_path="", source_hash="inc",
                         title="T", author="A"))
    store.save_chapters([
        Chapter(chapter_id="ch0000", sequence=0, title="Copyright", text="x", char_count=1, include=False),
        Chapter(chapter_id="ch0001", sequence=1, title="Chapter One", text="y", char_count=1, include=True),
    ])

    # GET exposes the include flags.
    d = client.get("/job/inc/chapters").get_json()
    flags = {c["chapter_id"]: c["include"] for c in d["chapters"]}
    assert flags == {"ch0000": False, "ch0001": True}

    # POST flips them; only listed chapters change.
    r = client.post("/job/inc/chapters/include",
                    json={"includes": {"ch0000": True, "ch0001": False}})
    assert r.status_code == 200 and r.get_json()["included"] == 1
    persisted = {c.chapter_id: c.include for c in JobStore("inc").load_chapters()}
    assert persisted == {"ch0000": True, "ch0001": False}


@pytest.mark.calibre
@pytest.mark.ffmpeg
@pytest.mark.skipif(not (HAVE_FFMPEG and HAVE_CALIBRE), reason="need calibre + ffmpeg")
def test_full_web_flow_fake_engine(client, synthetic_epub):
    # Import by path (as the in-app file browser does), using the fake engine.
    r = client.post("/import", data={"path": str(synthetic_epub), "engine": "fake"})
    assert r.status_code == 302
    job_id = r.headers["Location"].rstrip("/").split("/")[-1]
    _wait_idle(client)
    assert JobStore(job_id).load_state().stage == "extracted"

    # Save settings incl. bitrate; capture a segment mtime after a preview.
    assert client.post(f"/job/{job_id}/settings",
                       data={"engine": "fake", "voice_id": "default", "bitrate": "64"}).status_code == 200

    # Preview a specific chapter.
    chapters = JobStore(job_id).load_chapters()
    r = client.post(f"/job/{job_id}/preview", data={"seconds": "1", "chapter_id": chapters[0].chapter_id})
    assert r.status_code == 200
    _wait_idle(client)
    st = JobStore(job_id).load_state()
    assert st.preview_output and st.preview_output.endswith("_preview.wav")
    assert st.preview_at and st.output_path is None  # preview didn't clobber the lifecycle
    assert client.get(f"/job/{job_id}/preview.wav").status_code == 200

    # Change ONLY bitrate -> segment audio must be reused (content-addressing).
    store = JobStore(job_id)
    seg = store.load_segments()[0]
    mtime = store.segment_audio_path(seg.segment_id).stat().st_mtime_ns
    client.post(f"/job/{job_id}/settings", data={"engine": "fake", "voice_id": "default", "bitrate": "96"})
    r = client.post(f"/job/{job_id}/preview", data={"seconds": "1", "chapter_id": chapters[0].chapter_id})
    _wait_idle(client)
    assert store.segment_audio_path(seg.segment_id).stat().st_mtime_ns == mtime


# --- upload filename handling ------------------------------------------------

def test_upload_name_keeps_extension_for_non_ascii_titles():
    """secure_filename() strips non-ASCII to nothing, which used to also throw
    away the extension and make the import fail with a confusing message."""
    from ebook_audiobook.web.app import _safe_upload_name

    assert _safe_upload_name("Война и мир.epub").endswith(".epub")
    assert _safe_upload_name("日本語の本.epub").endswith(".epub")
    assert _safe_upload_name("Ünïcödé Bøøk.mobi").endswith(".mobi")


def test_upload_name_is_path_safe():
    from ebook_audiobook.web.app import _safe_upload_name

    for hostile in ("../../etc/passwd.epub", "..\\..\\windows\\evil.epub",
                    "/absolute/path.epub"):
        out = _safe_upload_name(hostile)
        assert "/" not in out and "\\" not in out and ".." not in out


def test_upload_name_does_not_invent_an_extension():
    from ebook_audiobook.web.app import _safe_upload_name

    # No extension in, none out — import then reports the format clearly rather
    # than guessing a parser.
    assert _safe_upload_name("book") == "book"
    assert "." not in _safe_upload_name("book")
    # A long trailing dotted run isn't an extension, so it isn't treated as one.
    assert _safe_upload_name("archive.tar.something-long") == "archive.tar.something-long"


def test_upload_name_never_empty():
    from ebook_audiobook.web.app import _safe_upload_name

    assert _safe_upload_name("").strip()
    assert _safe_upload_name("...").strip()
    assert _safe_upload_name("книга").strip()


def test_prereqs_endpoint_is_cached(client, monkeypatch):
    """Every page load hits this, and answering means spawning subprocesses.
    Repeated requests must not re-probe."""
    from ebook_audiobook import checks as checks_mod

    calls = []
    real = checks_mod.run_all

    def counting(*a, **k):
        calls.append(1)
        return real(*a, **k)

    monkeypatch.setattr(checks_mod, "run_all", counting)

    for _ in range(5):
        assert client.get("/api/prereqs").status_code == 200
    assert len(calls) == 1, f"probed {len(calls)} times for 5 requests"


def test_prereqs_reports_shape(client):
    d = client.get("/api/prereqs").get_json()
    assert set(d) == {"checks", "blocking", "ok"}
    assert isinstance(d["ok"], bool)
    for c in d["checks"]:
        assert {"name", "ok", "detail", "fix", "required"} <= set(c)
