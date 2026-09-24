"""Installing the speech engine from inside the app.

pip is faked with the output a real one prints (``Downloading <file> (<size>)``
then ``Progress <done> of <total>``, from ``--progress-bar raw``), so the
parsing, the progress, the failure paths and cancelling are all exercised
without downloading gigabytes.
"""

from __future__ import annotations

import subprocess
import sys
import types

import pytest

from ebook_audiobook import engine_setup, torchbuild


@pytest.fixture(autouse=True)
def fresh_status():
    engine_setup._status = engine_setup.Status()
    yield
    engine_setup._status = engine_setup.Status()


# --- which build ---------------------------------------------------------------------

def _smi(monkeypatch, stdout, returncode=0):
    def run(cmd, **kw):
        assert cmd[0] == "nvidia-smi"
        return types.SimpleNamespace(returncode=returncode, stdout=stdout)
    monkeypatch.setattr(engine_setup.subprocess, "run", run)


def test_the_card_is_read_the_way_the_installers_read_it(monkeypatch):
    _smi(monkeypatch, "NVIDIA GeForce RTX 3050 4GB Laptop GPU, 8.6\n")
    assert engine_setup.probe_gpu() == ("nvidia", "NVIDIA GeForce RTX 3050 4GB Laptop GPU", [(8, 6)])


def test_a_card_with_a_comma_in_its_name_keeps_it(monkeypatch):
    _smi(monkeypatch, "NVIDIA Quadro, Special Edition, 7.5\n")
    assert engine_setup.probe_gpu() == ("nvidia", "NVIDIA Quadro, Special Edition", [(7, 5)])


def test_a_broken_nvidia_smi_means_no_nvidia_card(monkeypatch):
    _smi(monkeypatch, "NVIDIA-SMI has failed because it couldn't communicate with the driver", 9)
    monkeypatch.setattr(engine_setup.device, "amd_gpu_in_sysfs", lambda: False)
    assert engine_setup.probe_gpu() == ("", "", [])


def test_no_nvidia_smi_at_all(monkeypatch):
    def run(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    monkeypatch.setattr(engine_setup.subprocess, "run", run)
    monkeypatch.setattr(engine_setup.device, "amd_gpu_in_sysfs", lambda: False)
    assert engine_setup.probe_gpu() == ("", "", [])


def test_the_plan_is_torchbuilds_decision(monkeypatch):
    monkeypatch.setattr(engine_setup, "probe_gpu",
                        lambda: ("nvidia", "NVIDIA GeForce RTX 3050 4GB Laptop GPU", [(8, 6)]))
    monkeypatch.setattr(engine_setup, "_platform", lambda: "windows")
    assert engine_setup.plan().id == "cu128"
    monkeypatch.setattr(engine_setup, "probe_gpu", lambda: ("", "", []))
    assert engine_setup.plan().id == "cpu"


# --- running pip ----------------------------------------------------------------------

class _FakePip:
    """Popen stand-in: prints a script, exits with a code."""
    runs: list = []

    def __init__(self, lines, rc=0):
        self.lines, self.rc = lines, rc
        self.terminated = False

    def __call__(self, cmd, **kw):
        _FakePip.runs.append(cmd)
        self.stdout = iter(line + "\n" for line in self.lines)
        return self

    def wait(self):
        return self.rc

    def poll(self):
        return self.rc

    def terminate(self):
        self.terminated = True

    def kill(self):
        pass


def _fake(monkeypatch, lines, rc=0):
    _FakePip.runs = []
    fake = _FakePip(lines, rc)
    monkeypatch.setattr(engine_setup.subprocess, "Popen", fake)
    return fake


REAL_OUTPUT = [
    "Looking in indexes: https://download.pytorch.org/whl/cu128, https://pypi.org/simple",
    "Collecting torch==2.9.1",
    "Downloading torch-2.9.1+cu128-cp312-cp312-win_amd64.whl (2847.3 MB)",
    "Progress 0 of 2985635840",
    "Progress 1048576 of 2985635840",
    "Progress 2985635840 of 2985635840",
    "Installing collected packages: torch",
    "Successfully installed torch-2.9.1+cu128",
]


def test_an_install_runs_the_three_commands_with_raw_progress(monkeypatch):
    _fake(monkeypatch, REAL_OUTPUT)
    seen = []
    orig = engine_setup._update
    monkeypatch.setattr(engine_setup, "_update",
                        lambda **f: (seen.append(dict(f)), orig(**f)))
    before = engine_setup.generation
    engine_setup.install(build=torchbuild.BUILDS["cu128"])
    assert len(_FakePip.runs) == 3
    for cmd, pip_args in zip(_FakePip.runs, torchbuild.install_commands(torchbuild.BUILDS["cu128"])):
        assert cmd[1:3] == ["-m", "pip"] and cmd[-6:-4] == ["--progress-bar", "raw"]
        assert pip_args[0] in cmd                       # the command torchbuild asked for
    assert {"file": "torch-2.9.1+cu128-cp312-cp312-win_amd64.whl",
            "done_bytes": 0, "total_bytes": 0} in seen
    assert {"done_bytes": 1048576, "total_bytes": 2985635840} in seen
    st = engine_setup.status()
    assert st["state"] == "done" and st["build"] == torchbuild.BUILDS["cu128"].label
    assert engine_setup.generation == before + 1


def test_pip_runs_under_python_not_pythonw(monkeypatch, tmp_path):
    """pythonw has no stdout: the app runs under it, pip must not."""
    (tmp_path / "pythonw.exe").write_bytes(b"")
    (tmp_path / "python.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "pythonw.exe"))
    assert engine_setup._python() == str(tmp_path / "python.exe")


def test_a_failure_reports_pips_last_words(monkeypatch):
    _fake(monkeypatch, ["Collecting torch==2.9.1",
                        "ERROR: Could not find a version that satisfies the requirement"], rc=1)
    with pytest.raises(RuntimeError, match="satisfies the requirement"):
        engine_setup.install(build=torchbuild.BUILDS["cpu"])
    st = engine_setup.status()
    assert st["state"] == "error" and "code 1" in st["error"]


def test_a_file_windows_has_locked_gets_advice(monkeypatch):
    _fake(monkeypatch, ["ERROR: Could not install packages due to an OSError: "
                        "[WinError 5] Access is denied: '...\\numpy\\_core\\_multiarray_umath.pyd'"], rc=1)
    with pytest.raises(RuntimeError, match="Quit the app"):
        engine_setup.install(build=torchbuild.BUILDS["cpu"])


def test_cancelling_stops_pip(monkeypatch):
    fake = _fake(monkeypatch, REAL_OUTPUT)
    with pytest.raises(engine_setup.Cancelled):
        engine_setup.install(should_cancel=lambda: True, build=torchbuild.BUILDS["cpu"])
    assert fake.terminated and engine_setup.status()["state"] == "cancelled"
    assert len(_FakePip.runs) == 1


# --- the app's side --------------------------------------------------------------------

@pytest.fixture
def client():
    from ebook_audiobook.web.app import create_app

    return create_app().test_client()


def test_the_page_learns_what_this_computer_would_get(client, monkeypatch):
    from ebook_audiobook.web import app as web_app

    monkeypatch.setattr(web_app, "_engine_present", lambda: False)
    monkeypatch.setattr(engine_setup, "plan", lambda: torchbuild.BUILDS["cu128"])
    d = client.get("/api/engine").get_json()
    assert d["installed"] is False and d["plan"]["id"] == "cu128"
    assert d["status"]["state"] == "idle"


def test_install_goes_through_the_worker(client, monkeypatch):
    from ebook_audiobook.web import app as web_app
    from ebook_audiobook.web.runner import runner

    monkeypatch.setattr(web_app, "_engine_present", lambda: False)
    submitted = []
    monkeypatch.setattr(runner, "submit", lambda job, kind, **kw: submitted.append((job, kind)))
    monkeypatch.setattr(runner, "is_busy", lambda job=None: False)
    assert client.post("/api/engine/install").get_json()["ok"] is True
    assert submitted == [("engine", "engine_install")]
    assert engine_setup.status()["state"] == "running"   # before the worker picks it up


def test_install_waits_for_a_render_to_finish(client, monkeypatch):
    from ebook_audiobook.web import app as web_app
    from ebook_audiobook.web.runner import runner

    monkeypatch.setattr(web_app, "_engine_present", lambda: False)
    monkeypatch.setattr(runner, "is_busy", lambda job=None: True)
    assert client.post("/api/engine/install").status_code == 409


def test_an_install_updates_the_startup_banner_at_once(client, monkeypatch):
    from ebook_audiobook import checks

    calls = []
    monkeypatch.setattr(checks, "run_all", lambda **kw: calls.append(1) or [])
    client.get("/api/prereqs")
    client.get("/api/prereqs")
    assert len(calls) == 1                        # cached
    monkeypatch.setattr(engine_setup, "generation", engine_setup.generation + 1)
    client.get("/api/prereqs")
    assert len(calls) == 2                        # not after an install


def test_the_settings_page_has_the_panel(client):
    assert b'id="engineBox"' in client.get("/settings").data
