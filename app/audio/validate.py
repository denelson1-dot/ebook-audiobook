"""Post-render validation of the finished .m4b.

Cheap automated confirmation, before a job is marked done, that the output is a
usable Plex audiobook: a valid container, marked as an audiobook, with the core
matching tags, at least one chapter, and (when the source had one) a cover.

``validate_m4b`` returns a list of human-readable problems — empty means good.
The caller decides how strict to be; ``worker`` treats a non-empty list as a
render failure so a silently-mistagged file never reaches the library.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from ..jobs.models import Book
from .tag import STIK_AUDIOBOOK, read_tags


def _ffprobe(path: Path) -> dict:
    if not shutil.which("ffprobe"):
        return {}
    proc = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_chapters", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        return {}
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return {}


def validate_m4b(path: Path, book: Book) -> list[str]:
    problems: list[str] = []
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return [f"output file missing or empty: {path}"]

    probe = _ffprobe(path)
    if probe:  # only assert container/chapters when ffprobe is available
        try:
            if float(probe.get("format", {}).get("duration", 0)) <= 0:
                problems.append("container has zero duration (truncated?)")
        except (TypeError, ValueError):
            problems.append("container duration unreadable (corrupt?)")
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
