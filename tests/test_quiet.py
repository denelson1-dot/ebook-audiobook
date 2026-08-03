"""Keeping the launch output clean.

Anything printed on the terminal that started the app is read as ours. Two
separate sources of other people's noise have to stay suppressed: the engine
libraries' import-time warnings, and the browser we spawn at startup.

The browser half only applies to the fallback path now. When a Chromium-family
browser is found, launcher.open_app_window spawns it itself and redirects the
child's streams directly; detached_std_fds is what covers webbrowser.open, which
hands us no control over the Popen.
"""

from __future__ import annotations

import os
import subprocess
import sys
import warnings

from ebook_audiobook import quiet
from ebook_audiobook.desktop.launcher import detached_std_fds

PERTH_WARNING = (
    "pkg_resources is deprecated as an API. See "
    "https://setuptools.pypa.io/en/latest/pkg_resources.html"
)


# --- engine import noise -----------------------------------------------------

def test_apply_silences_the_perth_warning():
    """perth raises this as a UserWarning, so a category filter would miss it."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.resetwarnings()
        quiet.apply()
        warnings.warn(PERTH_WARNING, UserWarning)
    assert caught == []


def test_apply_is_a_no_op_when_verbose(monkeypatch):
    """EBAB_VERBOSE=1 has to restore everything, for debugging the engine."""
    monkeypatch.setattr(quiet, "VERBOSE", True)
    with warnings.catch_warnings(record=True) as caught:
        warnings.resetwarnings()
        quiet.apply()
        warnings.warn(PERTH_WARNING, UserWarning)
    assert len(caught) == 1


def test_checks_registers_the_filters_before_importing_the_engine():
    """The regression this module exists for.

    check_tts_engine imports chatterbox to report whether the engine is present,
    which pulls in perth. That happens at startup without the TTS adapter ever
    being imported, so filters registered in the adapter came too late.
    """
    src = (
        subprocess.run(
            [sys.executable, "-c",
             "import sys; import ebook_audiobook.checks; "
             "print('ebook_audiobook.quiet' in sys.modules)"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    )
    assert src == "True"


# --- browser launch noise ----------------------------------------------------

def test_detached_std_fds_swallows_a_child_process():
    """Chrome writes GPU and updater errors to the stderr it inherits from us."""
    with detached_std_fds():
        done = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.stderr.write('CHILD NOISE'); sys.stderr.flush()"],
            close_fds=True,
        )
    assert done.returncode == 0  # it ran; its output went to devnull


def test_detached_std_fds_restores_our_own_descriptors(capfd):
    before = (os.dup(1), os.dup(2))
    try:
        with detached_std_fds():
            pass
        print("visible again", flush=True)
        assert "visible again" in capfd.readouterr().out
    finally:
        for fd in before:
            os.close(fd)


def test_detached_std_fds_restores_even_when_the_body_raises():
    try:
        with detached_std_fds():
            raise RuntimeError("browser blew up")
    except RuntimeError:
        pass
    print("still here", flush=True)  # would hit a closed fd if restore leaked
