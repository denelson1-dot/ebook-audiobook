"""Windows behaviour, simulated.

Nothing here runs on Windows itself unless CI's Windows job does: the file
sharing rules, drive letters and named mutexes are faked, so each test pins the
decision the code makes when Windows says what it says.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from ebook_audiobook import settings as app_settings
from ebook_audiobook import winfs


# --- winfs.replace -----------------------------------------------------------

def _flaky_replace(monkeypatch, failures: int):
    """os.replace that is refused *failures* times, as Windows refuses while a
    reader has the target open, then works."""
    calls = {"n": 0}
    real = os.replace

    def fake(src, dst):
        calls["n"] += 1
        if calls["n"] <= failures:
            raise PermissionError(5, "Access is denied")
        return real(src, dst)

    monkeypatch.setattr(winfs.os, "replace", fake)
    monkeypatch.setattr(winfs, "_DELAY", 0)
    return calls


def test_replace_waits_out_a_reader_on_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(winfs, "IS_WINDOWS", True)
    calls = _flaky_replace(monkeypatch, failures=3)
    src, dst = tmp_path / "new.tmp", tmp_path / "state.json"
    src.write_text("new", "utf-8")
    dst.write_text("old", "utf-8")
    winfs.replace(src, dst)
    assert dst.read_text("utf-8") == "new"
    assert calls["n"] == 4


def test_replace_gives_up_eventually(tmp_path, monkeypatch):
    monkeypatch.setattr(winfs, "IS_WINDOWS", True)
    _flaky_replace(monkeypatch, failures=10_000)
    src = tmp_path / "new.tmp"
    src.write_text("x", "utf-8")
    with pytest.raises(PermissionError):
        winfs.replace(src, tmp_path / "state.json")


def test_replace_does_not_retry_off_windows(tmp_path, monkeypatch):
    # Elsewhere a PermissionError is a real permissions problem.
    monkeypatch.setattr(winfs, "IS_WINDOWS", False)
    calls = _flaky_replace(monkeypatch, failures=1)
    src = tmp_path / "new.tmp"
    src.write_text("x", "utf-8")
    with pytest.raises(PermissionError):
        winfs.replace(src, tmp_path / "state.json")
    assert calls["n"] == 1


def test_job_state_save_survives_a_concurrent_reader(monkeypatch):
    """The render loop saves job_state.json after every segment while the UI
    polls it; on Windows the two collide, and that must not stop the render."""
    from ebook_audiobook.jobs.store import JobStore

    monkeypatch.setattr(winfs, "IS_WINDOWS", True)
    store = JobStore("abcdef0123456789").ensure()
    state = store.load_state()
    _flaky_replace(monkeypatch, failures=2)
    state.power_mode = "eco"
    store.save_state(state)
    assert store.load_state().power_mode == "eco"


# --- winfs.unlink ------------------------------------------------------------

def test_unlink_never_raises_for_a_file_in_use(tmp_path, monkeypatch):
    f = tmp_path / "book.m4b"
    f.write_bytes(b"x")

    def refuse(self, missing_ok=False):
        raise PermissionError(32, "being used by another process")

    monkeypatch.setattr(winfs, "IS_WINDOWS", True)
    monkeypatch.setattr(Path, "unlink", refuse)
    assert winfs.unlink(f) is False


def test_unlink_clears_read_only_on_windows(tmp_path, monkeypatch):
    f = tmp_path / "imported.epub"
    f.write_bytes(b"x")
    real_unlink = Path.unlink
    tries = {"n": 0}

    def read_only_until_chmod(self, missing_ok=False):
        tries["n"] += 1
        if tries["n"] == 1:
            raise PermissionError(5, "Access is denied")  # the read-only bit
        return real_unlink(self, missing_ok=missing_ok)

    chmods = []
    monkeypatch.setattr(winfs, "IS_WINDOWS", True)
    monkeypatch.setattr(Path, "unlink", read_only_until_chmod)
    monkeypatch.setattr(winfs.os, "chmod", lambda p, mode: chmods.append(mode))
    assert winfs.unlink(f) is True
    assert chmods == [stat.S_IWRITE]
    assert not f.exists()


def test_unlink_of_a_missing_file_is_fine(tmp_path):
    assert winfs.unlink(tmp_path / "nope") is True


def test_deleting_a_book_whose_audiobook_is_open_elsewhere(monkeypatch, tmp_path):
    """A player holding the .m4b open used to turn Delete into a 500 page, with
    the book already gone from the library and its files left behind."""
    from ebook_audiobook.jobs.store import JobStore

    store = JobStore("abcdef0123456789").ensure()
    out = tmp_path / "Book.m4b"
    out.write_bytes(b"audio")
    state = store.load_state()
    state.output_path = str(out)
    store.save_state(state)

    real_unlink = Path.unlink

    def locked_output(self, missing_ok=False):
        if self == out:
            raise PermissionError(32, "being used by another process")
        return real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", locked_output)
    store.delete()  # does not raise
    assert not store.exists()


# --- settings ----------------------------------------------------------------

def test_a_locked_settings_file_is_not_treated_as_corrupt(monkeypatch):
    """Moving settings.json aside is for a file that can't be parsed. One that
    can't be *opened* this instant (Windows, mid-save) is fine, and moving it
    would throw the user's library folder and preferences away."""
    saved = app_settings.load_settings()
    saved.audiobooks_root = "D:\\Audiobooks"
    saved.preferences_onboarded = True
    app_settings.save_settings(saved)
    path = app_settings._settings_path()

    def locked(p):
        raise PermissionError(32, "being used by another process")

    real_read = app_settings.winfs.read_text
    monkeypatch.setattr(app_settings.winfs, "read_text", locked)
    got = app_settings.load_settings()
    assert got.preferences_onboarded  # no first-run modal mid-render
    assert path.exists()
    assert not path.with_suffix(".corrupt.json").exists()
    # Not monkeypatch.undo(): that would also restore EBAB_DATA_ROOT and read
    # the developer's real settings.
    monkeypatch.setattr(app_settings.winfs, "read_text", real_read)
    assert app_settings.load_settings().audiobooks_root == "D:\\Audiobooks"


def test_a_corrupt_settings_file_is_still_moved_aside():
    path = app_settings._settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", "utf-8")
    got = app_settings.load_settings()
    assert got.preferences_onboarded is False
    assert path.with_suffix(".corrupt.json").exists()


# --- imports and voices -------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="uses POSIX permission bits")
def test_importing_a_read_only_book_keeps_a_deletable_copy(synthetic_epub, tmp_path):
    """copy2 carried a source's read-only attribute onto the library's copy,
    and Windows then refused to delete it along with its book."""
    from ebook_audiobook import worker
    from ebook_audiobook.config import paths

    src = tmp_path / "ro.epub"
    src.write_bytes(Path(synthetic_epub).read_bytes())
    src.chmod(0o444)
    job_id = worker.import_ebook(str(src), engine="fake")
    copies = list(paths().imports.glob(f"{job_id}*"))
    assert copies and os.access(copies[0], os.W_OK)


def test_a_voice_named_after_a_windows_device_gets_a_usable_file_name():
    from ebook_audiobook.voices import _slug

    assert _slug("Con") == "con-voice"
    assert _slug("AUX") == "aux-voice"
    assert _slug("Connie") == "connie"


# --- web: drives and job ids -------------------------------------------------

@pytest.fixture
def client():
    from ebook_audiobook.web import create_app

    return create_app().test_client()


def test_a_drive_root_lists_the_other_drives(client, monkeypatch, tmp_path):
    """Without them the picker could never leave C:, so a book on a USB stick
    couldn't be imported."""
    from ebook_audiobook.web import app as app_mod

    # Letters no test machine's temp folder lives on, so neither is the drive
    # being listed (which is left out; see the next test).
    monkeypatch.setattr(app_mod, "_windows_drives", lambda: ["Y:\\", "Z:\\"])
    root = Path(tmp_path.anchor)  # "/" off Windows, the temp folder's drive on it
    data = client.get("/api/fs", query_string={"path": str(root)}).get_json()
    names = [d["name"] for d in data["dirs"]]
    assert names[:2] == ["Y:\\", "Z:\\"]
    assert data["parent"] is None

    # Anywhere below a root, nothing changes.
    data = client.get("/api/fs", query_string={"path": str(tmp_path)}).get_json()
    assert "Z:\\" not in [d["name"] for d in data["dirs"]]


def test_the_current_drive_is_not_listed_as_another(client, monkeypatch, tmp_path):
    from ebook_audiobook.web import app as app_mod

    root = str(Path(tmp_path.anchor).resolve())
    monkeypatch.setattr(app_mod, "_windows_drives", lambda: [root, "E:\\"])
    data = client.get("/api/fs", query_string={"path": root}).get_json()
    assert [d["name"] for d in data["dirs"]].count(root) == 0


def test_windows_hidden_and_system_entries_are_not_listed(client, monkeypatch, tmp_path):
    from ebook_audiobook.web import app as app_mod

    (tmp_path / "Application Data").mkdir()
    (tmp_path / "Documents").mkdir()
    monkeypatch.setattr(app_mod, "_hidden_on_windows", lambda e: e.name == "Application Data")
    data = client.get("/api/fs", query_string={"path": str(tmp_path)}).get_json()
    names = [d["name"] for d in data["dirs"]]
    assert "Documents" in names and "Application Data" not in names


def test_no_drives_off_windows():
    from ebook_audiobook.web import app as app_mod

    if os.name != "nt":
        assert app_mod._windows_drives() == []


@pytest.mark.parametrize("bad", ["..%5C..%5Cx", "C:%5Cx", "a.b"])
def test_a_job_id_that_is_path_syntax_is_refused(client, bad):
    # Flask's default converter refuses "/" only; on Windows "\" and "C:" are
    # path syntax too.
    for route in ("download", "preview.wav", "status"):
        assert client.get(f"/job/{bad}/{route}").status_code == 404


# --- single instance ----------------------------------------------------------

def test_claim_launch_is_a_no_op_off_windows(monkeypatch):
    from ebook_audiobook.desktop import runtime

    if os.name != "nt":
        assert runtime.claim_launch() is True
        assert runtime.claim_launch(wait=0.1) is True


def test_a_slow_probe_can_keep_the_record(monkeypatch):
    """While waiting on a launch that holds the mutex, a slow answer means busy,
    not dead: the record must survive it, or every later probe finds nothing
    and a second copy starts after all."""
    from ebook_audiobook.desktop import runtime

    runtime.write(59999)

    class Refused:
        def open(self, *a, **k):
            raise OSError("timed out")

    monkeypatch.setattr(runtime, "_no_proxy_opener", lambda: Refused())
    assert runtime.probe(forget=False) is None
    assert runtime.read() is not None
    assert runtime.probe() is None
    assert runtime.read() is None


def test_the_probe_ignores_the_system_proxy():
    from ebook_audiobook.desktop import runtime
    import urllib.request

    opener = runtime._no_proxy_opener()
    assert not any(isinstance(h, urllib.request.ProxyHandler) and h.proxies
                   for h in opener.handlers)


def test_a_second_launch_waits_for_the_first_instead_of_racing_it(monkeypatch):
    from ebook_audiobook import cli
    from ebook_audiobook.desktop import runtime
    from ebook_audiobook.web import server

    answers = iter([None, None, "http://127.0.0.1:5005"])
    monkeypatch.setattr(runtime, "probe", lambda timeout=1.5, forget=True: next(answers))
    monkeypatch.setattr(runtime, "claim_launch", lambda wait=0.0: False)
    opened, served = [], []
    monkeypatch.setattr(server, "open_window", lambda url: opened.append(url))
    monkeypatch.setattr(server, "serve", lambda **k: served.append(k))

    class Args:
        host = port = None
        no_browser = False
        no_tray = False

    assert cli.cmd_web(Args()) == 0
    assert opened == ["http://127.0.0.1:5005"] and served == []


def test_a_second_launch_takes_over_when_the_first_exits(monkeypatch):
    from ebook_audiobook import cli
    from ebook_audiobook.desktop import runtime
    from ebook_audiobook.web import server

    monkeypatch.setattr(runtime, "probe", lambda timeout=1.5, forget=True: None)
    claims = iter([False, False, True])  # the owner exits on the second wait
    monkeypatch.setattr(runtime, "claim_launch", lambda wait=0.0: next(claims))
    served = []
    monkeypatch.setattr(server, "open_window", lambda url: pytest.fail("no window to open"))
    monkeypatch.setattr(server, "serve", lambda **k: served.append(k))

    class Args:
        host = port = None
        no_browser = False
        no_tray = True

    assert cli.cmd_web(Args()) == 0
    assert len(served) == 1


# --- keep awake ----------------------------------------------------------------

def test_keep_awake_holds_off_sleep_and_lets_go(monkeypatch):
    from ebook_audiobook import power

    calls = []

    class Kernel32:
        class SetThreadExecutionState:
            restype = argtypes = None

            def __new__(cls, flags):
                calls.append(flags)
                return 0x80000000  # the previous state: success

    class Windll:
        kernel32 = Kernel32

    monkeypatch.setattr(power.os, "name", "nt")
    monkeypatch.setattr(power.ctypes, "windll", Windll, raising=False)
    with power.keep_awake():
        assert calls == [power._ES_CONTINUOUS | power._ES_SYSTEM_REQUIRED]
    assert calls[-1] == power._ES_CONTINUOUS


def test_keep_awake_releases_even_when_the_render_fails(monkeypatch):
    from ebook_audiobook import power

    calls = []

    class Kernel32:
        class SetThreadExecutionState:
            restype = argtypes = None

            def __new__(cls, flags):
                calls.append(flags)
                return 1

    class Windll:
        kernel32 = Kernel32

    monkeypatch.setattr(power.os, "name", "nt")
    monkeypatch.setattr(power.ctypes, "windll", Windll, raising=False)
    with pytest.raises(RuntimeError):
        with power.keep_awake():
            raise RuntimeError("render failed")
    assert calls[-1] == power._ES_CONTINUOUS


def test_keep_awake_is_a_no_op_elsewhere(monkeypatch):
    from ebook_audiobook import power

    monkeypatch.setattr(power.os, "name", "posix")
    with power.keep_awake():
        pass


# --- the in-app update -------------------------------------------------------
#
# On Windows the app can't be running while pip replaces it, so "Install"
# starts the installer in a window of its own and quits; that window waits for
# the app to go, runs the installer with -Update, and starts the app again.

import base64
import sys

from ebook_audiobook import update


class _Popen:
    """Records what would have been started, and optionally refuses it."""

    def __init__(self, refuse: list[BaseException] | None = None):
        self.calls: list[dict] = []
        self.refuse = list(refuse or [])

    def __call__(self, cmd, **kwargs):
        self.calls.append({"cmd": cmd, **kwargs})
        if self.refuse:
            raise self.refuse.pop(0)
        return object()

    def script(self, i: int = -1) -> str:
        cmd = self.calls[i]["cmd"]
        return base64.b64decode(cmd[cmd.index("-EncodedCommand") + 1]).decode("utf-16-le")


@pytest.fixture
def on_windows(monkeypatch, tmp_path):
    """sys.platform says win32, and the app runs from an installer-made venv."""
    monkeypatch.setattr(sys, "platform", "win32")
    install = tmp_path / "Programs" / "ebook-audiobook"
    (install / "bin").mkdir(parents=True)
    (install / "bin" / "ebook-audiobook.cmd").write_text("@echo off\n")
    monkeypatch.setattr(sys, "prefix", str(install / "venv"))
    popen = _Popen()
    monkeypatch.setattr(update.subprocess, "Popen", popen)
    monkeypatch.setattr(update, "_handoff_started", False)

    def no_run(*a, **k):
        raise AssertionError("the installer must not run beside the app on Windows")

    monkeypatch.setattr(update.subprocess, "run", no_run)
    return {"install": install, "popen": popen}


def test_windows_installs_after_the_app_has_quit(on_windows):
    assert update.closes_to_install() is True
    update.start_windows_update(lang="fr")
    popen = on_windows["popen"]
    call = popen.calls[0]
    assert call["cmd"][0].lower().endswith("powershell.exe") or call["cmd"][0] == "powershell"
    # Not the app's working directory, which may be inside the venv an
    # update rebuilds.
    assert call["cwd"] == str(on_windows["install"])
    # Its own console window, so it neither pops up over the app from nowhere
    # nor dies with it.
    assert call["creationflags"] & update.CREATE_NEW_CONSOLE
    script = popen.script()
    # Waits for this very process to exit before anything is replaced...
    assert f"Get-Process -Id {os.getpid()}" in script
    assert script.index("WaitForExit") < script.index("[scriptblock]::Create")
    # ...runs the official installer, updating only, in the app's language,
    # into the install that is actually running...
    assert update.INSTALL_PS1 in script
    assert "-Update" in script and "-Yes" not in script
    assert "-Lang 'fr'" in script
    assert f"-InstallDir '{on_windows['install']}'" in script
    # ...and starts the app again from its Start Menu target.
    assert str(on_windows["install"] / "venv" / "Scripts" / "ebook-audiobook-gui.exe") in script
    assert script.index("[scriptblock]::Create") < script.index("Start-Process")


def test_the_update_window_is_not_mistaken_for_the_app(on_windows):
    """install.ps1 closes any process whose command line names the venv. The
    script travels encoded, so this window's own command line doesn't."""
    update.start_windows_update()
    cmd = " ".join(on_windows["popen"].calls[0]["cmd"])
    assert str(on_windows["install"]) not in cmd
    assert "venv" not in cmd


def test_breaking_away_from_the_launchers_job_is_optional(on_windows, monkeypatch):
    """The launcher .exe may hold the app in a job object that dies with it.
    Leaving it is tried first; a job that forbids it must not stop the update."""
    popen = _Popen(refuse=[PermissionError(5, "Access is denied")])
    monkeypatch.setattr(update.subprocess, "Popen", popen)
    assert update.start_windows_update() is True
    assert len(popen.calls) == 2
    assert popen.calls[0]["creationflags"] & update.CREATE_BREAKAWAY_FROM_JOB
    assert not popen.calls[1]["creationflags"] & update.CREATE_BREAKAWAY_FROM_JOB


def test_an_update_window_that_cannot_start_is_an_error(on_windows, monkeypatch):
    monkeypatch.setattr(update.subprocess, "Popen",
                        _Popen(refuse=[OSError(1), OSError(1)]))
    with pytest.raises(update.UpdateError):
        update.start_windows_update()
    monkeypatch.setattr(update.subprocess, "Popen", _Popen(refuse=[FileNotFoundError()]))
    with pytest.raises(update.UpdateError, match="PowerShell"):
        update.start_windows_update()
    # A failed start doesn't count as the one allowed: trying again works.
    monkeypatch.setattr(update.subprocess, "Popen", _Popen())
    assert update.start_windows_update() is True


def test_only_one_installer_is_ever_started(on_windows):
    """A second "Install" in the moment before the app quits (another tab, a
    double click) must not put two installers on one venv."""
    assert update.start_windows_update() is True
    assert update.start_windows_update() is False
    assert len(on_windows["popen"].calls) == 1


def test_the_command_line_update_hands_off_too(on_windows):
    """`ebook-audiobook update --apply` has numpy loaded as well, so it can't
    stay either. It doesn't restart the app it never started. --yes, being
    unattended, only updates: -Yes would install whatever a new user is
    offered; without it the new window asks, as the installer always has."""
    assert update.apply_update(yes=True) == 0
    script = on_windows["popen"].script()
    assert "-Update" in script and "-Yes" not in script
    assert "Start-Process" not in script
    update._handoff_started = False  # a new command, a new process
    assert update.apply_update(yes=False) == 0
    script = on_windows["popen"].script()
    assert "-Update" not in script and "-Yes" not in script


def test_a_source_checkout_updates_the_default_install(on_windows, monkeypatch, tmp_path):
    """A venv the installer didn't make is not handed to -InstallDir."""
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "checkout" / ".venv"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    install_dir, gui = update.installed_layout()
    assert install_dir == ""
    assert gui == str(tmp_path / "Local" / "ebook-audiobook" / "venv" / "Scripts"
                      / "ebook-audiobook-gui.exe")
    update.start_windows_update()
    assert "-InstallDir" not in on_windows["popen"].script()


def test_elsewhere_the_installer_still_runs_in_the_background(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert update.closes_to_install() is False


@pytest.mark.parametrize("text", [
    "plain", "O'Brien", "l’application n’a pas", "‘quoted’ ‚low‛", "a\nb", "$env:X `n",
])
def test_every_string_survives_powershell_quoting(text):
    """French puts a typographic apostrophe in every elision, and PowerShell
    ends a single-quoted string on one of those too."""
    lit = update._ps_literal(text)
    assert lit[0] == lit[-1] == "'"
    body = lit[1:-1]
    # Undo PowerShell's rule: a quote character doubled stands for itself.
    out, i = [], 0
    quotes = "'‘’‚‛"
    while i < len(body):
        ch = body[i]
        if ch in quotes:
            assert i + 1 < len(body) and body[i + 1] == ch, f"a lone quote ends the string early: {lit}"
            i += 2
        else:
            i += 1
        out.append(ch)
    assert "".join(out) == text


def test_a_failed_update_still_reopens_the_app_and_says_how_to_retry(on_windows):
    update.start_windows_update()
    script = on_windows["popen"].script()
    tail = script[script.index("} catch {"):]
    assert "Start-Process" in tail, "the app comes back whether or not the installer succeeded"
    assert "Read-Host" in tail, "a failure keeps the window open to be read"
    assert update.install_command() in script


def test_the_window_closes_a_copy_that_will_not_quit(on_windows):
    script = update.windows_update_script(wait_for=4242)
    assert f"WaitForExit({update.APP_EXIT_TIMEOUT_SECONDS * 1000})" in script
    assert "$app.Kill()" in script
    # The handle is taken before waiting, and a process younger than this
    # window can't be the app that started it: a recycled process id is
    # never the one waited on, or killed.
    assert script.index("$app.Handle") < script.index("WaitForExit")
    assert "$app.StartTime -gt (Get-Process -Id $PID).StartTime" in script
    assert script.index("StartTime") < script.index("WaitForExit")


def test_the_installer_is_read_as_utf8():
    """GitHub serves it with no charset; read as text, Windows PowerShell
    would take it for Latin-1 and garble the French and Spanish."""
    script = update.windows_update_script(wait_for=None)
    assert "[Text.Encoding]::UTF8.GetString(" in script
    assert "RawContentStream" in script


# --- the route ---------------------------------------------------------------

def _offer(monkeypatch):
    from ebook_audiobook.web import update_watch

    s = app_settings.load_settings()
    s.check_for_updates = True
    app_settings.save_settings(s)
    with update_watch._state.lock:
        update_watch._state.release = update.Release(version="99.0.0", tag="v99.0.0", url="")


@pytest.fixture
def windows_app(monkeypatch):
    from ebook_audiobook.web import create_app
    from ebook_audiobook.web import app as app_module
    from ebook_audiobook.web.runner import Runner

    monkeypatch.setattr(app_module, "runner", Runner())
    monkeypatch.setattr(update, "closes_to_install", lambda: True)
    started, quits = [], []
    monkeypatch.setattr(update, "start_windows_update", lambda **kw: started.append(kw) or True)
    monkeypatch.setattr(update, "apply_update", lambda *a, **k: pytest.fail("ran in place"))
    app = create_app()
    app.config["EBAB_SHUTDOWN"] = lambda: quits.append("quit")
    return {"app": app, "started": started, "quits": quits}


def _wait_for(pred, timeout=3.0):
    import time

    deadline = time.monotonic() + timeout
    while not pred() and time.monotonic() < deadline:
        time.sleep(0.02)


def test_install_on_windows_starts_the_installer_and_quits(windows_app, monkeypatch):
    _offer(monkeypatch)
    r = windows_app["app"].test_client().post("/updates/apply",
                                              headers={"Accept-Language": "fr"})
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "closing": True}
    assert len(windows_app["started"]) == 1
    _wait_for(lambda: windows_app["quits"])
    assert windows_app["quits"] == ["quit"]


def test_the_confirmation_is_told_the_app_will_close(windows_app, monkeypatch):
    _offer(monkeypatch)
    d = windows_app["app"].test_client().get("/api/updates/status").get_json()
    assert d["closes_to_install"] is True


def test_install_on_windows_does_not_quit_if_the_installer_did_not_start(windows_app, monkeypatch):
    _offer(monkeypatch)

    def refuse(**kw):
        raise update.UpdateError("Couldn't start the installer: nope")

    monkeypatch.setattr(update, "start_windows_update", refuse)
    r = windows_app["app"].test_client().post("/updates/apply")
    assert r.status_code == 500
    assert "nope" in r.get_json()["error"]
    _wait_for(lambda: windows_app["quits"], timeout=0.5)
    assert windows_app["quits"] == []


def test_a_second_install_while_the_first_is_starting_is_refused(windows_app, monkeypatch):
    _offer(monkeypatch)
    monkeypatch.setattr(update, "start_windows_update", lambda **kw: False)
    r = windows_app["app"].test_client().post("/updates/apply")
    assert r.status_code == 409
    _wait_for(lambda: windows_app["quits"], timeout=0.5)
    assert windows_app["quits"] == []


def test_install_on_windows_needs_an_app_that_can_quit(windows_app, monkeypatch):
    """Under a dev server nothing could quit, and a window that waits for the
    app would end up closing it instead."""
    _offer(monkeypatch)
    del windows_app["app"].config["EBAB_SHUTDOWN"]
    r = windows_app["app"].test_client().post("/updates/apply")
    assert r.status_code == 501
    assert windows_app["started"] == []


def test_install_on_windows_keeps_every_gate(windows_app, monkeypatch):
    """Nothing about the Windows route relaxes the checks that stop another
    site, or a render in progress, from reinstalling the app."""
    from ebook_audiobook.web import app as app_module

    client = windows_app["app"].test_client()
    assert client.post("/updates/apply").status_code == 409  # checks off
    _offer(monkeypatch)
    assert client.post("/updates/apply",
                       headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    app_module.runner.current = "job1:render"
    assert client.post("/updates/apply").status_code == 409
    assert windows_app["started"] == [] and windows_app["quits"] == []


# --- the in-app update, for a copy the setup.exe installed -------------------------

@pytest.fixture
def setup_install(on_windows, monkeypatch, tmp_path):
    """The app runs from <app>\\python, with Inno Setup's uninstaller beside it."""
    app = tmp_path / "Programs" / "ebook-audiobook-setup"
    (app / "python").mkdir(parents=True)
    (app / "unins000.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "prefix", str(app / "python"))
    return {**on_windows, "app": app}


def test_a_setup_install_is_recognised(setup_install):
    assert update.installed_by_setup() is True


def test_a_venv_install_is_not_a_setup_install(on_windows):
    assert update.installed_by_setup() is False


def test_a_setup_install_updates_with_the_next_setup_exe(setup_install):
    """Not install.ps1, which knows nothing of <app>\\python: the next setup.exe
    recognises this copy by its AppId and installs over it."""
    update.start_windows_update(lang="fr")
    popen = setup_install["popen"]
    assert popen.calls[0]["cwd"] == str(setup_install["app"])
    script = popen.script()
    assert f"Get-Process -Id {os.getpid()}" in script
    assert update.SETUP_EXE in script and update.INSTALL_PS1 not in script
    assert "curl.exe" in script and "'/SILENT'" in script
    assert script.index("WaitForExit") < script.index("curl.exe") < script.index("Start-Process -FilePath $setup")
    # Started again the way its shortcut starts it.
    pythonw = str(setup_install["app"] / "python" / "pythonw.exe")
    assert pythonw in script and "-ArgumentList '-m','ebook_audiobook','--gui'" in script
    assert script.index("Start-Process -FilePath $setup") < script.index(pythonw)


def test_a_setup_install_is_told_how_to_update_by_hand(setup_install):
    assert "ebook-audiobook-setup.exe" in update.install_command()
