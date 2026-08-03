"""A small, self-limiting diagnostic log.

When a render fails at hour three, the terminal window that would have shown
why is usually long gone. This records just enough to diagnose a failure after
the fact — and deliberately no more.

Three properties matter, in this order:

**It stays small.** One line of JSON per failure, a hard cap on how much of a
traceback is kept, size-based rotation, and an age cut-off. Worst case on disk
is a little under a megabyte, and a log nobody has written to in two weeks
deletes itself. This is a diagnostic aid, not an audit trail.

**It stays private.** The app's promise is that nothing leaves the machine, and
this does not change that: writing a log is local, and nothing here transmits
anything. :func:`issue_report` — the one function whose output is meant to be
pasted somewhere public — additionally strips home directory paths and omits
book titles, so sharing a failure doesn't share a reading history.

**It is machine-readable.** JSON Lines, so a person or an assistant can read the
whole file with one ``json.loads`` per line rather than parsing prose.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import platform
import re
import sys
import time
import traceback
from pathlib import Path

from .config import paths

# ~256 KB per file plus two rotations: under a megabyte, all in.
MAX_BYTES = 256_000
BACKUP_COUNT = 2
# A failure nobody investigated in a fortnight is not going to be investigated.
MAX_AGE_DAYS = 14
# One pathological traceback must not fill the file on its own.
MAX_TRACEBACK_CHARS = 4_000

_handlers: dict[str, logging.Logger] = {}


def log_dir() -> Path:
    return paths().root / "logs"


def log_path() -> Path:
    return log_dir() / "errors.log"


def _all_log_files() -> list[Path]:
    d = log_dir()
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("errors.log*") if p.is_file())


def _close_handlers() -> None:
    """Release the open file handles on the log, so its files can be deleted.

    Two separate reasons this has to happen before any log file is unlinked:

    * **Windows refuses to delete an open file.** The rotating handler holds
      ``errors.log`` open, so the unlink raised ``PermissionError``, the
      surrounding ``except OSError`` swallowed it, and prune/clear reported
      having removed nothing while the log sat exactly where it was.
    * **Dropping the ``_handlers`` entry was never enough, on any platform.**
      ``logging`` keeps loggers in a global registry of its own, so forgetting
      our reference left the handler attached and writing to a file with no
      name — and because ``_logger`` then saw no cached entry, the next
      ``record()`` attached a *second* handler to the same logger and every
      line after that was written twice.
    """
    for log in _handlers.values():
        for handler in list(log.handlers):
            log.removeHandler(handler)
            try:
                handler.close()
            except Exception:  # noqa: BLE001 - already closed, or a torn write
                pass
    _handlers.clear()


def prune(max_age_days: int = MAX_AGE_DAYS) -> int:
    """Delete log files untouched for longer than the age limit.

    Rotation bounds total size; this bounds age. Returns how many were removed.
    """
    cutoff = time.time() - max_age_days * 86_400
    stale = []
    for p in _all_log_files():
        try:
            if p.stat().st_mtime < cutoff:
                stale.append(p)
        except OSError:
            continue  # a log we cannot stat is not worth failing a render over
    if not stale:
        # The overwhelmingly common case — record() prunes on every write, and
        # closing the handler each time would mean reopening the file for every
        # single logged failure.
        return 0
    _close_handlers()
    removed = 0
    for p in stale:
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass  # a log we cannot tidy is not worth failing a render over
    return removed


def _logger() -> logging.Logger:
    """One rotating handler per data root.

    Keyed by path because the data root is redirected per-test (and by
    EBAB_DATA_ROOT), and a handler cached across roots would write to the
    previous one.
    """
    path = log_path()
    key = str(path)
    existing = _handlers.get(key)
    if existing is not None:
        return existing

    path.parent.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger(f"ebook_audiobook.errorlog.{abs(hash(key))}")
    log.setLevel(logging.ERROR)
    log.propagate = False  # never echo into the clean launch window
    # logging's registry hands back the same logger object every time, so one
    # that was used before a clear() still has its old handler attached. Adding
    # to it without this would write every subsequent line twice.
    for previous in list(log.handlers):
        log.removeHandler(previous)
        try:
            previous.close()
        except Exception:  # noqa: BLE001
            pass
    handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))  # the message is the JSON
    log.addHandler(handler)
    _handlers[key] = log
    return log


def _environment() -> dict:
    """The context that turns "it crashed" into a reproducible report."""
    from . import __version__

    env = {
        "version": __version__,
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(),
    }
    try:  # never let describing the device be the thing that fails
        from . import device

        env["device"] = device.select_device().describe()
    except Exception:  # noqa: BLE001
        env["device"] = "unknown"
    return env


def record(exc: BaseException, *, op: str, job_id: str | None = None,
           extra: dict | None = None) -> None:
    """Append one failure. Never raises — logging must not break a render."""
    try:
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        if len(tb) > MAX_TRACEBACK_CHARS:
            # Keep the tail: the innermost frames and the exception itself.
            tb = "...(truncated)...\n" + tb[-MAX_TRACEBACK_CHARS:]
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "op": op,
            "job_id": job_id,
            "error": type(exc).__name__,
            "message": str(exc)[:1_000],
            "traceback": tb,
            **_environment(),
        }
        if extra:
            entry.update(extra)
        prune()
        _logger().error(json.dumps(entry, ensure_ascii=False))
    except Exception:  # noqa: BLE001 - diagnostics must never mask the real error
        pass


def entries(limit: int | None = None) -> list[dict]:
    """Every logged failure, oldest first. Unreadable lines are skipped."""
    out: list[dict] = []
    # Backups are older than the live file: errors.log.2, errors.log.1, errors.log
    for p in sorted(_all_log_files(), key=lambda p: p.name, reverse=True):
        try:
            for line in p.read_text("utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue  # a torn line from a rotation; not worth reporting
        except OSError:
            continue
    return out[-limit:] if limit else out


def clear() -> int:
    """Delete every log file. Returns how many were removed."""
    _close_handlers()  # before the unlink, or Windows refuses it outright
    removed = 0
    for p in _all_log_files():
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def total_bytes() -> int:
    return sum(p.stat().st_size for p in _all_log_files() if p.exists())


# --- sharing a failure -------------------------------------------------------

# Anything that looks like a home directory, on any of the three platforms.
_HOME_PATTERNS = [
    re.compile(r"/Users/[^/\s\"']+"),
    re.compile(r"/home/[^/\s\"']+"),
    re.compile(r"[A-Za-z]:\\\\Users\\\\[^\\\\\s\"']+"),
    re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"']+"),
]


def redact(text: str) -> str:
    """Replace home directories with ``~``.

    A traceback is full of absolute paths, and an absolute path carries the
    user's account name. The rest of a path is kept: which directory a file was
    in is exactly what makes a report useful.
    """
    if not text:
        return text
    out = str(text)
    home = os.path.expanduser("~")
    if home and home != "~":
        out = out.replace(home, "~")
    for pat in _HOME_PATTERNS:
        out = pat.sub("~", out)
    return out


# Keys never included in a shared report: what someone reads is their business.
_PRIVATE_KEYS = {"book", "title", "author", "source_path"}


def issue_report(limit: int = 3) -> str:
    """A Markdown bug report for the most recent failures.

    Safe to paste into a public issue: home directories are replaced with ``~``
    and book titles are dropped. Returns a report saying so when there is
    nothing to report, rather than an empty string.
    """
    from . import __version__

    found = entries(limit=limit)
    lines = ["## Environment", ""]
    env = found[-1] if found else _environment()
    for label, key in (("ebook-audiobook", "version"), ("Python", "python"),
                       ("Platform", "platform"), ("Machine", "machine"),
                       ("Device", "device")):
        lines.append(f"- **{label}**: {env.get(key, 'unknown')}")
    if not found:
        lines += ["", "## Errors", "", "No errors have been logged.",
                  "", f"_Report generated by ebook-audiobook {__version__}._"]
        return "\n".join(lines)

    lines += ["", f"## Recent errors ({len(found)})", ""]
    for i, e in enumerate(reversed(found), 1):
        lines.append(f"### {i}. `{e.get('error', '?')}` during `{e.get('op', '?')}`")
        lines.append("")
        lines.append(f"- **When**: {e.get('ts', '?')}")
        if e.get("job_id"):
            lines.append(f"- **Job**: `{e['job_id']}`")
        for k, v in e.items():
            if k in _PRIVATE_KEYS or k in {
                "ts", "op", "job_id", "error", "message", "traceback",
                "version", "python", "platform", "machine", "device",
            }:
                continue
            lines.append(f"- **{k}**: {redact(str(v))}")
        lines += ["", "```", redact(e.get("message", "")), "```", "",
                  "<details><summary>Traceback</summary>", "",
                  "```python", redact(e.get("traceback", "")).rstrip(), "```",
                  "", "</details>", ""]
    lines.append(f"_Report generated by ebook-audiobook {__version__}. "
                 "Home directories replaced with `~`; book titles omitted._")
    return "\n".join(lines)


def write_report(dest: Path, limit: int = 3) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(issue_report(limit=limit), encoding="utf-8")
    return dest


def install_excepthook(op: str = "startup") -> None:
    """Record anything that kills the process outright, then behave normally."""
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        if not issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            record(exc, op=op)
        previous(exc_type, exc, tb)

    sys.excepthook = hook
