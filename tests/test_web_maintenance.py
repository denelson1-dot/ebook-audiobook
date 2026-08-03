"""The settings-page machinery: backup, updates, diagnostics.

The load-bearing test here is the last one. The app's headline promise is that
nothing leaves your machine, so the fact that rendering a page makes no outbound
request is a product guarantee, not an implementation detail — and guarantees
that aren't tested stop being true.
"""

from __future__ import annotations

import os
import time
import zipfile

import pytest

from ebook_audiobook import backup, errorlog, settings as app_settings
from ebook_audiobook.config import paths
from ebook_audiobook.web import create_app


@pytest.fixture
def client():
    return create_app().test_client()


@pytest.fixture
def some_data():
    """A data root with a little of everything, including rendered audio."""
    p = paths().ensure()
    (p.root / "settings.json").write_text("{}")
    (p.voices / "narrator.wav").write_text("clip")
    (p.imports / "abc.epub").write_text("book")
    job = p.jobs / "abc"
    (job / "segments").mkdir(parents=True, exist_ok=True)
    (job / "book.json").write_text('{"title": "T"}')
    (job / "segments" / "a.wav").write_text("AUDIO" * 500)
    return p


# --- backup -------------------------------------------------------------------

def test_estimate_defaults_to_excluding_rendered_audio(client, some_data):
    d = client.get("/backup/estimate").get_json()
    assert d["ok"]
    audio = next(c for c in d["categories"] if c["key"] == "job_audio")
    assert audio["included"] is False


def test_estimate_full_includes_rendered_audio(client, some_data):
    d = client.get("/backup/estimate?profile=full").get_json()
    audio = next(c for c in d["categories"] if c["key"] == "job_audio")
    assert audio["included"] is True
    assert d["bytes"] > client.get("/backup/estimate").get_json()["bytes"]


def test_estimate_rejects_an_unknown_profile(client, some_data):
    r = client.get("/backup/estimate?profile=enormous")
    assert r.status_code == 400
    assert "Unknown profile" in r.get_json()["error"]


def test_backup_downloads_a_real_archive(client, some_data, tmp_path):
    r = client.post("/backup", data={"profile": "projects"})
    assert r.status_code == 200
    assert "attachment" in r.headers["Content-Disposition"]
    archive = tmp_path / "got.zip"
    archive.write_bytes(r.data)
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
    assert backup.MANIFEST_NAME in names
    assert any(n.endswith("jobs/abc/book.json") for n in names)
    assert not any("/segments/" in n for n in names)


def test_the_temporary_archive_is_not_left_behind(client, some_data):
    """The archive is a copy; leaving it doubles the disk cost of every backup.

    Reading the body matters — cleanup is tied to the response being sent, the
    way any real client consumes it. Deleting earlier than that is what broke on
    Windows, which refuses to unlink a file that is still open.
    """
    r = client.post("/backup", data={"profile": "projects"})
    assert len(r.data) > 0
    leftovers = list(paths().tmp.glob("*.zip"))
    assert leftovers == [], f"backup left {leftovers} on disk"


def test_an_interrupted_download_still_cleans_up(client, some_data):
    """A client that disconnects halfway must not strand a copy either.

    Closing a partly-consumed response raises GeneratorExit through the same
    `finally` that the completed path uses.
    """
    r = client.post("/backup", data={"profile": "projects"})
    next(r.response)  # take the first chunk, then walk away
    r.close()
    assert list(paths().tmp.glob("*.zip")) == []


def test_an_abandoned_archive_is_swept_up_later(client, some_data):
    """A download killed mid-stream leaves a copy nothing else would remove,
    silently doubling the disk cost of every backup after it."""
    stale = paths().ensure().tmp / "ebook-audiobook-projects-19990101-000000.zip"
    stale.write_bytes(b"not really a zip")
    old = time.time() - 7200
    os.utime(stale, (old, old))

    client.post("/backup", data={"profile": "projects"})
    assert not stale.exists()


def test_a_fresh_archive_from_another_request_is_left_alone(client, some_data):
    """Two backups running at once must not delete each other's work."""
    inflight = paths().ensure().tmp / "ebook-audiobook-projects-20990101-000000.zip"
    inflight.write_bytes(b"someone else is streaming this")

    client.post("/backup", data={"profile": "projects"})
    assert inflight.exists()


def test_backup_rejects_an_unknown_profile(client, some_data):
    assert client.post("/backup", data={"profile": "enormous"}).status_code == 400


# --- updates ------------------------------------------------------------------

def test_update_checks_are_off_by_default(client):
    assert app_settings.load_settings().check_for_updates is False


def test_the_opt_in_can_be_saved(client):
    client.post("/settings", data={"check_for_updates": "1"})
    assert app_settings.load_settings().check_for_updates is True
    client.post("/settings", data={"check_for_updates": "0"})
    assert app_settings.load_settings().check_for_updates is False


def test_saving_the_opt_in_leaves_other_settings_alone(client):
    client.post("/settings", data={"power_mode": "quiet"})
    client.post("/settings", data={"check_for_updates": "1"})
    s = app_settings.load_settings()
    assert s.power_mode == "quiet" and s.check_for_updates is True


def test_the_check_endpoint_reports_an_available_update(client, monkeypatch):
    from ebook_audiobook import update as update_mod

    monkeypatch.setattr(update_mod, "check",
                        lambda **k: update_mod.Release("9.9.9", "v9.9.9", "https://x"))
    d = client.post("/updates/check").get_json()
    assert d["available"] is True
    assert d["latest"] == "9.9.9"
    assert "command" in d


def test_the_check_endpoint_survives_being_offline(client, monkeypatch):
    from ebook_audiobook import update as update_mod

    def boom(**k):
        raise update_mod.UpdateError("Couldn't reach GitHub to check for updates")

    monkeypatch.setattr(update_mod, "check", boom)
    d = client.post("/updates/check").get_json()
    assert d["ok"] and d["available"] is False
    assert "Couldn't reach GitHub" in d["message"]


def test_rendering_pages_makes_no_network_call(client, monkeypatch):
    """The product promise, as a test.

    Every page a user can land on, plus the status endpoints the browser polls
    continuously. If any of them ever grows an update check, this fails.
    """
    import urllib.request

    calls = []
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(
                            AssertionError("the page opened a network connection")))
    for url in ["/", "/new", "/voices", "/settings", "/api/status", "/diagnostics"]:
        client.get(url)
    assert calls == []


# --- diagnostics --------------------------------------------------------------

def test_diagnostics_reports_an_empty_log(client):
    d = client.get("/diagnostics").get_json()
    assert d["ok"] and d["errors"] == []
    assert d["max_age_days"] == errorlog.MAX_AGE_DAYS


def test_diagnostics_lists_recent_errors(client):
    try:
        raise ValueError("something broke")
    except ValueError as e:
        errorlog.record(e, op="render", job_id="j1")
    d = client.get("/diagnostics").get_json()
    assert len(d["errors"]) == 1
    assert d["errors"][0]["error"] == "ValueError"
    assert d["errors"][0]["job_id"] == "j1"


def test_the_report_downloads_as_markdown(client):
    try:
        raise ValueError("something broke")
    except ValueError as e:
        errorlog.record(e, op="render")
    r = client.get("/diagnostics/report")
    assert r.status_code == 200
    assert r.mimetype == "text/markdown"
    assert b"something broke" in r.data
    assert b"## Environment" in r.data


def test_the_report_does_not_leak_the_home_directory(client):
    import os

    home = os.path.expanduser("~")
    try:
        raise ValueError(f"could not read {home}/Books/Private.epub")
    except ValueError as e:
        errorlog.record(e, op="extract")
    assert home.encode() not in client.get("/diagnostics/report").data


def test_clearing_the_log(client):
    try:
        raise ValueError("x")
    except ValueError as e:
        errorlog.record(e, op="render")
    assert client.post("/diagnostics/clear").get_json()["removed"] >= 1
    assert client.get("/diagnostics").get_json()["errors"] == []
