"""How much of the speech engine fits on this machine's graphics card.

Chatterbox at full precision needs 3.6-4.1 GB on the card while it narrates,
and a "4 GB" laptop card leaves nearer 3.5 GB free. The model *loads* in that
space and only overflows partway through the first passage. Windows' NVIDIA
driver then quietly borrows system RAM instead of reporting an error, and the
render crawls at worse than CPU speed with the GPU sitting idle. No error means
no fallback, and the user sees an estimate of hundreds of hours with no reason.

So the way the engine runs is chosen from what the card actually has free,
measured at load time, and PyTorch is capped at that budget (see
:func:`fraction_for`). An overflow then raises an out-of-memory error the
adapter handles, instead of silently spilling. Measured on an RTX 3070 Ti with
the app's longest passage (500 characters):

``full``
    Full precision, as the app always ran. The speech tokenizer, which is only
    used once to read the voice clip, moves off the card afterwards. That saves
    0.46 GB at no cost: the audio is bit-for-bit what it was before.
``compact``
    The same, with the main model (T3) in bfloat16: about a gigabyte smaller
    and roughly 15% slower. The audio is the same voice, but not the same
    bytes, so it is part of the segment cache key (see
    :func:`version_suffix`). Needs native bfloat16, i.e. an RTX 30-series or
    newer, or a recent Radeon.

The CPU is always the last rung. A book keeps the tier it was first narrated
with (its *pin*, stored on the job), so a resumed render never re-renders hours
of audio just because another program is holding some of the card today. It
may *run* on a lower rung, as the out-of-memory fallback always could.

``EBAB_VRAM_BUDGET_GB`` caps the budget, to keep part of the card free for
something else, or to try the compact tier on a big card.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

FULL = "full"
COMPACT = "compact"
TIERS = (FULL, COMPACT)

GiB = 1024**3

ENGLISH = "en"
MULTILINGUAL = "mtl"

# Peak memory PyTorch reserves while narrating, with the tokenizer already off
# the card. Ordinary passages of a real book peaked at up to 3.69 GB at full
# precision and 2.50 GB compact (English, RTX 3070 Ti, chatterbox-tts 0.1.7,
# torch 2.9.1); a passage the model samples for longer goes higher, and the
# multilingual model peaks about half a gigabyte above English at full
# precision. Deliberately generous: guessing high costs a book 15% of speed on
# the compact tier, while guessing low means an overflow partway through and
# the rest of the render on the CPU.
NEEDS = {
    (ENGLISH, FULL): int(4.20 * GiB),
    (ENGLISH, COMPACT): int(2.90 * GiB),
    (MULTILINGUAL, FULL): int(4.70 * GiB),
    (MULTILINGUAL, COMPACT): int(3.00 * GiB),
}
# What loading alone takes: the weights plus the speech tokenizer, which is on
# the card until the voice has been prepared. Enough to try a tier on a card
# short of its full need; it may then overflow on a long passage and step down,
# which still beats starting on the CPU.
LOAD_NEEDS = {FULL: int(3.45 * GiB), COMPACT: int(2.40 * GiB)}

# Left outside PyTorch's pool. The CUDA context grows as kernels load on first
# use (0.21 GB measured once narration has started), and nothing else caps it.
# Not more on Windows: a 4 GB laptop's compact tier peaked at 2.92 GB inside a
# 3.0 GB budget, and a smaller budget would only have it fight the cap.
HEADROOM = 256 * 1024**2

BUDGET_ENV = "EBAB_VRAM_BUDGET_GB"


@dataclass(frozen=True)
class Probe:
    """What the device can offer, measured just before loading."""

    kind: str                  # "cuda" | "mps" | "cpu"
    free: int | None = None    # bytes free on the card right now (cuda only)
    total: int | None = None   # bytes on the card
    bf16: bool = False         # native bfloat16
    capped_by_env: bool = False

    @property
    def budget(self) -> int | None:
        """Bytes PyTorch may use: the free memory less the context's headroom,
        and no more than ``EBAB_VRAM_BUDGET_GB``."""
        if self.kind != "cuda" or self.free is None:
            return None
        budget = max(0, self.free - HEADROOM)
        limit = env_budget()
        return min(budget, limit) if limit is not None else budget


@dataclass(frozen=True)
class Rung:
    """One way to run the engine."""

    device: str   # "cuda" | "mps" | "cpu"
    tier: str     # FULL | COMPACT


def env_budget() -> int | None:
    raw = (os.environ.get(BUDGET_ENV) or "").strip()
    if not raw:
        return None
    try:
        gb = float(raw)
    except ValueError:
        return None
    return int(gb * GiB) if gb > 0 else None


def model_for(language: str | None) -> str:
    return ENGLISH if (language or "en") == "en" else MULTILINGUAL


def ladder(probe: Probe, pinned: str | None = None, model: str = ENGLISH) -> list[Rung]:
    """Every way to run on this machine, in the order to try them.

    The rungs that fit the budget come first, most faithful first. When none
    does, the smallest one that would at least load is still worth a try before
    the CPU, which is always last. A book pinned to ``compact`` never runs above
    it: its cached audio is compact audio.
    """
    cpu = Rung("cpu", FULL)
    if probe.kind == "mps":
        # Unified memory: the question of fitting on a card doesn't arise.
        return [Rung("mps", FULL), cpu]
    if probe.kind != "cuda":
        return [cpu]

    gpu = [Rung("cuda", FULL)]
    if probe.bf16:
        gpu.append(Rung("cuda", COMPACT))
        if pinned == COMPACT:
            gpu = gpu[1:]
    budget = probe.budget
    if budget is None:
        return gpu + [cpu]
    fits = [r for r in gpu if budget >= NEEDS[(model, r.tier)]]
    if not fits:
        # Largest first, so the last one that loads is the smallest.
        fits = [r for r in gpu if budget >= LOAD_NEEDS[r.tier]][-1:]
    return fits + [cpu]


def identity(pinned: str | None, loaded: Rung) -> str:
    """The tier a book's audio belongs to: its pin, or what it first loaded as."""
    return pinned if pinned in TIERS else loaded.tier


def version_suffix(tier: str) -> str:
    """What the tier adds to the engine version, and so to every segment's
    content hash. Full precision adds nothing, so audio cached before tiers
    existed is still found."""
    return "-bf16" if tier == COMPACT else ""


def fraction_for(probe: Probe) -> float | None:
    """The share of the card to allow PyTorch, for
    ``torch.cuda.set_per_process_memory_fraction``. None when there is no card
    or nothing to cap."""
    budget = probe.budget
    if budget is None or not probe.total:
        return None
    return max(0.05, min(1.0, budget / probe.total))


def probe(kind: str) -> Probe:
    """Measure the device the engine is about to load onto. Never raises."""
    capped = env_budget() is not None
    if kind != "cuda":
        return Probe(kind, capped_by_env=capped)
    try:
        import gc

        import torch

        # Anything an earlier task left in this process's pool goes back first:
        # a model kept alive by a reference cycle, or a cache nobody emptied.
        gc.collect()
        torch.cuda.empty_cache()
        free, total = torch.cuda.mem_get_info()
        try:
            bf16 = bool(torch.cuda.is_bf16_supported(including_emulation=False))
        except TypeError:  # an older torch without the argument
            major, _minor = torch.cuda.get_device_capability(0)
            bf16 = major >= 8
        return Probe("cuda", int(free), int(total), bf16, capped)
    except Exception:  # noqa: BLE001 - an unreadable card is treated as roomy
        return Probe("cuda", capped_by_env=capped)


def gb(n: int | None) -> str:
    return "?" if n is None else f"{n / GiB:.1f}"


def label(rung: Rung) -> str:
    return rung.device if rung.tier == FULL else f"{rung.device}, {rung.tier}"


def describe_ladder(rungs: list[Rung]) -> str:
    return " -> ".join(label(r) for r in rungs)
