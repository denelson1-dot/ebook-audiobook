"""Opening the UI in a window that looks like an application.

Every Chromium-family browser supports ``--app=<url>``: a window with no tab
strip, no URL bar, no bookmarks, its own taskbar entry and its own icon. That is
the entire trick. It costs no new dependency and no bundled runtime, which
matters here because the alternative — shipping a webview — means either a GTK
stack the venv cannot see or a second ~150 MB GUI toolkit next to torch.

Firefox has no equivalent; ``-kiosk`` is fullscreen, not chromeless, and its
site-specific-browser mode was removed. So the fallback when nothing here is
found is an ordinary browser tab, exactly as before.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path, PurePosixPath

from ..config import data_root

# Decided once, at import, the same way tools.py does it. Tests select a
# platform by overriding these rather than by reaching into `sys` or `os`:
# monkeypatching `os.name` mutates the os module for the whole interpreter, and
# pathlib reads it to choose between WindowsPath and PosixPath — so a test that
# does that turns every subsequent Path() into a NotImplementedError.
IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = os.name == "nt"

# Windows we opened ourselves, so quitting can close them. Only ever holds
# processes we spawned into our own profile — never the user's real browser,
# which is what the webbrowser.open fallback hands the URL to.
_spawned: list = []
_spawned_lock = threading.Lock()

# Order matters: the first one found wins. Chrome and Edge lead because their
# --app implementation is the best tested; Brave is Chromium and behaves
# identically, but it is more often someone's *only* browser than their
# preferred one for a local tool.
LINUX_BROWSERS = (
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
    "microsoft-edge-stable",
    "brave-browser",
    "vivaldi",
)

# macOS applications are found by bundle name, not by PATH — a Chrome install
# puts nothing in /usr/bin. For all of these the executable inside the bundle
# has the same name as the bundle itself.
MACOS_BROWSERS = ("Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium",
                  "Vivaldi")

# Both the system-wide location and the per-user one. Installing a browser by
# dragging it to ~/Applications is ordinary on macOS, and only checking
# /Applications would drop those users to a plain browser tab for no reason.
MACOS_APP_DIRS = ("/Applications", os.path.expanduser("~/Applications"))

# Windows installs to Program Files and usually adds nothing to PATH, so the
# well-known locations have to be checked directly. Edge is last only because
# Chrome is more likely to be the user's actual browser — it is present on every
# Windows 10+ machine, which makes this the most reliable platform of the three.
WINDOWS_BROWSERS = (
    r"Google\Chrome\Application\chrome.exe",
    r"BraveSoftware\Brave-Browser\Application\brave.exe",
    r"Microsoft\Edge\Application\msedge.exe",
)

# WM_CLASS for the app window. The .desktop file written by install.sh carries a
# matching StartupWMClass, which is what makes the window group under our icon in
# the taskbar instead of under the browser's.
WM_CLASS = "ebook-audiobook"


def profile_dir() -> Path:
    """A browser profile of our own.

    Without this the app window is just another window of the user's running
    browser: it shares that process, so quitting the browser closes the app, and
    it groups under the browser's taskbar icon rather than ours. A dedicated
    profile costs ~50 MB and a slower first launch, and buys a window that
    behaves like an independent application.
    """
    return data_root() / "browser-profile"


def _windows_candidates() -> list[str]:
    roots = [os.environ.get(v) for v in
             ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
    return [str(Path(root) / rel)
            for root in roots if root
            for rel in WINDOWS_BROWSERS]


def find_browser() -> str | None:
    """The browser executable to use, or None if there is no Chromium here.

    On macOS this deliberately returns the executable *inside* the bundle rather
    than a name to hand to ``open``. See :func:`_command` for why.
    """
    if IS_MACOS:
        for directory in MACOS_APP_DIRS:
            for name in MACOS_BROWSERS:
                exe = Path(directory) / f"{name}.app" / "Contents" / "MacOS" / name
                if exe.is_file():
                    return str(exe)
        return None
    if IS_WINDOWS:
        for candidate in _windows_candidates():
            if Path(candidate).is_file():
                return candidate
        return None
    for name in LINUX_BROWSERS:
        found = shutil.which(name)
        if found:
            return found
    return None


def _command(browser: str, url: str) -> list[str]:
    profile = str(profile_dir())
    flags = [
        f"--app={url}",
        f"--user-data-dir={profile}",
        # A fresh profile otherwise greets the user with a welcome tour and a
        # "make me your default browser" prompt, inside what is supposed to be
        # our application window.
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if IS_MACOS:
        # Deliberately NOT `open -na "Google Chrome" --args …`. `open` hands the
        # request to LaunchServices and exits immediately, so the process we get
        # back is already dead and close_windows() has nothing to signal — which
        # left the window behind on Quit showing a connection-error page, the
        # exact bug close_windows() exists to prevent. Executing the binary
        # inside the bundle gives us a real child to terminate. The separate
        # --user-data-dir is what guarantees a distinct instance, so `open -n`
        # was never buying anything here.
        return [browser, *flags]
    if not IS_WINDOWS:
        # X11 only; harmless (ignored) under Wayland, where the compositor uses
        # the app-id from the .desktop file instead.
        flags.append(f"--class={WM_CLASS}")
    return [browser, *flags]


def open_app_window(url: str) -> bool:
    """Open ``url`` as an application window. False if that wasn't possible.

    Never raises: a browser that fails to launch must degrade to an ordinary tab,
    not take the server down with it.
    """
    browser = find_browser()
    if not browser:
        return False
    try:
        profile_dir().mkdir(parents=True, exist_ok=True)
        child = subprocess.Popen(
            _command(browser, url),
            # Chrome writes a wall of GPU, component-updater and extension noise
            # to stderr on startup. Redirecting the child's streams directly is
            # possible here (unlike with webbrowser.open, which hands us no
            # control over the Popen) so no fd juggling is needed on this path.
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            # Detach from our process group so a Ctrl-C in the terminal that
            # started the server doesn't also kill the user's window.
            start_new_session=not IS_WINDOWS,
        )
    except (OSError, ValueError):
        return False
    with _spawned_lock:
        _spawned.append(child)
    _macos_activate(browser)
    return True


def _macos_activate(browser: str) -> None:
    """Bring the app window to the front on macOS.

    Executing the binary directly is what gives us a child process to close on
    Quit, but it bypasses LaunchServices — and LaunchServices is what raises and
    focuses an app on macOS. Without this, the common case of closing the window
    with the red button and then picking "Open" from the menu bar creates the
    window *behind* everything, or on another Space, and the click reads as
    having done nothing.

    ``open -a`` on the bundle activates the instance that already exists rather
    than starting another. Best effort; failing to focus is not worth an error.
    """
    if not IS_MACOS:
        return
    # …/Foo.app/Contents/MacOS/Foo -> …/Foo.app. PurePosixPath, not Path: this is
    # macOS-only code handling macOS-only paths, and going through the local
    # flavour would produce backslashes anywhere else.
    bundle = PurePosixPath(browser).parent.parent.parent
    if bundle.suffix != ".app":
        return
    try:
        subprocess.Popen(["open", "-a", str(bundle)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         stdin=subprocess.DEVNULL)
    except (OSError, ValueError):
        pass


def close_windows(timeout: float = 3.0) -> None:
    """Close the app windows we opened.

    Quitting from the tray otherwise leaves the window behind showing the
    browser's own "site can't be reached" page, which reads as a crash rather
    than as the app having been closed on purpose.

    Only processes started by :func:`open_app_window` are touched, and those
    always run in our own profile directory, so this can never close a window of
    the user's real browser.

    A second launch into an existing profile hands off to the browser process
    that already owns it and then exits, so that tracked process is dead by the
    time we get here; skipping it is correct, because terminating the *first*
    process closes every window of the profile anyway.
    """
    with _spawned_lock:
        children, _spawned[:] = list(_spawned), []
    for child in children:
        if child.poll() is not None:
            continue
        try:
            # SIGTERM rather than kill: Chrome flushes its profile on the way
            # out, and a half-written profile greets the user with a "restore
            # pages?" bubble inside the app window next time.
            child.terminate()
            child.wait(timeout=timeout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass


@contextlib.contextmanager
def detached_std_fds():
    """Point the OS-level stdout/stderr at devnull for the wrapped block.

    Only needed for the ``webbrowser.open`` fallback: it spawns the browser with
    no stream redirection, so the child inherits our file descriptors
    (``close_fds`` only covers fd 3 upwards) and Chrome's startup chatter lands
    on screen looking like our crash report. Redirecting at the fd level rather
    than swapping ``sys.stderr`` is what it takes, since the child inherits fds,
    not Python objects.

    The child keeps the devnull fds it was handed at exec time, so restoring ours
    afterwards does not un-silence it. These are process-wide fds and this runs
    on a background thread, so output from another thread during the wrapped call
    would be swallowed too — the window is the spawn alone, a few milliseconds,
    during which nothing else is expected to be printing.

    Under ``pythonw`` there are no real fds 1 and 2 to duplicate, so the
    redirection is skipped; there is no console for the noise to land on anyway.
    """
    try:
        saved = [os.dup(1), os.dup(2)]
    except OSError:
        yield
        return
    try:
        with open(os.devnull, "wb") as devnull:
            os.dup2(devnull.fileno(), 1)
            os.dup2(devnull.fileno(), 2)
            yield
    finally:
        for fd, original in zip((1, 2), saved):
            try:
                os.dup2(original, fd)
            finally:
                os.close(original)
