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

# The torch version we install, pinned exactly and deliberately.
#
# Chatterbox declares `torch==2.6.0`, which is why it used to be installed
# normally. We now install Chatterbox with --no-deps and choose torch ourselves,
# because 2.6.0 has no kernels for current hardware: its CUDA build stops at
# sm_90 (so no RTX 50-series) and the matching ROCm index predates RDNA4 (so no
# RX 9000-series).
#
# The pin has to be exact rather than a floor. PyPI's torch is far ahead of what
# the pinned indexes carry, and PEP 440 ranks a plain 2.13.0 above 2.9.1+cu128 —
# so `torch>=2.9` with PyPI as an extra index resolves to the default CUDA build
# from PyPI and quietly undoes the whole choice. That is exactly the 1.0.2 bug
# where a "250 MB" CPU install put 6.4 GB on disk. CI asserts this.
TORCH_PIN = "2.9.1"

# Chatterbox is installed with --no-deps, so this is its dependency list and we
# own it. Differences from what Chatterbox declares, each deliberate:
#
#   torch/torchaudio  omitted   we pin them ourselves, above
#   einops            ADDED     Chatterbox imports it unguarded in three
#                               modules but never declares it; it arrives
#                               transitively today by luck, and --no-deps ends
#                               that luck
#   gradio            dropped   never imported by the library, only by their
#                               demo app; a large download for nothing
#   spacy-pkuseg      dropped   Chinese segmentation, already behind a
#                               try/except that logs and continues
#   numpy/scipy       pinned    Chatterbox switches numpy major at Python 3.13;
#                               scipy must agree with whichever numpy lands or
#                               it refuses to import
#
# Re-check this list whenever CHATTERBOX_PIN moves. CI installs it for real.
CHATTERBOX_PIN = "chatterbox-tts==0.1.7"
CHATTERBOX_DEPS = (
    "librosa==0.11.0",
    "s3tokenizer",
    "transformers==5.2.0",
    "diffusers==0.29.0",
    "resemble-perth>=1.0.0",
    "conformer==0.3.2",
    "safetensors==0.5.3",
    "pykakasi==2.3.0",
    "pyloudnorm",
    "omegaconf",
    "einops",
    # resemble-perth still imports pkg_resources, removed in setuptools 81.
    "setuptools<81",
)

# Packages that must NOT end up installed. Their presence means the curated list
# above was bypassed and Chatterbox's own resolution ran instead.
FORBIDDEN_PACKAGES = ("gradio", "spacy-pkuseg")

# Lowest compute capability the default CUDA build has kernels for. Measured
# from the wheel, not assumed: torch 2.9.1+cu128 reports
# sm_70/75/80/86/90/100/120, so it gains Blackwell (RTX 50-series) and loses
# Pascal (GTX 10-series, sm_61) and Maxwell. Cards below this get cu126.
CU128_MIN_CC = (7, 0)


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
        return self.id != "cpu"


# Every index here must carry TORCH_PIN for the Python versions we support.
# CI resolves against each one and asserts the local version tag, so a bump that
# breaks this fails the build rather than shipping.
BUILDS = {
    "cpu": Build(
        id="cpu",
        index_url="https://download.pytorch.org/whl/cpu",
        label="CPU only",
        size="about 250 MB",
    ),
    # The default NVIDIA build. Covers Turing through Blackwell — crucially
    # sm_120, which is the RTX 50-series and which no CUDA 12.4 build has.
    "cu128": Build(
        id="cu128",
        index_url="https://download.pytorch.org/whl/cu128",
        label="NVIDIA CUDA 12.8",
        size="about 3 GB",
    ),
    # For cards older than Turing. cu128 has no kernels below sm_70, so a GTX
    # 10-series would install cleanly and then fail on the first render.
    "cu126": Build(
        id="cu126",
        index_url="https://download.pytorch.org/whl/cu126",
        label="NVIDIA CUDA 12.6",
        size="about 3 GB",
        note="chosen because this GPU predates the newer CUDA build",
    ),
    # ROCm 6.4 is the oldest index carrying TORCH_PIN that also supports RDNA4
    # (RX 9000-series). It needs a newer amdgpu kernel driver than 6.2.x did.
    "rocm": Build(
        id="rocm",
        index_url="https://download.pytorch.org/whl/rocm6.4",
        label="AMD ROCm 6.4",
        size="about 2.5 GB",
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


def cuda_build_for(compute_caps: "list[tuple[int, int]] | None" = None,
                   gpu_name: str = "") -> Build:
    """Which CUDA build has kernels for these GPUs.

    ``compute_caps`` is one ``(major, minor)`` per GPU. The **minimum** decides,
    not the first: a machine with a GTX 1080 Ti beside an RTX 5090 needs a build
    that runs on both, and only the older build does.

    With no capability available — an old driver that can't report one — the GPU
    name is the fallback, and failing that we take the newer build and say so.
    Guessing new is the right way round: the older cards are the rarer case, and
    the runtime check in :func:`arch_supported` catches a wrong guess with an
    actionable message instead of an opaque CUDA error.
    """
    if compute_caps:
        return BUILDS["cu126"] if min(compute_caps) < CU128_MIN_CC else BUILDS["cu128"]
    if gpu_name and is_old_nvidia(gpu_name):
        return BUILDS["cu126"]
    return BUILDS["cu128"]


def select(platform: str, arch: str = "", vendor: str = "",
           forced: str = "", gpu_name: str = "",
           compute_caps: "list[tuple[int, int]] | None" = None) -> Build:
    """The build for this machine.

    ``platform`` is ``linux``/``macos``/``windows``; ``vendor`` is
    ``nvidia``/``amd``/`""`; ``forced`` is the user's ``--cpu``/``--gpu``/
    ``--rocm``/``--cuda126``/``--cuda128`` choice, honoured everywhere it can be.

    macOS is settled first and deliberately ahead of ``forced``, because it is
    the one platform where the flags cannot change the answer — asking for CUDA
    there once made pip fetch a wheel that has never existed for any Mac.
    """
    if platform == "macos":
        return BUILDS["mac"]
    if forced == "cpu":
        return BUILDS["cpu"]
    if forced == "cuda126":
        return BUILDS["cu126"]
    if forced == "cuda128":
        return BUILDS["cu128"]
    if forced == "gpu":
        return cuda_build_for(compute_caps, gpu_name)
    if forced == "rocm":
        return BUILDS["rocm"]
    if vendor == "nvidia":
        return cuda_build_for(compute_caps, gpu_name)
    if vendor == "amd":
        return BUILDS["rocm"]
    return BUILDS["cpu"]


def install_commands(build: Build) -> list[list[str]]:
    """The pip invocations that install the engine, in order.

    Three commands rather than one, and the order matters. torch is pinned and
    resolved from the chosen index first; Chatterbox goes in with --no-deps so
    its `torch==2.6.0` cannot drag the pinned build back down; then the curated
    dependency list, constrained so nothing in it can move torch either.
    """
    index = (["--index-url", build.index_url,
              "--extra-index-url", "https://pypi.org/simple"]
             if build.index_url else [])
    pins = [f"torch=={TORCH_PIN}", f"torchaudio=={TORCH_PIN}"]
    return [
        ["install", *index, *pins],
        ["install", "--no-deps", CHATTERBOX_PIN],
        # The pins are repeated here as ordinary requirements, which makes pip
        # resolve them together with the dependency list. Without that, anything
        # in the list that requires torch is free to replace the build just
        # chosen — s3tokenizer does exactly this.
        ["install", *index, *pins, *CHATTERBOX_DEPS],
    ]


def _parse_sm(arch: str) -> "tuple[int, int] | None":
    """``"sm_86"`` -> ``(8, 6)``. The last digit is the minor version."""
    if not arch.startswith("sm_"):
        return None
    digits = arch[3:]
    if not digits.isdigit() or len(digits) < 2:
        return None
    return int(digits[:-1]), int(digits[-1])


def covers(arches: "list[str]", capability: "tuple[int, int]") -> bool:
    """Whether a build's architecture list can run on this GPU.

    Not an exact string match, because CUDA cubins are forward-compatible across
    *minor* versions within the same major: a binary built for sm_86 runs on an
    sm_89 device. That rule is why RTX 40-series cards (sm_89) work on builds
    that list only up to sm_86, and why a GTX 1080 Ti (sm_61) runs on the sm_60
    cubin in the CUDA 12.6 build. Matching exactly would tell both of those
    users their card was unsupported while it worked perfectly well.
    """
    major, minor = capability
    for arch in arches:
        parsed = _parse_sm(arch)
        if parsed and parsed[0] == major and parsed[1] <= minor:
            return True
    return False


def arch_supported(torch) -> str | None:
    """Whether the installed build has kernels for the GPU that's present.

    Returns None when all is well, or a sentence naming the fix. A build whose
    architecture list can't reach the card loads happily and then fails on the
    first real work, which without this check surfaces as an opaque CUDA error
    hours into a render.
    """
    try:
        if not torch.cuda.is_available():
            return None
        capability = torch.cuda.get_device_capability(0)
        arches = torch.cuda.get_arch_list()
    except Exception:  # noqa: BLE001 - never let a probe break a render
        return None
    # ROCm reports gfx* rather than sm_*; this check is CUDA-only.
    sm = [a for a in arches if a.startswith("sm_")]
    if not sm or covers(sm, capability):
        return None
    major, minor = capability
    newer = capability >= CU128_MIN_CC
    remedy = ("--cuda128" if newer else "--cuda126")
    return (f"this PyTorch has no kernels for your GPU (sm_{major}{minor}); "
            f"it was built for {', '.join(sm)}. Re-run the installer with "
            f"{remedy} to get a build that matches.")


def _emit(build: Build) -> str:
    """Shell-parseable output, consumed identically by both installers."""
    return "\n".join([
        f"EBAB_TORCH_ID={build.id}",
        f"EBAB_TORCH_PIN={TORCH_PIN}",
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
    p.add_argument("--forced", default="",
                   choices=["", "cpu", "gpu", "rocm", "cuda126", "cuda128"])
    p.add_argument("--gpu-name", dest="gpu_name", default="")
    p.add_argument("--compute-caps", dest="compute_caps", default="",
                   help="comma-separated per-GPU compute capabilities, e.g. '8.6,6.1'")
    a = p.parse_args(argv)
    caps = []
    for raw in (a.compute_caps or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            major, _, minor = raw.partition(".")
            caps.append((int(major), int(minor or 0)))
        except ValueError:
            # A driver that reports something unexpected must not break the
            # install; fall through to name-based detection instead.
            continue
    print(_emit(select(a.platform, a.arch, a.vendor, a.forced, a.gpu_name,
                       caps or None)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
