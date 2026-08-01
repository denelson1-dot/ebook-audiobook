"""Paths and default settings.

All local, private data (imported books, rendered audio, voice clips, settings)
lives under a single root so it is trivial to back up or wipe, and so nothing
private is ever near the source tree. See :func:`data_root` for how that root is
chosen on each platform.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .platform_dirs import user_data_dir

# Parent of the ``ebook_audiobook`` package directory. In a source checkout this
# is the repo root; in an installed copy it is ``site-packages`` — which is why
# it is only ever used together with :func:`_is_source_checkout`.
REPO_ROOT = Path(__file__).resolve().parent.parent


def _is_source_checkout() -> bool:
    """True when we're running from a cloned repo rather than an installed wheel.

    Checked by looking for the project's own ``pyproject.toml`` as a sibling of
    the package. An installed copy sits in ``site-packages``, which has no such
    file, so this can never be fooled into treating a shared library directory
    as somebody's checkout.
    """
    return (REPO_ROOT / "pyproject.toml").is_file() and (REPO_ROOT / "ebook_audiobook").is_dir()


def legacy_repo_data_root() -> Path:
    """The old repo-local data location, kept working for existing checkouts."""
    return REPO_ROOT / "local-data"


def data_root() -> Path:
    """Resolve the one directory that holds everything this app stores.

    Precedence, first match wins:

    1. ``EBAB_DATA_ROOT`` — explicit override, always respected.
    2. A ``local-data/`` directory that already exists inside a source checkout.
       This is what installs from before the packaged release used, so an
       existing setup keeps its books, jobs, and settings exactly where they are.
    3. The per-user OS data directory (see :mod:`ebook_audiobook.platform_dirs`).
       This is what a fresh install gets: outside the source tree, so upgrading
       or reinstalling the app never touches the user's library.
    """
    env = os.environ.get("EBAB_DATA_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    if _is_source_checkout():
        legacy = legacy_repo_data_root()
        if legacy.is_dir():
            return legacy
    return user_data_dir()


@dataclass(frozen=True)
class Paths:
    """Resolved local-data subdirectories. Created on demand."""

    root: Path

    @property
    def imports(self) -> Path:
        return self.root / "imports"

    @property
    def jobs(self) -> Path:
        return self.root / "jobs"

    @property
    def voices(self) -> Path:
        return self.root / "voices"

    @property
    def outputs(self) -> Path:
        return self.root / "outputs"

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def tmp(self) -> Path:
        return self.root / "tmp"

    def ensure(self) -> "Paths":
        for p in (self.imports, self.jobs, self.voices, self.outputs, self.models, self.tmp):
            p.mkdir(parents=True, exist_ok=True)
        return self


def paths() -> Paths:
    return Paths(data_root())


# --- Pipeline defaults -------------------------------------------------------

# Chatterbox natively outputs 24 kHz mono. The fake engine matches so the rest
# of the pipeline is engine-agnostic.
SAMPLE_RATE = 24_000

# Chunking: Chatterbox is happiest on short utterances. Group whole sentences up
# to TARGET chars; never exceed MAX in a single generation.
CHUNK_TARGET_CHARS = 350
CHUNK_MAX_CHARS = 500

# Graduated pauses baked into assembled audio (seconds), by structural boundary.
# These give the narration "breathing room" that matches the book's structure —
# short between sentences, longer between paragraphs, longest at scene breaks.
# Tunable by ear via a preview.
PAUSE_SENTENCE = 0.3         # between sentence-group chunks within a paragraph
PAUSE_PARAGRAPH = 0.7        # between paragraphs
PAUSE_SCENE = 1.3            # at a scene break (e.g. "* * *")
PAUSE_CHAPTER_TITLE = 0.9    # after a chapter title, before its body
PAUSE_BETWEEN_CHAPTERS = 1.1  # trailing gap at the end of each chapter
PAUSE_BEFORE_OUTRO = 5.0     # long lead-in before the synthetic closing outro

# Boundary label -> pause. Set on each Segment by the chunker; consumed by the
# assembler. Does NOT affect the rendered audio (so it isn't part of a segment's
# content hash) — only the silence stitched between segments.
BOUNDARY_PAUSES = {
    "sentence": PAUSE_SENTENCE,
    "paragraph": PAUSE_PARAGRAPH,
    "scene": PAUSE_SCENE,
    "chapter_title": PAUSE_CHAPTER_TITLE,
}

# Output encode.
DEFAULT_BITRATE_KBPS = 64  # mono spoken word; ~28 MB/hour.
SIZE_WARN_BYTES = 1_288_490_188  # 1.2 GB soft target from the plan.

# Rough speech rate for estimation (characters of normalized text per audio
# second). ~15 chars/s ≈ 150 wpm. Refined empirically after a preview render.
CHARS_PER_AUDIO_SECOND = 15.0


# Chatterbox generate() defaults (from chatterbox-tts 0.1.7). These are the full
# set of tunable knobs; there is no seed/top_k/speed in the engine API (we apply
# the seed ourselves via torch.manual_seed).
DEFAULT_EXAGGERATION = 0.5
DEFAULT_CFG_WEIGHT = 0.5
DEFAULT_TEMPERATURE = 0.8
DEFAULT_REPETITION_PENALTY = 1.2
DEFAULT_MIN_P = 0.05
DEFAULT_TOP_P = 1.0


@dataclass
class VoiceSettings:
    """User-selectable narration settings.

    The fields returned by :meth:`render_key` affect the *rendered audio* and so
    are hashed into each segment's identity (changing one re-renders). Settings
    that only affect the final container/encode (e.g. bitrate, in ``extra``) are
    deliberately excluded from that key so tweaking them never re-renders audio.
    """

    engine: str = "chatterbox"  # "chatterbox" | "fake"
    # Path to a rights-cleared local reference clip, or None for the engine's
    # built-in default narrator voice.
    reference_clip: str | None = None
    # Chatterbox generation knobs (ignored by other engines).
    exaggeration: float = DEFAULT_EXAGGERATION
    cfg_weight: float = DEFAULT_CFG_WEIGHT
    temperature: float = DEFAULT_TEMPERATURE
    repetition_penalty: float = DEFAULT_REPETITION_PENALTY
    min_p: float = DEFAULT_MIN_P
    top_p: float = DEFAULT_TOP_P
    seed: int = 0
    # Encode-only / miscellaneous settings (e.g. bitrate_kbps). NOT hashed into
    # the render key.
    extra: dict = field(default_factory=dict)

    # Fields that change the rendered audio (used for content-addressing).
    _RENDER_FIELDS = (
        "engine", "reference_clip", "exaggeration", "cfg_weight",
        "temperature", "repetition_penalty", "min_p", "top_p", "seed",
    )

    def render_key(self) -> dict:
        """The audio-affecting subset, for hashing. ``reference_clip`` is folded
        in by content elsewhere (see hashing.voice_key)."""
        return {f: getattr(self, f) for f in self._RENDER_FIELDS}

    def to_dict(self) -> dict:
        return {
            "engine": self.engine,
            "reference_clip": self.reference_clip,
            "exaggeration": self.exaggeration,
            "cfg_weight": self.cfg_weight,
            "temperature": self.temperature,
            "repetition_penalty": self.repetition_penalty,
            "min_p": self.min_p,
            "top_p": self.top_p,
            "seed": self.seed,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "VoiceSettings":
        return cls(
            engine=d.get("engine", "chatterbox"),
            reference_clip=d.get("reference_clip"),
            exaggeration=d.get("exaggeration", DEFAULT_EXAGGERATION),
            cfg_weight=d.get("cfg_weight", DEFAULT_CFG_WEIGHT),
            temperature=d.get("temperature", DEFAULT_TEMPERATURE),
            repetition_penalty=d.get("repetition_penalty", DEFAULT_REPETITION_PENALTY),
            min_p=d.get("min_p", DEFAULT_MIN_P),
            top_p=d.get("top_p", DEFAULT_TOP_P),
            seed=d.get("seed", 0),
            extra=d.get("extra", {}),
        )
