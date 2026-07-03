"""Output estimation: audio duration, encoded size, and render wall-clock.

These are pre-render sanity checks so a multi-hour job doesn't surprise you.
The render-rate figure is a placeholder until a preview measures the real rate
on this machine.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import config


def estimate_audio_seconds(total_chars: int) -> float:
    return total_chars / config.CHARS_PER_AUDIO_SECOND


def estimate_size_bytes(audio_seconds: float, bitrate_kbps: int) -> int:
    # kbps is kilobits/sec; /8 -> kilobytes/sec; *1000 -> bytes/sec.
    return int(audio_seconds * (bitrate_kbps * 1000 / 8))


def estimate_render_seconds(total_chars: int, chars_per_render_second: float | None) -> float | None:
    """Wall-clock render estimate. Returns None until a real rate is known."""
    if not chars_per_render_second or chars_per_render_second <= 0:
        return None
    return total_chars / chars_per_render_second


@dataclass
class Estimate:
    total_chars: int
    audio_seconds: float
    size_bytes: int
    render_seconds: float | None
    over_size_target: bool

    def human(self) -> str:
        def hms(s: float) -> str:
            s = int(s)
            return f"{s // 3600}h{(s % 3600) // 60:02d}m{s % 60:02d}s"

        size_mb = self.size_bytes / (1024 * 1024)
        parts = [
            f"~{self.total_chars:,} chars",
            f"audio ~{hms(self.audio_seconds)}",
            f"size ~{size_mb:.0f} MB",
        ]
        if self.render_seconds is not None:
            parts.append(f"render ~{hms(self.render_seconds)}")
        if self.over_size_target:
            parts.append("WARNING: over 1.2 GB target — consider a lower bitrate")
        return " | ".join(parts)


def estimate(total_chars: int, bitrate_kbps: int, chars_per_render_second: float | None = None) -> Estimate:
    audio = estimate_audio_seconds(total_chars)
    size = estimate_size_bytes(audio, bitrate_kbps)
    return Estimate(
        total_chars=total_chars,
        audio_seconds=audio,
        size_bytes=size,
        render_seconds=estimate_render_seconds(total_chars, chars_per_render_second),
        over_size_target=size > config.SIZE_WARN_BYTES,
    )
