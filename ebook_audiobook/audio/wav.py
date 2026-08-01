"""Thin WAV helpers over soundfile/numpy.

All internal audio is mono float32 at a single sample rate; the final AAC encode
is handled by ffmpeg in the package stage.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf


def write_wav(path, samples: np.ndarray, sample_rate: int) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.asarray(samples, dtype=np.float32)
    # Write to a temp file in the same directory, then atomically rename. A crash
    # mid-write can then never leave a truncated segment behind — the final path
    # only ever appears complete, and an interrupted write just re-renders. The
    # temp suffix isn't ".wav", so pass format explicitly. 16-bit PCM: small,
    # universally readable, plenty for speech.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".wav.tmp")
    os.close(fd)
    try:
        sf.write(tmp, data, sample_rate, subtype="PCM_16", format="WAV")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_wav(path) -> tuple[np.ndarray, int]:
    data, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if data.ndim > 1:  # collapse to mono if a clip came in stereo
        data = data.mean(axis=1)
    return data, sr


def duration_seconds(path) -> float:
    info = sf.info(str(path))
    return float(info.frames) / float(info.samplerate)


def silence(seconds: float, sample_rate: int) -> np.ndarray:
    n = max(0, int(round(seconds * sample_rate)))
    return np.zeros(n, dtype=np.float32)


def is_valid_audio(path, min_seconds: float = 0.05) -> bool:
    """A rendered segment is valid if it decodes and is longer than a floor."""
    try:
        return duration_seconds(path) >= min_seconds
    except Exception:
        return False
