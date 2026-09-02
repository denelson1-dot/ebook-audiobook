"""Launching the local web UI, as a desktop application.

Bound to localhost only, on a port that is guaranteed to be free, served by
waitress rather than Flask's development server. Waitress is pure Python (so it
installs everywhere without a compiler), is a real WSGI server rather than one
that prints a warning telling you not to use it, and handles concurrent requests
predictably on Windows — which matters here because the browser polls a status
endpoint continuously while a render runs for hours.

The process is shaped around one constraint: **the tray icon owns the main
thread and the server runs behind it.** pystray's macOS backend is an
``NSStatusItem``, and AppKit will only run an event loop on thread zero. Rather
than keep two different process shapes, every platform uses that one. When there
is no tray — headless, SSH, a stock GNOME desktop, ``--no-tray`` — the main
thread simply blocks on the server thread instead, which behaves exactly like
the plain blocking server this used to be.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser

from .. import i18n, settings as app_settings
from . import create_app
from ..desktop import launcher, runtime, tray

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5005

# How long to wait for waitress to drain after being asked to stop. Renders are
# checkpointed to disk every segment and the worker is a daemon thread, so the
# worst case of giving up here is losing the segment in flight — which the next
# run re-renders from cache anyway. Waiting longer would just make Quit feel
# broken.
SHUTDOWN_GRACE = 3.0


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


def open_window(url: str) -> None:
    """Show the UI: an application window if we can, an ordinary tab if not."""
    if launcher.open_app_window(url):
        return
    try:
        # No Chromium anywhere, so this hands the URL to whatever the user's
        # default browser is — including Firefox, which has no app mode. The fd
        # juggling is needed because webbrowser.open gives us no control over
        # how the child is spawned.
        with launcher.detached_std_fds():
            webbrowser.open(url)
    except Exception:  # noqa: BLE001 - headless box, or no browser configured
        pass


def _open_when_ready(url: str, host: str, port: int, timeout: float = 20.0) -> None:
    target = _browser_host(host)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex((target, port)) == 0:
                break
        time.sleep(0.2)
    open_window(url)


def _announce(url: str, has_tray: bool) -> None:
    """Say where we are, but only to a console someone is actually looking at.

    Launched from the application menu there is no terminal at all, and the old
    "leave this window open" instruction was both invisible and, now that the
    server outlives its window, wrong.
    """
    stderr = getattr(sys, "stderr", None)
    try:
        if not (stderr and stderr.isatty()):
            return
    except (AttributeError, ValueError):
        return
    print(f"ebook-audiobook is running at {url}", file=stderr, flush=True)
    if has_tray:
        print("Closing the window leaves it running in the tray. "
              "Quit from there, or press Ctrl-C here.", file=stderr, flush=True)
    else:
        print("Closing the window leaves it running here. Press Ctrl-C to stop.",
              file=stderr, flush=True)


def _quit_label() -> str:
    """The tray's quit text, which doubles as its only way to warn."""
    from .runner import runner

    return "Quit — stops the running render" if runner.is_busy() else "Quit"


def serve(host: str | None = None, port: int | None = None,
          open_browser: bool = True, use_tray: bool = True) -> None:
    """Start the UI and block until quit."""
    host = host or os.environ.get("EBAB_HOST", DEFAULT_HOST)
    if port is None:
        port = int(os.environ.get("EBAB_PORT", DEFAULT_PORT))
    port = choose_port(host, port)

    if os.environ.get("EBAB_NO_BROWSER") == "1":
        open_browser = False

    app = create_app()
    url = f"http://{_browser_host(host)}:{port}"

    try:
        from waitress import create_server
    except ImportError:
        # Should not happen (waitress is a hard dependency), but a working UI on
        # the dev server beats refusing to start. No tray on this path: there is
        # no way to stop app.run() from another thread, so the main thread has
        # to stay in it.
        _announce(url, has_tray=False)
        if open_browser:
            threading.Thread(target=_open_when_ready, args=(url, host, port),
                             daemon=True).start()
        app.run(host=host, port=port, debug=False, threaded=True)
        return

    server = create_server(
        app, host=host, port=port,
        threads=8,
        # A render can pin the worker thread for a long time; the browser's
        # status polling must not start timing out while that happens.
        channel_timeout=600,
        ident="ebook-audiobook",
    )

    stopping = threading.Event()

    def request_stop() -> None:
        """Ask everything to stop. Must stay fast, and safe to call twice.

        Deliberately does no draining. This runs on whichever thread asked to
        quit — and when that is the tray, it is the AppKit main thread, where
        blocking means a spinning beachball and a "Not Responding" badge. The
        slow work happens in :func:`drain` once the main thread is free again.
        """
        if stopping.is_set():
            return
        stopping.set()
        # Retract the record first. It advertises a live instance, and from here
        # on there isn't one — draining can take seconds, and a relaunch during
        # that window should start cleanly rather than try to adopt a server
        # that is on its way out.
        runtime.clear()
        try:
            server.close()
        except Exception:  # noqa: BLE001 - already closing
            pass
        tray.stop()  # releases the main thread from the tray's event loop

    def drain() -> None:
        """The slow half of shutdown, on the main thread once the loop is done."""
        try:
            # Closing the listening socket alone doesn't retire the worker
            # threads, and the asyncore loop keeps running while the browser's
            # poll connections are still in its map. Blocks for up to 5s.
            server.task_dispatcher.shutdown()
        except Exception:  # noqa: BLE001
            pass
        # So the window doesn't outlive the server and leave the browser's
        # connection-error page as our parting screen.
        launcher.close_windows()

    # How /quit reaches back into the server it is being served by.
    app.config["EBAB_SHUTDOWN"] = request_stop

    server_thread = threading.Thread(target=server.run, daemon=True,
                                     name="ebab-waitress")
    server_thread.start()

    # Written only once the socket is actually bound (create_server binds in its
    # constructor), so a second launch can never find a record for a port that
    # is not yet listening.
    runtime.write(port, host)

    if open_browser:
        # Daemon thread so it can never hold up shutdown.
        threading.Thread(target=_open_when_ready, args=(url, host, port),
                         daemon=True).start()

    # The tray menu has no browser to ask, so its language is the desktop's,
    # unless Settings says otherwise. Set here, once — never at import time,
    # which is what keeps the CLI and the test suite English on a French Mac.
    i18n.set_process_language(
        i18n.resolve(app_settings.load_settings().language, i18n.detect_os_language()))

    has_tray = use_tray and tray.available()
    _announce(url, has_tray)

    if has_tray:
        threading.Thread(target=_watch_busy_state, args=(stopping,), daemon=True,
                         name="ebab-tray-label").start()

    try:
        if has_tray:
            # on_terminate is macOS-only (see tray.run): AppKit exits the
            # process straight after it, so the drain has to happen inside it.
            tray.run(url, lambda: open_window(url), request_stop, _quit_label,
                     on_terminate=lambda: (request_stop(), drain()))
        # Every pystray backend catches its own main-loop failures and returns
        # normally, so tray.run() returning tells us nothing about whether a
        # tray ever appeared. Trusting it meant that on a machine where the tray
        # could not start — a stock GNOME session, a Mac with no window server —
        # the app exited about a second after launch, silently, having served
        # nothing. The only trustworthy signal that we are meant to be finished
        # is somebody actually having asked us to stop.
        if not stopping.is_set():
            server_thread.join()
    except KeyboardInterrupt:
        stderr = getattr(sys, "stderr", None)
        if stderr:
            print("\nstopped.", file=stderr)
    finally:
        request_stop()
        drain()
        server_thread.join(timeout=SHUTDOWN_GRACE)
        runtime.clear()  # belt and braces: request_stop already did this


def _watch_busy_state(stopping: threading.Event, interval: float = 2.0) -> None:
    """Rebuild the tray menu whenever the worker starts or stops.

    No pystray backend re-evaluates a menu label when the menu is opened; each
    one builds its menu once and caches it. So without this the quit warning
    would be frozen at whatever was true at launch — always "idle" — and would
    never appear in the one situation it exists for: a render started an hour
    ago from the web UI, and someone reaching for Quit in the tray.
    """
    from .runner import runner

    last = None
    while not stopping.wait(interval):
        busy = runner.is_busy()
        if busy != last:
            last = busy
            tray.refresh()
