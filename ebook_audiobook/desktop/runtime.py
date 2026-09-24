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

from .. import winfs
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
        winfs.replace(tmp, path)
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


def clear(force: bool = False) -> None:
    """Forget the recorded instance. Safe to call when there isn't one.

    Only clears a record this process wrote, unless ``force``. A restart hands
    over to a fresh copy of the app that writes its own record within a second,
    while this one is still draining — and the departing process must not then
    delete its successor's. That left a live instance nobody could find, so the
    next launch saw no record, believed nothing was running, and started a
    second worker over the same job store.
    """
    path = runtime_path()
    if not force:
        try:
            import json

            if json.loads(path.read_text("utf-8")).get("pid") != os.getpid():
                return  # someone else's record; not ours to remove
        except (OSError, ValueError):
            pass  # unreadable or already gone: falling through to unlink is fine
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass  # read-only data dir, or a race with another instance's cleanup


def url_for(record: dict) -> str:
    host = record.get("host") or "127.0.0.1"
    # 0.0.0.0 is bindable but not connectable; a window pointed at it fails.
    if host in ("0.0.0.0", "", "::"):
        host = "127.0.0.1"
    return f"http://{host}:{record['port']}"


# Held for the life of the process, and released by Windows when it ends,
# however it ends.
_launch_mutex = None


def claim_launch(wait: float = 0.0) -> bool:
    """Whether this process may start the server, waiting up to *wait* seconds.

    Windows only; True everywhere else. The runtime record appears only once
    the server is up, which on a cold start is several seconds after the click
    — long enough for an impatient second double-click to find no record and
    start a second copy. A named mutex, owned by the first launch from its very
    start, closes that window: a later launch sees it owned and waits for the
    first to answer instead. Ownership passes on when its owner exits, so a
    restart after an update is not held up by the process it replaces. Keyed
    by data folder, like everything else that is "this instance".
    """
    global _launch_mutex
    if os.name != "nt":
        return True
    try:
        import ctypes
        import hashlib
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if _launch_mutex is None:
            k32.CreateMutexW.restype = wintypes.HANDLE
            k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            tag = hashlib.sha1(str(data_root()).lower().encode("utf-8")).hexdigest()[:16]
            handle = k32.CreateMutexW(None, False, f"Local\\{APP_ID}-{tag}")
            if not handle:
                return True
            _launch_mutex = handle
        k32.WaitForSingleObject.restype = wintypes.DWORD
        k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        result = k32.WaitForSingleObject(_launch_mutex, int(wait * 1000))
    except (AttributeError, OSError):
        return True  # no guard is better than no app
    # WAIT_OBJECT_0, or WAIT_ABANDONED: the previous owner exited.
    return result in (0x0, 0x80)


def _no_proxy_opener() -> urllib.request.OpenerDirector:
    # urllib honours the system proxy, which on Windows comes from the
    # registry and does not necessarily exempt 127.0.0.1 - on a managed laptop
    # the probe would ask a corporate proxy for our own loopback port, fail,
    # and a second copy would start.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe(timeout: float = 1.5, forget: bool = True) -> str | None:
    """The URL of a live instance, or None.

    Deliberately does not consult the recorded PID. PIDs are recycled, so a stale
    record can name a live process that is something else entirely; asking the
    port whether it is us is both simpler and correct. A record that fails to
    answer is deleted on the way out, so a crashed instance self-heals on the
    next launch rather than needing the user to find and delete a file.
    *forget* False keeps the record even so: for a caller that knows the
    instance is alive and merely slow to answer.
    """
    record = read()
    if not record:
        return None
    url = url_for(record)
    try:
        with _no_proxy_opener().open(f"{url}/api/status", timeout=timeout) as r:
            if r.status != 200:
                raise OSError(f"status {r.status}")
            body = json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError):
        if forget:
            clear()
        return None
    if not isinstance(body, dict) or body.get("app") != APP_ID:
        # Something else owns that port now. Drop the record, but do not touch
        # whatever is running there.
        clear()
        return None
    return url
