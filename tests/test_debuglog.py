"""The performance log, and the book's record of how it was narrated.

The log is off unless someone turns it on, never breaks a render, never
records a title, and goes into the bug report with home folders replaced. The
book remembers the tier its audio was made with, and says where it is running.
"""

from __future__ import annotations

import json
import os

import pytest

from ebook_audiobook import debuglog, errorlog, settings, tiers, worker
from ebook_audiobook.config import VoiceSettings
from ebook_audiobook.jobs.models import Book, Chapter
from ebook_audiobook.jobs.store import JobStore
from ebook_audiobook.tts.fake import FakeAdapter


@pytest.fixture(autouse=True)
def debug_env_off(monkeypatch):
    monkeypatch.delenv(debuglog.ENV, raising=False)


def _seed_job(job_id="dbgjob"):
    store = JobStore(job_id).ensure()
    store.save_book(Book(job_id=job_id, source_path="/none.epub", source_hash=job_id,
                         title="A Private Title", author="Nobody"))
    store.save_chapters([
        Chapter(chapter_id="ch0000", sequence=0, title="Chapter One",
                text="This is the first chapter. It has two sentences.", char_count=48),
    ])
    store.save_voice(VoiceSettings(engine="fake"))
    return store


def _turn_on():
    s = settings.load_settings()
    s.debug_log = True
    settings.save_settings(s)


# --- the log itself ------------------------------------------------------------------

def test_off_by_default_and_writes_nothing():
    assert settings.load_settings().debug_log is False
    assert not debuglog.enabled()
    debuglog.event("segment", seconds=1.0)
    assert debuglog.entries() == [] and not debuglog.log_path().exists()


def test_the_setting_turns_it_on_and_off():
    _turn_on()
    debuglog.event("segment", seconds=1.5)
    s = settings.load_settings()
    s.debug_log = False
    settings.save_settings(s)
    debuglog.event("segment", seconds=2.5)   # after it was turned off
    assert [e["seconds"] for e in debuglog.entries()] == [1.5]


def test_the_environment_variable_turns_it_on_for_one_run(monkeypatch):
    monkeypatch.setenv(debuglog.ENV, "1")
    debuglog.event("engine_plan", ladder=["cuda, compact", "cpu"])
    (e,) = debuglog.entries()
    assert e["kind"] == "engine_plan" and e["ladder"] == ["cuda, compact", "cpu"] and e["ts"]


def test_an_event_never_raises(monkeypatch):
    monkeypatch.setenv(debuglog.ENV, "1")
    debuglog.event("odd", thing=object(), kind_of="x")   # not JSON: written as text
    assert debuglog.entries()[-1]["kind"] == "odd"
    monkeypatch.setattr(errorlog, "open_logger", lambda *a, **k: 1 / 0)
    debuglog.event("segment")   # a broken log is silent, not a failed render


def test_clear_removes_it(monkeypatch):
    monkeypatch.setenv(debuglog.ENV, "1")
    debuglog.event("segment")
    assert debuglog.total_bytes() > 0
    assert debuglog.clear() == 1
    assert debuglog.entries() == []


def test_the_report_carries_it_with_home_folders_replaced(monkeypatch):
    monkeypatch.setenv(debuglog.ENV, "1")
    home = os.path.expanduser("~")
    debuglog.event("render_start", data_root=os.path.join(home, "books"))
    report = errorlog.issue_report()
    assert "## Performance log (last 1 events)" in report
    assert home not in report and "~" in report


def test_a_report_without_it_has_no_such_section():
    assert "Performance log" not in errorlog.issue_report()


# --- a render with the log on ----------------------------------------------------------

def test_a_render_logs_its_start_and_every_passage():
    _seed_job("logged")
    _turn_on()
    worker.render_job("logged", power_mode="balanced")
    kinds = [e["kind"] for e in debuglog.entries()]
    assert kinds[0] == "render_start" and "segment" in kinds
    start = debuglog.entries()[0]
    # The job's own mode, not the global default the environment also reports.
    assert start["power_mode"] == "balanced" and "default_power_mode" in start
    seg = next(e for e in debuglog.entries() if e["kind"] == "segment")
    assert seg["chars"] > 0 and seg["seconds"] >= 0 and seg["audio"] > 0


def test_no_title_ever_reaches_the_log():
    _seed_job("private")
    _turn_on()
    worker.render_job("private")
    assert "A Private Title" not in debuglog.log_path().read_text("utf-8")


def test_a_broken_log_cannot_stop_a_render(monkeypatch):
    _seed_job("sturdy")
    _turn_on()
    monkeypatch.setattr(debuglog, "environment", lambda: 1 / 0)
    st = worker.render_job("sturdy")
    assert st.stage == "done"


# --- the book's tier ---------------------------------------------------------------------

class _TieredFake(FakeAdapter):
    """The fake engine, reporting a tier and a place to run like Chatterbox."""
    pins: list = []

    def __init__(self, voice, tier=None):
        super().__init__(voice)
        _TieredFake.pins.append(tier)

    identity_tier = tiers.COMPACT
    runtime = {"device": "cuda", "name": "Fake GPU", "backend": "cuda",
               "tier": tiers.COMPACT, "vram_gb": 4.0, "reason": None}


@pytest.fixture
def tiered(monkeypatch):
    _TieredFake.pins = []
    from ebook_audiobook.tts.adapter import VoiceConfig

    monkeypatch.setattr(worker, "get_adapter",
                        lambda voice, sr, tier=None: _TieredFake(VoiceConfig(sample_rate=sr), tier))
    return _TieredFake


def test_a_new_book_is_pinned_to_the_tier_it_first_narrates_with(tiered):
    store = _seed_job("fresh")
    worker.render_job("fresh", preview_max_seconds=5)
    st = store.load_state()
    assert tiered.pins == [None]
    assert st.engine_tier == tiers.COMPACT
    assert st.engine["name"] == "Fake GPU"


def test_a_pinned_book_hands_its_pin_to_the_engine(tiered):
    store = _seed_job("pinned")
    st = store.load_state()
    st.engine_tier = tiers.FULL
    store.save_state(st)
    worker.render_job("pinned", preview_max_seconds=5)
    assert tiered.pins == [tiers.FULL]
    assert store.load_state().engine_tier == tiers.FULL   # never overwritten


def test_a_book_with_audio_from_before_tiers_is_held_at_full_precision(tiered):
    store = _seed_job("legacy")
    (store.segments_dir / "0123456789abcdef.wav").write_bytes(b"RIFF")
    assert worker._engine_pin(store) == tiers.FULL


def test_a_book_with_nothing_narrated_has_no_pin():
    assert worker._engine_pin(_seed_job("empty")) is None


def test_measuring_pins_too(tiered):
    store = _seed_job("measured")
    worker.measure_job("measured")
    assert store.load_state().engine_tier == tiers.COMPACT


# --- the web side -------------------------------------------------------------------------

@pytest.fixture
def client():
    from ebook_audiobook.web.app import create_app

    return create_app().test_client()


def test_the_settings_switch_saves(client):
    r = client.post("/settings", data={"debug_log": "1"}).get_json()
    assert r["debug_log"] is True and settings.load_settings().debug_log is True
    # Saving something else leaves it alone.
    client.post("/settings", data={"autoplay_preview": "0"})
    assert settings.load_settings().debug_log is True
    client.post("/settings", data={"debug_log": "0"})
    assert settings.load_settings().debug_log is False


def test_the_settings_page_shows_the_switch(client):
    assert b'id="debugLogCheck"' in client.get("/settings").data


def test_diagnostics_report_the_log_and_clear_it(client, monkeypatch):
    monkeypatch.setenv(debuglog.ENV, "1")
    debuglog.event("segment")
    d = client.get("/diagnostics").get_json()
    assert d["debug_bytes"] > 0
    client.post("/diagnostics/clear")
    assert client.get("/diagnostics").get_json()["debug_bytes"] == 0


def test_the_job_status_says_where_narration_runs(client, tiered):
    _seed_job("status")
    worker.render_job("status", preview_max_seconds=5)
    d = client.get("/job/status/status").get_json()
    assert d["engine_tier"] == tiers.COMPACT
    assert d["engine"]["tier"] == tiers.COMPACT and d["engine"]["vram_gb"] == 4.0


def test_the_engine_line_is_cleared_while_the_next_model_loads(tiered, monkeypatch):
    """A stale "narrating on the GPU" during a load that is about to land on
    the CPU would say the opposite of what is happening."""
    store = _seed_job("stale")
    worker.render_job("stale", preview_max_seconds=5)
    assert store.load_state().engine is not None
    seen = []

    class Loader(_TieredFake):
        def load(self):
            seen.append(store.load_state().engine)

    from ebook_audiobook.tts.adapter import VoiceConfig

    monkeypatch.setattr(worker, "get_adapter",
                        lambda voice, sr, tier=None: Loader(VoiceConfig(sample_rate=sr), tier))
    worker.render_job("stale", preview_max_seconds=5)
    assert seen == [None]


def test_state_files_written_before_tiers_still_load():
    store = _seed_job("old")
    path = store.dir / "job_state.json"
    d = json.loads(path.read_text()) if path.exists() else {"job_id": "old"}
    d.pop("engine_tier", None)
    d.pop("engine", None)
    path.write_text(json.dumps(d))
    st = store.load_state()
    assert st.engine_tier is None and st.engine is None
