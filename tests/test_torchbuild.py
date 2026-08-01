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
    assert tb.select("linux", vendor="nvidia").id == "cu128"


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
    ("cpu", "cpu"), ("gpu", "cu128"), ("rocm", "rocm"),
    ("cuda126", "cu126"), ("cuda128", "cu128"),
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
    assert parsed["EBAB_TORCH_ID"] == "cu128"
    assert parsed["EBAB_TORCH_INDEX"] == tb.BUILDS["cu128"].index_url
    assert parsed["EBAB_TORCH_PIN"] == tb.TORCH_PIN
    assert parsed["EBAB_TORCH_SIZE"]


def test_emitted_values_contain_no_newlines():
    """The installers read this line-by-line; an embedded newline would silently
    truncate a value."""
    for build in tb.BUILDS.values():
        for line in tb._emit(build).splitlines():
            assert line.count("=") >= 1
        assert tb._emit(build).count("\n") == 5


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
    assert "--cuda126" in msg  # names the exact flag that fixes it


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


# --- choosing between the two CUDA builds ------------------------------------
#
# Measured from the wheels, not assumed:
#   cu128 -> sm_70 75 80 86 90 100 120   (gains RTX 50-series, loses Pascal)
#   cu126 -> sm_50 60 70 75 80 86 90     (covers Pascal via sm_60)

CU128_ARCHES = ["sm_70", "sm_75", "sm_80", "sm_86", "sm_90", "sm_100", "sm_120"]
CU126_ARCHES = ["sm_50", "sm_60", "sm_70", "sm_75", "sm_80", "sm_86", "sm_90"]


@pytest.mark.parametrize("cap,expected", [
    ((12, 0), "cu128"),   # RTX 5090 — the whole reason for this upgrade
    ((8, 9), "cu128"),    # RTX 4090
    ((8, 6), "cu128"),    # RTX 3070 Ti
    ((7, 5), "cu128"),    # RTX 2060 — exactly at the cutover
    ((7, 0), "cu128"),    # Titan V
    ((6, 1), "cu126"),    # GTX 1080 Ti — just below it
    ((5, 2), "cu126"),    # GTX 970
])
def test_the_cuda_build_matches_the_card(cap, expected):
    assert tb.cuda_build_for([cap]).id == expected


def test_the_oldest_card_decides_on_a_mixed_machine():
    """A build that can't run the older card would break that GPU entirely, so
    the minimum wins — not the first, and not the best."""
    assert tb.cuda_build_for([(12, 0), (6, 1)]).id == "cu126"
    assert tb.cuda_build_for([(6, 1), (12, 0)]).id == "cu126"


def test_an_unknown_capability_falls_back_to_the_name():
    """Drivers too old to report a compute capability still report a model."""
    assert tb.cuda_build_for(None, "NVIDIA GeForce GTX 1080 Ti").id == "cu126"
    assert tb.cuda_build_for([], "NVIDIA GeForce RTX 4090").id == "cu128"


def test_knowing_nothing_picks_the_newer_build():
    """Guess new: modern cards dominate, and arch_supported() catches a wrong
    guess with an actionable message rather than an opaque CUDA error."""
    assert tb.cuda_build_for(None, "").id == "cu128"


# --- CUDA's minor-version compatibility rule ---------------------------------

def test_an_exact_arch_match_is_covered():
    assert tb.covers(CU128_ARCHES, (8, 6))


def test_a_newer_minor_runs_on_an_older_cubin():
    """RTX 40-series is sm_89 and no build lists it, yet it works: cubins are
    forward-compatible across minor versions within a major. Matching exactly
    would tell every 4090 owner their card was unsupported."""
    assert tb.covers(CU128_ARCHES, (8, 9))


def test_a_gtx_1080ti_is_covered_by_the_sm_60_cubin():
    """cu126 lists sm_60 but not sm_61. The 1080 Ti runs on it regardless."""
    assert tb.covers(CU126_ARCHES, (6, 1))


def test_an_older_minor_is_not_covered_by_a_newer_cubin():
    """Compatibility only runs upward: an sm_86 binary will not run on sm_80."""
    assert not tb.covers(["sm_86"], (8, 0))


def test_a_major_version_gap_is_never_covered():
    assert not tb.covers(CU128_ARCHES, (6, 1))    # Pascal on cu128
    assert not tb.covers(CU126_ARCHES, (12, 0))   # Blackwell on cu126


@pytest.mark.parametrize("junk", ["", "gfx1100", "sm_", "sm_x", "compute_80"])
def test_unparseable_arch_strings_are_ignored(junk):
    assert not tb.covers([junk], (8, 6))


def test_blackwell_on_cu126_names_the_right_flag():
    msg = tb.arch_supported(_fake_torch(capability=(12, 0), arches=CU126_ARCHES))
    assert msg is not None and "--cuda128" in msg


def test_a_4090_on_cu128_is_not_flagged():
    """The regression this rule exists to prevent."""
    assert tb.arch_supported(_fake_torch(capability=(8, 9), arches=CU128_ARCHES)) is None


# --- the install sequence ----------------------------------------------------

def test_chatterbox_goes_in_with_no_deps():
    """Its torch==2.6.0 pin would otherwise replace the build we just chose."""
    cmds = tb.install_commands(tb.BUILDS["cu128"])
    cb = [c for c in cmds if tb.CHATTERBOX_PIN in c]
    assert len(cb) == 1
    assert "--no-deps" in cb[0]


def test_torch_is_pinned_exactly_in_every_command_that_names_it():
    """A floor would resolve to a newer torch from PyPI and undo the choice."""
    for cmd in tb.install_commands(tb.BUILDS["cu128"]):
        for arg in cmd:
            if arg.startswith("torch"):
                assert arg in (f"torch=={tb.TORCH_PIN}",
                               f"torchaudio=={tb.TORCH_PIN}"), arg


def test_the_dependency_command_repeats_the_pins():
    """Otherwise a dep that requires torch is free to replace the build."""
    deps_cmd = tb.install_commands(tb.BUILDS["cu128"])[-1]
    assert f"torch=={tb.TORCH_PIN}" in deps_cmd
    assert "librosa==0.11.0" in deps_cmd


def test_einops_is_in_the_curated_list():
    """Chatterbox imports it unguarded and never declares it. Under --no-deps
    its absence is an ImportError at model load."""
    assert "einops" in tb.CHATTERBOX_DEPS


@pytest.mark.parametrize("dropped", tb.FORBIDDEN_PACKAGES)
def test_the_packages_we_deliberately_drop_are_not_reinstalled(dropped):
    joined = " ".join(tb.CHATTERBOX_DEPS)
    assert dropped not in joined


def test_macos_gets_no_index_flags():
    """There is one Mac wheel, on PyPI; passing an index could only narrow it."""
    for cmd in tb.install_commands(tb.BUILDS["mac"]):
        assert "--index-url" not in cmd
