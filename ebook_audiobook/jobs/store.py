"""File-based job store. No database — each job is a directory of JSON plus a
content-addressed cache of rendered segment WAVs.

Layout (under local-data/jobs/<job_id>/):
    book.json            source + metadata + cover path
    chapters.json        normalized chapters
    segments.jsonl       chunked render units (one JSON object per line)
    voice_settings.json  selected engine/voice; hashed into segment ids
    job_state.json       stage, progress, error, output
    segments/<sid>.wav   rendered audio, keyed by content address
    chapters/<cid>.wav   assembled per-chapter audio
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ..config import VoiceSettings, paths
from .models import Book, Chapter, JobState, Segment, Stage


def _atomic_write(path: Path, text: str) -> None:
    """Write then rename, so a crash mid-write never corrupts state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(text)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _prune_library_dirs(book_dir: Path) -> None:
    """After a library-mode .m4b is deleted, remove its cover sidecar and any
    now-empty folders, walking up the ``Author/[Series/]Title`` tree but stopping
    at — and never removing — the configured library root. Stops at the first
    non-empty folder (e.g. an Author with other books left)."""
    from .. import settings  # lazy: settings imports this module

    root = settings.audiobooks_root()
    if not root:
        return
    root = Path(root).resolve()
    try:
        d = book_dir.resolve()
    except OSError:
        return
    if root not in d.parents:  # output wasn't under the library root — leave it
        return

    for name in ("cover.jpg", "cover.png"):  # sidecars we wrote
        (d / name).unlink(missing_ok=True)

    while d != root and root in d.parents:
        try:
            d.rmdir()  # succeeds only when empty
        except OSError:
            break  # still holds other books/files, or already gone
        d = d.parent


class JobStore:
    def __init__(self, job_id: str):
        self.job_id = job_id
        self.dir = paths().jobs / job_id
        self.segments_dir = self.dir / "segments"
        self.chapters_audio_dir = self.dir / "chapters"

    # --- lifecycle ----------------------------------------------------------

    def ensure(self) -> "JobStore":
        self.dir.mkdir(parents=True, exist_ok=True)
        self.segments_dir.mkdir(parents=True, exist_ok=True)
        self.chapters_audio_dir.mkdir(parents=True, exist_ok=True)
        return self

    @classmethod
    def list_ids(cls) -> list[str]:
        base = paths().jobs
        if not base.exists():
            return []
        return sorted(p.name for p in base.iterdir() if p.is_dir())

    def exists(self) -> bool:
        return (self.dir / "book.json").exists()

    # --- storage / cleanup --------------------------------------------------

    def preview_path(self) -> Path:
        return paths().outputs / f"{self.job_id}_preview.wav"

    def output_path(self) -> Path | None:
        op = self.load_state().output_path
        return Path(op) if op and op.endswith(".m4b") else None

    def _tree_bytes(self, path: Path) -> int:
        if not path.exists():
            return 0
        if path.is_file():
            return path.stat().st_size
        total = 0
        for p in path.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
        return total

    def imported_source(self) -> Path | None:
        """The copy of the ebook made at import time, if it's still there.

        Lives in ``imports/`` rather than the job directory, so it has to be
        found via book.json and cleaned up explicitly — otherwise deleting a job
        leaves the whole ebook on disk and the space the UI promised to free
        never comes back.
        """
        try:
            src = Path(self.load_book().source_path)
        except (OSError, ValueError, KeyError):
            return None
        # Only ever remove files we put in imports/ ourselves. A job created
        # by the CLI from a path outside the data root must never have the
        # user's own copy of the book deleted out from under them.
        try:
            imports = paths().imports.resolve()
            if src.resolve().parent != imports:
                return None
        except OSError:
            return None
        return src if src.is_file() else None

    def disk_bytes(self) -> int:
        """All bytes this job occupies: its job dir, its imported source copy,
        and its final output."""
        total = self._tree_bytes(self.dir) + self._tree_bytes(self.preview_path())
        src = self.imported_source()
        if src:
            total += self._tree_bytes(src)
        out = self.output_path()
        if out:
            total += self._tree_bytes(out)
        return total

    def intermediate_bytes(self) -> int:
        """Reclaimable bytes: rendered segment/chapter audio, the normalized
        EPUB, and the preview — everything except the final .m4b and metadata."""
        total = self._tree_bytes(self.segments_dir) + self._tree_bytes(self.chapters_audio_dir)
        total += self._tree_bytes(self.dir / "normalized.epub")
        total += self._tree_bytes(self.preview_path())
        return total

    def cleanup_intermediates(self) -> int:
        """Delete large regenerable artifacts, keeping the .m4b + JSON metadata
        so the job stays in the history. Returns bytes freed."""
        import shutil

        freed = self.intermediate_bytes()
        shutil.rmtree(self.segments_dir, ignore_errors=True)
        shutil.rmtree(self.chapters_audio_dir, ignore_errors=True)
        (self.dir / "normalized.epub").unlink(missing_ok=True)
        self.preview_path().unlink(missing_ok=True)
        self.segments_dir.mkdir(parents=True, exist_ok=True)
        self.chapters_audio_dir.mkdir(parents=True, exist_ok=True)
        # The preview file is gone; clear its state so the UI stops pointing an
        # <audio> element at a now-missing file (which 404s on reload).
        state = self.load_state()
        state.preview_output = None
        state.preview_at = None
        self.save_state(state)
        return freed

    def delete(self) -> int:
        """Remove the job entirely: its directory, its final output, and its
        preview. Returns bytes freed."""
        import shutil

        freed = self.disk_bytes()
        state = self.load_state()
        out = self.output_path()
        src = self.imported_source()  # read before the job dir (and book.json) go
        shutil.rmtree(self.dir, ignore_errors=True)
        self.preview_path().unlink(missing_ok=True)
        if src:
            src.unlink(missing_ok=True)
        if out:
            Path(out).unlink(missing_ok=True)
            # For a library-tree output, tidy up the book's own folder (its
            # cover.jpg sidecar and now-empty Author/Series dirs) rather than
            # leaving empty shells behind. Never touches a flat folder the user
            # picked, nor the configured library root itself.
            if state.output_mode == "library":
                _prune_library_dirs(Path(out).parent)
        return freed

    # --- book ---------------------------------------------------------------

    def save_book(self, book: Book) -> None:
        _atomic_write(self.dir / "book.json", json.dumps(book.to_dict(), indent=2))

    def load_book(self) -> Book:
        return Book.from_dict(json.loads((self.dir / "book.json").read_text("utf-8")))

    # --- chapters -----------------------------------------------------------

    def save_chapters(self, chapters: list[Chapter]) -> None:
        _atomic_write(
            self.dir / "chapters.json",
            json.dumps([c.to_dict() for c in chapters], indent=2),
        )

    def load_chapters(self) -> list[Chapter]:
        p = self.dir / "chapters.json"
        if not p.exists():
            return []
        return [Chapter.from_dict(d) for d in json.loads(p.read_text("utf-8"))]

    # --- segments -----------------------------------------------------------

    def save_segments(self, segments: list[Segment]) -> None:
        lines = "\n".join(json.dumps(s.to_dict()) for s in segments)
        _atomic_write(self.dir / "segments.jsonl", lines + ("\n" if lines else ""))

    def load_segments(self) -> list[Segment]:
        p = self.dir / "segments.jsonl"
        if not p.exists():
            return []
        out = []
        for line in p.read_text("utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(Segment.from_dict(json.loads(line)))
        return out

    def segment_audio_path(self, segment_id: str) -> Path:
        return self.segments_dir / f"{segment_id}.wav"

    def chapter_audio_path(self, chapter_id: str) -> Path:
        return self.chapters_audio_dir / f"{chapter_id}.wav"

    # --- voice --------------------------------------------------------------

    def save_voice(self, voice: VoiceSettings) -> None:
        _atomic_write(self.dir / "voice_settings.json", json.dumps(voice.to_dict(), indent=2))

    def load_voice(self) -> VoiceSettings:
        p = self.dir / "voice_settings.json"
        if not p.exists():
            return VoiceSettings()
        return VoiceSettings.from_dict(json.loads(p.read_text("utf-8")))

    # --- state --------------------------------------------------------------

    def save_state(self, state: JobState) -> None:
        _atomic_write(self.dir / "job_state.json", json.dumps(state.to_dict(), indent=2))

    def load_state(self) -> JobState:
        p = self.dir / "job_state.json"
        if not p.exists():
            return JobState(job_id=self.job_id)
        return JobState.from_dict(json.loads(p.read_text("utf-8")))

    def set_stage(self, stage: Stage, message: str | None = None) -> JobState:
        state = self.load_state()
        state.stage = stage.value
        if stage != Stage.ERROR:
            state.error = None
        if message:
            state.messages.append(message)
        self.save_state(state)
        return state
