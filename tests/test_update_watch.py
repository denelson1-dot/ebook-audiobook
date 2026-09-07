"""The background, opt-in update checker.

Two things this defends: that the loop never asks GitHub anything while the
setting is off (the same consent promise ebook_audiobook.update makes, just
automated), and that turning the setting off forgets whatever was found
rather than leaving a stale banner up with no way to have earned it.
"""

from __future__ import annotations

import threading

from ebook_audiobook import settings, update
from ebook_audiobook.web import update_watch


def _release(version="9.9.9"):
    return update.Release(version=version, tag=f"v{version}", url="https://example/rel")


# --- status() -------------------------------------------------------------

def test_status_reports_disabled_by_default():
    d = update_watch.status()
    assert d["enabled"] is False
    assert d["available"] is False


def test_status_is_unavailable_with_nothing_found_yet():
    settings.save_settings(settings.Settings(check_for_updates=True))
    d = update_watch.status()
    assert d["enabled"] is True
    assert d["available"] is False
    assert d["version"] is None


def test_status_reports_a_newer_release_once_found():
    settings.save_settings(settings.Settings(check_for_updates=True))
    update_watch._state.release = _release("9.9.9")
    d = update_watch.status()
    assert d["available"] is True
    assert d["version"] == "9.9.9"


def test_status_hides_a_release_that_is_not_actually_newer():
    """A stale record (this build is already newer) must not offer a downgrade."""
    settings.save_settings(settings.Settings(check_for_updates=True))
    update_watch._state.release = _release(update.current_version())
    assert update_watch.status()["available"] is False


def test_dismissing_a_version_hides_it_but_not_a_newer_one():
    settings.save_settings(settings.Settings(check_for_updates=True))
    update_watch._state.release = _release("9.9.9")
    update_watch.dismiss("9.9.9")
    assert update_watch.status()["available"] is False

    update_watch._state.release = _release("9.9.10")
    assert update_watch.status()["available"] is True


def test_dismissal_is_persisted():
    update_watch.dismiss("9.9.9")
    assert settings.load_settings().updates_dismissed_version == "9.9.9"


# --- the background loop ---------------------------------------------------

def test_the_loop_never_checks_while_disabled(monkeypatch):
    """The consent this module exists to preserve: no timer fires a check
    GitHub sees unless the setting explicitly authorised it."""
    calls = []
    monkeypatch.setattr(update_watch, "_check_once", lambda: calls.append(1))
    monkeypatch.setattr(update_watch, "FIRST_CHECK_DELAY_SECONDS", 0)
    monkeypatch.setattr(update_watch, "POLL_INTERVAL_SECONDS", 0)

    stopping = threading.Event()
    # settings default to check_for_updates=False; stop after a couple of ticks.
    ticks = [0]
    real_wait = stopping.wait

    def counting_wait(timeout):
        ticks[0] += 1
        if ticks[0] > 3:
            stopping.set()
        return real_wait(0)

    monkeypatch.setattr(stopping, "wait", counting_wait)
    update_watch.run_loop(stopping)
    assert calls == []


def test_the_loop_checks_once_enabled(monkeypatch):
    settings.save_settings(settings.Settings(check_for_updates=True))
    calls = []
    monkeypatch.setattr(update_watch, "_check_once", lambda: calls.append(1))
    monkeypatch.setattr(update_watch, "FIRST_CHECK_DELAY_SECONDS", 0)
    monkeypatch.setattr(update_watch, "CHECK_INTERVAL_SECONDS", 10_000)

    stopping = threading.Event()
    ticks = [0]
    real_wait = stopping.wait

    def counting_wait(timeout):
        ticks[0] += 1
        if ticks[0] > 3:
            stopping.set()
        return real_wait(0)

    monkeypatch.setattr(stopping, "wait", counting_wait)
    update_watch.run_loop(stopping)
    # Checked on the first eligible tick, and not again within the interval.
    assert calls == [1]


def test_turning_it_off_forgets_what_was_found(monkeypatch):
    """A banner must not survive the consent that produced it being withdrawn."""
    settings.save_settings(settings.Settings(check_for_updates=False))
    update_watch._state.release = _release("9.9.9")
    monkeypatch.setattr(update_watch, "FIRST_CHECK_DELAY_SECONDS", 0)

    stopping = threading.Event()
    ticks = [0]
    real_wait = stopping.wait

    def counting_wait(timeout):
        ticks[0] += 1
        if ticks[0] > 1:
            stopping.set()
        return real_wait(0)

    monkeypatch.setattr(stopping, "wait", counting_wait)
    update_watch.run_loop(stopping)
    assert update_watch._state.release is None


def test_a_failed_check_is_swallowed_not_raised(monkeypatch):
    settings.save_settings(settings.Settings(check_for_updates=True))
    monkeypatch.setattr(update, "check", lambda **k: (_ for _ in ()).throw(update.UpdateError("offline")))
    update_watch._check_once()  # must not raise
    assert update_watch._state.release is None


# --- applying -------------------------------------------------------------

def test_start_apply_runs_the_installer_and_reports_done(monkeypatch):
    update_watch._state.release = _release("9.9.9")
    monkeypatch.setattr(update, "apply_update", lambda yes=False, timeout=3600: 0)
    assert update_watch.start_apply() is True

    import time

    deadline = time.monotonic() + 2
    while update_watch.status()["apply_state"] == "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    d = update_watch.status()
    assert d["apply_state"] == "done"
    assert d["applied_version"] == "9.9.9"


def test_start_apply_reports_installer_failure(monkeypatch):
    monkeypatch.setattr(update, "apply_update", lambda yes=False, timeout=3600: 1)
    update_watch.start_apply()

    import time

    deadline = time.monotonic() + 2
    while update_watch.status()["apply_state"] == "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    d = update_watch.status()
    assert d["apply_state"] == "error"
    assert "1" in d["apply_error"]


def test_start_apply_reports_an_update_error(monkeypatch):
    def boom(yes=False, timeout=3600):
        raise update.UpdateError("no curl or PowerShell found")

    monkeypatch.setattr(update, "apply_update", boom)
    update_watch.start_apply()

    import time

    deadline = time.monotonic() + 2
    while update_watch.status()["apply_state"] == "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    d = update_watch.status()
    assert d["apply_state"] == "error"
    assert "curl" in d["apply_error"]


def test_start_apply_refuses_a_second_run_while_one_is_in_flight(monkeypatch):
    started = threading.Event()
    finish = threading.Event()

    def slow_apply(yes=False, timeout=3600):
        started.set()
        finish.wait(2)
        return 0

    monkeypatch.setattr(update, "apply_update", slow_apply)
    assert update_watch.start_apply() is True
    started.wait(2)
    assert update_watch.start_apply() is False
    finish.set()
