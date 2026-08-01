"""Which PyTorch build belongs on this machine.

One module owns this, because the answer is needed in four places that must
agree: ``install.sh``, ``install.ps1``, the runtime checks in
:mod:`ebook_audiobook.device`, and the CI job that asserts the installers
resolve what they claim to. Before this existed the CUDA index URL was written
out in six places with no single source of truth, and the ROCm one in five —
so a bump could be applied to some and not others, and nothing would notice.

The installers reach this through ``python -m ebook_audiobook.torchbuild``,
which prints shell-parseable ``KEY=value`` lines. That works because the app is
installed into the virtualenv *before* the PyTorch step runs, so by the time the
decision is needed this module is importable.

A note on what "build" means here: CUDA and ROCm are not different versions of
PyTorch, they are different *builds* of the same version, published to different
package indexes. There is no single wheel that drives both an NVIDIA and an AMD
GPU, and there never will be. Picking the index is therefore the whole job.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Minimum Python the installers will build an environment on.
PYTHON_REQUIRED = (3, 11)


@dataclass(frozen=True)
class Build:
    """One PyTorch flavour and everything a caller needs to describe it."""

    id: str
    # Package index to resolve torch from. Empty means "PyPI default", which is
    # correct only on macOS, where the standard wheel already contains Metal
    # support and no CUDA build has ever existed.
    index_url: str
    label: str          # shown to the user as "Detected: ..."
    size: str           # advertised download size
    note: str = ""      # caveat worth printing underneath

    @property
    def is_gpu(self) -> bool:
        return self.id in ("cu124", "rocm", "mac")


# Download sizes are measured, not guessed — see the size assertion in CI.
BUILDS = {
    "cpu": Build(
        id="cpu",
        index_url="https://download.pytorch.org/whl/cpu",
        label="CPU only",
        size="about 250 MB",
    ),
    "cu124": Build(
        id="cu124",
        index_url="https://download.pytorch.org/whl/cu124",
        label="NVIDIA CUDA",
        size="about 2.5 GB",
    ),
    # Not interchangeable with the other ROCm indexes: this is the one carrying
    # the torch version Chatterbox pins. Changing it without re-checking that
    # the pin still resolves drops AMD users onto the CPU build silently, which
    # is why CI resolves against whatever is written here.
    "rocm": Build(
        id="rocm",
        index_url="https://download.pytorch.org/whl/rocm6.2.4",
        label="AMD ROCm",
        size="about 2 GB",
    ),
    # macOS has exactly one wheel, from PyPI, and it already contains Metal.
    # Neither --cpu nor --gpu can change what gets downloaded here.
    "mac": Build(
        id="mac",
        index_url="",
        label="Apple",
        size="about 250 MB",
    ),
}


# NVIDIA cards old enough that a modern CUDA build has no kernels for them.
# Matched on the marketing name, as a fallback for when the driver is too old to
# report a compute capability.
OLD_NVIDIA_PATTERNS = (
    r"\bGTX\s*(7|8|9|10)\d{2}\b",     # Kepler/Maxwell/Pascal GeForce
    r"\bTITAN\s*(X|Xp|Z)\b",
    r"\bQuadro\s*[MPK]\d",
    r"\bTesla\s*[KMP]\d",
)


def is_old_nvidia(gpu_name: str) -> bool:
    """Whether this GPU name looks like a pre-Turing card."""
    return any(re.search(p, gpu_name or "", re.IGNORECASE)
               for p in OLD_NVIDIA_PATTERNS)


def select(platform: str, arch: str = "", vendor: str = "",
           forced: str = "", gpu_name: str = "") -> Build:
    """The build for this machine.

    ``platform`` is ``linux``/``macos``/``windows``; ``vendor`` is
    ``nvidia``/``amd``/`""`; ``forced`` is the user's ``--cpu``/``--gpu``/
    ``--rocm`` choice, which is honoured everywhere it can be.

    macOS is settled first and deliberately ahead of ``forced``, because it is
    the one platform where the flags cannot change the answer — asking for CUDA
    there once made pip fetch a wheel that has never existed for any Mac.
    """
    if platform == "macos":
        return BUILDS["mac"]
    if forced == "cpu":
        return BUILDS["cpu"]
    if forced == "gpu":
        return BUILDS["cu124"]
    if forced == "rocm":
        return BUILDS["rocm"]
    if vendor == "nvidia":
        return BUILDS["cu124"]
    if vendor == "amd":
        return BUILDS["rocm"]
    return BUILDS["cpu"]


def arch_supported(torch) -> str | None:
    """Whether the installed build has kernels for the GPU that's present.

    Returns None when all is well, or a sentence naming the fix. A build whose
    architecture list omits the card loads happily and then fails on the first
    real work, which without this check surfaces as an opaque CUDA error hours
    into a render.
    """
    try:
        if not torch.cuda.is_available():
            return None
        major, minor = torch.cuda.get_device_capability(0)
        arches = torch.cuda.get_arch_list()
    except Exception:  # noqa: BLE001 - never let a probe break a render
        return None
    if not arches:
        return None
    # ROCm reports gfx* rather than sm_*; this check is CUDA-only.
    sm = [a for a in arches if a.startswith("sm_")]
    if not sm:
        return None
    if f"sm_{major}{minor}" in arches:
        return None
    return (f"this PyTorch has no kernels for your GPU (sm_{major}{minor}); "
            f"it was built for {', '.join(sm)}. Re-run the installer to get a "
            f"build that matches.")


def _emit(build: Build) -> str:
    """Shell-parseable output, consumed identically by both installers."""
    return "\n".join([
        f"EBAB_TORCH_ID={build.id}",
        f"EBAB_TORCH_INDEX={build.index_url}",
        f"EBAB_TORCH_LABEL={build.label}",
        f"EBAB_TORCH_SIZE={build.size}",
        f"EBAB_TORCH_NOTE={build.note}",
    ])


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(
        description="Print the PyTorch build to install on this machine.")
    p.add_argument("--platform", required=True,
                   choices=["linux", "macos", "windows"])
    p.add_argument("--arch", default="")
    p.add_argument("--vendor", default="", choices=["", "nvidia", "amd"])
    p.add_argument("--forced", default="", choices=["", "cpu", "gpu", "rocm"])
    p.add_argument("--gpu-name", dest="gpu_name", default="")
    a = p.parse_args(argv)
    print(_emit(select(a.platform, a.arch, a.vendor, a.forced, a.gpu_name)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
