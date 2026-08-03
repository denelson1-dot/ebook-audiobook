"""Smoke test that the *installed* web UI serves real pages.

The failure this exists to catch: a wheel built without its Jinja templates
installs and imports perfectly, then returns a 500 for every page. Nothing in
the unit tests notices, because a source checkout always has the templates on
disk. So: boot the actual server the way a user's launcher does, over real HTTP.
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

FAILURES: list[str] = []


def check(condition: bool, description: str) -> None:
    print(f"  [{'ok  ' if condition else 'FAIL'}] {description}")
    if not condition:
        FAILURES.append(description)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url: str, timeout: float = 20.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def post(url: str, timeout: float = 20.0) -> tuple[int, str]:
    req = urllib.request.Request(url, data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ebab-web-"))
    os.environ["EBAB_DATA_ROOT"] = str(tmp / "data")
    os.environ["EBAB_NO_BROWSER"] = "1"
    # CI has no display and no tray. This is also the path a headless install
    # takes, so exercising it here is the point, not a workaround.
    os.environ["EBAB_NO_TRAY"] = "1"

    from ebook_audiobook.web.server import serve

    port = free_port()
    print(f"starting the server on 127.0.0.1:{port}")
    threading.Thread(
        target=lambda: serve(host="127.0.0.1", port=port, open_browser=False),
        daemon=True,
    ).start()

    deadline = time.monotonic() + 45
    ready = False
    while time.monotonic() < deadline:
        code, _ = get(f"http://127.0.0.1:{port}/", timeout=2)
        if code == 200:
            ready = True
            break
        time.sleep(0.4)
    check(ready, "server started and answered on /")
    if not ready:
        return report()

    base = f"http://127.0.0.1:{port}"

    print("\npages render (this is what a missing-templates wheel breaks)")
    for path in ("/", "/new", "/voices", "/settings"):
        code, body = get(base + path)
        check(code == 200, f"GET {path} -> {code}")
        # A 200 from an error handler isn't good enough: check for real markup.
        check("<html" in body.lower() or "<!doctype" in body.lower(),
              f"GET {path} returned HTML, not an error page")

    print("\nstatic assets are packaged")
    for path in ("/static/app.css", "/static/app.js"):
        code, body = get(base + path)
        check(code == 200 and len(body) > 100, f"GET {path} -> {code}, {len(body)} bytes")

    print("\nJSON endpoints")
    import json

    for path in ("/api/status", "/api/voices", "/api/prereqs", "/api/space"):
        code, body = get(base + path)
        check(code == 200, f"GET {path} -> {code}")
        try:
            json.loads(body)
            check(True, f"GET {path} returned valid JSON")
        except ValueError:
            check(False, f"GET {path} returned valid JSON")

    print("\n404s behave")
    code, _ = get(base + "/job/does-not-exist")
    check(code == 404, f"unknown job -> {code}")

    print("\nthe icons a desktop launcher needs are in the wheel")
    from ebook_audiobook.desktop import tray

    check(tray.ASSETS.is_dir(), f"assets directory installed at {tray.ASSETS}")
    for name in (tray.ICON_FILE, "icon.ico", "icon.icns", "icon-256.png"):
        check((tray.ASSETS / name).is_file(), f"{name} packaged")

    print("\nthe running instance is discoverable")
    # Without this a second launch starts a second server over the same job
    # store, with two workers writing the same files.
    from ebook_audiobook.desktop import runtime

    record = runtime.read()
    check(bool(record) and record.get("port") == port,
          f"runtime.json records the live port ({record})")
    check(runtime.probe() == base, "probe() finds the running instance")

    print("\nquit stops the server")
    code, body = post(base + "/quit")
    check(code == 200, f"POST /quit -> {code} {body[:80]}")

    stopped = False
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if get(f"{base}/", timeout=2)[0] == 0:
            stopped = True
            break
        time.sleep(0.4)
    check(stopped, "server stopped answering after /quit")
    check(runtime.read() is None, "runtime.json removed on the way out")

    return report()


def report() -> int:
    print()
    if FAILURES:
        print(f"WEB SMOKE TEST FAILED — {len(FAILURES)} problem(s):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("WEB SMOKE TEST PASSED — installed UI serves its pages and assets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
