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

    monkeypatch.setattr(app_mod, "_windows_drives", lambda: ["C:\\", "E:\\"])
    root = Path(tmp_path.anchor)  # "/" here, standing in for C:\
    data = client.get("/api/fs", query_string={"path": str(root)}).get_json()
    names = [d["name"] for d in data["dirs"]]
    assert names[:2] == ["C:\\", "E:\\"]
    assert data["parent"] is None

    # Anywhere below a root, nothing changes.
    data = client.get("/api/fs", query_string={"path": str(tmp_path)}).get_json()
    assert "E:\\" not in [d["name"] for d in data["dirs"]]


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
