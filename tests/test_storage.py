"""Working files: what gets counted, what may be deleted, and what must not.

The dangerous case is the one these tests spend most of their time on. A book
that stopped part-way through a render is holding, in its segment cache, hours
of narration that nothing else can reproduce cheaply — and the on-disk shape of
that cache is indistinguishable from a finished book's leftovers. Anything that
frees space in bulk has to tell them apart or it silently destroys work.
"""

from __future__ import annotations

import pytest

from ebook_audiobook import settings as app_settings, storage
from ebook_audiobook.jobs.models import Book, JobState, Stage
from ebook_audiobook.jobs.store import JobStore
from ebook_audiobook.web import create_app


@pytest.fixture
def client():
    return create_app().test_client()


def make_job(job_id: str, *, stage: str, segments: int = 0, total: int = 0,
             output: bool = False, title: str = "A Book") -> JobStore:
    """A job with a believable amount of stuff on disk."""
    store = JobStore(job_id).ensure()
    store.save_book(Book(job_id=job_id, source_path="/nowhere/x.epub",
                         source_hash="h", title=title, author="An Author"))
    state = JobState(job_id=job_id, stage=stage, total_segments=total,
                     rendered_segments=segments)
    if output:
        out = store.dir / f"{job_id}.m4b"
        out.write_bytes(b"m" * 400)
        state.output_path = str(out)
    store.save_state(state)
    for i in range(segments):
        (store.segments_dir / f"seg{i}.wav").write_bytes(b"w" * 1000)
    return store


# --- classification ---------------------------------------------------------

def test_finished_book_is_safe_to_free():
    make_job("done1", stage=Stage.DONE.value, segments=5, total=5, output=True)
    s = storage.survey()
    book = s.books[0]
    assert book.reclaim == storage.SAFE
    assert book.working_bytes == 5000
    assert "Finished" in book.reason


def test_stopped_render_is_held_back_and_says_what_it_would_cost():
    make_job("stopped", stage=Stage.CANCELLED.value, segments=40, total=100)
    book = storage.survey().books[0]
    assert book.reclaim == storage.HELD
    # The row has to name the price, or "free up space" is a trick question.
    assert "40" in book.reason and "100" in book.reason


def test_failed_render_is_also_held():
    make_job("broke", stage=Stage.ERROR.value, segments=12, total=90)
    assert storage.survey().books[0].reclaim == storage.HELD


def test_nothing_narrated_yet_is_safe():
    """A preview and a normalized EPUB cost seconds to remake, not hours."""
    store = make_job("fresh", stage=Stage.EXTRACTED.value)
    (store.dir / "normalized.epub").write_bytes(b"e" * 900)
    book = storage.survey().books[0]
    assert book.reclaim == storage.SAFE
    assert book.working_bytes == 900


def test_preview_leftovers_are_not_mistaken_for_an_interrupted_render():
    """Generating a preview caches a few segments and puts the stage back.

    That looks identical on disk to a render that stopped part-way, but it is a
    few seconds of work rather than hours — so warning that deleting it costs a
    re-narration would be scaremongering.
    """
    make_job("previewed", stage=Stage.EXTRACTED.value, segments=15, total=151)
    book = storage.survey().books[0]
    assert book.reclaim == storage.SAFE
    assert "preview" in book.reason.lower()

    freed, skipped = storage.free(["previewed"])
    assert freed == 15_000 and skipped == []


def test_a_render_that_stopped_is_still_held_back():
    """The same segment count, but a render really was interrupted."""
    make_job("stopped", stage=Stage.CANCELLED.value, segments=15, total=151)
    assert storage.survey().books[0].reclaim == storage.HELD


def test_book_with_no_working_files_is_not_offered():
    make_job("empty", stage=Stage.DONE.value, output=True)
    book = storage.survey().books[0]
    assert book.reclaim == storage.NONE
    assert book.working_bytes == 0


def test_a_job_being_narrated_right_now_is_never_touched():
    make_job("live", stage=Stage.RENDERING.value, segments=3, total=50)
    book = storage.survey(busy_job_id="live").books[0]
    assert book.reclaim == storage.BUSY
    freed, skipped = storage.free(["live"], busy_job_id="live", force=True)
    assert freed == 0 and skipped == ["live"]


# --- totals -----------------------------------------------------------------

def test_survey_totals_split_reclaimable_from_the_rest():
    make_job("a", stage=Stage.DONE.value, segments=10, total=10, output=True)
    make_job("b", stage=Stage.CANCELLED.value, segments=4, total=60)
    s = storage.survey()
    assert s.safe_bytes == 10_000
    assert s.held_bytes == 4_000
    assert s.working_bytes == 14_000
    assert s.output_bytes == 400
    assert s.total_bytes >= s.working_bytes + s.output_bytes
    assert len(s.safe_books) == 1


def test_biggest_reclaim_is_listed_first():
    make_job("small", stage=Stage.DONE.value, segments=2, total=2, output=True)
    make_job("big", stage=Stage.DONE.value, segments=9, total=9, output=True)
    make_job("held", stage=Stage.CANCELLED.value, segments=30, total=99)
    ids = [b.job_id for b in storage.survey().books]
    # Safe first (largest first), then everything held back.
    assert ids == ["big", "small", "held"]


# --- freeing ----------------------------------------------------------------

def test_free_deletes_the_safe_ones_and_leaves_the_rest():
    make_job("ok", stage=Stage.DONE.value, segments=6, total=6, output=True)
    make_job("risky", stage=Stage.CANCELLED.value, segments=7, total=80)

    freed, skipped = storage.free(["ok", "risky"])
    assert freed == 6_000
    assert skipped == ["risky"]
    assert storage.survey().safe_bytes == 0
    # The stopped render still has every one of its narrated segments.
    assert JobStore("risky").intermediate_bytes() == 7_000


def test_force_reaches_past_the_safety_check():
    make_job("risky", stage=Stage.CANCELLED.value, segments=7, total=80)
    freed, skipped = storage.free(["risky"], force=True)
    assert freed == 7_000 and skipped == []


def test_freeing_keeps_the_audiobook_itself():
    store = make_job("keep", stage=Stage.DONE.value, segments=3, total=3, output=True)
    out = store.output_path()
    storage.free(["keep"])
    assert out.exists(), "the .m4b is the one thing that cannot be regenerated"


def test_free_ignores_a_job_that_no_longer_exists():
    freed, skipped = storage.free(["ghost"])
    assert freed == 0 and skipped == ["ghost"]


# --- the automatic option ---------------------------------------------------

def test_auto_free_does_nothing_unless_asked():
    make_job("auto", stage=Stage.DONE.value, segments=5, total=5, output=True)
    assert storage.free_after_render("auto") == 0
    assert JobStore("auto").intermediate_bytes() == 5_000


def test_auto_free_runs_when_switched_on():
    make_job("auto", stage=Stage.DONE.value, segments=5, total=5, output=True)
    s = app_settings.load_settings()
    s.auto_free_working_files = True
    app_settings.save_settings(s)

    assert storage.free_after_render("auto") == 5_000
    assert JobStore("auto").intermediate_bytes() == 0


def test_auto_free_leaves_an_unfinished_render_alone():
    """The setting says "when a book finishes". A cancelled one has not."""
    make_job("half", stage=Stage.CANCELLED.value, segments=5, total=50)
    s = app_settings.load_settings()
    s.auto_free_working_files = True
    app_settings.save_settings(s)

    assert storage.free_after_render("half") == 0
    assert JobStore("half").intermediate_bytes() == 5_000


# --- the routes -------------------------------------------------------------

def test_storage_page_renders(client):
    make_job("a", stage=Stage.DONE.value, segments=4, total=4, output=True, title="Piranesi")
    r = client.get("/storage")
    assert r.status_code == 200
    assert b"Piranesi" in r.data


def test_storage_api_reports_the_split(client):
    make_job("a", stage=Stage.DONE.value, segments=4, total=4, output=True)
    make_job("b", stage=Stage.CANCELLED.value, segments=9, total=70)
    d = client.get("/api/storage").get_json()
    assert d["safe_bytes"] == 4_000
    assert d["held_bytes"] == 9_000
    assert d["safe_count"] == 1
    assert len(d["books"]) == 2


def test_free_route_defaults_to_every_safe_book(client):
    make_job("a", stage=Stage.DONE.value, segments=4, total=4, output=True)
    make_job("b", stage=Stage.DONE.value, segments=6, total=6, output=True)
    make_job("c", stage=Stage.CANCELLED.value, segments=8, total=90)

    d = client.post("/storage/free").get_json()
    assert d["freed_bytes"] == 10_000
    assert JobStore("c").intermediate_bytes() == 8_000


def test_free_route_can_be_pointed_at_one_book(client):
    make_job("a", stage=Stage.DONE.value, segments=4, total=4, output=True)
    make_job("b", stage=Stage.DONE.value, segments=6, total=6, output=True)

    d = client.post("/storage/free", data={"job_id": "a"}).get_json()
    assert d["freed_bytes"] == 4_000
    assert JobStore("b").intermediate_bytes() == 6_000


def test_free_route_reports_what_it_refused(client):
    make_job("c", stage=Stage.CANCELLED.value, segments=8, total=90)
    d = client.post("/storage/free", data={"job_id": "c"}).get_json()
    assert d["freed_bytes"] == 0 and d["skipped"] == ["c"]


def test_free_route_honours_force(client):
    make_job("c", stage=Stage.CANCELLED.value, segments=8, total=90)
    d = client.post("/storage/free", data={"job_id": "c", "force": "1"}).get_json()
    assert d["freed_bytes"] == 8_000


def test_settings_route_saves_the_auto_free_preference(client):
    d = client.post("/settings", data={"auto_free_working_files": "1"}).get_json()
    assert d["auto_free_working_files"] is True
    assert app_settings.load_settings().auto_free_working_files is True

    d = client.post("/settings", data={"auto_free_working_files": "0"}).get_json()
    assert d["auto_free_working_files"] is False


def test_saving_another_setting_leaves_auto_free_alone(client):
    s = app_settings.load_settings()
    s.auto_free_working_files = True
    app_settings.save_settings(s)

    client.post("/settings", data={"power_mode": "quiet"})
    assert app_settings.load_settings().auto_free_working_files is True


# --- what the shelf and the sidebar are drawn from --------------------------

def test_cover_is_served_when_the_ebook_had_one(client, tmp_path):
    from ebook_audiobook.jobs.models import Book

    art = tmp_path / "cover.jpg"
    art.write_bytes(b"\xff\xd8\xff\xe0jpegish")
    store = make_job("withcover", stage=Stage.DONE.value, output=True)
    book = store.load_book()
    book.cover_path = str(art)
    store.save_book(book)

    r = client.get("/job/withcover/cover.jpg")
    assert r.status_code == 200
    assert r.mimetype == "image/jpeg"
    assert r.data == art.read_bytes()


def test_cover_404s_rather_than_erroring_when_there_is_none(client):
    make_job("nocover", stage=Stage.DONE.value, output=True)
    assert client.get("/job/nocover/cover.jpg").status_code == 404


def test_cover_404s_when_the_file_has_gone_missing(client, tmp_path):
    """The path is recorded in book.json; the file can still be deleted."""
    from ebook_audiobook.jobs.models import Book

    store = make_job("goneart", stage=Stage.DONE.value, output=True)
    book = store.load_book()
    book.cover_path = str(tmp_path / "vanished.jpg")
    store.save_book(book)
    assert client.get("/job/goneart/cover.jpg").status_code == 404


def test_two_books_get_different_fallback_covers():
    """A shelf of coverless books has to be scannable, not a wall of one grey."""
    from ebook_audiobook.web.app import COVER_TINTS, cover_tint

    a, b = cover_tint("Piranesi"), cover_tint("Station Eleven")
    assert a in COVER_TINTS and b in COVER_TINTS
    assert a != b
    assert cover_tint("Piranesi") == a, "the same book must always look the same"


def test_listening_time_comes_from_the_finished_file():
    from ebook_audiobook.web.app import fmt_listening

    assert fmt_listening(224_000_000, 64) == "7h 47m"
    assert fmt_listening(5_000_000, 64) == "10m"
    assert fmt_listening(None, 64) is None
    assert fmt_listening(1000, 0) is None      # no bitrate: no answer, not a crash


def test_status_carries_the_running_job_for_the_sidebar(client, monkeypatch):
    """The dock follows you onto every page, so /api/status has to say enough
    to draw it without a second request per page."""
    from ebook_audiobook.web import app as web_app
    from ebook_audiobook.web.runner import runner

    make_job("live", stage=Stage.RENDERING.value, segments=3, total=50, title="Middlemarch")
    monkeypatch.setattr(runner, "current", "live:render", raising=False)

    d = client.get("/api/status").get_json()
    assert d["job"]["job_id"] == "live"
    assert d["job"]["title"] == "Middlemarch"
    assert d["job"]["stage_label"] == "Narrating"
    assert d["job"]["total_segments"] == 50


def test_status_says_nothing_when_idle(client):
    make_job("quiet", stage=Stage.DONE.value, output=True)
    assert client.get("/api/status").get_json()["job"] is None


def test_every_stage_has_words_of_its_own():
    """No stage is ever shown as its raw enum name."""
    from ebook_audiobook.jobs.models import STAGE_LABELS, Stage, stage_label

    for stage in Stage:
        assert stage.value in STAGE_LABELS, stage
        assert STAGE_LABELS[stage.value] != stage.value, stage
    # Two of them say who did the stopping.
    assert stage_label("cancelled") == "Stopped by you"
    assert stage_label("error") == "Stopped by a problem"
    # An unknown stage from an older job file falls through rather than blowing up.
    assert stage_label("something-new") == "something-new"


# --- opening a folder in the file manager -----------------------------------

def test_reveal_only_accepts_names_it_knows(client):
    """The route ends in starting a program with a path argument.

    So it takes a *name* for one of the app's own folders and resolves it here.
    Anything else — including a path — is simply not a name it knows.
    """
    assert client.post("/reveal", data={"what": "nonsense"}).status_code == 404
    assert client.post("/reveal", data={"what": "/etc"}).status_code == 404
    assert client.post("/reveal", data={"what": "../../etc"}).status_code == 404
    assert client.post("/reveal", data={}).status_code == 404


def test_reveal_opens_a_known_folder(client, monkeypatch):
    from ebook_audiobook import tools
    from ebook_audiobook.config import paths

    opened = []
    monkeypatch.setattr(tools, "reveal", lambda p: opened.append(p) or True)

    d = client.post("/reveal", data={"what": "data"}).get_json()
    assert d["ok"] and opened == [paths().root]


def test_reveal_finds_a_finished_books_own_folder(client, monkeypatch):
    from ebook_audiobook import tools

    opened = []
    monkeypatch.setattr(tools, "reveal", lambda p: opened.append(p) or True)

    store = make_job("shown", stage=Stage.DONE.value, output=True)
    d = client.post("/reveal", data={"job_id": "shown"}).get_json()
    assert d["ok"] and opened == [store.output_path().parent]


def test_reveal_says_so_when_there_is_no_folder_yet(client):
    """A book that has never been narrated has nowhere to show."""
    make_job("notyet", stage=Stage.EXTRACTED.value)
    r = client.post("/reveal", data={"job_id": "notyet"})
    assert r.status_code == 404 and "doesn't exist" in r.get_json()["error"]


def test_reveal_reports_a_desktop_with_no_file_manager(client, monkeypatch):
    from ebook_audiobook import tools

    monkeypatch.setattr(tools, "reveal", lambda p: False)
    r = client.post("/reveal", data={"what": "data"})
    assert r.status_code == 501 and "file manager" in r.get_json()["error"]


def test_tools_reveal_refuses_a_path_that_is_not_a_folder(tmp_path):
    from ebook_audiobook import tools

    f = tmp_path / "a-file.txt"
    f.write_text("x")
    assert tools.reveal(f) is False
    assert tools.reveal(tmp_path / "nope") is False


# --- playing a preview when it is ready -------------------------------------

def test_previews_play_themselves_by_default():
    from ebook_audiobook import settings as app_settings

    assert app_settings.Settings().autoplay_preview is True


def test_the_autoplay_preference_can_be_turned_off_and_on(client):
    from ebook_audiobook import settings as app_settings

    assert client.post("/settings", data={"autoplay_preview": "0"}).get_json()[
        "autoplay_preview"] is False
    assert app_settings.load_settings().autoplay_preview is False

    assert client.post("/settings", data={"autoplay_preview": "1"}).get_json()[
        "autoplay_preview"] is True


def test_saving_something_else_leaves_autoplay_alone(client):
    from ebook_audiobook import settings as app_settings

    client.post("/settings", data={"autoplay_preview": "0"})
    client.post("/settings", data={"power_mode": "quiet"})
    assert app_settings.load_settings().autoplay_preview is False


def test_the_job_page_is_told_what_the_preference_is(client):
    from ebook_audiobook import settings as app_settings

    make_job("previewjob", stage=Stage.EXTRACTED.value, title="A Book")
    JobStore("previewjob").save_chapters([])

    assert b"AUTOPLAY_PREVIEW = true" in client.get("/job/previewjob").data
    s = app_settings.load_settings()
    s.autoplay_preview = False
    app_settings.save_settings(s)
    assert b"AUTOPLAY_PREVIEW = false" in client.get("/job/previewjob").data


def test_the_settings_page_shows_the_toggle(client):
    body = client.get("/settings").data
    assert b"autoplayCheck" in body
    assert b"Play a preview as soon as it is ready" in body
