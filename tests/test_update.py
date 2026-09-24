"""The version check.

Two things are being defended. The obvious one is that version comparison is
numeric, so 1.10.0 is correctly newer than 1.9.0 — string comparison gets that
backwards and would tell users to downgrade.

The less obvious one is that this module is the only part of the app that opens
a network connection for its own purposes. The product promises nothing leaves
the machine, so a check has to be something the user asked for, never something
that happens on its own.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from ebook_audiobook import settings, update


# --- version comparison -------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("1.2.3", (1, 2, 3)),
    ("v1.2.3", (1, 2, 3)),
    ("V1.2.3", (1, 2, 3)),
    ("1.2.3.dev0", (1, 2, 3)),
    ("1.2.3+local", (1, 2, 3)),
    ("1.2", (1, 2)),
    ("", (0,)),
    ("garbage", (0,)),
])
def test_parse_version(text, expected):
    assert update.parse_version(text) == expected


@pytest.mark.parametrize("candidate,current", [
    ("1.1.3", "1.1.2"),
    ("1.2.0", "1.1.9"),
    ("2.0.0", "1.9.9"),
    ("1.10.0", "1.9.0"),   # numeric, not lexicographic
])
def test_is_newer(candidate, current):
    assert update.is_newer(candidate, current)


@pytest.mark.parametrize("candidate,current", [
    ("1.1.2", "1.1.2"),
    ("1.1.1", "1.1.2"),
    ("1.9.0", "1.10.0"),
])
def test_is_not_newer(candidate, current):
    assert not update.is_newer(candidate, current)


# --- the network call ---------------------------------------------------------

def _fake_response(payload: dict):
    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return R(json.dumps(payload).encode())


def test_check_reads_the_tag(monkeypatch):
    monkeypatch.setattr(update.urllib.request, "urlopen",
                        lambda *a, **k: _fake_response(
                            {"tag_name": "v9.9.9", "html_url": "https://example/rel"}))
    release = update.check()
    assert release.version == "9.9.9"
    assert release.tag == "v9.9.9"
    assert release.notes_url == "https://example/rel"


def test_status_reports_an_available_update(monkeypatch):
    monkeypatch.setattr(update, "check",
                        lambda **k: update.Release("9.9.9", "v9.9.9", "https://example"))
    available, release, message = update.status()
    assert available and release.version == "9.9.9"
    assert "9.9.9 is available" in message


def test_status_reports_being_up_to_date(monkeypatch):
    monkeypatch.setattr(update, "check",
                        lambda **k: update.Release(update.current_version(), "v", ""))
    available, _release, message = update.status()
    assert not available
    assert "latest version" in message


def test_an_unreleased_build_is_not_called_the_latest(monkeypatch):
    """A checkout mid-release is ahead of the newest tag, not level with it."""
    monkeypatch.setattr(update, "current_version", lambda: "1.1.3")
    monkeypatch.setattr(update, "check",
                        lambda **k: update.Release("1.1.2", "v1.1.2", ""))
    available, _release, message = update.status()
    assert not available
    assert "ahead of the latest release" in message
    assert "1.1.2" in message and "1.1.3" in message


def test_being_offline_is_explained_not_raised(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(update.urllib.request, "urlopen", boom)
    available, release, message = update.status()
    assert not available and release is None
    assert "Couldn't reach GitHub" in message


def test_rate_limiting_says_so(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.HTTPError("url", 403, "rate limited", {}, None)

    monkeypatch.setattr(update.urllib.request, "urlopen", boom)
    with pytest.raises(update.UpdateError, match="rate-limited"):
        update.check()


def test_no_releases_yet_says_so(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.HTTPError("url", 404, "not found", {}, None)

    monkeypatch.setattr(update.urllib.request, "urlopen", boom)
    with pytest.raises(update.UpdateError, match="No published releases"):
        update.check()


def test_a_reply_without_a_tag_is_an_error(monkeypatch):
    monkeypatch.setattr(update.urllib.request, "urlopen",
                        lambda *a, **k: _fake_response({"html_url": "x"}))
    with pytest.raises(update.UpdateError, match="no release tag"):
        update.check()


# --- consent ------------------------------------------------------------------

def test_update_checks_are_off_by_default():
    """The app's promise is that nothing leaves the machine unless asked."""
    assert settings.Settings().check_for_updates is False


def test_the_setting_round_trips():
    saved = settings.save_settings(settings.Settings(check_for_updates=True))
    assert saved.check_for_updates is True
    assert settings.load_settings().check_for_updates is True


def test_importing_the_module_makes_no_network_call(monkeypatch):
    """Import time must be silent — no start-up poll, ever."""
    called = []
    monkeypatch.setattr(update.urllib.request, "urlopen",
                        lambda *a, **k: called.append(1))
    import importlib

    importlib.reload(update)
    assert called == []


# --- the upgrade path ---------------------------------------------------------

def test_install_command_is_the_official_one():
    """An upgrade route that isn't the install route is one nobody tests."""
    cmd = update.install_command()
    assert "install-macos-linux.sh" in cmd or "install-windows.ps1" in cmd
    assert update.REPO in cmd


def _capture_run(monkeypatch, platform="linux"):
    """subprocess.run that records the command instead of running it. The
    platform is pinned: on Windows, apply_update starts a real PowerShell
    window instead (see test_windows.py)."""
    import sys

    monkeypatch.setattr(sys, "platform", platform)
    seen = {}

    class Done:
        returncode = 0

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return Done()

    monkeypatch.setattr(update.subprocess, "run", fake_run)
    return seen


def test_apply_update_runs_the_installer(monkeypatch):
    seen = _capture_run(monkeypatch)
    assert update.apply_update(yes=True) == 0
    assert "install-macos-linux.sh" in " ".join(seen["cmd"])


@pytest.mark.parametrize("kwargs,flag", [
    ({"update_only": True}, "--update"),
    ({"yes": True}, "--yes"),
    ({"yes": True, "update_only": True}, "--update"),
    ({}, None),
])
def test_the_installer_gets_the_right_flag(monkeypatch, kwargs, flag):
    """The app's own Install asks for --update, never --yes: a confirmed update
    is not consent to a Homebrew cask, a sudo prompt or a speech engine the
    install was set up without. The CLI's --yes keeps meaning --yes."""
    seen = _capture_run(monkeypatch)
    update.apply_update(**kwargs)
    script = seen["cmd"][-1]
    if flag is None:
        assert "--yes" not in script and "--update" not in script
    else:
        assert script.endswith(f"| bash -s -- {flag}")
        assert ({"--yes", "--update"} - {flag}).pop() not in script


_BASH = pytest.mark.skipif(__import__("sys").platform == "win32", reason="needs bash")
# The real one: _capture_run replaces subprocess.run for the module under test,
# which is the same module object these tests would otherwise call.
_run = __import__("subprocess").run


def _pipeline_with(monkeypatch, source: str, **kwargs):
    """apply_update's own shell command, with curl swapped for *source*."""
    seen = _capture_run(monkeypatch)
    update.apply_update(**kwargs)
    script = seen["cmd"][-1]
    download = f"curl -fsSL {update.INSTALL_SH}"
    assert download in script
    return script.replace(download, source)


@_BASH
@pytest.mark.parametrize("kwargs,expected", [
    ({"update_only": True}, "args: --update"),
    ({"yes": True}, "args: --yes"),
    ({}, "args: "),
])
def test_the_flag_reaches_the_installer(monkeypatch, tmp_path, kwargs, expected):
    """`curl … | bash -- --yes` makes bash run a file named "--yes" and exit
    127, which is what every in-app update on macOS and Linux did until -s."""
    fake = tmp_path / "installer.sh"
    fake.write_text('echo "args: $*"\n')
    script = _pipeline_with(monkeypatch, f"cat {fake}", **kwargs)
    out = _run(["bash", "-c", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == expected.strip()


@_BASH
def test_a_failed_download_is_a_failed_update(monkeypatch):
    """Without pipefail, bash reads the empty script a failed curl leaves it,
    exits 0, and the banner says the update is ready."""
    script = _pipeline_with(monkeypatch, "false", update_only=True)
    assert _run(["bash", "-c", script]).returncode != 0


# --- install.sh --update ------------------------------------------------------

from pathlib import Path  # noqa: E402

INSTALL_SH = Path(__file__).resolve().parents[1] / "install.sh"


def _flavour(venv: Path) -> str:
    import subprocess

    text = INSTALL_SH.read_text(encoding="utf-8")
    block = text[text.index("# --- BEGIN installed-torch-flavour"):
                 text.index("# --- END installed-torch-flavour")]
    out = subprocess.run(["bash", "-c", f'{block}\ninstalled_torch_flavour "$1"', "_", str(venv)],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


@_BASH
@pytest.mark.parametrize("dist_info,expected", [
    (None, ""),                               # no engine: --update adds none
    ("torch-2.9.1+cpu.dist-info", "cpu"),     # stays CPU
    ("torch-2.9.1+cu128.dist-info", "gpu"),   # stays CUDA; the card picks 12.6/12.8
    ("torch-2.9.1+cu126.dist-info", "gpu"),
    ("torch-2.9.1+rocm6.4.dist-info", "rocm"),
    ("torch-2.9.1.dist-info", "any"),         # the one macOS build: nothing to keep
])
def test_update_keeps_the_speech_engine_it_finds(tmp_path, dist_info, expected):
    site = tmp_path / "venv" / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    (site / "torchaudio-2.9.1.dist-info").mkdir()  # must not be taken for torch
    if dist_info:
        (site / dist_info).mkdir()
    assert _flavour(tmp_path / "venv") == expected


@_BASH
def test_update_with_nothing_installed_installs_nothing(tmp_path):
    """--update is never a first install: with no venv it stops before looking
    for Python, and leaves no trace."""
    import subprocess

    target = tmp_path / "app"
    env = {"HOME": str(tmp_path / "home"), "PATH": "/usr/bin:/bin", "LANG": "C"}
    out = subprocess.run(["bash", str(INSTALL_SH), "--update", "--dir", str(target)],
                         capture_output=True, text=True, env=env)
    assert out.returncode == 1
    assert "nothing to update" in out.stderr
    assert not target.exists()
    assert not (tmp_path / "home").exists()


@_BASH
def test_update_is_documented_in_the_installer_help():
    import subprocess

    out = subprocess.run(["bash", str(INSTALL_SH), "--help"], capture_output=True, text=True)
    assert "--update" in out.stdout


def test_platform_hint_mentions_apple_silicon(monkeypatch):
    monkeypatch.setattr(update.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(update.platform, "machine", lambda: "arm64")
    assert "Apple Silicon" in update.platform_hint()
    assert "Metal" in update.platform_hint()


def test_platform_hint_is_honest_about_intel_macs(monkeypatch):
    monkeypatch.setattr(update.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(update.platform, "machine", lambda: "x86_64")
    assert "CPU only" in update.platform_hint()
