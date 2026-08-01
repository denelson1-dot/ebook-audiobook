"""Post-render validation of the finished .m4b.

Cheap automated confirmation, before a job is marked done, that the output is a
usable Plex audiobook: a valid container, marked as an audiobook, with the core
matching tags, at least one chapter, and (when the source had one) a cover.

``validate_m4b`` returns a list of human-readable problems — empty means good.
The caller decides how strict to be; ``worker`` treats a non-empty list as a
render failure so a silently-mistagged file never reaches the library.

Container inspection deliberately does not *require* ffprobe. Most users get
their ffmpeg from the bundled ``imageio-ffmpeg`` wheel, which ships ffmpeg but
no ffprobe, and "we couldn't run the optional checker" must never be reported as
"your audiobook is broken". Chapters are read back with
``ffmpeg -i file -f ffmetadata -`` and duration comes from mutagen, so the full
check runs with ffmpeg alone; ffprobe is used when present because its JSON is
more precise.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import tools
from ..jobs.models import Book
from .tag import STIK_AUDIOBOOK, read_tags


def _probe_with_ffprobe(path: Path) -> dict | None:
    """``{"duration": float|None, "chapters": int}`` via ffprobe, or None."""
    exe = tools.ffprobe_path()
    if not exe:
        return None
    proc = tools.run(
        [exe, "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_chapters", path],
        timeout=120,
    )
    if proc.returncode != 0 or not proc.stdout:
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None
    raw_duration = data.get("format", {}).get("duration")
    try:
        duration = float(raw_duration) if raw_duration is not None else None
    except (TypeError, ValueError):
        duration = None
    return {"duration": duration, "chapters": len(data.get("chapters") or [])}


def _probe_with_ffmpeg(path: Path) -> dict | None:
    """Same shape as :func:`_probe_with_ffprobe`, using only ffmpeg + mutagen.

    ``-f ffmetadata -`` dumps the container's metadata as an INI-ish text block
    in which each chapter is a ``[CHAPTER]`` section, so counting those is enough
    to tell whether the chapter markers survived the mux. Some ffmpeg builds exit
    non-zero on this invocation even after writing a perfectly good dump, so the
    stdout content — not the return code — is what's trusted here.
    """
    exe = tools.ffmpeg_path()
    if not exe:
        return None
    proc = tools.run([exe, "-hide_banner", "-loglevel", "error", "-i", path,
                      "-f", "ffmetadata", "-"], timeout=120)
    out = proc.stdout or ""
    if ";FFMETADATA" not in out:
        return None
    chapters = out.count("[CHAPTER]")

    try:
        from mutagen.mp4 import MP4

        info = MP4(str(path)).info
        duration = float(info.length) if info and info.length else None
    except Exception:  # noqa: BLE001 - an unreadable container is reported below
        duration = None
    return {"duration": duration, "chapters": chapters}


def probe_container(path: Path) -> dict | None:
    """Duration and chapter count for an .m4b, or None if nothing could read it."""
    return _probe_with_ffprobe(path) or _probe_with_ffmpeg(path)


def validate_m4b(path: Path, book: Book) -> list[str]:
    problems: list[str] = []
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return [f"output file missing or empty: {path}"]

    probe = probe_container(path)
    if probe:  # only assert container/chapters when something could read it
        duration = probe.get("duration")
        if duration is None:
            problems.append("container duration unreadable (corrupt?)")
        elif duration <= 0:
            problems.append("container has zero duration (truncated?)")
        if not probe.get("chapters"):
            problems.append("no chapter markers found")

    try:
        tags = read_tags(path)
    except Exception as e:  # noqa: BLE001 - a broken container surfaces here too
        return problems + [f"could not read MP4 tags: {e}"]

    if tags.get("stik") != STIK_AUDIOBOOK:
        problems.append(f"media type is not Audiobook (stik={tags.get('stik')!r})")
    for key, label in (("title", "title"), ("album", "album"),
                       ("artist", "artist"), ("album_artist", "album artist")):
        if not tags.get(key):
            problems.append(f"missing {label} tag")

    # Cover is only expected if the source ebook actually provided one.
    if book.cover_path and Path(book.cover_path).exists() and not tags.get("has_cover"):
        problems.append("cover art was available but not embedded")

    return problems
