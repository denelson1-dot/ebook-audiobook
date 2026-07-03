from app import config
from app.audio import estimate


def test_audio_seconds():
    assert estimate.estimate_audio_seconds(1500) == 100.0  # 15 chars/sec


def test_size_bytes_matches_bitrate():
    # 64 kbps for 100 s = 64000 bits/s * 100 / 8 = 800,000 bytes
    assert estimate.estimate_size_bytes(100, 64) == 800_000


def test_render_seconds_none_without_rate():
    assert estimate.estimate_render_seconds(1000, None) is None
    assert estimate.estimate_render_seconds(1000, 0) is None


def test_render_seconds_with_rate():
    assert estimate.estimate_render_seconds(1000, 50) == 20.0


def test_over_size_target_flag():
    # ~1.2 GB target. 50 hours of audio at 96 kbps blows past it.
    big_chars = int(50 * 3600 * config.CHARS_PER_AUDIO_SECOND)
    est = estimate.estimate(big_chars, 96)
    assert est.over_size_target is True


def test_normal_book_under_target():
    # A 15-hour book at 64 kbps is comfortably under 1.2 GB.
    chars = int(15 * 3600 * config.CHARS_PER_AUDIO_SECOND)
    est = estimate.estimate(chars, 64)
    assert est.over_size_target is False
    assert "MB" in est.human()
