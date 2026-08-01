"""Which PyTorch build gets installed — the decision that used to live in six
places at once.

Most of these cover machines we don't have. That is the point: an installer that
picks the wrong index doesn't fail loudly, it quietly gives someone a CPU render
that takes thirty hours instead of three.
"""

from __future__ import annotations

import types

import pytest

from ebook_audiobook import torchbuild as tb


# --- the macOS rule, which is a regression test for a shipped bug -------------

@pytest.mark.parametrize("forced", ["", "cpu", "gpu", "rocm"])
def test_macos_never_gets_a_cuda_index(forced):
    """`--gpu` on a Mac once asked pip for a cu124 wheel that has never existed
    for any Mac, so the flag meant to help guaranteed a failed install."""
    build = tb.select("macos", arch="arm64", forced=forced)
    assert build.id == "mac"
    assert "cuda" not in build.index_url.lower()
    assert "cu1" not in build.index_url


def test_macos_uses_the_default_pypi_wheel():
    """There is one Mac wheel and it already contains Metal; pinning an index
    would only ever narrow it."""
    assert tb.select("macos", arch="arm64").index_url == ""


# --- vendor detection --------------------------------------------------------

def test_nvidia_gets_cuda():
    assert tb.select("linux", vendor="nvidia").id == "cu124"


def test_amd_gets_rocm():
    assert tb.select("linux", vendor="amd").id == "rocm"


def test_no_gpu_gets_cpu():
    assert tb.select("linux").id == "cpu"


def test_windows_amd_gets_cpu_not_rocm():
    """PyTorch ships no ROCm wheels for Windows, so an AMD card there is a CPU
    render whether we like it or not."""
    assert tb.select("windows", vendor="").id == "cpu"


# --- forced choices ----------------------------------------------------------

@pytest.mark.parametrize("forced,expected", [
    ("cpu", "cpu"), ("gpu", "cu124"), ("rocm", "rocm"),
])
def test_force_flags_win_over_detection(forced, expected):
    """A user overriding a bad probe must actually get what they asked for."""
    assert tb.select("linux", vendor="nvidia", forced=forced).id == expected


def test_forcing_cpu_on_an_amd_machine_gives_cpu():
    assert tb.select("linux", vendor="amd", forced="cpu").id == "cpu"


# --- every build is coherent -------------------------------------------------

@pytest.mark.parametrize("build_id", sorted(tb.BUILDS))
def test_each_build_is_fully_described(build_id):
    b = tb.BUILDS[build_id]
    assert b.id == build_id
    assert b.label and b.size
    if b.index_url:
        assert b.index_url.startswith("https://download.pytorch.org/whl/")


def test_the_rocm_index_pins_a_specific_rocm_version():
    """A bare .../whl/rocm would float across ROCm releases; the version in the
    URL is what CI resolves against."""
    assert "rocm" in tb.BUILDS["rocm"].index_url
    assert tb.BUILDS["rocm"].index_url.rstrip("/").split("/")[-1] != "rocm"


# --- old NVIDIA cards --------------------------------------------------------

@pytest.mark.parametrize("name", [
    "NVIDIA GeForce GTX 1080 Ti", "NVIDIA GeForce GTX 970",
    "NVIDIA TITAN Xp", "Quadro P4000", "Tesla K80",
])
def test_pre_turing_cards_are_recognised(name):
    assert tb.is_old_nvidia(name)


@pytest.mark.parametrize("name", [
    "NVIDIA GeForce RTX 3070 Ti", "NVIDIA GeForce RTX 4090",
    "NVIDIA GeForce RTX 5090", "NVIDIA RTX A4000", "",
])
def test_modern_cards_are_not_flagged_as_old(name):
    assert not tb.is_old_nvidia(name)


# --- the shell interface both installers parse -------------------------------

def test_emitted_keys_round_trip(capsys):
    assert tb.main(["--platform", "linux", "--vendor", "nvidia"]) == 0
    out = capsys.readouterr().out
    parsed = dict(line.split("=", 1) for line in out.strip().splitlines())
    assert parsed["EBAB_TORCH_ID"] == "cu124"
    assert parsed["EBAB_TORCH_INDEX"] == tb.BUILDS["cu124"].index_url
    assert parsed["EBAB_TORCH_SIZE"]


def test_emitted_values_contain_no_newlines():
    """The installers read this line-by-line; an embedded newline would silently
    truncate a value."""
    for build in tb.BUILDS.values():
        for line in tb._emit(build).splitlines():
            assert line.count("=") >= 1
        assert tb._emit(build).count("\n") == 4


# --- the runtime arch backstop ----------------------------------------------

def _fake_torch(available=True, capability=(8, 6), arches=None):
    mod = types.SimpleNamespace()
    mod.cuda = types.SimpleNamespace(
        is_available=lambda: available,
        get_device_capability=lambda i: capability,
        get_arch_list=lambda: arches if arches is not None else
        ["sm_70", "sm_75", "sm_80", "sm_86", "sm_90"],
    )
    return mod


def test_a_supported_card_reports_no_problem():
    assert tb.arch_supported(_fake_torch(capability=(8, 6))) is None


def test_a_card_with_no_kernels_is_named_with_its_sm():
    """A GTX 1080 Ti (sm_61) on a build starting at sm_70 loads fine and then
    fails on the first real work."""
    msg = tb.arch_supported(_fake_torch(capability=(6, 1)))
    assert msg is not None
    assert "sm_61" in msg
    assert "Re-run the installer" in msg


def test_a_blackwell_card_on_an_old_build_is_caught():
    """The exact case that motivated the upgrade: sm_120 absent from cu124."""
    msg = tb.arch_supported(_fake_torch(capability=(12, 0)))
    assert msg is not None and "sm_120" in msg


def test_no_gpu_means_nothing_to_report():
    assert tb.arch_supported(_fake_torch(available=False)) is None


def test_rocm_arch_lists_are_not_judged():
    """ROCm reports gfx* rather than sm_*; this check is CUDA-only and must not
    invent a problem on a working Radeon."""
    assert tb.arch_supported(_fake_torch(arches=["gfx1030", "gfx1100"])) is None


def test_a_broken_probe_never_raises():
    broken = types.SimpleNamespace()
    broken.cuda = types.SimpleNamespace(
        is_available=lambda: True,
        get_device_capability=lambda i: (_ for _ in ()).throw(RuntimeError("boom")),
        get_arch_list=lambda: [],
    )
    assert tb.arch_supported(broken) is None
