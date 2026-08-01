"""Device selection: the CUDA/MPS/CPU choice and its failure modes.

Real GPUs can't be assumed in CI (or on the developer's machine), so torch is
faked. That is the point: these tests cover the machines we *don't* have —
an M-series Mac, an Intel Mac, a 2 GB card — which is exactly where the
interesting bugs live.
"""

from __future__ import annotations

import sys
import types

import pytest

from ebook_audiobook import device


class _FakeCuda:
    OutOfMemoryError = type("OutOfMemoryError", (RuntimeError,), {})

    def __init__(self, available=False, name="Fake GPU", vram=8 * 1024**3, raises=None,
                 arch=None):
        self._available = available
        self._name = name
        self._vram = vram
        self._raises = raises
        self._arch = arch

    def is_available(self):
        if self._raises:
            raise self._raises
        return self._available

    def get_device_name(self, _i):
        return self._name

    def get_device_properties(self, _i):
        return types.SimpleNamespace(total_memory=self._vram, gcnArchName=self._arch or "")


class _FakeMpsBackend:
    def __init__(self, built=False, available=False):
        self._built, self._available = built, available

    def is_built(self):
        return self._built

    def is_available(self):
        return self._available


def _fake_torch(cuda=None, mps=None, hip=None):
    """A stand-in torch. ``hip`` set means a ROCm build, which is the only
    reliable way to tell an AMD GPU from an NVIDIA one — both report as CUDA."""
    mod = types.ModuleType("torch")
    mod.cuda = cuda or _FakeCuda()
    mod.backends = types.SimpleNamespace(mps=mps) if mps is not None else types.SimpleNamespace()
    mod.version = types.SimpleNamespace(hip=hip, cuda=None if hip else "12.4")
    mod.OutOfMemoryError = RuntimeError
    return mod


@pytest.fixture
def fake_torch(monkeypatch):
    """Install a fake ``torch`` for the duration of one test."""

    def install(cuda=None, mps=None, hip=None):
        mod = _fake_torch(cuda, mps, hip)
        monkeypatch.setitem(sys.modules, "torch", mod)
        return mod

    return install


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv(device.ENV_OVERRIDE, raising=False)
    monkeypatch.delenv("PYTORCH_ENABLE_MPS_FALLBACK", raising=False)


# --- the MPS fallback switch -------------------------------------------------

def test_mps_fallback_is_enabled_on_macos(monkeypatch):
    """Without this, an op Metal doesn't implement kills the render outright."""
    monkeypatch.setattr(device.sys, "platform", "darwin")
    device.enable_mps_fallback()
    assert device.os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] == "1"


def test_mps_fallback_not_set_off_macos(monkeypatch):
    monkeypatch.setattr(device.sys, "platform", "linux")
    device.enable_mps_fallback()
    assert "PYTORCH_ENABLE_MPS_FALLBACK" not in device.os.environ


def test_mps_fallback_respects_an_explicit_optout(monkeypatch):
    monkeypatch.setattr(device.sys, "platform", "darwin")
    monkeypatch.setenv("PYTORCH_ENABLE_MPS_FALLBACK", "0")
    device.enable_mps_fallback()
    assert device.os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] == "0"


# --- Apple Silicon -----------------------------------------------------------

def test_apple_silicon_selects_mps(fake_torch, monkeypatch):
    monkeypatch.setenv("PYTORCH_ENABLE_MPS_FALLBACK", "1")
    fake_torch(mps=_FakeMpsBackend(built=True, available=True))
    dev = device.select_device()
    assert dev.kind == "mps"
    assert dev.is_fast
    assert dev.note is None


def test_intel_mac_falls_back_to_cpu(fake_torch):
    """MPS is compiled in, but there's no Apple GPU to run it on."""
    fake_torch(mps=_FakeMpsBackend(built=True, available=False))
    assert device.select_device().kind == "cpu"


def test_cpu_only_wheel_on_apple_silicon_uses_cpu(fake_torch):
    """A CPU-only torch build reports built=False even on an M-series Mac."""
    fake_torch(mps=_FakeMpsBackend(built=False, available=True))
    assert device.select_device().kind == "cpu"


def test_mps_without_fallback_enabled_is_flagged(fake_torch):
    """Renders still work, but an unsupported op can end one abruptly — say so."""
    fake_torch(mps=_FakeMpsBackend(built=True, available=True))
    dev = device.select_device()
    assert dev.kind == "mps"
    assert "PYTORCH_ENABLE_MPS_FALLBACK" in (dev.note or "")


def test_old_torch_without_an_mps_backend(fake_torch):
    fake_torch()  # no torch.backends.mps at all
    assert device.select_device().kind == "cpu"


# --- NVIDIA ------------------------------------------------------------------

def test_cuda_preferred_over_mps(fake_torch):
    fake_torch(cuda=_FakeCuda(available=True, name="RTX 4090"),
               mps=_FakeMpsBackend(built=True, available=True))
    dev = device.select_device()
    assert dev.kind == "cuda"
    assert "RTX 4090" in dev.name


def test_small_card_is_reported_but_still_used(fake_torch):
    """A 2 GB card loads and then dies mid-render; warn rather than refuse."""
    fake_torch(cuda=_FakeCuda(available=True, name="GTX 1050", vram=2 * 1024**3))
    dev = device.select_device()
    assert dev.kind == "cuda"
    assert "2.0 GB" in dev.note


def test_broken_driver_does_not_crash(fake_torch):
    """nvidia-smi/NVML failures raise out of is_available() on some machines."""
    fake_torch(cuda=_FakeCuda(raises=RuntimeError("Driver/library version mismatch")))
    assert device.select_device().kind == "cpu"


def test_missing_torch_reports_cpu(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)  # import torch -> raises
    dev = device.select_device()
    assert dev.kind == "cpu"
    assert "PyTorch isn't installed" in dev.note


# --- the EBAB_DEVICE override ------------------------------------------------

def test_override_forces_cpu_on_a_cuda_machine(fake_torch, monkeypatch):
    fake_torch(cuda=_FakeCuda(available=True))
    monkeypatch.setenv(device.ENV_OVERRIDE, "cpu")
    dev = device.select_device()
    assert dev.kind == "cpu"
    assert dev.forced


def test_override_for_absent_hardware_says_so(fake_torch, monkeypatch):
    """Asking for a GPU that isn't there must not silently look like it worked."""
    fake_torch()
    monkeypatch.setenv(device.ENV_OVERRIDE, "cuda")
    dev = device.select_device()
    assert dev.kind == "cpu"
    assert "no usable cuda device" in dev.note


def test_a_typo_in_the_override_is_reported(fake_torch, monkeypatch):
    fake_torch(cuda=_FakeCuda(available=True))
    monkeypatch.setenv(device.ENV_OVERRIDE, "gpu")  # not one of cuda/mps/cpu
    dev = device.select_device()
    assert dev.kind == "cuda"  # probe still ran
    assert "ignored" in dev.note


def test_override_is_case_and_space_insensitive(fake_torch, monkeypatch):
    fake_torch(cuda=_FakeCuda(available=True))
    monkeypatch.setenv(device.ENV_OVERRIDE, "  CPU ")
    assert device.select_device().kind == "cpu"


# --- out-of-memory detection -------------------------------------------------

def test_detects_typed_cuda_oom(fake_torch):
    mod = fake_torch()
    mod.OutOfMemoryError = _FakeCuda.OutOfMemoryError
    assert device.is_out_of_memory(_FakeCuda.OutOfMemoryError("boom"))


def test_detects_mps_oom_by_message(fake_torch):
    """MPS reports exhaustion as a plain RuntimeError, so the text is all we get."""
    fake_torch()
    assert device.is_out_of_memory(RuntimeError("MPS backend out of memory"))


def test_detects_cuda_oom_by_message(fake_torch):
    fake_torch()
    assert device.is_out_of_memory(
        RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")
    )


def test_unrelated_errors_are_not_oom(fake_torch):
    fake_torch()
    assert not device.is_out_of_memory(ValueError("bad input"))


def test_empty_cache_never_raises(fake_torch):
    """Reclaiming memory is best-effort; it must never take down a render."""
    mod = fake_torch()

    def explode():
        raise RuntimeError("no")

    mod.cuda.empty_cache = explode
    device.empty_cache("cuda")  # must not raise
    device.empty_cache("mps")
    device.empty_cache("cpu")


def test_describe_is_readable():
    dev = device.Device("cuda", "RTX 3070 Ti", "only 2.0 GB of VRAM")
    assert dev.describe() == "RTX 3070 Ti (cuda) — only 2.0 GB of VRAM"
    assert device.Device("cpu", "x86_64").describe() == "x86_64 (cpu)"


# --- AMD / ROCm --------------------------------------------------------------
#
# A ROCm build of PyTorch impersonates the CUDA API: torch.cuda.is_available()
# is True on a Radeon, and torch.cuda.get_device_name() returns the AMD model.
# torch.version.hip is the only honest discriminator, and getting it wrong means
# telling an AMD user they own an NVIDIA card.

def test_an_amd_gpu_is_reported_as_rocm_not_cuda(fake_torch):
    fake_torch(cuda=_FakeCuda(available=True, name="AMD Radeon RX 7900 XTX",
                              arch="gfx1100"),
               hip="6.2.41134")
    dev = device.select_device()
    assert dev.kind == "cuda"          # what torch needs
    assert dev.backend == "ROCm"       # what the user is told
    assert "Radeon" in dev.name
    assert "ROCm" in dev.describe()
    assert "cuda" not in dev.describe().lower().replace("rocm", "")


def test_an_nvidia_gpu_is_still_reported_as_cuda(fake_torch):
    fake_torch(cuda=_FakeCuda(available=True, name="NVIDIA GeForce RTX 4090"))
    dev = device.select_device()
    assert dev.backend == "cuda"


def test_a_small_radeon_gets_the_same_vram_warning(fake_torch):
    fake_torch(cuda=_FakeCuda(available=True, name="AMD Radeon RX 6500 XT",
                              vram=4 * 1024**3 - 1, arch="gfx1034"),
               hip="6.2.41134")
    dev = device.select_device()
    assert dev.backend == "ROCm"
    assert "VRAM" in dev.note


def test_rocm_that_cannot_see_the_radeon_explains_the_override(fake_torch, monkeypatch):
    """The single most common AMD failure: an unsupported architecture, where
    ROCm reports no device at all and the fix is one environment variable."""
    fake_torch(cuda=_FakeCuda(available=False), hip="6.2.41134")
    monkeypatch.setattr(device, "amd_gpu_in_sysfs", lambda: True)
    dev = device.select_device()
    assert dev.kind == "cpu"
    assert device.HSA_OVERRIDE_ENV in dev.note
    assert "10.3.0" in dev.note and "11.0.0" in dev.note


def test_rocm_build_with_no_amd_card_just_uses_the_cpu(fake_torch, monkeypatch):
    """No Radeon present means nothing to explain — don't invent a problem."""
    fake_torch(cuda=_FakeCuda(available=False), hip="6.2.41134")
    monkeypatch.setattr(device, "amd_gpu_in_sysfs", lambda: False)
    dev = device.select_device()
    assert dev.kind == "cpu"
    assert device.HSA_OVERRIDE_ENV not in (dev.note or "")


def test_a_cuda_build_with_no_gpu_says_nothing_about_rocm(fake_torch, monkeypatch):
    monkeypatch.setattr(device, "amd_gpu_in_sysfs", lambda: True)  # AMD iGPU, say
    fake_torch(cuda=_FakeCuda(available=False))  # but a CUDA build of torch
    dev = device.select_device()
    assert dev.kind == "cpu"
    assert device.HSA_OVERRIDE_ENV not in (dev.note or "")


def test_gfx_arch_strips_the_feature_suffix(fake_torch):
    """ROCm reports 'gfx1030:sramecc-:xnack-'; only the base name is the arch."""
    mod = fake_torch(cuda=_FakeCuda(available=True, name="RX 6800",
                                    arch="gfx1031:sramecc-:xnack-"),
                     hip="6.2")
    assert device._gfx_arch(mod) == "gfx1031"


def test_amd_sysfs_probe_is_linux_only(monkeypatch):
    monkeypatch.setattr(device.sys, "platform", "darwin")
    assert device.amd_gpu_in_sysfs() is False
