"""The one abstraction that matters.

Everything above this line (extract/normalize/chunk/assemble) is engine-agnostic
and testable on CPU. Engines implement ``synthesize`` and expose an
``engine_version`` that participates in each segment's content hash, so swapping
or upgrading an engine correctly invalidates cached audio.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np


@dataclass
class VoiceConfig:
    reference_clip: str | None = None
    exaggeration: float = 0.5
    cfg_weight: float = 0.5
    temperature: float = 0.8
    repetition_penalty: float = 1.2
    min_p: float = 0.05
    top_p: float = 1.0
    seed: int = 0
    sample_rate: int = 24_000


@dataclass
class AudioClip:
    samples: np.ndarray  # mono float32 in [-1, 1]
    sample_rate: int


class TTSAdapter(ABC):
    def __init__(self, voice: VoiceConfig):
        self.voice = voice

    @property
    @abstractmethod
    def engine_version(self) -> str:
        """Stable identifier for engine + model + params that affect output."""

    def load(self) -> None:
        """Load heavy model state once, before a batch of synthesize calls."""

    def unload(self) -> None:
        """Release model/GPU memory (e.g. before running a Whisper QA pass)."""

    @abstractmethod
    def synthesize(self, text: str) -> AudioClip:
        """Render one text chunk to a mono float32 clip."""
