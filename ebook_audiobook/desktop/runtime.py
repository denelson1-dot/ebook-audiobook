"""Where the running instance is, so a second launch can find it.

Without this, launching the app twice is silently destructive rather than merely
redundant: :func:`~ebook_audiobook.web.server.choose_port` sees 5005 taken, asks
the OS for any free port, and the second process starts its own ``Runner`` over
the *same* ``JobStore``. Two workers then write job state for the same job with
no lock between them, and both believe they own the GPU model.

So the server records where it is, and a launch that finds a live instance opens
a window onto it instead of starting another one.

The record is advisory, never authoritative. It can be stale (the process was
killed), it can be a lie (the port was recycled to something else), and on a
shared machine it can belong to another user's instance. Every consumer must go
through :func:`probe`, which believes the file only after the port answers and
identifies itself as ours.
"""

from __future__ import annotations

import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

from ..config import data_root

FILENAME = "runtime.json"

# The marker /api/status returns. A port being open proves something is there,
# not that it is us — an unrelated dev server inheriting port 5005 would happily
# return 200 for a GET and we would hand the user its window.
APP_ID = "ebook-audiobook"


def runtime_path() -> Path:
    return data_root() / FILENAME


def write(port: int, host: str = "127.0.0.1") -> Path:
    """Record the live instance. Write-then-rename, as everywhere else that
    persists state, so a crash mid-write leaves the old record rather than a
    half-written one that fails to parse."""
    path = runtime_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"app": APP_ID, "host": host, "port": int(port), "pid": os.getpid()},
                         indent=2)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def read() -> dict | None:
    """The recorded instance, or None if there isn't a readable one."""
    try:
        record = json.loads(runtime_path().read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict) or not record.get("port"):
        return None
    return record


def clear() -> None:
    """Forget the recorded instance. Safe to call when there isn't one."""
    try:
        runtime_path().unlink(missing_ok=True)
    except OSError:
        pass  # read-only data dir, or a race with another instance's cleanup


def url_for(record: dict) -> str:
    host = record.get("host") or "127.0.0.1"
    # 0.0.0.0 is bindable but not connectable; a window pointed at it fails.
    if host in ("0.0.0.0", "", "::"):
        host = "127.0.0.1"
    return f"http://{host}:{record['port']}"


def probe(timeout: float = 1.5) -> str | None:
    """The URL of a live instance, or None.

    Deliberately does not consult the recorded PID. PIDs are recycled, so a stale
    record can name a live process that is something else entirely; asking the
    port whether it is us is both simpler and correct. A record that fails to
    answer is deleted on the way out, so a crashed instance self-heals on the
    next launch rather than needing the user to find and delete a file.
    """
    record = read()
    if not record:
        return None
    url = url_for(record)
    try:
        with urllib.request.urlopen(f"{url}/api/status", timeout=timeout) as r:
            if r.status != 200:
                raise OSError(f"status {r.status}")
            body = json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        clear()
        return None
    if not isinstance(body, dict) or body.get("app") != APP_ID:
        # Something else owns that port now. Drop the record, but do not touch
        # whatever is running there.
        clear()
        return None
    return url
