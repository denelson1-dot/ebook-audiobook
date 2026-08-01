"""Package per-chapter WAVs into a single chaptered .m4b with metadata + cover.

Uses ffmpeg's concat demuxer to encode straight from the chapter WAVs (no giant
intermediate WAV), an FFMETADATA sidecar for chapter markers, and an optional
attached picture for cover art.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf

from .. import config, tools


class PackagingError(RuntimeError):
    pass


@dataclass
class ChapterAudio:
    title: str
    path: Path


def _chapter_seconds(chapters: list[ChapterAudio]) -> list[float]:
    """Duration of each assembled chapter WAV.

    Read once and reused for both the chapter markers and the encode timeout, so
    a long book isn't stat'd twice. A chapter that won't open here is fatal and
    worth saying plainly: it means the assembly step produced nothing usable, and
    muxing on would emit an audiobook silently missing a chapter.
    """
    out = []
    for ch in chapters:
        try:
            out.append(float(sf.info(str(ch.path)).duration))
        except Exception as e:  # noqa: BLE001 - missing, truncated, or unreadable
            raise PackagingError(
                f"Chapter audio for “{ch.title}” could not be read ({ch.path}): {e}"
            ) from e
    return out


def _ffmetadata(title: str, author: str, chapters: list[ChapterAudio],
                durations: list[float]) -> str:
    def esc(v: str) -> str:
        # FFMETADATA escaping: =, ;, #, \ and newlines.
        for ch in ("\\", "=", ";", "#"):
            v = v.replace(ch, "\\" + ch)
        return v.replace("\n", " ")

    lines = [";FFMETADATA1", f"title={esc(title)}", f"artist={esc(author)}",
             f"album={esc(title)}", "genre=Audiobook"]
    cursor_ms = 0
    for ch, seconds in zip(chapters, durations):
        dur_ms = int(round(seconds * 1000))
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={cursor_ms}",
                  f"END={cursor_ms + dur_ms}", f"title={esc(ch.title)}"]
        cursor_ms += dur_ms
    return "\n".join(lines) + "\n"


# AAC encoding runs far faster than real time, but "far" varies hugely: a recent
# desktop manages several hundred times, an old laptop encoding from a network
# share nearer ten. A fixed cap therefore can't suit both a novella and a
# 60-hour doorstop, and getting it wrong throws away the entire render at the
# very last step. The budget scales with the audio, with a floor for short books.
MIN_ENCODE_TIMEOUT = 900          # 15 min — plenty for anything small
ENCODE_TIMEOUT_PER_AUDIO_SECOND = 0.25   # i.e. assume no worse than 4x real time


def encode_timeout(audio_seconds: float) -> int:
    """Wall-clock budget for the ffmpeg mux/encode of this much audio."""
    return int(max(MIN_ENCODE_TIMEOUT, audio_seconds * ENCODE_TIMEOUT_PER_AUDIO_SECOND))


def package_m4b(
    out_path: Path,
    chapters: list[ChapterAudio],
    title: str,
    author: str,
    cover_path: Path | None = None,
    bitrate_kbps: int = config.DEFAULT_BITRATE_KBPS,
    workdir: Path | None = None,
    timeout: int | None = None,
) -> Path:
    try:
        ffmpeg = tools.require_ffmpeg()
    except tools.MissingToolError as e:
        raise PackagingError(str(e)) from e
    if not chapters:
        raise PackagingError("no chapter audio to package")

    workdir = workdir or out_path.parent
    workdir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # concat list (absolute paths, single-quote-escaped per concat demuxer rules)
    list_path = workdir / "concat.txt"
    list_path.write_text(
        "\n".join(f"file '{ch.path.resolve().as_posix().replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'"
                  for ch in chapters) + "\n",
        encoding="utf-8",
    )
    durations = _chapter_seconds(chapters)
    meta_path = workdir / "ffmeta.txt"
    meta_path.write_text(_ffmetadata(title, author, chapters, durations), encoding="utf-8")

    cmd = [str(ffmpeg), "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
           "-i", str(meta_path)]
    have_cover = cover_path is not None and Path(cover_path).exists()
    if have_cover:
        cmd += ["-i", str(cover_path)]

    cmd += ["-map", "0:a", "-map_metadata", "1"]
    if have_cover:
        cmd += ["-map", "2:v", "-c:v", "mjpeg", "-disposition:v", "attached_pic"]
    cmd += ["-c:a", "aac", "-b:a", f"{bitrate_kbps}k", "-ac", "1",
            "-movflags", "+faststart", "-f", "mp4", str(out_path)]

    budget = timeout if timeout is not None else encode_timeout(sum(durations))
    try:
        proc = tools.run(cmd, timeout=budget)
    except subprocess.TimeoutExpired as e:
        out_path.unlink(missing_ok=True)  # never leave a half-written .m4b
        raise PackagingError(
            f"Encoding the audiobook took longer than {budget // 60} minutes and "
            f"was stopped. The narration is already rendered and cached, so "
            f"starting the render again will resume at this final step."
        ) from e
    if proc.returncode != 0 or not out_path.exists():
        raise PackagingError(_encode_error_message(out_path, proc))
    return out_path


def _encode_error_message(out_path: Path, proc: subprocess.CompletedProcess) -> str:
    """Turn an ffmpeg failure into something a user can act on."""
    err = (proc.stderr or "") + (proc.stdout or "")
    low = err.lower()
    if "permission denied" in low or "access is denied" in low:
        return (
            f"Couldn't write the audiobook to {out_path}. If it's open in a "
            f"player (or being scanned by Plex), close it and render again — "
            f"Windows and some network shares lock a file while it's in use."
        )
    if "no space left" in low or "disk full" in low or "not enough space" in low:
        return (
            f"Ran out of disk space writing {out_path}. Free some space, then "
            f"render again — the narration is cached, so it resumes at this "
            f"final step rather than starting over."
        )
    return f"ffmpeg packaging failed ({proc.returncode}):\n{err[-1000:]}"
