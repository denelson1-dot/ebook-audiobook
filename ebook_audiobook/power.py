"""How hard a render is allowed to push the machine.

A full render is hours of sustained, unapologetic load. On a desktop that is
exactly what you want. On the laptop somebody is also trying to work on — and
especially on a fanless MacBook Air, where the only response to sustained load is
to get hot and then throttle — it is antisocial, and the honest answer is to let
the user trade wall-clock time for a quiet machine they can keep using.

Three levers actually move the needle, and which one dominates depends on where
the work is happening:

**Thread count** decides everything for a CPU render. PyTorch grabs every core it
can see by default, which is why a CPU render pins all cores and the fans go to
maximum. Capping it is the single biggest quality-of-life change on a laptop.

**Pacing** decides everything for a GPU render, where the CPU is nearly idle and
the GPU is the heat source. Threads are irrelevant there; a short pause between
segments is what lets the die cool, and it is also what stops a render starving
whatever else wants the GPU (a video call, a game, the window server).

**Scheduling priority** decides how the machine *feels* while it happens. It is
applied per-thread, which matters: the render must yield without the web UI
becoming unresponsive too.

macOS is different in two ways. It has no per-thread niceness at all:
``setpriority(PRIO_PROCESS, 0, …)`` there lowers the *whole process*, web server
included, and an unprivileged process can never raise it back — so one quiet
render would leave every later full-speed render, and the UI, permanently
niced. And it has something better: thread QoS classes, the documented way to
say "this is background work, prefer the efficiency cores". On Apple Silicon
that is the difference between a warm laptop and a cool one, and no amount of
ordinary niceness achieves it. So on Darwin the priority lever is QoS alone,
applied to the render thread and fully reversible.
"""

from __future__ import annotations

from .i18n import N_, _
import ctypes
import os
import sys
import threading
import time
from dataclasses import dataclass

MODE_FULL = "full"
MODE_BALANCED = "balanced"
MODE_QUIET = "quiet"
MODES = (MODE_FULL, MODE_BALANCED, MODE_QUIET)

DEFAULT_MODE = MODE_FULL

# Shown in the UI and by `check`. Kept here so the CLI, the web UI, and the
# settings page can't drift into describing the same mode three different ways.
MODE_LABELS = {
    MODE_FULL: N_("Full speed"),
    MODE_BALANCED: N_("Balanced"),
    MODE_QUIET: N_("Quiet / background"),
}

MODE_DESCRIPTIONS = {
    MODE_FULL: N_("Use everything available. Fastest, and the machine will be busy."),
    MODE_BALANCED: N_("Leave room to keep working. Roughly 10–25%% slower."),
    MODE_QUIET: N_("Stay out of the way — fewer cores, cooler, quieter fans. "
                "Roughly 2x slower."),
}

# Darwin thread QoS classes, from <sys/qos.h>, applied with
# pthread_set_qos_class_self_np(). UTILITY is "long-running work the user is not
# waiting on"; BACKGROUND additionally prefers the efficiency cores and throttles
# I/O. DEFAULT puts a thread back where a fresh one starts.
_QOS_DEFAULT = 0x15
_QOS_UTILITY = 0x11
_QOS_BACKGROUND = 0x09
_DARWIN_QOS = {"utility": _QOS_UTILITY, "background": _QOS_BACKGROUND}


@dataclass(frozen=True)
class Profile:
    """What one mode actually does."""

    mode: str
    nice_delta: int          # added to the render thread's niceness (not on macOS)
    thread_divisor: int      # cores // this, floored at 1; 1 means "leave alone"
    pause_ratio: float       # rest for this fraction of the last segment's time
    darwin_qos: str | None   # macOS thread QoS class: "utility", "background", None


PROFILES = {
    MODE_FULL: Profile(MODE_FULL, 0, 1, 0.0, None),
    MODE_BALANCED: Profile(MODE_BALANCED, 5, 2, 0.15, "utility"),
    MODE_QUIET: Profile(MODE_QUIET, 10, 4, 0.5, "background"),
}


def normalize_mode(mode: str | None) -> str:
    m = (mode or "").strip().lower()
    return m if m in MODES else DEFAULT_MODE


def profile_for(mode: str | None) -> Profile:
    return PROFILES[normalize_mode(mode)]


# --- applying it -------------------------------------------------------------

# Set when this thread's scheduling priority has been lowered in a way we cannot
# undo (Linux: an unprivileged process may raise its niceness but never lower it
# again). The runner reads this and retires the thread rather than letting a
# quiet render's priority leak into the next full-speed one.
_tainted = threading.local()


def thread_is_tainted() -> bool:
    """Has this thread been left at a priority we can't restore?"""
    return bool(getattr(_tainted, "value", False))


def _lower_thread_priority(delta: int) -> tuple[bool, bool]:
    """Lower the calling thread's scheduling priority by ``delta``.

    Returns ``(applied, reversible)``. Deliberately per-thread rather than
    per-process: on Linux each thread is a schedulable task, so setpriority
    targets this one alone, and Windows has an explicit per-thread API. That
    keeps the web UI responsive while the render yields.

    macOS is skipped on purpose. Darwin has no per-thread niceness: the same
    ``setpriority`` call lowers the whole process — every waitress thread with
    it — and cannot be undone by an unprivileged process, so a single quiet
    render would slow the UI and every later render for the life of the
    process. Its lever is :func:`_darwin_qos`, which is per-thread and reversible.
    """
    if delta <= 0:
        return False, True
    if sys.platform == "win32":
        return _windows_below_normal(), True
    if sys.platform == "darwin":
        return False, True
    try:
        # Linux: PRIO_PROCESS on a thread id addresses that thread.
        current = os.getpriority(os.PRIO_PROCESS, 0)
        os.setpriority(os.PRIO_PROCESS, 0, min(19, current + delta))
        # An unprivileged process can raise niceness but never lower it back.
        return True, False
    except (OSError, AttributeError, ValueError):
        return False, True


def _windows_below_normal() -> bool:
    """Put this thread below normal priority (reversible, unlike POSIX nice)."""
    try:
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        # THREAD_PRIORITY_BELOW_NORMAL = -1
        return bool(kernel32.SetThreadPriority(kernel32.GetCurrentThread(), -1))
    except Exception:  # noqa: BLE001 - priority is a nicety, never fatal
        return False


def _darwin_qos(qos: str | None) -> bool:
    """Put the calling thread in a Darwin QoS class; ``None`` restores the default.

    This is the lever that matters on Apple Silicon: background QoS is what
    steers work onto the efficiency cores, which is how a long render stops
    cooking a fanless MacBook. Per-thread, so the web server keeps its own
    class, and reversible, unlike POSIX niceness.
    """
    if sys.platform != "darwin":
        return False
    cls = _QOS_DEFAULT if qos is None else _DARWIN_QOS.get(qos)
    if cls is None:
        return False
    try:
        libc = ctypes.CDLL("libSystem.B.dylib", use_errno=True)
        fn = libc.pthread_set_qos_class_self_np
        fn.argtypes = (ctypes.c_uint, ctypes.c_int)
        fn.restype = ctypes.c_int
        return fn(cls, 0) == 0
    except Exception:  # noqa: BLE001
        return False


def _limit_torch_threads(divisor: int) -> int | None:
    """Cap PyTorch's CPU thread pool. Returns the new count, or None.

    The dominant lever for a CPU render: left alone, torch takes every core and
    the machine becomes unusable. Harmless on a GPU render, where the pool is
    barely touched.
    """
    if divisor <= 1:
        return None
    try:
        import torch
    except Exception:  # noqa: BLE001 - no engine installed; nothing to limit
        return None
    cores = os.cpu_count() or 1
    target = max(1, cores // divisor)
    try:
        torch.set_num_threads(target)
        return target
    except Exception:  # noqa: BLE001
        return None


def apply(profile: Profile) -> list[str]:
    """Apply a profile to the calling thread. Returns notes on what took effect.

    Every step is best-effort and independent: a container that forbids changing
    priority, a torch build without thread control, a platform without a
    background QoS class — none of those should stop a render, and each simply
    doesn't appear in the notes.
    """
    notes: list[str] = []
    if profile.mode == MODE_FULL:
        # Undo anything a previous quiet render left behind that we *can* undo.
        _darwin_qos(None)
        return notes

    threads = _limit_torch_threads(profile.thread_divisor)
    if threads is not None:
        notes.append(f"CPU threads limited to {threads}")

    applied, reversible = _lower_thread_priority(profile.nice_delta)
    if applied:
        notes.append("running at lower scheduling priority")
        if not reversible:
            _tainted.value = True

    if profile.darwin_qos and _darwin_qos(profile.darwin_qos):
        notes.append("using efficiency cores (background QoS)"
                     if profile.darwin_qos == "background"
                     else "running as a utility-class task")

    if profile.pause_ratio:
        notes.append(f"resting {int(profile.pause_ratio * 100)}% of the time")
    return notes


def pace(profile: Profile, work_seconds: float) -> float:
    """Rest in proportion to the work just done. Returns seconds slept.

    Proportional rather than fixed because segments vary from under a second to
    tens of seconds; a fixed pause would be either pointless or crippling. The
    cap keeps one unusually long segment from stalling a render for a minute.
    """
    if profile.pause_ratio <= 0 or work_seconds <= 0:
        return 0.0
    nap = min(work_seconds * profile.pause_ratio, 10.0)
    time.sleep(nap)
    return nap


def describe(mode: str | None) -> str:
    m = normalize_mode(mode)
    return f"{_(MODE_LABELS[m])} — {_(MODE_DESCRIPTIONS[m])}"
