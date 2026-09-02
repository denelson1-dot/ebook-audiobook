"""Deterministic no-dependency engine for tests and full dry runs.

Produces quiet, decaying tones whose duration scales with the text length, so
the whole pipeline (chunking, assembly, chapter markers, .m4b packaging) can be
exercised end-to-end without a GPU, model download, or network.
"""

from __future__ import annotations

import hashlib

import numpy as np

from .adapter import AudioClip, TTSAdapter, VoiceConfig


class FakeAdapter(TTSAdapter):
    def __init__(self, voice: VoiceConfig):
        super().__init__(voice)

    @property
    def engine_version(self) -> str:
        # English keeps the version every existing test hash was made with; any
        # other language shows in it, so a test can prove the language arrived.
        lang = self.voice.language or "en"
        return "fake-1" if lang == "en" else f"fake-1-{lang}"

    def synthesize(self, text: str) -> AudioClip:
        sr = self.voice.sample_rate
        # ~15 chars/sec of "speech", floored so even short chunks are audible.
        seconds = max(0.3, len(text) / 15.0)
        n = int(seconds * sr)
        t = np.arange(n, dtype=np.float32) / sr
        # Pitch derived from the text hash -> stable per chunk, varied across.
        seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)
        freq = 110.0 + (seed % 220)
        envelope = np.exp(-t * 0.5).astype(np.float32)
        samples = 0.05 * envelope * np.sin(2 * np.pi * freq * t).astype(np.float32)
        return AudioClip(samples=samples, sample_rate=sr)
