"""The tray icon: proof that a render is still running after its window closed.

The window is a browser process, so closing it never stops the server — which is
what makes unattended overnight renders work, and also what makes them
invisible. A tray icon is the fix: somewhere to see the app is alive, somewhere
to get the window back, and somewhere to quit on purpose.

Support is uneven and this module is written around that:

* **Windows** — ``Shell_NotifyIcon``. No extra dependency, always works.
* **macOS** — ``NSStatusItem`` in the menu bar, via pyobjc.
* **Linux, KDE/XFCE/Cinnamon/Budgie** — AppIndicator if the GTK bindings can be
  reached (see :func:`_reach_system_gi`), otherwise XEmbed over python-xlib.
* **Linux, GNOME** — GNOME removed the tray and requires a user-installed shell
  extension to get one back. No library routes around that, so on a stock GNOME
  desktop this returns False and the app runs trayless.

Every path returns False rather than raising. A missing tray must never be the
reason the server doesn't start.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

# Decided once, at import — see the note in launcher.py on why tests must
# override these rather than monkeypatch sys.platform.
IS_MACOS = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

ASSETS = Path(__file__).resolve().parent.parent / "assets"

# The tray icon is drawn small; 64 px is the largest size any of the three
# platforms asks for and downsamples cleanly for the rest.
ICON_FILE = "icon-64.png"

_lock = threading.Lock()
_icon = None  # the running pystray.Icon, so /quit can stop it from a request

# macOS only: the NSApplication delegate that turns "the system wants us gone"
# into an orderly shutdown, and the callback it runs. Module-level so AppKit's
# unretained delegate reference stays valid for the life of the process.
_app_delegate = None
_app_delegate_class = None
_on_terminate = None


def _reach_system_gi() -> bool:
    """Make the distro's GTK bindings importable, if they aren't already.

    install.sh builds the venv with ``include-system-site-packages = false``,
    which is right — letting the distro's numpy and Pillow shadow the ones torch
    was installed against is a genuine way to break a working GPU setup. The cost
    is that ``gi``, which is only ever shipped as a system package and cannot be
    pip-installed, is invisible from inside the venv.

    So: probe, and only extend the path if the probe says it will work. The
    directory is **appended**, never prepended, so venv packages keep priority
    and nothing already importable can be shadowed — the only modules this can
    newly resolve are ones that were not importable at all a moment ago.

    The path stays extended once the probe succeeds. Reverting it after importing
    ``gi`` would be tidier but is wrong in practice: pystray's GTK and
    AppIndicator backends import further system modules lazily, from inside a
    running main loop, long after this function has returned.

    A venv on a different Python than the system one fails the probe naturally —
    ``gi`` is a compiled extension tagged with its ABI — so no version check is
    needed here.
    """
    try:
        import gi  # noqa: F401
        return True
    except ImportError:
        pass

    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    candidates = [
        "/usr/lib/python3/dist-packages",            # Debian, Ubuntu, Mint
        f"/usr/lib/python{version}/site-packages",   # Arch, Fedora
        f"/usr/lib64/python{version}/site-packages",  # Fedora, openSUSE
    ]
    for candidate in candidates:
        if not os.path.isdir(candidate) or candidate in sys.path:
            continue
        sys.path.append(candidate)
        try:
            import gi  # noqa: F401
            return True
        except ImportError:
            sys.path.remove(candidate)
    return False


def _load_image():
    from PIL import Image

    return Image.open(ASSETS / ICON_FILE)


def _macos_has_gui_session() -> bool:
    """Whether this macOS process is attached to a window server.

    Over SSH, or under launchd, ``import AppKit`` still succeeds and only fails
    later — deep inside ``NSStatusBar.systemStatusBar()``, where it can abort the
    process rather than raise something catchable. So the question has to be
    asked before pystray is allowed anywhere near AppKit.

    ``CGSessionCopyCurrentDictionary`` returns None with no window server, which
    is the documented way to ask. Quartz is present whenever pystray is — it
    declares the dependency itself — but treat an import failure as "no GUI"
    rather than assuming the best.
    """
    try:
        from Quartz import CGSessionCopyCurrentDictionary

        return CGSessionCopyCurrentDictionary() is not None
    except Exception:  # noqa: BLE001 - no pyobjc, or no window server at all
        return False


def available() -> bool:
    """Whether a tray icon is worth attempting."""
    if os.environ.get("EBAB_NO_TRAY") == "1":
        return False
    if not (ASSETS / ICON_FILE).is_file():
        return False
    if IS_LINUX:
        # No display server means no tray, and pystray's Xorg backend blocks for
        # a while before admitting it.
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return False
        _reach_system_gi()
    elif IS_MACOS and not _macos_has_gui_session():
        return False
    # Ask the real question last: is the library actually installed? An install
    # done with --no-deps (which is how the TTS engine goes in) leaves the app
    # importable and pystray absent, and without this check the startup banner
    # would promise a tray icon that can never appear.
    try:
        import pystray  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def run(url: str, on_show, on_quit, quit_label=None, on_terminate=None) -> bool:
    """Show the tray icon and run its event loop until the app quits.

    **Blocks**, and must be called from the main thread: pystray's macOS backend
    is an ``NSStatusItem``, and AppKit only runs an event loop on thread zero.
    That is why the server moved to a background thread rather than the tray.

    ``quit_label`` returns the text for the quit item. That is how the tray warns
    about quitting mid-render: no backend here can raise a confirmation dialog,
    so the warning goes in the label the user is about to click. (The web UI,
    which *can* show a dialog, asks properly — see the Quit control in
    base.html.) It is **not** re-evaluated when the menu opens; no pystray
    backend offers that hook. The caller must call :func:`refresh` when the
    answer changes.

    ``on_terminate`` is macOS only: it runs, synchronously, when the operating
    system asks the application to quit — Ctrl-C in the terminal that started
    it (pystray turns that into ``NSApp.terminate:``), a log-out, a shutdown.
    AppKit then calls ``exit()`` without unwinding Python, so anything that must
    happen on the way out (retract the runtime record, close the app window)
    has to happen inside this callback or not at all. Ignored elsewhere.

    Returns False if no tray could be created. **A True return does not mean the
    tray ran**: every backend catches its own main-loop failures and returns
    normally, so this cannot distinguish a clean exit from an instant crash. The
    caller must decide it is finished on its own evidence — see how ``serve()``
    keys off its ``stopping`` event rather than this value.
    """
    global _icon

    if not available():
        return False
    try:
        import pystray
    except Exception:  # noqa: BLE001 - missing, or a backend that won't import
        return False

    try:
        menu = pystray.Menu(
            # default=True only becomes a click action on backends that set
            # HAS_DEFAULT_ACTION — which AppIndicator and macOS both do not. On
            # those it merely renders bold, and clicking the icon opens the menu.
            # Worth keeping for the backends that honour it (Windows, GTK).
            pystray.MenuItem("Open ebook-audiobook", lambda: on_show(), default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                (lambda item: quit_label()) if quit_label else "Quit",
                lambda: on_quit()),
        )
        icon = pystray.Icon(
            "ebook-audiobook",
            icon=_load_image(),
            title=f"ebook-audiobook — {url}",
            menu=menu,
        )
    except Exception:  # noqa: BLE001
        return False

    with _lock:
        _icon = icon
    try:
        _macos_hide_dock_icon()
        _macos_install_terminate_handler(on_terminate)
        icon.run()
    except Exception:  # noqa: BLE001 - a desktop with no usable tray protocol
        return False
    finally:
        with _lock:
            _icon = None
    return True


def _macos_hide_dock_icon() -> None:
    """Keep the app out of the Dock and the ⌘-Tab switcher.

    pystray never sets an activation policy, so NSApp defaults to Regular and
    macOS gives the process a Dock tile and an empty application menu. That
    would be wrong here even if it looked tidy: the executable has been exec'd
    out of the .app bundle by the time this runs, so the tile shows a generic
    Python icon labelled "Python" rather than anything of ours.

    Accessory (policy 1) is what a menu-bar-only app uses. Best effort — a
    failure here is cosmetic and must not stop the tray appearing.
    """
    if not IS_MACOS:
        return
    try:
        import AppKit

        AppKit.NSApplication.sharedApplication().setActivationPolicy_(
            AppKit.NSApplicationActivationPolicyAccessory)
    except Exception:  # noqa: BLE001
        pass


def _macos_install_terminate_handler(on_terminate) -> None:
    """Make ``NSApp.terminate:`` shut the app down properly instead of just
    ending the process.

    pystray's macOS backend answers SIGINT with ``terminate:``, and macOS sends
    the same on log-out or shutdown. Without a delegate AppKit's answer is to
    call ``exit()`` on the spot: no ``finally`` block in ``serve()`` ever runs,
    the runtime record stays behind advertising a server that is gone, and the
    app window is left showing the browser's connection-error page. The delegate
    runs the orderly shutdown first, then lets the termination proceed.

    Best effort, macOS only. One delegate class per process: Objective-C class
    names are global, and declaring it twice raises.
    """
    global _app_delegate, _app_delegate_class, _on_terminate
    if not IS_MACOS or on_terminate is None:
        return
    _on_terminate = on_terminate
    try:
        import AppKit
        import objc

        if _app_delegate_class is None:
            class EbabAppDelegate(AppKit.NSObject):
                # NSApplicationTerminateReply is an NSUInteger: declare the
                # signature rather than let it be inferred, because a wrong
                # guess here does not raise — it returns garbage to AppKit.
                @objc.typedSelector(b"Q@:@")
                def applicationShouldTerminate_(self, sender):
                    _run_terminate_callback()
                    return 1  # NSTerminateNow

            _app_delegate_class = EbabAppDelegate
        _app_delegate = _app_delegate_class.alloc().init()
        AppKit.NSApplication.sharedApplication().setDelegate_(_app_delegate)
    except Exception:  # noqa: BLE001 - cosmetic on the way out; never block the tray
        _app_delegate = None


def _run_terminate_callback() -> None:
    """The delegate's work, kept out of the Objective-C method so a test can
    drive it without AppKit."""
    cb = _on_terminate
    if cb is None:
        return
    try:
        cb()
    except Exception:  # noqa: BLE001 - we are exiting either way
        pass


def _on_main_thread(fn) -> bool:
    """Schedule ``fn`` on the AppKit main thread. False if that isn't possible.

    AppKit is not thread-safe, and both callers here run on background threads:
    :func:`refresh` from the busy-state watcher, :func:`stop` from a ``/quit``
    request. Rebuilding an ``NSMenu`` on a status item from another thread while
    the user may be looking at that menu is the kind of thing that works a
    thousand times and then crashes. ``callAfter`` hands the call to the run
    loop that ``icon.run()`` is spinning, which is where it belongs. Anything
    scheduled before the loop starts is queued until it does.
    """
    if not IS_MACOS:
        return False
    try:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(_quietly, fn)
        return True
    except Exception:  # noqa: BLE001 - no pyobjc, or the loop is gone
        return False


def _quietly(fn) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001 - backend teardown race
        pass


def refresh() -> None:
    """Rebuild the menu, so a dynamic label reflects the world as it is now.

    Every backend builds its menu once and caches it; none re-runs the label
    callbacks when the menu is opened. Without this the quit warning would be
    fixed at whatever the state was when the app started — which is always
    "idle", making it useless precisely when it matters.
    """
    with _lock:
        icon = _icon
    if icon is None:
        return
    if _on_main_thread(icon.update_menu):
        return
    _quietly(icon.update_menu)


def stop() -> None:
    """End the tray loop from another thread. No-op if there isn't one.

    Called by the shutdown path so that quitting from the web UI also releases
    the main thread, rather than leaving a tray icon for a server that has gone.
    """
    with _lock:
        icon = _icon
    if icon is None:
        return
    if _on_main_thread(icon.stop):
        return
    _quietly(icon.stop)
