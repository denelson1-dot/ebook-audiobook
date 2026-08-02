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


def test_apply_update_runs_the_installer(monkeypatch):
    seen = {}

    class Done:
        returncode = 0

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return Done()

    monkeypatch.setattr(update.subprocess, "run", fake_run)
    assert update.apply_update(yes=True) == 0
    joined = " ".join(seen["cmd"])
    assert "install-macos-linux.sh" in joined or "install-windows.ps1" in joined


def test_platform_hint_mentions_apple_silicon(monkeypatch):
    monkeypatch.setattr(update.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(update.platform, "machine", lambda: "arm64")
    assert "Apple Silicon" in update.platform_hint()
    assert "Metal" in update.platform_hint()


def test_platform_hint_is_honest_about_intel_macs(monkeypatch):
    monkeypatch.setattr(update.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(update.platform, "machine", lambda: "x86_64")
    assert "CPU only" in update.platform_hint()
