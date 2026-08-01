"""Launching the local web UI.

Bound to localhost only, on a port that is guaranteed to be free, served by
waitress rather than Flask's development server. Waitress is pure Python (so it
installs everywhere without a compiler), is a real WSGI server rather than one
that prints a warning telling you not to use it, and handles concurrent requests
predictably on Windows — which matters here because the browser polls a status
endpoint continuously while a render runs for hours.
"""

from __future__ import annotations

import contextlib
import os
import socket
import sys
import threading
import time
import webbrowser

from . import create_app

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5005


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        # Deliberately no SO_REUSEADDR: on Linux it would let us bind a port
        # another process holds in TIME_WAIT and report it "free", and on
        # Windows SO_REUSEADDR allows stealing a port that is actively in use.
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def choose_port(host: str, preferred: int) -> int:
    """Use the preferred port if free; otherwise ask the OS for any open port so
    we never collide with something else already bound to localhost."""
    if _port_is_free(host, preferred):
        return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _browser_host(host: str) -> str:
    """A host a browser can actually connect to (0.0.0.0 isn't one)."""
    return "127.0.0.1" if host in ("0.0.0.0", "", "::") else host


@contextlib.contextmanager
def _detached_std_fds():
    """Point the OS-level stdout/stderr at devnull for the wrapped block.

    webbrowser.open spawns the browser with subprocess.Popen and no stdout or
    stderr redirection, so it inherits our file descriptors (close_fds only
    covers fd 3 upwards). Chrome then writes a wall of GPU, component updater
    and extension messages to stderr on startup, and because the app is launched
    from a terminal window the user is told to leave open, that lands on screen
    looking like our crash report. Redirecting at the fd level (rather than
    swapping sys.stderr) is what it takes, since the child inherits fds, not
    Python objects.

    The child keeps the devnull fds it was handed at exec time, so restoring
    ours afterwards does not un-silence it. These are process-wide fds and this
    runs on a background thread, so output from another thread during the wrapped
    call would be swallowed too — the window is the Popen call alone, a few
    milliseconds, during which nothing else is expected to be printing.
    """
    with open(os.devnull, "wb") as devnull:
        saved = [os.dup(1), os.dup(2)]
        try:
            os.dup2(devnull.fileno(), 1)
            os.dup2(devnull.fileno(), 2)
            yield
        finally:
            # Flush anything the browser call buffered before restoring, so it
            # cannot spill onto the real stderr afterwards.
            for fd, original in zip((1, 2), saved):
                os.dup2(original, fd)
                os.close(original)


def _open_browser_when_ready(host: str, port: int, timeout: float = 20.0) -> None:
    target = _browser_host(host)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex((target, port)) == 0:
                break
        time.sleep(0.2)
    try:
        with _detached_std_fds():
            webbrowser.open(f"http://{target}:{port}")
    except Exception:  # noqa: BLE001 - headless box, or no browser configured
        pass


def serve(host: str | None = None, port: int | None = None,
          open_browser: bool = True) -> None:
    """Start the UI and block until interrupted."""
    host = host or os.environ.get("EBAB_HOST", DEFAULT_HOST)
    if port is None:
        port = int(os.environ.get("EBAB_PORT", DEFAULT_PORT))
    port = choose_port(host, port)

    if os.environ.get("EBAB_NO_BROWSER") == "1":
        open_browser = False

    app = create_app()
    url = f"http://{_browser_host(host)}:{port}"
    print(f"ebook-audiobook is running at {url}", file=sys.stderr, flush=True)
    print("Leave this window open while you use it. Press Ctrl-C to stop.",
          file=sys.stderr, flush=True)

    if open_browser:
        # Daemon thread so it can never hold up shutdown.
        threading.Thread(target=_open_browser_when_ready,
                         args=(host, port), daemon=True).start()

    try:
        from waitress import serve as waitress_serve
    except ImportError:
        # Should not happen (waitress is a hard dependency), but a working UI on
        # the dev server beats refusing to start.
        app.run(host=host, port=port, debug=False, threaded=True)
        return

    try:
        waitress_serve(
            app, host=host, port=port,
            threads=8,
            # A render can pin the worker thread for a long time; the browser's
            # status polling must not start timing out while that happens.
            channel_timeout=600,
            ident="ebook-audiobook",
        )
    except KeyboardInterrupt:
        print("\nstopped.", file=sys.stderr)
