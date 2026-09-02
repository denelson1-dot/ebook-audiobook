"""Which piece of silicon the TTS engine actually runs on.

One place decides this, because the answer is needed in three: the engine picks
a device to load the model onto, ``check`` reports it to the user, and the
installer's advice only makes sense if it matches what the app will really do.
They used to each work it out themselves and could disagree.

The three devices that matter, in preference order:

``cuda``
    A discrete GPU — NVIDIA through CUDA, or **AMD through ROCm**. Both arrive
    here as ``"cuda"``: a ROCm build of PyTorch deliberately impersonates the
    CUDA API, so ``torch.cuda.is_available()`` is True on a Radeon and the only
    honest way to tell them apart is ``torch.version.hip``. Getting that wrong
    means telling an AMD user they have an NVIDIA card. Roughly 10x a CPU.
    Chatterbox wants about 4 GB of VRAM; less than that still loads and then
    dies partway through a long render, so the shortfall is reported up front
    and :mod:`ebook_audiobook.tts.chatterbox` recovers by falling back to CPU
    rather than losing the render.

``mps``
    Apple Silicon (M1 and later) via Metal. Much faster than the CPU on the same
    Mac. Needs :func:`enable_mps_fallback` to have run before torch was imported
    — see that function; without it a render dies partway through.

``cpu``
    Always works, always slow. The honest fallback.

``EBAB_DEVICE=cpu|cuda|mps`` overrides the choice, for when the probe is wrong
or a user wants to keep their GPU free for something else.
"""

from __future__ import annotations

from .i18n import _
import os
import sys
from dataclasses import dataclass

ENV_OVERRIDE = "EBAB_DEVICE"
VALID = ("cuda", "mps", "cpu")

# Chatterbox's weights plus generation working set. Measured around 3.5 GB in
# practice; 4 warns the marginal cards without crying wolf on the 6 GB ones.
MIN_VRAM_BYTES = 4 * 1024**3


def enable_mps_fallback() -> None:
    """Let Apple Silicon fall back to the CPU for ops Metal doesn't implement.

    PyTorch's MPS backend does not cover every operator. Without this, the first
    uncovered one raises ``NotImplementedError`` and takes the render with it —
    and because the gap depends on the specific op, it can happen an hour in
    rather than on the first segment. With it, that single op runs on the CPU and
    everything else stays on the GPU.

    Torch reads this variable **when it is imported**, so it has to be set before
    that happens. It is therefore called from the package's ``__init__``, which
    is the one thing guaranteed to run before any of our modules import torch.
    Set explicitly to ``0`` and we leave that choice alone.
    """
    if sys.platform == "darwin":
        os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")


@dataclass(frozen=True)
class Device:
    """The chosen device and what to tell the user about it."""

    kind: str            # "cuda" | "mps" | "cpu"
    name: str            # human-readable, e.g. "NVIDIA GeForce RTX 4070"
    note: str | None = None   # caveat worth surfacing (low VRAM, forced, …)
    forced: bool = False      # chosen by EBAB_DEVICE rather than probed
    # How the device is actually driven. "cuda" and "rocm" share kind="cuda"
    # because that is the string torch wants; this is what the *user* is told.
    backend: str = ""

    @property
    def is_fast(self) -> bool:
        return self.kind in ("cuda", "mps")

    def describe(self) -> str:
        label = self.backend or self.kind
        base = f"{self.name} ({label})" if self.name else label
        return _("%(device)s — %(note)s", device=base, note=self.note) if self.note else base


# AMD's PCI vendor id, for spotting a Radeon without ROCm or torch installed.
_AMD_PCI_VENDOR = "0x1002"

# Consumer Radeons whose GPU architecture ROCm doesn't list as supported, but
# which work once pointed at the nearest architecture that is. Every one of
# these is a card someone plausibly owns: the RX 6700/6600 and RX 7600 families
# were mainstream parts. Without the override ROCm simply reports no device.
_GFX_OVERRIDES = {
    "gfx1031": "10.3.0",   # RX 6700 / 6700 XT / 6750
    "gfx1032": "10.3.0",   # RX 6600 / 6600 XT / 6650
    "gfx1033": "10.3.0",
    "gfx1034": "10.3.0",   # RX 6500 XT
    "gfx1035": "10.3.0",   # Rembrandt integrated
    "gfx1036": "10.3.0",
    "gfx1101": "11.0.0",   # RX 7800 XT / 7700 XT
    "gfx1102": "11.0.0",   # RX 7600 / 7600 XT
    "gfx1103": "11.0.0",   # Phoenix integrated
}

HSA_OVERRIDE_ENV = "HSA_OVERRIDE_GFX_VERSION"


def amd_gpu_in_sysfs() -> bool:
    """Is there an AMD GPU on this machine, judged without torch or ROCm?

    Read straight from the kernel's DRM nodes, so it stays true even when ROCm
    is missing or refusing to enumerate the card — which is exactly the state we
    need to recognise in order to explain it.
    """
    if sys.platform != "linux":
        return False
    try:
        from pathlib import Path

        for card in Path("/sys/class/drm").glob("card[0-9]*"):
            vendor = card / "device" / "vendor"
            if vendor.is_file() and vendor.read_text().strip() == _AMD_PCI_VENDOR:
                return True
    except OSError:
        pass
    return False


def _gfx_arch(torch) -> str | None:
    """The ROCm architecture name (e.g. ``gfx1030``) of the first GPU."""
    try:
        raw = getattr(torch.cuda.get_device_properties(0), "gcnArchName", "") or ""
    except Exception:  # noqa: BLE001
        return None
    # Reported as e.g. "gfx1030:sramecc-:xnack-"; only the base name matters.
    arch = raw.split(":")[0].strip()
    return arch or None


def _gpu_device(torch) -> Device | None:
    """The discrete GPU, whichever vendor made it.

    NVIDIA and AMD are both reached through ``torch.cuda`` — a ROCm build
    reimplements that API rather than adding its own — so one probe covers both
    and only the labelling differs.
    """
    is_rocm = getattr(getattr(torch, "version", None), "hip", None) is not None
    try:
        if not torch.cuda.is_available():
            return _rocm_present_but_unusable() if is_rocm else None
        name = torch.cuda.get_device_name(0)
    except Exception:  # noqa: BLE001 - a broken driver must not stop the app
        return _rocm_present_but_unusable() if is_rocm else None

    backend = "ROCm" if is_rocm else "cuda"
    notes = []
    # Does this build actually have kernels for this card? A mismatch loads
    # perfectly happily and then fails on the first real work, which without
    # this surfaces as an opaque CUDA error partway through a render.
    from .torchbuild import arch_supported

    mismatch = arch_supported(torch)
    if mismatch:
        notes.append(mismatch)
    try:
        vram = torch.cuda.get_device_properties(0).total_memory
        if vram < MIN_VRAM_BYTES:
            notes.append(_("only %(gb)s GB of VRAM; if it runs out mid-render the job "
                           "continues on the CPU", gb=f"{vram / 1024**3:.1f}"))
    except Exception:  # noqa: BLE001 - VRAM is a nicety, not a requirement
        pass
    if is_rocm:
        arch = _gfx_arch(torch)
        if arch and arch in _GFX_OVERRIDES and not os.environ.get(HSA_OVERRIDE_ENV):
            # It enumerated anyway — good — but say why it might not have, so a
            # user comparing notes with a forum thread isn't confused.
            notes.append(_("%(arch)s works here without %(env)s", arch=arch, env=HSA_OVERRIDE_ENV))
    return Device("cuda", name, "; ".join(notes) or None, backend=backend)


def _rocm_present_but_unusable() -> Device | None:
    """A ROCm build of torch that can't see the Radeon sitting in the machine.

    Overwhelmingly this is the unsupported-architecture case, and the fix is one
    environment variable — but only if somebody tells you it exists. Returning
    None here would send the user to the CPU with no explanation for why the
    ROCm install they just did appears to have done nothing.
    """
    if not amd_gpu_in_sysfs():
        return None
    hint = _("ROCm is installed but can't use this Radeon. Two usual causes: the "
             "card's architecture isn't on ROCm's supported list — try setting "
             "%(env)s (10.3.0 for RX 6000, 11.0.0 for RX 7000) before "
             "starting — or the amdgpu kernel driver is older than ROCm 6.4 "
             "needs, which a system update fixes. Running on the CPU meanwhile", env=HSA_OVERRIDE_ENV)
    cpu = _cpu_device()
    return Device("cpu", cpu.name, hint, backend="cpu")


def _mps_device(torch) -> Device | None:
    """Apple Silicon, but only when Metal is genuinely usable.

    ``is_built()`` and ``is_available()`` answer different questions and both
    matter: a CPU-only torch wheel on an M-series Mac reports built=False, while
    an Intel Mac (or macOS older than 12.3) reports built=True, available=False.
    Treating either as a GPU would load the model onto a device that can't run
    it.
    """
    mps = getattr(torch.backends, "mps", None)
    if mps is None:
        return None
    try:
        if not mps.is_built() or not mps.is_available():
            return None
    except Exception:  # noqa: BLE001
        return None
    import platform

    chip = platform.machine()  # "arm64" on Apple Silicon
    name = f"Apple Silicon GPU ({chip})" if chip else "Apple Silicon GPU"
    note = None
    if os.environ.get("PYTORCH_ENABLE_MPS_FALLBACK") != "1":
        # enable_mps_fallback() didn't get to run before torch was imported.
        # Renders can still work, but an unsupported op will end one abruptly.
        note = _("Metal CPU fallback is off, so an unsupported operation could "
                 "stop a render; set PYTORCH_ENABLE_MPS_FALLBACK=1")
    return Device("mps", name, note, backend="Metal")


def _cpu_device() -> Device:
    import platform

    name = platform.processor() or platform.machine() or "CPU"
    return Device("cpu", name, _("much slower than a GPU — expect a long render"),
                  backend="cpu")


def select_device() -> Device:
    """The device the engine should use, and why. Never raises.

    Returns a CPU device when torch isn't installed at all, so callers that only
    want to *describe* the situation don't have to handle the import failing.
    """
    forced = (os.environ.get(ENV_OVERRIDE) or "").strip().lower()
    try:
        import torch
    except Exception:  # noqa: BLE001 - torch is an optional dependency
        return Device("cpu", "CPU", _("PyTorch isn't installed, so nothing can render yet"),
                      backend="cpu")

    if forced in VALID:
        probed = {"cuda": _gpu_device, "mps": _mps_device}.get(forced)
        dev = probed(torch) if probed else _cpu_device()
        if dev is not None:
            return Device(dev.kind, dev.name, dev.note, forced=True, backend=dev.backend)
        # Asked for hardware this machine doesn't have. Say so rather than
        # silently ignoring the request or crashing on an unusable device.
        cpu = _cpu_device()
        return Device("cpu", cpu.name,
                      _("%(env)s=%(forced)s was requested but no usable %(forced)s device "
                        "was found, so this is running on the CPU", env=ENV_OVERRIDE, forced=forced),
                      forced=True, backend="cpu")
    # Anything left in `forced` is a typo. Probe normally, but say the value was
    # ignored — silently honouring nothing looks identical to it having worked.
    dev = _gpu_device(torch) or _mps_device(torch)
    if dev is None:
        dev = _cpu_device()
    if forced:
        note = _("%(env)s=%(forced)s isn't one of %(valid)s — ignored",
                 env=ENV_OVERRIDE, forced=repr(forced), valid=", ".join(VALID))
        return Device(dev.kind, dev.name, f"{note}; {dev.note}" if dev.note else note,
                      backend=dev.backend)
    return dev


def empty_cache(kind: str) -> None:
    """Hand back whatever GPU memory the process is holding. Never raises.

    Called after unloading the model, and after a CUDA out-of-memory so a retry
    has a chance of finding room.
    """
    try:
        import torch

        if kind == "cuda":
            torch.cuda.empty_cache()
        elif kind == "mps":
            # Present since torch 2.0; older wheels simply skip this.
            mps = getattr(torch, "mps", None)
            if mps is not None and hasattr(mps, "empty_cache"):
                mps.empty_cache()
    except Exception:  # noqa: BLE001 - reclaiming memory is best-effort
        pass


def is_out_of_memory(exc: BaseException) -> bool:
    """Whether an exception is a GPU running out of memory.

    Matched by type where torch gives us one, and by message otherwise: MPS
    reports exhaustion as a plain ``RuntimeError``, and so do some CUDA paths
    that surface the allocator's error rather than raising the typed one.
    """
    try:
        import torch

        if isinstance(exc, getattr(torch, "OutOfMemoryError", ())):
            return True
        cuda_oom = getattr(torch.cuda, "OutOfMemoryError", None)
        if cuda_oom is not None and isinstance(exc, cuda_oom):
            return True
    except Exception:  # noqa: BLE001 - fall back to the message test
        pass
    text = str(exc).lower()
    return "out of memory" in text or "mps backend out of memory" in text
