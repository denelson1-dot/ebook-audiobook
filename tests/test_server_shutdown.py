"""The server must not mistake a broken tray for a finished session.

Every pystray backend wraps its main loop in a bare ``except`` and returns
normally afterwards, so ``tray.run()`` returning proves nothing about whether a
tray ever appeared. The first version of this code read that return value as
"the tray ran the whole session", which meant that on any machine where the tray
could not start — a stock GNOME desktop, a Mac reached over SSH — the app exited
about a second after launch, silently, having served nothing at all.
"""

from __future__ import annotations

import threading

import pytest

from ebook_audiobook.web import server


@pytest.fixture
def fake_stack(monkeypatch):
    """Stand in for waitress, the tray and the browser, so serve() can be run
    to completion in-process without binding anything."""
    state = {"joined": 0, "closed": 0, "drained": 0, "windows_closed": 0,
             "tray_ran": False}

    class FakeServer:
        def __init__(self):
            self.task_dispatcher = self
            self.stop = threading.Event()

        def run(self):
            self.stop.wait(5)
            state["joined"] += 1

        def close(self):
            state["closed"] += 1
            self.stop.set()

        def shutdown(self):
            state["drained"] += 1

    fake = FakeServer()
    monkeypatch.setattr(server, "create_app", lambda: _StubApp())
    monkeypatch.setattr("waitress.create_server", lambda *a, **k: fake)
    monkeypatch.setattr(server.runtime, "write", lambda *a, **k: None)
    monkeypatch.setattr(server.runtime, "clear", lambda: None)
    monkeypatch.setattr(server.launcher, "close_windows",
                        lambda *a, **k: state.__setitem__(
                            "windows_closed", state["windows_closed"] + 1))
    monkeypatch.setattr(server.tray, "stop", lambda: None)
    monkeypatch.setattr(server.tray, "refresh", lambda: None)
    state["server"] = fake
    return state


class _StubApp:
    def __init__(self):
        self.config = {}


def test_a_tray_that_dies_instantly_does_not_end_the_session(fake_stack, monkeypatch):
    """The regression. tray.run() returns True having done nothing; the server
    must keep serving rather than fall through to shutdown."""
    monkeypatch.setattr(server.tray, "available", lambda: True)
    monkeypatch.setattr(server.tray, "run", lambda *a, **k: True)

    done = threading.Event()
    threading.Thread(
        target=lambda: (server.serve(host="127.0.0.1", port=1, open_browser=False),
                        done.set()),
        daemon=True).start()

    # serve() should now be blocked on the server thread, NOT returning.
    assert not done.wait(1.5), "serve() returned even though nothing asked it to stop"

    # And a real stop request still ends it cleanly.
    fake_stack["server"].close()
    assert done.wait(5), "serve() did not return after the server stopped"
    assert fake_stack["drained"] == 1
    assert fake_stack["windows_closed"] == 1


def test_quitting_through_the_tray_ends_the_session(fake_stack, monkeypatch):
    """The other side of it: a genuine quit must not then block forever."""
    monkeypatch.setattr(server.tray, "available", lambda: True)

    def run_tray(url, on_show, on_quit, quit_label=None, **kw):
        on_quit()  # the user picks Quit
        return True

    monkeypatch.setattr(server.tray, "run", run_tray)

    done = threading.Event()
    threading.Thread(
        target=lambda: (server.serve(host="127.0.0.1", port=1, open_browser=False),
                        done.set()),
        daemon=True).start()
    assert done.wait(5), "serve() hung after the tray quit"
    assert fake_stack["closed"] >= 1
    assert fake_stack["drained"] == 1


def test_the_quit_handler_does_not_block_the_calling_thread(fake_stack, monkeypatch):
    """On macOS the tray dispatches menu actions on the AppKit main thread.
    Draining waitress there means a beachball and a 'Not Responding' badge, so
    request_stop() must hand back promptly and leave the slow work to serve()."""
    monkeypatch.setattr(server.tray, "available", lambda: True)
    timings = {}

    def run_tray(url, on_show, on_quit, quit_label=None, **kw):
        import time
        start = time.monotonic()
        on_quit()
        timings["elapsed"] = time.monotonic() - start
        # Whatever happened, the drain must not have run yet.
        timings["drained_during_quit"] = fake_stack["drained"]
        return True

    monkeypatch.setattr(server.tray, "run", run_tray)
    server.serve(host="127.0.0.1", port=1, open_browser=False)

    assert timings["elapsed"] < 0.5, "the quit handler blocked its caller"
    assert timings["drained_during_quit"] == 0
    assert fake_stack["drained"] == 1  # ...but it did happen, afterwards


def test_no_tray_still_blocks_and_shuts_down_cleanly(fake_stack, monkeypatch):
    """The headless path must behave exactly like the plain blocking server."""
    monkeypatch.setattr(server.tray, "available", lambda: False)
    monkeypatch.setattr(server.tray, "run",
                        lambda *a, **k: pytest.fail("tray must not be started"))

    done = threading.Event()
    threading.Thread(
        target=lambda: (server.serve(host="127.0.0.1", port=1,
                                     open_browser=False, use_tray=False),
                        done.set()),
        daemon=True).start()
    assert not done.wait(1.0), "serve() returned without being asked to stop"
    fake_stack["server"].close()
    assert done.wait(5)
    assert fake_stack["drained"] == 1


def test_a_system_terminate_drains_before_the_process_is_allowed_to_exit(fake_stack, monkeypatch):
    """macOS: pystray answers Ctrl-C with NSApp.terminate:, and log-out sends
    the same. AppKit then calls exit() without unwinding Python, so the finally
    block in serve() never runs. Everything that matters on the way out — the
    runtime record, the app window — has to happen inside the callback, and
    has to have happened by the time it returns."""
    monkeypatch.setattr(server.tray, "available", lambda: True)
    seen = {}

    def run_tray(url, on_show, on_quit, quit_label=None, on_terminate=None):
        assert on_terminate is not None
        on_terminate()
        # By the time AppKit gets its answer the drain and the windows are done.
        seen["drained"] = fake_stack["drained"]
        seen["windows_closed"] = fake_stack["windows_closed"]
        return True

    monkeypatch.setattr(server.tray, "run", run_tray)
    server.serve(host="127.0.0.1", port=1, open_browser=False)
    assert seen == {"drained": 1, "windows_closed": 1}
    # And serve()'s own finally block, when it does get to run, is idempotent.
    assert fake_stack["drained"] == 2
