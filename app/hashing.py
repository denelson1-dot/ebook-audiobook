"""Content addressing.

The whole pipeline hangs off one idea: a segment's identity is a hash of the
exact text plus the exact voice settings plus the engine version. Store the
rendered WAV under that id and resume, cache-invalidation, and re-render all
fall out for free.
"""

from __future__ import annotations

import hashlib
import json

from .config import VoiceSettings


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def text_hash(text: str) -> str:
    return _sha(text.encode("utf-8"))[:16]


def file_hash(path) -> str:
    """Stream a file through sha256 (used to identify an imported ebook)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()[:16]


def voice_key(voice: VoiceSettings, sample_rate: int) -> str:
    """Canonical, order-stable key for the audio-affecting voice settings.

    ``reference_clip`` is folded in by *content* (its file hash) when present, so
    swapping the clip file for a different one invalidates cached audio even if
    the path is reused.
    """
    # Only render-affecting fields (render_key) are hashed — encode-only settings
    # like bitrate live in voice.extra and must NOT invalidate cached audio.
    payload = dict(voice.render_key())
    ref = payload.get("reference_clip")
    if ref:
        try:
            payload["reference_clip"] = file_hash(ref)
        except OSError:
            payload["reference_clip"] = f"path:{ref}"
    payload["sample_rate"] = sample_rate
    return _sha(json.dumps(payload, sort_keys=True).encode("utf-8"))[:16]


def segment_id(text: str, engine_version: str, voice_key_str: str) -> str:
    """Stable id for a rendered segment: text + engine + voice settings."""
    key = f"{engine_version}\x00{voice_key_str}\x00{text}"
    return _sha(key.encode("utf-8"))[:20]
