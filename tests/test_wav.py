import numpy as np
import pytest

from app.audio import wav
from app.audio.wav import duration_seconds, is_valid_audio, read_wav, write_wav


def _tone(seconds=1.0, sr=24000):
    return (0.1 * np.sin(np.linspace(0, 400, int(seconds * sr)))).astype(np.float32)


def test_write_wav_roundtrips_and_leaves_no_temp(tmp_path):
    p = tmp_path / "seg_abc.wav"
    write_wav(p, _tone(1.0), 24000)
    data, sr = read_wav(p)
    assert sr == 24000 and abs(duration_seconds(p) - 1.0) < 0.01 and is_valid_audio(p)
    # The atomic temp file must not linger next to the real segment.
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_wav_is_atomic_on_failure(tmp_path, monkeypatch):
    p = tmp_path / "seg_abc.wav"
    write_wav(p, _tone(1.0), 24000)  # a good, complete segment already on disk

    # A crash mid-write must NOT replace or truncate the existing file, and must
    # not leave a stray temp file — the reader keeps seeing the intact segment.
    def boom(*a, **k):
        raise RuntimeError("killed mid-write")
    monkeypatch.setattr(wav.sf, "write", boom)

    with pytest.raises(RuntimeError, match="killed mid-write"):
        write_wav(p, _tone(0.5), 24000)

    assert abs(duration_seconds(p) - 1.0) < 0.01  # original untouched, not truncated
    assert list(tmp_path.glob("*.tmp")) == []     # temp cleaned up
