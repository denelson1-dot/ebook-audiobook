"""Assemble rendered segment WAVs into per-chapter WAVs.

Streams through soundfile so a long chapter never has to sit in RAM in full.
Between segments it inserts a *graduated* pause chosen by each segment's
structural boundary (sentence < paragraph < scene), and a longer pause at the
end of each chapter (the gap before the next chapter in the final file).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from .. import config
from ..audio.wav import read_wav


def gap_for(boundary: str) -> float:
    """Seconds of silence to insert after a segment with this boundary."""
    return config.BOUNDARY_PAUSES.get(boundary, config.PAUSE_SENTENCE)


def assemble_chapter(
    out_path: Path,
    specs: list[tuple[Path, float]],
    sample_rate: int = config.SAMPLE_RATE,
    chapter_pause: float = config.PAUSE_BETWEEN_CHAPTERS,
    lead_pause: float = 0.0,
) -> float:
    """Write one chapter WAV; return its duration in seconds.

    ``specs`` is a list of ``(segment_wav_path, gap_after_seconds)``. The gap
    after the final segment is ignored in favour of ``chapter_pause`` (the gap
    belongs between chapters, not inside one). ``lead_pause`` prepends silence
    before the first segment — used to set the closing outro clearly apart.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    chap_gap = np.zeros(int(round(chapter_pause * sample_rate)), dtype=np.float32)

    total_frames = 0
    with sf.SoundFile(str(out_path), "w", samplerate=sample_rate, channels=1, subtype="PCM_16") as out:
        if lead_pause > 0:
            lead = np.zeros(int(round(lead_pause * sample_rate)), dtype=np.float32)
            out.write(lead)
            total_frames += len(lead)
        for i, (seg_path, gap_after) in enumerate(specs):
            data, sr = read_wav(seg_path)
            if sr != sample_rate:
                raise ValueError(f"segment {seg_path} sample rate {sr} != {sample_rate}")
            out.write(data)
            total_frames += len(data)
            if i < len(specs) - 1:
                gap = np.zeros(int(round(gap_after * sample_rate)), dtype=np.float32)
                out.write(gap)
                total_frames += len(gap)
        out.write(chap_gap)
        total_frames += len(chap_gap)

    return total_frames / sample_rate
