"""Run the local web UI, bound to localhost only, and open the browser.

    python -m app.web            # picks a free port, opens the browser
    EBAB_PORT=8000 python -m app.web
    EBAB_NO_BROWSER=1 python -m app.web

Or just ``./run`` from the repo root (which uses the venv automatically).
"""

from __future__ import annotations

import os
import socket
import threading
import time
import webbrowser

from . import create_app


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _choose_port(host: str, preferred: int) -> int:
    """Use the preferred port if free; otherwise ask the OS for any open port so
    we never collide with something else already bound to localhost."""
    if _port_is_free(host, preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _open_browser_when_ready(host: str, port: int, timeout: float = 15.0) -> None:
    browser_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex((browser_host, port)) == 0:
                break
        time.sleep(0.2)
    webbrowser.open(f"http://{browser_host}:{port}")


def main() -> None:
    app = create_app()
    host = os.environ.get("EBAB_HOST", "127.0.0.1")  # localhost only by default
    preferred = int(os.environ.get("EBAB_PORT", "5005"))
    port = _choose_port(host, preferred)

    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}"
    print(f"ebook-audiobook web UI → {url}  (Ctrl-C to stop)")

    if os.environ.get("EBAB_NO_BROWSER") != "1":
        # Open the browser once the server is accepting connections. Daemon
        # thread so it never blocks shutdown.
        threading.Thread(
            target=_open_browser_when_ready, args=(host, port), daemon=True
        ).start()

    # No debug/reloader: the reloader would spawn a second process and (with the
    # real engine) load the model twice on one 8 GB GPU.
    app.run(host=host, port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
