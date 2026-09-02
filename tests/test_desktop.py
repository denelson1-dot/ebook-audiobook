"""The desktop shell: one instance, a real window, and a tray that can be absent.

The thing worth protecting here is the singleton. Two instances over one data
root means two ``Runner`` threads writing job state for the same job with no lock
between them, so every path that decides "is one already running?" has to be
wrong in the safe direction: a live instance must always be found, and a dead
record must never be believed.
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from ebook_audiobook import cli
from ebook_audiobook.desktop import launcher, runtime, tray
from ebook_audiobook.desktop import runtime as cli_runtime


# --- runtime.json ------------------------------------------------------------

def test_write_then_read_round_trips():
    runtime.write(5005)
    record = runtime.read()
    assert record["port"] == 5005
    assert record["app"] == runtime.APP_ID
    assert record["host"] == "127.0.0.1"


def test_read_is_none_when_nothing_was_written():
    assert runtime.read() is None


def test_read_survives_a_corrupt_record():
    """A half-written or hand-edited file must not stop the app from starting."""
    path = runtime.runtime_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert runtime.read() is None


def test_read_rejects_a_record_with_no_port():
    path = runtime.runtime_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"app": runtime.APP_ID}), encoding="utf-8")
    assert runtime.read() is None


def test_clear_removes_the_record():
    runtime.write(5005)
    runtime.clear()
    assert runtime.read() is None
    runtime.clear()  # idempotent: no error when there is nothing to remove


def test_write_is_atomic_leaving_no_temp_files():
    runtime.write(5005)
    leftovers = list(runtime.runtime_path().parent.glob("*.tmp"))
    assert leftovers == []


@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1", "http://127.0.0.1:5005"),
    # 0.0.0.0 is bindable but not connectable; a window pointed at it fails.
    ("0.0.0.0", "http://127.0.0.1:5005"),
    ("", "http://127.0.0.1:5005"),
    ("192.168.1.10", "http://192.168.1.10:5005"),
])
def test_url_for_gives_something_a_browser_can_reach(host, expected):
    assert runtime.url_for({"host": host, "port": 5005}) == expected


# --- probe -------------------------------------------------------------------

class _Stub(BaseHTTPRequestHandler):
    """Answers /api/status with whatever the test put in ``payload``."""

    payload: dict = {}

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's naming
        body = json.dumps(self.payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # keep the test output clean


@pytest.fixture
def stub_server():
    """A throwaway HTTP server, so probe() is exercised over real sockets."""
    server = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def test_probe_finds_a_live_instance(stub_server):
    _Stub.payload = {"app": runtime.APP_ID, "busy": False}
    runtime.write(stub_server.server_port)
    assert runtime.probe() == f"http://127.0.0.1:{stub_server.server_port}"


def test_probe_ignores_a_stranger_on_the_recorded_port(stub_server):
    """The port got recycled to something else that also answers 200.

    Believing it would hand the user an unrelated web app in a window titled
    ebook-audiobook, so the marker in the payload is what decides.
    """
    _Stub.payload = {"hello": "some other dev server"}
    runtime.write(stub_server.server_port)
    assert runtime.probe() is None
    assert runtime.read() is None  # and the bad record is dropped


def test_probe_clears_a_record_for_a_dead_instance():
    """The common case: the app was killed, so the file outlived the process."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]
    runtime.write(dead_port)
    assert runtime.probe() is None
    assert runtime.read() is None


def test_probe_is_none_with_no_record():
    assert runtime.probe() is None


# --- finding a browser -------------------------------------------------------

def test_find_browser_prefers_the_first_listed(monkeypatch):
    monkeypatch.setattr(launcher, "IS_MACOS", False)
    monkeypatch.setattr(launcher, "IS_WINDOWS", False)
    present = {"brave-browser": "/usr/bin/brave-browser",
               "google-chrome": "/usr/bin/google-chrome"}
    monkeypatch.setattr(launcher.shutil, "which", lambda n: present.get(n))
    assert launcher.find_browser() == "/usr/bin/google-chrome"


def test_find_browser_falls_through_to_what_is_installed(monkeypatch):
    monkeypatch.setattr(launcher, "IS_MACOS", False)
    monkeypatch.setattr(launcher, "IS_WINDOWS", False)
    monkeypatch.setattr(launcher.shutil, "which",
                        lambda n: "/usr/bin/brave-browser" if n == "brave-browser" else None)
    assert launcher.find_browser() == "/usr/bin/brave-browser"


def test_find_browser_is_none_when_there_is_no_chromium(monkeypatch):
    """Firefox-only machines exist, and must fall back to an ordinary tab."""
    monkeypatch.setattr(launcher, "IS_MACOS", False)
    monkeypatch.setattr(launcher, "IS_WINDOWS", False)
    monkeypatch.setattr(launcher.shutil, "which", lambda n: None)
    assert launcher.find_browser() is None


def test_command_asks_for_an_app_window_and_its_own_profile(monkeypatch):
    monkeypatch.setattr(launcher, "IS_MACOS", False)
    monkeypatch.setattr(launcher, "IS_WINDOWS", False)
    cmd = launcher._command("/usr/bin/google-chrome", "http://127.0.0.1:5005")
    assert cmd[0] == "/usr/bin/google-chrome"
    assert "--app=http://127.0.0.1:5005" in cmd
    assert f"--class={launcher.WM_CLASS}" in cmd
    assert any(c.startswith("--user-data-dir=") for c in cmd)
    # Sharing the user's real profile would put our window in their browser's
    # process, so closing the browser would close the app.
    profile = next(c for c in cmd if c.startswith("--user-data-dir="))
    assert profile.endswith("browser-profile")


def test_command_on_macos_runs_the_binary_not_open(monkeypatch):
    """`open` exits the instant it hands off, leaving close_windows() nothing to
    signal — so Quit would strand the window showing a connection error."""
    monkeypatch.setattr(launcher, "IS_MACOS", True)
    exe = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    cmd = launcher._command(exe, "http://127.0.0.1:5005")
    assert cmd[0] == exe
    assert "open" not in cmd
    assert "--app=http://127.0.0.1:5005" in cmd
    # --class is X11-only and macOS rejects unknown flags less gracefully.
    assert not any(c.startswith("--class=") for c in cmd)


def test_macos_data_root_with_a_space_stays_one_argument(monkeypatch):
    """~/Library/Application Support/... has a space in it. Split across two
    argv entries, Chrome would write its profile somewhere unintended."""
    monkeypatch.setattr(launcher, "IS_MACOS", True)
    monkeypatch.setenv("EBAB_DATA_ROOT",
                       "/Users/dave/Library/Application Support/ebook-audiobook")
    cmd = launcher._command("/Applications/Chromium.app/Contents/MacOS/Chromium",
                            "http://127.0.0.1:5005")
    profile = [c for c in cmd if c.startswith("--user-data-dir=")]
    assert len(profile) == 1
    # Compare as path parts, not as a string: this test also runs on Windows,
    # where the separator is a backslash.
    parts = Path(profile[0].split("=", 1)[1]).parts
    assert parts[-3:] == ("Application Support", "ebook-audiobook", "browser-profile")


def test_find_browser_on_macos_returns_the_bundle_executable(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "IS_MACOS", True)
    bundle = tmp_path / "Applications" / "Brave Browser.app" / "Contents" / "MacOS"
    bundle.mkdir(parents=True)
    (bundle / "Brave Browser").write_text("#!/bin/sh\n")
    monkeypatch.setattr(launcher, "MACOS_APP_DIRS", (str(tmp_path / "Applications"),))
    assert launcher.find_browser() == str(bundle / "Brave Browser")


def test_find_browser_on_macos_checks_the_per_user_folder(monkeypatch, tmp_path):
    """Dragging a browser to ~/Applications instead of /Applications is normal,
    and must not silently demote the user to a plain browser tab."""
    monkeypatch.setattr(launcher, "IS_MACOS", True)
    per_user = tmp_path / "home" / "Applications"
    exe = per_user / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setattr(launcher, "MACOS_APP_DIRS",
                        (str(tmp_path / "nowhere"), str(per_user)))
    assert launcher.find_browser() == str(exe)


def test_find_browser_on_macos_is_none_when_nothing_installed(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, "IS_MACOS", True)
    monkeypatch.setattr(launcher, "MACOS_APP_DIRS", (str(tmp_path),))
    assert launcher.find_browser() is None


def test_open_app_window_reports_failure_rather_than_raising(monkeypatch):
    monkeypatch.setattr(launcher, "find_browser", lambda: "/nonexistent/browser")
    assert launcher.open_app_window("http://127.0.0.1:5005") is False


def test_open_app_window_is_false_with_no_browser(monkeypatch):
    monkeypatch.setattr(launcher, "find_browser", lambda: None)
    assert launcher.open_app_window("http://127.0.0.1:5005") is False


# --- closing what we opened --------------------------------------------------

class _FakeChild:
    def __init__(self, alive=True):
        self.alive = alive
        self.terminated = False

    def poll(self):
        return None if self.alive else 0

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.alive = False


def test_close_windows_terminates_a_live_window(monkeypatch):
    child = _FakeChild()
    monkeypatch.setattr(launcher, "_spawned", [child])
    launcher.close_windows()
    assert child.terminated


def test_close_windows_leaves_already_dead_processes_alone(monkeypatch):
    """A second launch into an existing profile, and `open` on macOS, both hand
    off and exit immediately — there is nothing there to signal."""
    child = _FakeChild(alive=False)
    monkeypatch.setattr(launcher, "_spawned", [child])
    launcher.close_windows()
    assert not child.terminated


def test_close_windows_forgets_what_it_closed(monkeypatch):
    """Shutdown can run twice — the tray's Quit and then the finally block."""
    child = _FakeChild()
    monkeypatch.setattr(launcher, "_spawned", [child])
    launcher.close_windows()
    launcher.close_windows()
    assert launcher._spawned == []


def test_close_windows_never_touches_the_users_own_browser(monkeypatch):
    """The webbrowser.open fallback hands the URL to the user's real browser.
    We have no handle on it and must not acquire one."""
    monkeypatch.setattr(launcher, "find_browser", lambda: None)
    monkeypatch.setattr(launcher, "_spawned", [])
    launcher.open_app_window("http://127.0.0.1:5005")
    assert launcher._spawned == []


# --- the tray, and doing without one -----------------------------------------

def test_tray_is_declined_when_switched_off(monkeypatch):
    monkeypatch.setenv("EBAB_NO_TRAY", "1")
    assert tray.available() is False


def test_tray_is_declined_with_no_display(monkeypatch):
    """Headless boxes and SSH sessions. pystray's Xorg backend blocks for a
    while before admitting this, so it is checked before we get there."""
    monkeypatch.setattr(tray, "IS_LINUX", True)
    monkeypatch.setattr(tray, "IS_MACOS", False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert tray.available() is False


def test_tray_run_reports_failure_rather_than_raising(monkeypatch):
    """A desktop with no usable tray protocol — a stock GNOME session is the
    common one — must leave the app running, not take it down."""
    monkeypatch.setattr(tray, "available", lambda: True)

    def explode(*a, **k):
        raise RuntimeError("no tray here")

    monkeypatch.setitem(__import__("sys").modules, "pystray", None)
    assert tray.run("http://127.0.0.1:5005", explode, explode) is False


def test_tray_stop_is_safe_with_no_tray_running():
    tray.stop()  # must not raise


def test_tray_refresh_is_safe_with_no_tray_running():
    tray.refresh()  # must not raise


def test_tray_refresh_rebuilds_the_menu(monkeypatch):
    """Every backend caches its menu, so a dynamic label is frozen at whatever
    was true at startup unless something explicitly rebuilds it.

    Off macOS: there the call is handed to the AppKit run loop, which this
    test does not run — see test_on_macos_refresh_runs_on_the_appkit_thread."""
    monkeypatch.setattr(tray, "IS_MACOS", False)

    class _Icon:
        def __init__(self):
            self.updates = 0

        def update_menu(self):
            self.updates += 1

    icon = _Icon()
    tray._icon = icon
    try:
        tray.refresh()
    finally:
        tray._icon = None
    assert icon.updates == 1


class _FakeAppHelper:
    """Stands in for PyObjCTools.AppHelper: records what was handed to the main
    thread instead of needing an AppKit run loop to hand it to."""

    def __init__(self):
        self.queued = []

    def callAfter(self, fn, *args):  # noqa: N802 - pyobjc's name
        self.queued.append((fn, args))

    def drain(self):
        for fn, args in self.queued:
            fn(*args)
        self.queued.clear()


@pytest.fixture
def fake_apphelper(monkeypatch):
    import types

    helper = _FakeAppHelper()
    pkg = types.ModuleType("PyObjCTools")
    pkg.AppHelper = helper
    monkeypatch.setitem(sys.modules, "PyObjCTools", pkg)
    monkeypatch.setitem(sys.modules, "PyObjCTools.AppHelper", helper)
    monkeypatch.setattr(tray, "IS_MACOS", True)
    return helper


class _RecordingIcon:
    def __init__(self):
        self.updates = 0
        self.stops = 0
        self.thread_ids = []

    def update_menu(self):
        self.updates += 1
        self.thread_ids.append(threading.get_ident())

    def stop(self):
        self.stops += 1
        self.thread_ids.append(threading.get_ident())


def test_on_macos_refresh_runs_on_the_appkit_thread(fake_apphelper):
    """The busy-state watcher lives on a background thread, and an NSMenu
    rebuilt from there while the user has it open is a crash waiting to
    happen. The call has to be handed to the run loop, not made in place."""
    icon = _RecordingIcon()
    tray._icon = icon
    try:
        tray.refresh()
        assert icon.updates == 0          # not done here...
        fake_apphelper.drain()            # ...but once the main loop gets to it
        assert icon.updates == 1
    finally:
        tray._icon = None


def test_on_macos_stop_runs_on_the_appkit_thread(fake_apphelper):
    """/quit arrives on a waitress thread; same rule."""
    icon = _RecordingIcon()
    tray._icon = icon
    try:
        tray.stop()
        assert icon.stops == 0
        fake_apphelper.drain()
        assert icon.stops == 1
    finally:
        tray._icon = None


def test_a_failing_menu_rebuild_on_the_main_thread_is_swallowed(fake_apphelper):
    """An exception escaping a callAfter callback is printed by pyobjc and,
    worse, could tear down the loop mid-shutdown; keep it contained."""
    class _Broken:
        def update_menu(self):
            raise RuntimeError("torn down")

    tray._icon = _Broken()
    try:
        tray.refresh()
        fake_apphelper.drain()  # must not raise
    finally:
        tray._icon = None


def test_off_macos_refresh_and_stop_are_direct(monkeypatch):
    """Nothing about the other platforms changed: no pyobjc, no marshalling."""
    monkeypatch.setattr(tray, "IS_MACOS", False)
    monkeypatch.setitem(sys.modules, "PyObjCTools", None)
    icon = _RecordingIcon()
    tray._icon = icon
    try:
        tray.refresh()
        tray.stop()
    finally:
        tray._icon = None
    assert (icon.updates, icon.stops) == (1, 1)
    assert set(icon.thread_ids) == {threading.get_ident()}


def test_the_terminate_callback_runs_and_is_contained(monkeypatch):
    """What the NSApplication delegate does when macOS says quit: run the
    orderly shutdown, and never let it raise back into AppKit."""
    ran = []
    monkeypatch.setattr(tray, "_on_terminate", lambda: ran.append(True))
    tray._run_terminate_callback()
    assert ran == [True]

    def explode():
        raise RuntimeError("mid-drain")

    monkeypatch.setattr(tray, "_on_terminate", explode)
    tray._run_terminate_callback()  # must not raise

    monkeypatch.setattr(tray, "_on_terminate", None)
    tray._run_terminate_callback()


def test_the_terminate_handler_is_not_installed_off_macos(monkeypatch):
    monkeypatch.setattr(tray, "IS_MACOS", False)
    monkeypatch.setitem(sys.modules, "AppKit", None)
    monkeypatch.setattr(tray, "_on_terminate", None)
    tray._macos_install_terminate_handler(lambda: None)
    assert tray._on_terminate is None


def test_the_terminate_handler_survives_a_missing_appkit(monkeypatch):
    """Best effort: the tray must still run if the delegate can't be made."""
    monkeypatch.setattr(tray, "IS_MACOS", True)
    monkeypatch.setitem(sys.modules, "AppKit", None)
    monkeypatch.setattr(tray, "_app_delegate", None)
    tray._macos_install_terminate_handler(lambda: None)  # must not raise
    assert tray._app_delegate is None


def test_tray_is_declined_when_pystray_is_missing(monkeypatch):
    """An install done with --no-deps leaves the app importable and pystray
    absent. Without this check the startup banner promises a tray icon that can
    never appear."""
    import builtins

    real_import = builtins.__import__

    def no_pystray(name, *args, **kwargs):
        if name == "pystray":
            raise ImportError("no pystray")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pystray)
    assert tray.available() is False


def test_macos_tray_is_declined_without_a_window_server(monkeypatch):
    """Over SSH to a Mac, AppKit imports fine and only dies later — deep enough
    that it can abort the process instead of raising."""
    monkeypatch.setattr(tray, "IS_MACOS", True)
    monkeypatch.setattr(tray, "IS_LINUX", False)
    monkeypatch.setattr(tray, "_macos_has_gui_session", lambda: False)
    assert tray.available() is False


def test_macos_gui_session_check_is_false_without_quartz(monkeypatch):
    """No pyobjc means we cannot ask, and 'no' is the safe answer.

    Written so it holds on a real Mac too, where Quartz *is* importable and a CI
    runner may well have a window server — the first version of this asserted
    the Linux answer and duly failed on macOS.
    """
    import builtins

    real_import = builtins.__import__

    def no_quartz(name, *args, **kwargs):
        if name == "Quartz":
            raise ImportError("no pyobjc here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_quartz)
    assert tray._macos_has_gui_session() is False


def test_macos_activation_is_skipped_off_darwin(monkeypatch):
    calls = []
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(launcher, "IS_MACOS", False)
    launcher._macos_activate("/usr/bin/google-chrome")
    assert calls == []


def test_macos_activation_targets_the_bundle(monkeypatch):
    """Bundle paths are POSIX even when the test runs on Windows, which is why
    _macos_activate uses PurePosixPath rather than the local flavour."""
    calls = []
    monkeypatch.setattr(launcher.subprocess, "Popen",
                        lambda cmd, **k: calls.append(cmd))
    monkeypatch.setattr(launcher, "IS_MACOS", True)
    monkeypatch.setattr(launcher, "_macos_activate_pid", lambda pid, **k: False)
    launcher._macos_activate(
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", pid=4242)
    _join_activation_threads()
    assert calls == [["open", "-a", "/Applications/Google Chrome.app"]]


def _join_activation_threads():
    for t in threading.enumerate():
        if t.name == "ebab-activate":
            t.join(5)


def test_macos_activation_does_not_block_its_caller(monkeypatch):
    """The caller is a tray menu action on the AppKit main thread, or a request
    handler; a browser still starting must not hold either of them up."""
    started = threading.Event()
    release = threading.Event()

    def slow_activate(pid, **k):
        started.set()
        release.wait(5)
        return True

    monkeypatch.setattr(launcher, "IS_MACOS", True)
    monkeypatch.setattr(launcher, "_macos_activate_pid", slow_activate)
    launcher._macos_activate("/Applications/Chromium.app/Contents/MacOS/Chromium", pid=1)
    assert started.wait(2)   # the work began...
    release.set()            # ...and we were not waiting on it
    _join_activation_threads()


def test_macos_activation_prefers_our_own_process_over_the_bundle(monkeypatch):
    """`open -a` names the bundle, and the user's own Chrome is the same bundle:
    LaunchServices may raise theirs and leave our window where it was. The pid
    names ours and nothing else, so when that works `open` is not attempted."""
    calls = []
    monkeypatch.setattr(launcher.subprocess, "Popen",
                        lambda cmd, **k: calls.append(cmd))
    monkeypatch.setattr(launcher, "_macos_activate_pid", lambda pid, **k: pid == 4242)
    launcher._macos_activate_now("/Applications/Google Chrome.app", 4242)
    assert calls == []


def test_macos_activation_falls_back_to_open_when_the_process_is_gone(monkeypatch):
    """A second launch into an existing profile hands its URL to the instance
    that owns it and exits, so there is no pid to activate — only the bundle."""
    calls = []
    monkeypatch.setattr(launcher.subprocess, "Popen",
                        lambda cmd, **k: calls.append(cmd))
    monkeypatch.setattr(launcher, "_macos_activate_pid", lambda pid, **k: False)
    launcher._macos_activate_now("/Applications/Google Chrome.app", 4242)
    assert calls == [["open", "-a", "/Applications/Google Chrome.app"]]
    calls.clear()
    launcher._macos_activate_now("/Applications/Google Chrome.app", None)
    assert calls == [["open", "-a", "/Applications/Google Chrome.app"]]


class _FakeAppKit:
    """Just enough of AppKit for _macos_activate_pid: a table of running
    applications by pid, each recording whether it was activated."""

    NSApplicationActivateIgnoringOtherApps = 1 << 1

    def __init__(self, running: dict, answer=True):
        self.activated = []
        outer = self

        class NSRunningApplication:
            @staticmethod
            def runningApplicationWithProcessIdentifier_(pid):
                return _App(pid) if pid in running else None

        class _App:
            def __init__(self, pid):
                self.pid = pid

            def activateWithOptions_(self, options):
                outer.activated.append((self.pid, options))
                return answer

        self.NSRunningApplication = NSRunningApplication


def test_activate_by_pid_activates_exactly_that_process(monkeypatch):
    fake = _FakeAppKit(running={4242: True})
    monkeypatch.setitem(sys.modules, "AppKit", fake)
    assert launcher._macos_activate_pid(4242, timeout=1, interval=0.01) is True
    assert fake.activated == [(4242, fake.NSApplicationActivateIgnoringOtherApps)]


def test_activate_by_pid_waits_for_the_browser_to_register(monkeypatch):
    """A cold profile takes a second or two to reach the window server; asking
    once, at Popen time, would always come back empty."""
    running = {}
    fake = _FakeAppKit(running=running)
    monkeypatch.setitem(sys.modules, "AppKit", fake)
    threading.Timer(0.15, lambda: running.setdefault(7, True)).start()
    assert launcher._macos_activate_pid(7, timeout=3, interval=0.02) is True
    assert fake.activated == [(7, fake.NSApplicationActivateIgnoringOtherApps)]


def test_activate_by_pid_gives_up_when_the_process_never_appears(monkeypatch):
    fake = _FakeAppKit(running={})
    monkeypatch.setitem(sys.modules, "AppKit", fake)
    assert launcher._macos_activate_pid(99999, timeout=0.1, interval=0.02) is False
    assert fake.activated == []


def test_activate_by_pid_reports_a_refusal(monkeypatch):
    """Since macOS 14 activation is cooperative and a refusal is a False
    return, not an error — and False is what sends the caller on to `open`."""
    fake = _FakeAppKit(running={4242: True}, answer=False)
    monkeypatch.setitem(sys.modules, "AppKit", fake)
    assert launcher._macos_activate_pid(4242, timeout=1, interval=0.01) is False


def test_activate_by_pid_is_false_without_pyobjc(monkeypatch):
    monkeypatch.setitem(sys.modules, "AppKit", None)
    assert launcher._macos_activate_pid(4242, timeout=0.1) is False


def test_the_icon_the_tray_asks_for_is_actually_shipped():
    """A wheel missing its assets starts fine and simply has no icon, which is
    exactly the kind of failure nothing notices."""
    assert (tray.ASSETS / tray.ICON_FILE).is_file()


# --- one instance ------------------------------------------------------------

def test_relaunch_opens_a_window_instead_of_a_second_server(monkeypatch):
    """The hazard this exists for: a second Runner over the same JobStore."""
    monkeypatch.setattr(cli_runtime, "probe", lambda: "http://127.0.0.1:5005")
    opened, served = [], []
    monkeypatch.setattr("ebook_audiobook.web.server.open_window", opened.append)
    monkeypatch.setattr("ebook_audiobook.web.server.serve",
                        lambda **kw: served.append(kw))

    assert cli.cmd_web(_web_args(no_browser=False)) == 0
    assert opened == ["http://127.0.0.1:5005"]
    assert served == []


def test_relaunch_with_no_browser_still_declines_to_start_a_second_server(monkeypatch):
    monkeypatch.setattr(cli_runtime, "probe", lambda: "http://127.0.0.1:5005")
    opened, served = [], []
    monkeypatch.setattr("ebook_audiobook.web.server.open_window", opened.append)
    monkeypatch.setattr("ebook_audiobook.web.server.serve",
                        lambda **kw: served.append(kw))

    assert cli.cmd_web(_web_args(no_browser=True)) == 0
    assert served == []
    assert opened == []


def test_first_launch_starts_the_server(monkeypatch):
    monkeypatch.setattr(cli_runtime, "probe", lambda: None)
    served = []
    monkeypatch.setattr("ebook_audiobook.web.server.serve",
                        lambda **kw: served.append(kw))

    assert cli.cmd_web(_web_args()) == 0
    assert len(served) == 1


def test_an_explicit_port_always_starts_its_own_server(monkeypatch):
    """Asking for a specific address is asking for a specific server, so the
    singleton check is skipped — otherwise `--port 6000` would silently hand
    back the instance already running on 5005."""
    monkeypatch.setattr(cli_runtime, "probe",
                        lambda: pytest.fail("should not have probed"))
    served = []
    monkeypatch.setattr("ebook_audiobook.web.server.serve",
                        lambda **kw: served.append(kw))

    assert cli.cmd_web(_web_args(port=6000)) == 0
    assert served[0]["port"] == 6000


def _web_args(**overrides):
    return SimpleNamespace(**{"host": None, "port": None, "no_browser": True,
                              "no_tray": True, **overrides})


# --- remembering where the window was ---------------------------------------

def test_window_flags_are_absent_until_there_is_something_to_remember():
    from ebook_audiobook.desktop import launcher

    assert launcher._saved_window_flags() == []


def test_window_flags_reopen_the_window_where_it_was():
    from ebook_audiobook import settings as app_settings
    from ebook_audiobook.desktop import launcher

    s = app_settings.load_settings()
    s.window_geometry = {"x": 1431, "y": 137, "width": 1705, "height": 1197}
    app_settings.save_settings(s)

    flags = launcher._saved_window_flags()
    assert "--window-size=1705,1197" in flags
    assert "--window-position=1431,137" in flags
    # and they actually reach the browser command line
    cmd = launcher._command("/usr/bin/chromium", "http://127.0.0.1:5005/")
    assert "--window-size=1705,1197" in cmd


@pytest.mark.parametrize("geometry", [
    {"x": 0, "y": 0, "width": 10, "height": 10},          # absurdly small
    {"x": 0, "y": 0, "width": 99999, "height": 900},      # absurdly wide
    {"x": 0, "y": 0, "width": 1400},                      # incomplete
    {"nonsense": True},
    "not even a mapping",
])
def test_a_nonsense_geometry_is_ignored_rather_than_reopening_off_screen(geometry):
    """A window remembered at 12000px — a monitor unplugged since — would reopen
    somewhere the user cannot reach, with no obvious way back."""
    from ebook_audiobook import settings as app_settings
    from ebook_audiobook.desktop import launcher

    s = app_settings.load_settings()
    s.window_geometry = geometry
    app_settings.save_settings(s)
    assert launcher._saved_window_flags() == []


def test_the_page_can_report_its_geometry():
    from ebook_audiobook import settings as app_settings
    from ebook_audiobook.web import create_app

    c = create_app().test_client()
    assert c.post("/api/window", data={"x": 100, "y": 60, "width": 1400, "height": 900}).status_code == 200
    assert app_settings.load_settings().window_geometry == {
        "x": 100, "y": 60, "width": 1400, "height": 900}


@pytest.mark.parametrize("payload", [
    {"x": 0, "y": 0, "width": 10, "height": 10},           # a minimised window
    {"x": 99999, "y": 0, "width": 1400, "height": 900},    # off in space
    {"x": "a", "y": 0, "width": 1400, "height": 900},      # junk
    {"x": 0, "y": 0},                                      # incomplete
])
def test_implausible_geometry_is_refused_not_stored(payload):
    from ebook_audiobook import settings as app_settings
    from ebook_audiobook.web import create_app

    c = create_app().test_client()
    assert c.post("/api/window", data=payload).status_code == 400
    assert app_settings.load_settings().window_geometry is None
