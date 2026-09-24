"""Windows' file-sharing rules, absorbed where the app writes and deletes.

On Windows a file cannot be replaced or deleted while any other handle has it
open, and Python's ``open()`` never grants the sharing that would allow it. The
app meets this constantly without any bug of its own: the web UI reads
``job_state.json`` about twice a second while a render saves it after every
segment, so sooner or later a save lands mid-read, and ``os.replace`` fails with
"Access is denied" — which, unhandled, stops the render. A reader is done within
milliseconds, so waiting briefly and trying again is the whole fix.

Everywhere else the first error is raised at once, exactly as before: on macOS
and Linux a PermissionError is a real permissions problem, and retrying it would
only delay the message.
"""

from __future__ import annotations

import os
import stat
import sys
import time
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"

# 40 × 25 ms: a full second before giving up — far longer than any reader holds
# a small JSON file open, and short enough that a genuinely stuck file still
# reports promptly.
_ATTEMPTS = 40
_DELAY = 0.025


def replace(src: str | os.PathLike, dst: str | os.PathLike) -> None:
    """``os.replace``, retried on Windows while something else has *dst* open."""
    for attempt in range(_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if not IS_WINDOWS or attempt == _ATTEMPTS - 1:
                raise
            time.sleep(_DELAY)


def read_text(path: str | os.PathLike) -> str:
    """``Path.read_text("utf-8")``, retried on Windows the same way.

    Opening a file at the instant another thread replaces it can be refused
    too. Only used where a failed read has consequences out of proportion to a
    momentary clash (settings.json, which is read on every request).
    """
    for attempt in range(_ATTEMPTS):
        try:
            return Path(path).read_text("utf-8")
        except PermissionError:
            if not IS_WINDOWS or attempt == _ATTEMPTS - 1:
                raise
            time.sleep(_DELAY)
    raise AssertionError("unreachable")


def unlink(path: str | os.PathLike) -> bool:
    """Delete a file if it exists; True if it is gone afterwards.

    Never raises. Windows refuses to delete a file that is read-only (an ebook
    copied off a CD or a read-only share keeps that attribute) or that is open
    elsewhere (an audiobook playing in another app). The first is cleared and
    retried; the second leaves the file where it is rather than failing the
    whole operation halfway through.
    """
    p = Path(path)
    try:
        p.unlink(missing_ok=True)
        return True
    except PermissionError:
        if IS_WINDOWS:
            try:
                os.chmod(p, stat.S_IWRITE)
                p.unlink(missing_ok=True)
                return True
            except OSError:
                return False
        return False
    except OSError:
        return False
