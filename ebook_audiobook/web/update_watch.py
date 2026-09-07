"""Background, opt-in checking for a newer release — the automatic half of
:mod:`ebook_audiobook.update`.

That module's whole premise is consent: a version check is a request to
GitHub carrying your IP address, so it only ever happens when someone presses
a button. This module does not change that promise, it automates the button:
while, and only while, Settings -> Updates -> "Automatically check for
updates" is on, a background thread calls the exact same
:func:`ebook_audiobook.update.check` every few hours and remembers the
answer. Turning the setting off forgets whatever was found and the thread
goes back to doing nothing but sleep. There is still no start-up poll and no
check the setting did not authorise — the timer *is* the authorisation.

Installing what was found is a second, separate consent: finding an update
only ever populates :func:`status`, which the UI turns into a dismissible
banner (see base.html). Nothing here downloads or installs anything until
:func:`start_apply` is called, which only happens from a request the user
made by clicking "Install" after a confirmation dialog — the same
confirm-before-apply shape the manual "Check for updates" button in Settings
already has.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .. import settings as app_settings
from .. import update as update_mod

# How often the background thread actually asks GitHub, once enabled. Releases
# here are expected every few months at most, so this is not about catching one
# quickly — it's just frequent enough that "automatic" feels true, without
# making GitHub hear from every opted-in install every few minutes.
CHECK_INTERVAL_SECONDS = 2 * 3600
# A short buffer so the very first check — the "on launch" behaviour — doesn't
# race the app's own startup (opening the window, loading the tray) by firing
# before either exists to show the result in.
FIRST_CHECK_DELAY_SECONDS = 20
# How often the loop wakes up to re-read the setting. Short, so flipping the
# toggle on takes effect within a minute rather than needing a restart; the
# interval above is what actually rate-limits the GitHub call.
POLL_INTERVAL_SECONDS = 60


@dataclass
class _State:
    lock: threading.Lock = field(default_factory=threading.Lock)
    release: update_mod.Release | None = None
    apply_state: str = "idle"  # idle | running | done | error
    apply_error: str = ""
    applied_version: str = ""


_state = _State()


def status() -> dict:
    """Everything the update banner needs, from memory only — no network."""
    s = app_settings.load_settings()
    with _state.lock:
        release = _state.release
        apply_state = _state.apply_state
        apply_error = _state.apply_error
        applied_version = _state.applied_version
    available = bool(
        release
        and update_mod.is_newer(release.version, update_mod.current_version())
        and release.version != s.updates_dismissed_version
    )
    return {
        "enabled": s.check_for_updates,
        "available": available,
        "version": release.version if release else None,
        "notes_url": release.notes_url if release else None,
        "current": update_mod.current_version(),
        "apply_state": apply_state,
        "apply_error": apply_error,
        "applied_version": applied_version,
    }


def dismiss(version: str) -> None:
    """"Not now" for this release — the banner stays quiet until a newer one
    ships. Persisted, so it survives the app being closed and reopened."""
    s = app_settings.load_settings()
    s.updates_dismissed_version = version
    app_settings.save_settings(s)


def _check_once() -> None:
    try:
        release = update_mod.check()
    except update_mod.UpdateError:
        # Offline, rate-limited, GitHub having a bad day: try again next tick.
        # Nothing here is worth surfacing — the banner simply stays as it was.
        return
    with _state.lock:
        _state.release = release


def run_loop(stopping: threading.Event) -> None:
    """The background thread's body. Runs for the life of the server.

    Never tears itself down or spins back up when the setting flips — it just
    reads it every tick. That keeps the setting the single source of truth
    instead of racing a request thread that just changed it.
    """
    if stopping.wait(FIRST_CHECK_DELAY_SECONDS):
        return
    next_check = 0.0
    while not stopping.is_set():
        if app_settings.load_settings().check_for_updates:
            now = time.monotonic()
            if now >= next_check:
                _check_once()
                next_check = now + CHECK_INTERVAL_SECONDS
        else:
            # Off: forget what we knew, so a stale banner can't outlive consent,
            # and so turning it back on later starts from a clean check rather
            # than an answer that might be hours or days old.
            with _state.lock:
                _state.release = None
            next_check = 0.0
        if stopping.wait(POLL_INTERVAL_SECONDS):
            return


def start_apply() -> bool:
    """Install the release found above, in the background. False if one is
    already in progress. Runs the exact installer a manual upgrade would."""
    with _state.lock:
        if _state.apply_state == "running":
            return False
        _state.apply_state = "running"
        _state.apply_error = ""
    threading.Thread(target=_apply_worker, daemon=True, name="ebab-update-apply").start()
    return True


def _apply_worker() -> None:
    with _state.lock:
        release = _state.release
    try:
        code = update_mod.apply_update(yes=True)
    except update_mod.UpdateError as e:
        with _state.lock:
            _state.apply_state = "error"
            _state.apply_error = str(e)
        return
    with _state.lock:
        if code == 0:
            _state.apply_state = "done"
            _state.applied_version = release.version if release else ""
        else:
            _state.apply_state = "error"
            _state.apply_error = f"the installer exited with code {code}"
