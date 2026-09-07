"""Web-layer integration tests. The heavier ones use Calibre + ffmpeg via the
fake engine (no GPU), driving the same routes the browser hits."""

import re
import time

import pytest

from ebook_audiobook.jobs.models import Book, Chapter, JobState, Stage
from ebook_audiobook.jobs.store import JobStore
from ebook_audiobook.config import paths
from ebook_audiobook.web import create_app

from ebook_audiobook import tools

HAVE_FFMPEG = tools.ffmpeg_path() is not None
HAVE_CALIBRE = tools.ebook_convert_path() is not None


@pytest.fixture
def client():
    return create_app().test_client()


def test_pages_render(client):
    for url in ["/", "/new", "/voices", "/api/voices", "/api/status", "/api/updates/status"]:
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


def test_uploading_a_book_leaves_no_second_copy_behind(client, synthetic_epub):
    """An upload used to be saved under its own name *and* copied to a
    content-addressed name, so every book uploaded through the browser was
    stored twice and the staging copy was never reclaimed by anything."""
    import io

    data = {
        "file": (io.BytesIO(synthetic_epub.read_bytes()), "War and Peace.epub"),
        "engine": "fake",
    }
    r = client.post("/import", data=data, content_type="multipart/form-data")
    assert r.status_code == 302
    job_id = r.headers["Location"].rstrip("/").split("/")[-1]

    imports = paths().imports
    left = sorted(p.name for p in imports.iterdir() if p.is_file())
    assert left == [f"{job_id}.epub"], f"staging copies left in imports/: {left}"
    assert not list(paths().tmp.glob("upload-*")), "upload staging file not cleaned up"


def test_upload_with_a_non_ascii_name_still_imports(client, synthetic_epub):
    """secure_filename() strips non-ASCII to nothing; the extension decides which
    importer runs, so it has to survive independently of the stem."""
    import io

    data = {
        "file": (io.BytesIO(synthetic_epub.read_bytes()), "Война и мир.epub"),
        "engine": "fake",
    }
    r = client.post("/import", data=data, content_type="multipart/form-data")
    assert r.status_code == 302, r.data[:400]


# --- disk space: the working volume and the destination are often different ---

def test_space_reports_the_working_volume_by_default(client):
    d = client.get("/api/space").get_json()
    assert d["free_bytes"] is not None and d["total_bytes"] > 0
    assert d["work"]["free_bytes"] == d["free_bytes"]
    assert "output" not in d  # nothing asked about a destination


def test_space_reports_the_destination_volume_too(client, tmp_path):
    """A Plex library on a NAS or USB drive has nothing to do with the free
    space where the temporary WAVs are written."""
    dest = tmp_path / "library"
    dest.mkdir()
    d = client.get(f"/api/space?path={dest}").get_json()
    assert d["output"]["free_bytes"] is not None
    assert d["output"]["same_volume"] is True  # tmp_path really is the same fs here


def test_space_handles_a_destination_that_does_not_exist_yet(client, tmp_path):
    """The book's folder is only created when the render starts, so the plan
    dialog necessarily asks about a path that isn't there — it must measure the
    nearest existing parent rather than returning nothing."""
    not_yet_created = tmp_path / "library" / "Author" / "Title (2024)"
    d = client.get(f"/api/space?path={not_yet_created}").get_json()
    assert d["output"]["free_bytes"] is not None


def test_space_survives_an_unreachable_destination(client):
    """An unmounted share must report unknown, not 500 the render dialog."""
    d = client.get("/api/space?path=/definitely/not/mounted/anywhere/xyz").get_json()
    assert "output" in d  # answered rather than erroring


# --- render intensity --------------------------------------------------------

def test_saving_the_power_mode_does_not_wipe_the_library_folder(client, tmp_path):
    """The settings page saves each field independently. An absent field has to
    mean 'leave it alone' — treating it as 'clear it' would silently unset the
    Plex library folder the moment someone changed the render mode."""
    lib = tmp_path / "audiobooks"
    lib.mkdir()
    r = client.post("/settings", data={"audiobooks_root": str(lib)})
    assert r.get_json()["ok"]

    r = client.post("/settings", data={"power_mode": "quiet"})
    body = r.get_json()
    assert body["power_mode"] == "quiet"
    assert body["audiobooks_root"] == str(lib), "the library folder was cleared"


def test_clearing_the_library_folder_still_works(client, tmp_path):
    """An explicitly empty value must still mean 'clear'."""
    lib = tmp_path / "audiobooks"
    lib.mkdir()
    client.post("/settings", data={"audiobooks_root": str(lib)})
    r = client.post("/settings", data={"audiobooks_root": ""})
    assert r.get_json()["audiobooks_root"] is None


def test_an_invented_power_mode_is_rejected_not_stored(client):
    """Falls back to full speed rather than to the new-install default: a value
    nobody can parse is no reason to halve someone's throughput."""
    r = client.post("/settings", data={"power_mode": "ludicrous"})
    assert r.get_json()["power_mode"] == "full"


def test_a_fresh_install_starts_on_balanced():
    """No settings.json at all — the only case that counts as a new machine.
    Contrast Settings()'s own dataclass default, which stays "full" so that
    filling in a missing key from an *existing* file can't slow it down."""
    from ebook_audiobook import power, settings

    assert settings.Settings().power_mode == power.MODE_FULL
    assert settings.load_settings().power_mode == power.MODE_BALANCED
    assert settings.default_power_mode() == power.MODE_BALANCED


def test_an_existing_install_keeps_full_speed_across_the_upgrade():
    """The regression that matters: render intensity postdates settings.json,
    so a machine that has been at full speed since before the setting existed
    has no power_mode key at all. Reading that file must not quietly move it to
    Balanced — the render it is midway through would just get slower."""
    from ebook_audiobook import power, settings

    existing = settings.Settings(audiobooks_root="/tmp/somewhere").to_dict()
    del existing["power_mode"]
    settings.save_settings(settings.Settings.from_dict(existing))
    assert settings.load_settings().power_mode == power.MODE_FULL


def test_a_saved_choice_survives_the_new_default():
    """Someone who deliberately picked Full speed keeps it."""
    from ebook_audiobook import power, settings

    settings.save_settings(settings.Settings(power_mode=power.MODE_FULL))
    assert settings.default_power_mode() == power.MODE_FULL


# --- appearance ---------------------------------------------------------------

def _html_tag(html: str) -> str:
    """Just the ``<html ...>`` opening tag — asserting against the whole page
    is a trap here, since the onboarding modal's own swatch cards carry
    literal ``data-scheme="classic"``/``data-scheme="modern"`` attributes that
    can make a naive substring check pass (or fail) for the wrong reason."""
    return re.search(r"<html[^>]*>", html).group()


def _finish_onboarding(client):
    """Answers the first-run modal with no explicit choices, so a page render
    reflects whatever is actually saved (the dataclass defaults, absent any
    other change) rather than the modal's own suggested "modern"/"system" —
    see app.py's onboarding_needed override."""
    client.post("/settings", data={"onboarding_complete": "1"})


def test_color_scheme_and_mode_default_to_classic_system(client):
    _finish_onboarding(client)
    tag = _html_tag(client.get("/settings").data.decode())
    assert 'data-scheme="classic"' in tag
    assert 'data-mode-setting="system"' in tag


def test_appearance_round_trips(client):
    _finish_onboarding(client)
    r = client.post("/settings", data={"color_scheme": "modern", "color_mode": "dark"})
    body = r.get_json()
    assert body["color_scheme"] == "modern"
    assert body["color_mode"] == "dark"
    tag = _html_tag(client.get("/settings").data.decode())
    assert 'data-scheme="modern"' in tag
    assert 'data-mode-setting="dark"' in tag


def test_an_invented_color_scheme_is_rejected_not_stored(client):
    r = client.post("/settings", data={"color_scheme": "psychedelic"})
    assert r.get_json()["color_scheme"] == "classic"


def test_an_invented_color_mode_is_rejected_not_stored(client):
    r = client.post("/settings", data={"color_mode": "sepia"})
    assert r.get_json()["color_mode"] == "system"


def test_setting_the_scheme_does_not_touch_the_mode(client):
    """Independent axes: saving one form field must never silently reset the
    other, the same rule the library-folder/power-mode split already keeps."""
    client.post("/settings", data={"color_mode": "dark"})
    r = client.post("/settings", data={"color_scheme": "modern"})
    assert r.get_json()["color_mode"] == "dark"


# --- first-run onboarding -------------------------------------------------

def test_onboarding_modal_shows_on_a_fresh_install(client):
    assert 'id="onboardOverlay"' in client.get("/").data.decode()


def test_onboarding_suggests_classic_system_before_anything_is_chosen(client):
    """Nothing saved yet, so the modal — and the page underneath it — render
    the same pre-selected answers the modal itself shows (Classic, Match my
    system), read from the onboarding_needed branch rather than
    s.color_scheme/s.color_mode directly, so the two can never disagree."""
    tag = _html_tag(client.get("/").data.decode())
    assert 'data-scheme="classic"' in tag
    assert 'data-mode-setting="system"' in tag


def test_completing_onboarding_hides_the_modal_and_saves_the_choice(client):
    r = client.post("/settings", data={
        "language": "fr", "color_scheme": "classic", "color_mode": "dark",
        "check_for_updates": "1", "onboarding_complete": "1",
    })
    assert r.get_json()["ok"] is True
    d = client.get("/").data.decode()
    assert 'id="onboardOverlay"' not in d
    tag = _html_tag(d)
    assert 'data-scheme="classic"' in tag
    assert 'data-mode-setting="dark"' in tag


def test_a_fresh_data_folder_needs_onboarding():
    """No settings.json at all — the only case that should ever show the
    modal — versus Settings()'s own dataclass default, which stays True so
    that filling in a missing key from an *existing* file never re-triggers
    it (see the other test in this section)."""
    from ebook_audiobook import settings

    assert settings.Settings().preferences_onboarded is True
    assert settings.load_settings().preferences_onboarded is False


def test_onboarding_does_not_reappear_for_an_existing_install():
    """The regression that matters: upgrading from a version that predates
    this modal must never interrupt someone who has been using the app for
    months. Simulated the same way an old settings.json actually looks — every
    other field present, this one simply missing."""
    from ebook_audiobook import settings

    existing = settings.Settings(audiobooks_root="/tmp/somewhere").to_dict()
    del existing["preferences_onboarded"]
    settings.save_settings(settings.Settings.from_dict(existing))
    assert settings.load_settings().preferences_onboarded is True


# --- automatic updates ----------------------------------------------------

def test_updates_status_is_off_by_default(client):
    d = client.get("/api/updates/status").get_json()
    assert d["enabled"] is False
    assert d["available"] is False
    assert d["apply_state"] == "idle"


def _offer_an_update(monkeypatch):
    """Put the app in the only state from which an install is allowed: checks
    on, and a newer release actually found."""
    from ebook_audiobook import settings as app_settings
    from ebook_audiobook import update
    from ebook_audiobook.web import update_watch

    s = app_settings.load_settings()
    s.check_for_updates = True
    app_settings.save_settings(s)
    release = update.Release(version="99.0.0", tag="v99.0.0", url="")
    with update_watch._state.lock:
        update_watch._state.release = release
    monkeypatch.setattr(update, "apply_update", lambda yes=False, timeout=3600: 0)


def test_updates_apply_starts_in_the_background(client, monkeypatch):
    _offer_an_update(monkeypatch)
    r = client.post("/updates/apply")
    assert r.get_json()["ok"] is True


def test_updates_apply_refuses_unless_an_update_is_actually_on_offer(client, monkeypatch):
    """This endpoint runs the installer, which is `curl … | bash`, and the app
    listens on a fixed localhost port with no CSRF token. The confirm dialog
    lives in the browser, so it cannot be the only gate: a plain cross-origin
    form POST is a CORS-simple request and would otherwise make the app
    reinstall itself unattended, on a machine whose promise is that nothing
    happens without being asked.
    """
    from ebook_audiobook import settings as app_settings
    from ebook_audiobook import update

    invoked = []
    monkeypatch.setattr(update, "apply_update",
                        lambda yes=False, timeout=3600: invoked.append(yes) or 0)

    # Checks turned off, and no release known.
    s = app_settings.load_settings()
    s.check_for_updates = False
    app_settings.save_settings(s)
    assert client.post("/updates/apply").status_code == 409

    # Checks on and a release found, but the request came from another site.
    _offer_an_update(monkeypatch)
    assert client.post("/updates/apply",
                       headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403

    assert invoked == [], "the installer must not have run"


def test_updates_dismiss_is_remembered(client):
    r = client.post("/updates/dismiss", data={"version": "9.9.9"})
    assert r.get_json()["ok"] is True
    from ebook_audiobook import settings as app_settings

    assert app_settings.load_settings().updates_dismissed_version == "9.9.9"


def test_the_render_mode_is_remembered_on_the_job(client, tmp_path):
    """A resume after a restart must not silently go back to full speed on
    someone's laptop."""
    store = JobStore("powerjob").ensure()
    store.save_book(Book(job_id="powerjob", source_path="x.epub", source_hash="h"))
    store.save_chapters([Chapter(chapter_id="c0", sequence=0, title="One",
                                 text="hi", char_count=2, include=True)])
    store.save_state(JobState(job_id="powerjob"))

    out = tmp_path / "out"
    out.mkdir()
    r = client.post("/job/powerjob/render",
                    data={"output_mode": "folder", "output_dir": str(out),
                          "power_mode": "balanced"})
    assert r.get_json()["ok"], r.get_json()
    assert JobStore("powerjob").load_state().power_mode == "balanced"


def test_fs_browser_hides_dotfiles_and_appledouble_sidecars(client, tmp_path):
    """Finder leaves .DS_Store everywhere, and on an external or network volume
    macOS writes a ``._Book.epub`` beside every ``Book.epub``. The sidecar has
    the extension and none of the content, so listed it looks like a second
    copy of the book and then fails to import."""
    books = tmp_path / "books"
    books.mkdir()
    (books / "Book.epub").write_bytes(b"x")
    (books / "._Book.epub").write_bytes(b"x")
    (books / ".DS_Store").write_bytes(b"x")
    (books / ".hidden").mkdir()
    r = client.get("/api/fs?kind=ebook&path=" + str(books))
    data = r.get_json()
    assert [f["name"] for f in data["files"]] == ["Book.epub"]
    assert [d["name"] for d in data["dirs"]] == []


# --- the window's geometry ---------------------------------------------------------

def test_window_geometry_rejects_nonsense_without_a_server_error(client):
    """int(float('inf')) is an OverflowError, which is not a ValueError; the
    route used to 500 on it. Every rejection here is a 400."""
    for bad in ("inf", "-inf", "nan", "abc", ""):
        r = client.post("/api/window", data={"x": bad, "y": "0", "width": "800", "height": "600"})
        assert r.status_code == 400, bad
    r = client.post("/api/window", data={"x": "10", "y": "20", "width": "100", "height": "600"})
    assert r.status_code == 400  # too small to be a window
    r = client.post("/api/window", data={"x": "10", "y": "20", "width": "1200", "height": "800"})
    assert r.status_code == 200


# --- one render at a time, per book ---------------------------------------------------

def _seed_readable_job(job_id="busyjob"):
    store = JobStore(job_id).ensure()
    store.save_book(Book(job_id=job_id, source_path="/none.epub", source_hash=job_id))
    store.save_chapters([Chapter(chapter_id="ch0000", sequence=0, title="One",
                                 text="Some text here.", char_count=15)])
    store.save_state(JobState(job_id=job_id, stage=Stage.EXTRACTED.value))
    return store


def test_a_second_render_request_is_refused_while_one_is_running(client, monkeypatch):
    """A double-click or a retried request must not queue a second full pass
    over the same book, re-packaging it for nothing."""
    from ebook_audiobook.web.runner import runner

    _seed_readable_job()
    monkeypatch.setattr(runner, "is_busy", lambda job_id=None: job_id == "busyjob")
    submitted = []
    monkeypatch.setattr(runner, "submit", lambda *a, **k: submitted.append(a))

    r = client.post("/job/busyjob/render", data={"output_mode": "folder"})
    assert r.status_code == 409
    r = client.post("/job/busyjob/preview", data={"seconds": "30"})
    assert r.status_code == 409
    assert submitted == []


def test_preview_length_is_clamped(client, monkeypatch):
    """Zero would divide the progress by nothing after a full model load."""
    from ebook_audiobook.web.runner import runner

    _seed_readable_job()
    monkeypatch.setattr(runner, "is_busy", lambda job_id=None: False)
    submitted = []
    monkeypatch.setattr(runner, "submit", lambda *a, **k: submitted.append(k))

    client.post("/job/busyjob/preview", data={"seconds": "0"})
    client.post("/job/busyjob/preview", data={"seconds": "99999"})
    assert [k["seconds"] for k in submitted] == [5.0, 300.0]


# --- whether the estimates are current is the server's call --------------------------

def test_status_says_whether_the_measurement_matches_the_saved_settings(client):
    from ebook_audiobook import config, hashing

    store = _seed_readable_job("measured")
    voice = store.load_voice()
    st = store.load_state()
    st.chars_per_audio_second = 14.0
    st.measured_voice_key = hashing.voice_key(voice, config.SAMPLE_RATE)
    store.save_state(st)
    assert client.get("/job/measured/status").get_json()["measured_here"] is True

    client.post("/job/measured/settings", data={"cfg_weight": "0.2"})
    assert client.get("/job/measured/status").get_json()["measured_here"] is False


# --- the sidebar during a voice audition ---------------------------------------------

def test_status_during_a_voice_audition_has_no_book_and_no_error(client, monkeypatch):
    from ebook_audiobook.web.runner import runner

    monkeypatch.setattr(runner, "current", "voicetest-male-british:voice_test")
    monkeypatch.setattr(runner, "is_busy", lambda job_id=None: True)
    d = client.get("/api/status").get_json()
    assert d["busy"] is True and d["kind"] == "voice_test" and d["job"] is None


# --- voices: the default narrator, and a bad clip ---------------------------------------

def test_the_default_narrator_for_new_books_can_be_chosen(client):
    from ebook_audiobook import settings as app_settings
    from ebook_audiobook.voices import default_voice_id

    r = client.post("/voices/female-british/default")
    assert r.status_code == 302
    assert app_settings.load_settings().default_voice_id == "female-british"
    assert default_voice_id() == "female-british"
    assert b"new books start here" in client.get("/voices").data

    assert client.post("/voices/no-such-voice/default").status_code == 404


def test_a_bad_voice_clip_is_reported_on_the_page(client, tmp_path):
    """abort(400) landed the user on Flask's bare error page with no way back."""
    r = client.post("/voices/add", data={"name": "Nope", "path": str(tmp_path / "missing.txt")})
    assert r.status_code == 400
    assert b"alert-error" in r.data and b"Add a voice" in r.data  # the page, with the reason


def test_an_audition_starts_by_forgetting_the_previous_sample(client, monkeypatch):
    """The page waits for the sample file to appear, so an old one would be
    mistaken for the new one — and played — the instant the request returns."""
    from ebook_audiobook.web.runner import runner

    monkeypatch.setattr(runner, "submit", lambda *a, **k: None)
    old = paths().voices / "_sample_male-british.wav"
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_bytes(b"old")
    assert client.post("/voices/male-british/test").get_json()["ok"] is True
    assert not old.exists()


# --- the first-run nudge ------------------------------------------------------------------

def test_flipping_an_unrelated_switch_does_not_dismiss_the_setup_prompt(client):
    from ebook_audiobook import settings as app_settings

    client.post("/settings", data={"autoplay_preview": "0"})
    assert app_settings.load_settings().setup_dismissed is False
    client.post("/settings", data={"audiobooks_root": ""})
    assert app_settings.load_settings().setup_dismissed is True


def test_a_corrupt_settings_file_is_kept_rather_than_overwritten(monkeypatch):
    """Falling back to defaults is right — the app has to start. Writing those
    defaults over the file is not: the first-run modal then presents the loss
    as a fresh install, and the user's library folder is gone with no trace."""
    import json

    from ebook_audiobook import settings as app_settings
    from ebook_audiobook.config import paths

    s = app_settings.load_settings()
    s.audiobooks_root = "/media/books"
    s.language = "fr"
    app_settings.save_settings(s)

    path = paths().root / "settings.json"
    path.write_text('{"audiobooks_root": "/media/bo', encoding="utf-8")  # a killed write

    loaded = app_settings.load_settings()
    assert loaded.audiobooks_root is None      # defaults, so the app starts
    kept = paths().root / "settings.corrupt.json"
    assert kept.exists(), "the unreadable file was destroyed"
    assert "media/bo" in kept.read_text("utf-8")

    # Valid JSON that isn't an object used to raise out of from_dict.
    path.write_text("null", encoding="utf-8")
    assert app_settings.load_settings() is not None


def test_the_settings_mode_buttons_do_not_reach_into_the_onboarding_modal(client):
    """base.html's first-run modal carries three .mode-btn elements of its own.
    An unscoped selector bound the Settings handler to them, so clicking Light
    inside the modal also POSTed the setting and reloaded — discarding the
    language and palette just chosen there."""
    import re

    body = client.get("/settings").data.decode()
    assert "onboardOverlay" in body, "expected the first-run modal on a fresh data root"
    assert len(re.findall(r'class="mode-btn', body)) == 6
    assert '#settingsModeToggle .mode-btn' in body
    assert '#onboardModeToggle .mode-btn' in body
