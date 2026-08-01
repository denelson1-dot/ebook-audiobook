"""Which piece of silicon the TTS engine actually runs on.

One place decides this, because the answer is needed in three: the engine picks
a device to load the model onto, ``check`` reports it to the user, and the
installer's advice only makes sense if it matches what the app will really do.
They used to each work it out themselves and could disagree.

The three devices that matter, in preference order:

``cuda``
    An NVIDIA GPU. Roughly 10x a CPU. Chatterbox wants about 4 GB of VRAM; less
    than that still loads and then dies partway through a long render, so the
    shortfall is reported up front and :mod:`ebook_audiobook.tts.chatterbox`
    recovers by falling back to CPU rather than losing the render.

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

    @property
    def is_fast(self) -> bool:
        return self.kind in ("cuda", "mps")

    def describe(self) -> str:
        base = f"{self.name} ({self.kind})" if self.name else self.kind
        return f"{base} — {self.note}" if self.note else base


def _cuda_device(torch) -> Device | None:
    try:
        if not torch.cuda.is_available():
            return None
        name = torch.cuda.get_device_name(0)
    except Exception:  # noqa: BLE001 - a broken driver must not stop the app
        return None
    note = None
    try:
        vram = torch.cuda.get_device_properties(0).total_memory
        if vram < MIN_VRAM_BYTES:
            note = (f"only {vram / 1024**3:.1f} GB of VRAM; if it runs out "
                    f"mid-render the job continues on the CPU")
    except Exception:  # noqa: BLE001 - VRAM is a nicety, not a requirement
        pass
    return Device("cuda", name, note)


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
        note = ("Metal CPU fallback is off, so an unsupported operation could "
                "stop a render; set PYTORCH_ENABLE_MPS_FALLBACK=1")
    return Device("mps", name, note)


def _cpu_device() -> Device:
    import platform

    name = platform.processor() or platform.machine() or "CPU"
    return Device("cpu", name, "much slower than a GPU — expect a long render")


def select_device() -> Device:
    """The device the engine should use, and why. Never raises.

    Returns a CPU device when torch isn't installed at all, so callers that only
    want to *describe* the situation don't have to handle the import failing.
    """
    forced = (os.environ.get(ENV_OVERRIDE) or "").strip().lower()
    try:
        import torch
    except Exception:  # noqa: BLE001 - torch is an optional dependency
        return Device("cpu", "CPU", "PyTorch isn't installed, so nothing can render yet")

    if forced in VALID:
        probed = {"cuda": _cuda_device, "mps": _mps_device}.get(forced)
        dev = probed(torch) if probed else _cpu_device()
        if dev is not None:
            return Device(dev.kind, dev.name, dev.note, forced=True)
        # Asked for hardware this machine doesn't have. Say so rather than
        # silently ignoring the request or crashing on an unusable device.
        cpu = _cpu_device()
        return Device("cpu", cpu.name,
                      f"{ENV_OVERRIDE}={forced} was requested but no usable "
                      f"{forced} device was found, so this is running on the CPU",
                      forced=True)
    # Anything left in `forced` is a typo. Probe normally, but say the value was
    # ignored — silently honouring nothing looks identical to it having worked.
    dev = _cuda_device(torch) or _mps_device(torch)
    if dev is None:
        dev = _cpu_device()
    if forced:
        note = f"{ENV_OVERRIDE}={forced!r} isn't one of {', '.join(VALID)} — ignored"
        return Device(dev.kind, dev.name, f"{note}; {dev.note}" if dev.note else note)
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
