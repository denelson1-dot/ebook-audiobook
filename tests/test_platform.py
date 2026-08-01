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
