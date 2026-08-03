"""Quitting the application.

Now that the window is a browser window and the server outlives it, this is the
deliberate way out — and it is one click away in the sidebar, so a multi-hour
render has to survive a stray click on it.
"""

from __future__ import annotations

import pytest

from ebook_audiobook.desktop import runtime
from ebook_audiobook.web import create_app
from ebook_audiobook.web import app as app_module
from ebook_audiobook.web.runner import Runner

calls: list[str] = []


@pytest.fixture
def runner(monkeypatch):
    """A Runner of this test's own, in place of the module-level singleton.

    The singleton is shared by the whole session and has a live worker thread
    behind it, so tests elsewhere can flip it to busy at any moment — which made
    every quit here answer 409 depending only on how the suite happened to be
    ordered. Resetting its fields between tests was not enough; the fix is not
    to touch it. The routes look the global up by name at call time, so
    rebinding it here is all it takes.
    """
    fresh = Runner()
    monkeypatch.setattr(app_module, "runner", fresh)
    return fresh


@pytest.fixture
def app(runner):
    """An app wired up the way serve() wires it: with something to shut down."""
    calls.clear()
    app = create_app()
    app.config["EBAB_SHUTDOWN"] = lambda: calls.append("shutdown")
    return app


def test_status_identifies_the_app(app):
    """A second launch uses this marker to tell our port from a stranger's."""
    body = app.test_client().get("/api/status").get_json()
    assert body["app"] == runtime.APP_ID


def test_status_reports_the_running_kind(app, runner):
    runner.current = "job123:render"
    body = app.test_client().get("/api/status").get_json()
    assert body["kind"] == "render"
    assert body["busy"] is True


def test_status_kind_is_null_when_idle(app):
    body = app.test_client().get("/api/status").get_json()
    assert body["kind"] is None


def test_quit_when_idle_shuts_down(app):
    r = app.test_client().post("/quit")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    _drain()
    assert calls == ["shutdown"]


def test_quit_refuses_mid_render(app, runner):
    """The regression that matters: one click must not bin a six-hour render."""
    runner.current = "job123:render"
    r = app.test_client().post("/quit")
    assert r.status_code == 409
    body = r.get_json()
    assert body["busy"] is True
    # The UI words its warning from this — a render and a voice sample deserve
    # very different sentences.
    assert body["kind"] == "render"
    _drain()
    assert calls == []


def test_quit_mid_render_proceeds_when_forced(app, runner):
    runner.current = "job123:render"
    r = app.test_client().post("/quit?force=1")
    assert r.status_code == 200
    _drain()
    assert calls == ["shutdown"]


def test_forced_quit_cancels_the_running_job(app, runner):
    """Ask the worker to stop at its next checkpoint rather than just exiting,
    so the job's own state is left consistent on disk."""
    runner.current = "job123:render"
    app.test_client().post("/quit?force=1")
    assert runner._cancel_requested("job123")


def test_quit_is_unavailable_without_a_server_to_stop():
    """Under `flask run` or the test client there is nothing to shut down, and
    saying so beats a 500 or a silent no-op."""
    r = create_app().test_client().post("/quit")
    assert r.status_code == 501
    assert r.get_json()["ok"] is False


def test_quit_control_is_hidden_when_it_cannot_work():
    """The sidebar button is rendered only for a real application process."""
    assert b'id="quitBtn"' not in create_app().test_client().get("/").data


def test_quit_control_is_shown_in_the_app(app):
    assert b'id="quitBtn"' in app.test_client().get("/").data


def _drain() -> None:
    """The route answers first and shuts down a beat later, so the browser gets
    a response instead of a dropped connection. Wait that beat out."""
    import time

    time.sleep(0.4)
