"""Render intensity: the trade of wall-clock time for a usable machine.

The interesting risk isn't that a mode fails to slow things down — it's that it
succeeds and then leaks. POSIX niceness is one-way for an unprivileged process,
so a quiet render's priority would silently apply to every job after it unless
something notices. That's what most of this covers.
"""

from __future__ import annotations

import sys
import threading
import types

import pytest

from ebook_audiobook import power


@pytest.fixture(autouse=True)
def clean_taint():
    """The taint flag is thread-local and must not leak between tests."""
    power._tainted.value = False
    yield
    power._tainted.value = False


# --- modes -------------------------------------------------------------------

def test_the_default_is_full_speed():
    """Nobody should get a slower render than they asked for by accident."""
    assert power.DEFAULT_MODE == power.MODE_FULL
    assert power.profile_for(None).mode == power.MODE_FULL


@pytest.mark.parametrize("mode", power.MODES)
def test_every_mode_has_a_label_and_a_description(mode):
    assert power.MODE_LABELS[mode] and power.MODE_DESCRIPTIONS[mode]
    assert mode in power.PROFILES


@pytest.mark.parametrize("bad", ["turbo", "", None, "FULL SPEED", "  ", "quiet;rm -rf"])
def test_an_unknown_mode_falls_back_rather_than_raising(bad):
    """Settings files get hand-edited and old jobs carry stale values."""
    assert power.normalize_mode(bad) == power.MODE_FULL


@pytest.mark.parametrize("raw,expected", [
    ("QUIET", "quiet"), ("  balanced ", "balanced"), ("Full", "full"),
])
def test_modes_are_case_and_space_insensitive(raw, expected):
    assert power.normalize_mode(raw) == expected


def test_quiet_is_gentler_than_balanced_on_every_axis():
    """The modes must actually be ordered, or the labels are a lie."""
    balanced, quiet = power.PROFILES["balanced"], power.PROFILES["quiet"]
    assert quiet.nice_delta > balanced.nice_delta
    assert quiet.thread_divisor > balanced.thread_divisor
    assert quiet.pause_ratio > balanced.pause_ratio


def test_full_speed_changes_nothing():
    full = power.PROFILES["full"]
    assert (full.nice_delta, full.thread_divisor, full.pause_ratio) == (0, 1, 0.0)


# --- pacing ------------------------------------------------------------------

def test_full_speed_never_sleeps():
    assert power.pace(power.profile_for("full"), 5.0) == 0.0


def test_resting_is_proportional_to_the_work_just_done(monkeypatch):
    """Segments range from under a second to tens; a fixed pause would be either
    pointless or crippling."""
    slept = []
    monkeypatch.setattr(power.time, "sleep", slept.append)
    quiet = power.profile_for("quiet")
    power.pace(quiet, 2.0)
    assert slept == [2.0 * quiet.pause_ratio]


def test_one_enormous_segment_cannot_stall_the_render(monkeypatch):
    slept = []
    monkeypatch.setattr(power.time, "sleep", slept.append)
    power.pace(power.profile_for("quiet"), 10_000.0)
    assert slept == [10.0]  # capped


def test_a_cached_segment_costs_no_rest(monkeypatch):
    """Resumed renders skip already-done segments; those must not be paced."""
    slept = []
    monkeypatch.setattr(power.time, "sleep", slept.append)
    assert power.pace(power.profile_for("quiet"), 0.0) == 0.0
    assert slept == []


# --- thread limiting ---------------------------------------------------------

def test_thread_cap_scales_with_the_machine(monkeypatch):
    """The dominant lever for a CPU render — torch otherwise takes every core."""
    calls = []
    fake = types.ModuleType("torch")
    fake.set_num_threads = calls.append
    monkeypatch.setitem(sys.modules, "torch", fake)
    monkeypatch.setattr(power.os, "cpu_count", lambda: 16)

    assert power._limit_torch_threads(4) == 4
    assert calls == [4]


def test_thread_cap_never_goes_below_one(monkeypatch):
    fake = types.ModuleType("torch")
    fake.set_num_threads = lambda n: None
    monkeypatch.setitem(sys.modules, "torch", fake)
    monkeypatch.setattr(power.os, "cpu_count", lambda: 2)
    assert power._limit_torch_threads(8) == 1


def test_full_speed_leaves_the_thread_pool_alone(monkeypatch):
    called = []
    fake = types.ModuleType("torch")
    fake.set_num_threads = called.append
    monkeypatch.setitem(sys.modules, "torch", fake)
    assert power._limit_torch_threads(1) is None
    assert called == []


def test_missing_torch_is_not_an_error(monkeypatch):
    """--no-tts installs have no torch; applying a mode must still work."""
    monkeypatch.setitem(sys.modules, "torch", None)
    assert power._limit_torch_threads(4) is None


# --- priority, and the leak it would otherwise cause --------------------------

def test_an_irreversible_priority_drop_taints_the_thread(monkeypatch):
    """POSIX niceness is one-way, so something must remember to retire the
    thread — otherwise one quiet render slows every job that follows."""
    monkeypatch.setattr(power, "_lower_thread_priority", lambda d: (True, False))
    monkeypatch.setattr(power, "_limit_torch_threads", lambda d: None)
    monkeypatch.setattr(power, "_darwin_background", lambda e: False)

    assert not power.thread_is_tainted()
    power.apply(power.profile_for("quiet"))
    assert power.thread_is_tainted()


def test_a_reversible_priority_drop_does_not_taint(monkeypatch):
    """Windows and macOS can put it back, so the thread stays reusable."""
    monkeypatch.setattr(power, "_lower_thread_priority", lambda d: (True, True))
    monkeypatch.setattr(power, "_limit_torch_threads", lambda d: None)
    monkeypatch.setattr(power, "_darwin_background", lambda e: False)
    power.apply(power.profile_for("quiet"))
    assert not power.thread_is_tainted()


def test_the_taint_flag_is_per_thread():
    """The web server's threads must not be retired because a render was quiet."""
    power._tainted.value = True
    seen = []
    t = threading.Thread(target=lambda: seen.append(power.thread_is_tainted()))
    t.start()
    t.join()
    assert seen == [False]


def test_full_speed_applies_nothing_and_reports_nothing(monkeypatch):
    monkeypatch.setattr(power, "_lower_thread_priority",
                        lambda d: pytest.fail("full speed must not touch priority"))
    assert power.apply(power.profile_for("full")) == []


def test_apply_reports_what_actually_took_effect(monkeypatch):
    """Notes drive what the user is told, so they must reflect reality — not
    what was attempted."""
    monkeypatch.setattr(power, "_limit_torch_threads", lambda d: 3)
    monkeypatch.setattr(power, "_lower_thread_priority", lambda d: (True, True))
    monkeypatch.setattr(power, "_darwin_background", lambda e: True)
    notes = power.apply(power.profile_for("quiet"))
    joined = " | ".join(notes)
    assert "3" in joined and "priority" in joined
    assert "efficiency cores" in joined
    assert "50%" in joined


def test_apply_survives_a_platform_that_refuses_everything(monkeypatch):
    """A locked-down container must not stop a render from happening at all."""
    monkeypatch.setattr(power, "_limit_torch_threads", lambda d: None)
    monkeypatch.setattr(power, "_lower_thread_priority", lambda d: (False, True))
    monkeypatch.setattr(power, "_darwin_background", lambda e: False)
    notes = power.apply(power.profile_for("quiet"))
    assert all("priority" not in n for n in notes)  # didn't claim what it didn't do


def test_priority_change_never_raises_when_the_os_refuses(monkeypatch):
    def refuse(*a, **k):
        raise OSError("not permitted")

    monkeypatch.setattr(power.os, "getpriority", refuse, raising=False)
    applied, reversible = power._lower_thread_priority(10)
    assert applied is False


def test_darwin_background_is_a_noop_off_macos(monkeypatch):
    monkeypatch.setattr(power.sys, "platform", "linux")
    assert power._darwin_background(True) is False


def test_describe_is_readable():
    text = power.describe("quiet")
    assert text.startswith(power.MODE_LABELS["quiet"])
    assert "slower" in text
