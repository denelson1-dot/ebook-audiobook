"""Cross-platform behaviour: data locations, tool discovery, and name safety.

These are the things that silently differ between Linux, macOS, and Windows and
that we therefore cannot verify just by running the app here. Where a real
platform check isn't possible in-process, the platform is simulated by patching
``sys.platform`` and the environment, so at least the *logic* is pinned on every
OS the CI matrix runs.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ebook_audiobook import config, platform_dirs, tools
from ebook_audiobook.jobs.models import Book
from ebook_audiobook.pipeline import layout


# --- per-user data directories ----------------------------------------------

def test_user_data_dir_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\Someone\AppData\Local")
    got = platform_dirs.user_data_dir()
    assert got.name == "ebook-audiobook"
    assert "AppData" in str(got) and "Local" in str(got)


def test_user_data_dir_macos(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    got = platform_dirs.user_data_dir()
    assert got == Path.home() / "Library" / "Application Support" / "ebook-audiobook"


def test_user_data_dir_linux_respects_xdg(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "/custom/share")
    assert platform_dirs.user_data_dir() == Path("/custom/share/ebook-audiobook")


def test_user_data_dir_linux_default(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert platform_dirs.user_data_dir() == Path.home() / ".local/share/ebook-audiobook"


# --- data root resolution ----------------------------------------------------

def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("EBAB_DATA_ROOT", str(tmp_path / "elsewhere"))
    assert config.data_root() == (tmp_path / "elsewhere").resolve()


def test_existing_repo_local_data_is_preserved(monkeypatch, tmp_path):
    """An upgrade must never orphan the library of someone who ran from source.

    This is the regression guard for existing installs: if a checkout already has
    a local-data/ directory, that stays the data root even though fresh installs
    now use the per-user OS location.
    """
    fake_repo = tmp_path / "checkout"
    (fake_repo / "ebook_audiobook").mkdir(parents=True)
    (fake_repo / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (fake_repo / "local-data").mkdir()

    monkeypatch.delenv("EBAB_DATA_ROOT", raising=False)
    monkeypatch.setattr(config, "REPO_ROOT", fake_repo)
    assert config.data_root() == fake_repo / "local-data"


def test_fresh_checkout_without_local_data_uses_user_dir(monkeypatch, tmp_path):
    fake_repo = tmp_path / "checkout"
    (fake_repo / "ebook_audiobook").mkdir(parents=True)
    (fake_repo / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    # deliberately no local-data/

    monkeypatch.delenv("EBAB_DATA_ROOT", raising=False)
    monkeypatch.setattr(config, "REPO_ROOT", fake_repo)
    assert config.data_root() == platform_dirs.user_data_dir()


def test_installed_copy_uses_user_dir_not_site_packages(monkeypatch, tmp_path):
    """Installed in site-packages there is no pyproject.toml, so even a stray
    'local-data' directory beside the package must not be adopted."""
    site_packages = tmp_path / "site-packages"
    (site_packages / "ebook_audiobook").mkdir(parents=True)
    (site_packages / "local-data").mkdir()  # some other package's junk

    monkeypatch.delenv("EBAB_DATA_ROOT", raising=False)
    monkeypatch.setattr(config, "REPO_ROOT", site_packages)
    assert config.data_root() == platform_dirs.user_data_dir()


# --- external tool discovery -------------------------------------------------

def test_ffmpeg_prefers_system_over_bundled(monkeypatch):
    tools.reset_cache()
    monkeypatch.setattr(tools.shutil, "which",
                        lambda n: "/usr/bin/ffmpeg" if n == "ffmpeg" else None)
    assert tools.ffmpeg_path() == Path("/usr/bin/ffmpeg")
    assert tools.ffmpeg_is_bundled() is False
    tools.reset_cache()


def test_ffmpeg_falls_back_to_bundled(monkeypatch, tmp_path):
    tools.reset_cache()
    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(tools.shutil, "which", lambda n: None)
    monkeypatch.setattr(tools, "_bundled_ffmpeg", lambda: fake)
    assert tools.ffmpeg_path() == fake
    tools.reset_cache()


def test_require_ffmpeg_message_is_actionable(monkeypatch):
    tools.reset_cache()
    monkeypatch.setattr(tools.shutil, "which", lambda n: None)
    monkeypatch.setattr(tools, "_bundled_ffmpeg", lambda: None)
    with pytest.raises(tools.MissingToolError) as e:
        tools.require_ffmpeg()
    assert "ffmpeg" in str(e.value).lower()
    tools.reset_cache()


def test_calibre_found_outside_path_on_macos(monkeypatch, tmp_path):
    """macOS Calibre installs an .app bundle and puts nothing on PATH."""
    tools.reset_cache()
    bundle = tmp_path / "calibre.app/Contents/MacOS/ebook-convert"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("", encoding="utf-8")

    monkeypatch.delenv("EBAB_EBOOK_CONVERT", raising=False)
    monkeypatch.setattr(tools.shutil, "which", lambda n: None)
    monkeypatch.setattr(tools, "_calibre_candidates", lambda: [bundle])
    assert tools.ebook_convert_path() == bundle
    tools.reset_cache()


def test_calibre_env_override(monkeypatch, tmp_path):
    tools.reset_cache()
    exe = tmp_path / "my-ebook-convert"
    exe.write_text("", encoding="utf-8")
    monkeypatch.setenv("EBAB_EBOOK_CONVERT", str(exe))
    assert tools.ebook_convert_path() == exe
    tools.reset_cache()


def test_install_hints_exist_for_every_platform():
    for tool in ("calibre", "ffmpeg"):
        for plat in ("win32", "darwin", "linux"):
            assert tools._HINTS[tool][plat].strip()


def test_run_decodes_non_utf8_output_without_crashing():
    """A tool emitting bytes that aren't valid UTF-8 must not kill the render."""
    proc = tools.run([sys.executable, "-c",
                      r"import sys; sys.stdout.buffer.write(b'caf\xe9 \xff\xfe')"])
    assert proc.returncode == 0
    assert "caf" in proc.stdout  # undecodable bytes replaced, not raised


def test_run_does_not_hang_on_stdin_read():
    """stdin is DEVNULL, so a tool that reads it gets EOF instead of blocking."""
    proc = tools.run([sys.executable, "-c", "import sys; print(len(sys.stdin.read()))"],
                     timeout=15)
    assert proc.stdout.strip() == "0"


# --- path/name safety --------------------------------------------------------

@pytest.mark.parametrize("reserved", ["CON", "con", "PRN", "aux", "NUL", "COM1", "lpt9"])
def test_windows_reserved_names_are_escaped(reserved):
    """These names cannot exist as files on Windows, in any case, ever."""
    out = layout.sanitize_component(reserved)
    assert out.split(".")[0].lower() not in layout._WINDOWS_RESERVED
    assert out.lower().startswith(reserved.lower())


def test_reserved_name_with_extension_is_escaped():
    assert layout.sanitize_component("nul.txt").split(".")[0].lower() != "nul"


def test_long_titles_are_truncated():
    title = "The Exceedingly And Unnecessarily Protracted Chronicle Of A Very Long Book Title Indeed That Goes On"
    out = layout.sanitize_component(title)
    assert len(out) <= layout.MAX_COMPONENT_CHARS
    assert out.startswith("The Exceedingly")
    assert not out.endswith(" ")


def test_long_path_stays_within_windows_limit():
    """Author + series + title + filename must fit in Windows' 260-char MAX_PATH."""
    long = "Q" * 300
    book = Book(job_id="j", source_path="s", source_hash="h",
                title=long, author=long, series=long, series_index="3")
    p = layout.library_m4b_path(Path(r"C:/Users/Someone/Audiobooks"), book)
    assert len(str(p)) < 260, f"path too long for Windows: {len(str(p))}"


def test_illegal_characters_removed_for_all_platforms():
    out = layout.sanitize_component('Book: A "Story" <of> Sorts | Part*1?')
    for bad in '/\\*?:"<>|':
        assert bad not in out
    assert "Story" in out


def test_trailing_dot_stripped():
    """Windows silently drops trailing dots, so the path we write must not have one."""
    assert not layout.sanitize_component("Mr. Smith Jr.").endswith(".")


def test_empty_or_junk_title_falls_back():
    assert layout.sanitize_component("", "Untitled") == "Untitled"
    assert layout.sanitize_component('///:::', "Untitled") == "Untitled"


# --- launching without a console (Windows pythonw.exe) -----------------------

def test_console_setup_survives_absent_streams(monkeypatch):
    """Desktop/Start-Menu shortcuts run under pythonw.exe, where sys.stdout and
    sys.stderr are None. Anything that prints would otherwise die with
    AttributeError before the browser ever opened."""
    from ebook_audiobook.cli import _use_utf8_console

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    _use_utf8_console()

    assert sys.stdout is not None and sys.stderr is not None
    print("this must not raise")              # the actual failure mode
    print("nor this", file=sys.stderr)
    sys.stdout.write("nor a direct write")
    sys.stdout.flush()


def test_console_setup_is_idempotent():
    """It runs on every entry point; calling it twice must be harmless."""
    from ebook_audiobook.cli import _use_utf8_console

    _use_utf8_console()
    _use_utf8_console()
    print("still fine")


def test_gui_entry_point_is_exported():
    """pyproject registers this as a gui_script; a rename would silently break
    every desktop shortcut created by the installer."""
    from ebook_audiobook import cli

    assert callable(cli.main_gui)


# --- the interface must work the same on all three desktops -----------------

def test_the_ui_never_reaches_out_to_the_network():
    """The app's promise is that the update check is the only thing that talks
    to a server without being asked. A webfont in the stylesheet, or any other
    remote asset, quietly breaks that on every page load — and leaves the
    interface looking wrong on a machine with no connection."""
    import re
    from pathlib import Path

    web = Path(__file__).resolve().parent.parent / "ebook_audiobook" / "web"
    for f in list((web / "static").iterdir()) + list((web / "templates").iterdir()):
        if not f.is_file():
            continue
        found = re.findall(r'(?:https?:)?//[a-z0-9.-]+\.[a-z]{2,}', f.read_text("utf-8"), re.I)
        # A bare "//" in a JS comment or a doubled slash in a path is not a host.
        remote = [u for u in found if not u.startswith("//" + "/")]
        assert not remote, f"{f.name} references {remote}"


def test_fonts_come_from_the_operating_system():
    from pathlib import Path

    css = (Path(__file__).resolve().parent.parent / "ebook_audiobook" / "web"
           / "static" / "app.css").read_text("utf-8")
    assert "@font-face" not in css, "a bundled webfont would need shipping and licensing"
    assert "@import" not in css
    # Each stack has to name something that exists on every desktop.
    assert "Segoe UI" in css, "no Windows UI font in the stack"
    assert "-apple-system" in css or "system-ui" in css, "no macOS UI font in the stack"
    assert "Georgia" in css, "no serif fallback that Windows and Linux both have"


@pytest.mark.parametrize("platform, is_windows, expected", [
    ("darwin", False, "open"),
    ("win32", True, "explorer"),
    ("linux", False, "xdg-open"),
])
def test_reveal_uses_each_desktop_s_own_file_manager(platform, is_windows, expected,
                                                     monkeypatch, tmp_path):
    """One code path per desktop, and none of them may block: a file manager is a
    window that stays open, so waiting on it would hang the request."""
    import subprocess as sp

    from ebook_audiobook import tools

    calls = {}

    class FakePopen:
        def __init__(self, cmd, **kw):
            calls["cmd"], calls["kw"] = cmd, kw

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(tools, "IS_WINDOWS", is_windows)
    monkeypatch.setattr(tools.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(tools.shutil, "which", lambda n: f"/usr/bin/{n}")

    assert tools.reveal(tmp_path) is True
    assert expected in calls["cmd"][0]
    assert str(tmp_path) in calls["cmd"]
    # never inherits stdin, never blocks, never flashes a console
    assert calls["kw"]["stdin"] is sp.DEVNULL
    assert calls["kw"]["stdout"] is sp.DEVNULL


def test_reveal_gives_up_gracefully_on_a_desktop_without_xdg_open(monkeypatch, tmp_path):
    from ebook_audiobook import tools

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(tools, "IS_WINDOWS", False)
    monkeypatch.setattr(tools.shutil, "which", lambda n: None)
    assert tools.reveal(tmp_path) is False


def test_reveal_survives_an_operating_system_that_refuses(monkeypatch, tmp_path):
    from ebook_audiobook import tools

    def boom(*a, **k):
        raise OSError("no")

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(tools, "IS_WINDOWS", False)
    monkeypatch.setattr(tools.shutil, "which", lambda n: "/usr/bin/xdg-open")
    monkeypatch.setattr(tools.subprocess, "Popen", boom)
    assert tools.reveal(tmp_path) is False


def test_window_geometry_flags_are_the_same_everywhere():
    """--window-size and --window-position are Chromium flags, not OS calls, so
    the same two reach the browser on every platform. Wayland ignores the
    position (a client cannot place itself there); that is the compositor's
    call and not something for this code to work around."""
    from ebook_audiobook import settings as app_settings
    from ebook_audiobook.desktop import launcher

    s = app_settings.load_settings()
    s.window_geometry = {"x": 40, "y": 50, "width": 1280, "height": 860}
    app_settings.save_settings(s)
    for browser in ("/usr/bin/chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        cmd = launcher._command(browser, "http://127.0.0.1:5005/")
        assert "--window-size=1280,860" in cmd
        assert "--window-position=40,50" in cmd
