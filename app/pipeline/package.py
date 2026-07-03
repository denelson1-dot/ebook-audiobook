"""Package per-chapter WAVs into a single chaptered .m4b with metadata + cover.

Uses ffmpeg's concat demuxer to encode straight from the chapter WAVs (no giant
intermediate WAV), an FFMETADATA sidecar for chapter markers, and an optional
attached picture for cover art.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf

from .. import config


class PackagingError(RuntimeError):
    pass


@dataclass
class ChapterAudio:
    title: str
    path: Path


def _ffmetadata(title: str, author: str, chapters: list[ChapterAudio]) -> str:
    def esc(v: str) -> str:
        # FFMETADATA escaping: =, ;, #, \ and newlines.
        for ch in ("\\", "=", ";", "#"):
            v = v.replace(ch, "\\" + ch)
        return v.replace("\n", " ")

    lines = [";FFMETADATA1", f"title={esc(title)}", f"artist={esc(author)}",
             f"album={esc(title)}", "genre=Audiobook"]
    cursor_ms = 0
    for ch in chapters:
        dur_ms = int(round(sf.info(str(ch.path)).duration * 1000))
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={cursor_ms}",
                  f"END={cursor_ms + dur_ms}", f"title={esc(ch.title)}"]
        cursor_ms += dur_ms
    return "\n".join(lines) + "\n"


def package_m4b(
    out_path: Path,
    chapters: list[ChapterAudio],
    title: str,
    author: str,
    cover_path: Path | None = None,
    bitrate_kbps: int = config.DEFAULT_BITRATE_KBPS,
    workdir: Path | None = None,
    timeout: int = 3600,
) -> Path:
    if not shutil.which("ffmpeg"):
        raise PackagingError("ffmpeg not found. Install with: sudo apt install ffmpeg")
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
    meta_path = workdir / "ffmeta.txt"
    meta_path.write_text(_ffmetadata(title, author, chapters), encoding="utf-8")

    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
           "-i", str(meta_path)]
    have_cover = cover_path is not None and Path(cover_path).exists()
    if have_cover:
        cmd += ["-i", str(cover_path)]

    cmd += ["-map", "0:a", "-map_metadata", "1"]
    if have_cover:
        cmd += ["-map", "2:v", "-c:v", "mjpeg", "-disposition:v", "attached_pic"]
    cmd += ["-c:a", "aac", "-b:a", f"{bitrate_kbps}k", "-ac", "1",
            "-movflags", "+faststart", "-f", "mp4", str(out_path)]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0 or not out_path.exists():
        raise PackagingError(f"ffmpeg packaging failed ({proc.returncode}):\n{proc.stderr[-1000:]}")
    return out_path
