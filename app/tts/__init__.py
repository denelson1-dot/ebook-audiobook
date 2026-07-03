"""Text-to-speech engines behind a single small interface.

``get_adapter(voice)`` is the only entry point the rest of the app uses.
"""

from __future__ import annotations

from ..config import VoiceSettings
from .adapter import AudioClip, TTSAdapter, VoiceConfig
from .fake import FakeAdapter


def get_adapter(voice: VoiceSettings, sample_rate: int) -> TTSAdapter:
    cfg = VoiceConfig(
        reference_clip=voice.reference_clip,
        exaggeration=voice.exaggeration,
        cfg_weight=voice.cfg_weight,
        temperature=voice.temperature,
        repetition_penalty=voice.repetition_penalty,
        min_p=voice.min_p,
        top_p=voice.top_p,
        seed=voice.seed,
        sample_rate=sample_rate,
    )
    if voice.engine == "fake":
        return FakeAdapter(cfg)
    if voice.engine == "chatterbox":
        # Imported lazily so torch is only required when actually rendering.
        from .chatterbox import ChatterboxAdapter

        return ChatterboxAdapter(cfg)
    raise ValueError(f"unknown TTS engine: {voice.engine!r}")


__all__ = ["AudioClip", "TTSAdapter", "VoiceConfig", "FakeAdapter", "get_adapter"]
