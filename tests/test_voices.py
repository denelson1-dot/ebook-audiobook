import shutil
import subprocess

import numpy as np
import pytest
import soundfile as sf

from app.voices import DEFAULT_VOICE_ID, VoiceLibrary

HAVE_FFMPEG = shutil.which("ffmpeg") is not None


@pytest.fixture
def clip(tmp_path):
    p = tmp_path / "sample.wav"
    sf.write(str(p), np.zeros(2400, dtype=np.float32), 24000, subtype="PCM_16")
    return str(p)


def test_default_voice_always_present():
    lib = VoiceLibrary()
    ids = [v.id for v in lib.list()]
    assert DEFAULT_VOICE_ID in ids
    assert lib.get(DEFAULT_VOICE_ID).is_default
    assert lib.clip_path(DEFAULT_VOICE_ID) is None


def test_add_list_get_delete(clip):
    lib = VoiceLibrary()
    v = lib.add("Warm Baritone", src_path=clip)
    assert v.id == "warm-baritone"
    assert not v.is_default
    assert lib.get(v.id).name == "Warm Baritone"
    assert lib.clip_path(v.id).exists()

    assert v.id in [x.id for x in lib.list()]
    assert lib.delete(v.id) is True
    assert lib.get(v.id) is None
    assert not lib.clip_path(v.id) if lib.clip_path(v.id) else True


def test_duplicate_names_get_unique_ids(clip):
    lib = VoiceLibrary()
    a = lib.add("Voice", src_path=clip)
    b = lib.add("Voice", src_path=clip)
    assert a.id != b.id


def test_cannot_delete_default():
    assert VoiceLibrary().delete(DEFAULT_VOICE_ID) is False


def test_unsupported_format_rejected(tmp_path):
    bad = tmp_path / "notaudio.txt"
    bad.write_text("nope")
    with pytest.raises(ValueError):
        VoiceLibrary().add("Bad", src_path=str(bad))


def test_wav_input_stored_as_wav(clip):
    v = VoiceLibrary().add("Plain Wav", src_path=clip)
    assert v.clip_filename.endswith(".wav")


@pytest.mark.ffmpeg
@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_mp4_audio_import_transcodes_to_wav(tmp_path, clip):
    # Build a real .mp4 (AAC audio) from the wav, then import it.
    mp4 = tmp_path / "voice.mp4"
    subprocess.run(["ffmpeg", "-y", "-i", clip, "-c:a", "aac", str(mp4)],
                   capture_output=True, check=True)
    v = VoiceLibrary().add("From MP4", src_path=str(mp4))
    clip_path = VoiceLibrary().clip_path(v.id)
    assert clip_path.suffix == ".wav" and clip_path.exists()
    # And it's a readable WAV.
    assert sf.info(str(clip_path)).frames > 0
