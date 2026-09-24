"""A detailed performance log, off unless someone turns it on.

The error log (:mod:`errorlog`) explains a render that *failed*. It has nothing
to say about one that is merely slow, which on a laptop is the likelier
complaint, and the reasons there are invisible: which card the engine chose,
whether it fitted, whether it had to step down to the CPU, the power mode, the
mains cable. This records exactly that, one JSON line per event:

* ``engine_plan`` - the card, its free and total memory, and the rungs chosen
  from them (see :mod:`tiers`)
* ``engine_loaded`` - the rung that loaded, how long it took, what it holds
* ``step_down`` - the engine moving to a smaller tier, or to the CPU, and why
* ``stalled`` - a passage on the card abandoned for making no progress, with
  the card's free memory as the driver saw it at that moment
* ``render_start`` - the job, the power mode and what it applied, the thread's
  priority, whether the machine is on mains power
* ``segment`` - every passage: characters, seconds of work, seconds of audio,
  PyTorch's peak on the card, and the card's free memory per the driver

Off by default. It writes a line every few seconds for the length of a render,
and nobody should pay for that without asking. Turned on in Settings (or with
``EBAB_DEBUG=1`` for one run). Like the error log it stays on this computer, is
capped at about 1.5 MB, deletes itself after a fortnight of disuse, and never
records book titles; :func:`report_lines` also replaces home directories with
``~`` before anything is shared.
"""

from __future__ import annotations

import json
import os
import platform
import time

from . import errorlog

ENV = "EBAB_DEBUG"
MAX_BYTES = 512_000
BACKUP_COUNT = 2
MAX_AGE_DAYS = 14


def log_path():
    return errorlog.log_dir() / "debug.log"


def _files() -> list:
    d = errorlog.log_dir()
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("debug.log*") if p.is_file())


def enabled() -> bool:
    """Read fresh each time: it is one small file, and a cached answer would
    keep logging after the switch was turned off."""
    if os.environ.get(ENV) == "1":
        return True
    try:
        from .settings import load_settings

        return bool(load_settings().debug_log)
    except Exception:  # noqa: BLE001
        return False


def event(kind: str, **fields) -> None:
    """Append one event if the log is on. Never raises."""
    try:
        if not enabled():
            return
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "kind": kind}
        entry.update(fields)
        prune()
        errorlog.open_logger(log_path(), MAX_BYTES, BACKUP_COUNT).error(
            json.dumps(entry, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001 - diagnostics must never break a render
        pass


def environment() -> dict:
    """The machine as the engine sees it. Imports torch, so it is called where
    the engine is already loaded, or by ``diagnose``."""
    from . import __version__, power, settings

    env = {
        "version": __version__,
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(),
        "cpus": os.cpu_count(),
        "default_power_mode": settings.default_power_mode(),
        **power.power_source(),
    }
    try:
        import torch

        env["torch"] = torch.__version__
        env["cuda"] = getattr(torch.version, "cuda", None)
        env["hip"] = getattr(torch.version, "hip", None)
    except Exception:  # noqa: BLE001
        env["torch"] = None
    try:
        from . import device

        env["device"] = device.select_device().describe()
    except Exception:  # noqa: BLE001
        env["device"] = "unknown"
    return env


def entries(limit: int | None = None) -> list[dict]:
    """Every event, oldest first. Unreadable lines are skipped."""
    out: list[dict] = []
    for p in sorted(_files(), key=lambda p: p.name, reverse=True):
        try:
            for line in p.read_text("utf-8", errors="replace").splitlines():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        except OSError:
            continue
    return out[-limit:] if limit else out


def prune(max_age_days: int = MAX_AGE_DAYS) -> int:
    cutoff = time.time() - max_age_days * 86_400
    stale = []
    for p in _files():
        try:
            if p.stat().st_mtime < cutoff:
                stale.append(p)
        except OSError:
            continue
    if not stale:
        return 0
    errorlog._close_handlers()  # Windows will not delete an open file
    removed = 0
    for p in stale:
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def clear() -> int:
    errorlog._close_handlers()
    removed = 0
    for p in _files():
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def total_bytes() -> int:
    return sum(p.stat().st_size for p in _files() if p.exists())


def report_lines(limit: int = 40) -> list[str]:
    """The recent events as Markdown for a bug report, home directories
    replaced. Empty when there is nothing logged."""
    found = entries(limit=limit)
    if not found:
        return []
    lines = [f"## Performance log (last {len(found)} events)", "",
             "<details><summary>debug.log</summary>", "", "```"]
    for e in found:
        lines.append(errorlog.redact(json.dumps(e, ensure_ascii=False, default=str)))
    lines += ["```", "", "</details>", ""]
    return lines
