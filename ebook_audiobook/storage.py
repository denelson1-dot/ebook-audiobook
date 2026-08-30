"""What this app is holding on disk, and which of it is safe to throw away.

Narrating a book leaves behind the raw audio of every sentence — several
gigabytes per book, and by a wide margin the largest thing stored here. It is
also the only part that is regenerable, which makes "reclaim it" both an
obvious offer and a dangerous one.

The danger is that :meth:`JobStore.cleanup_intermediates` cannot tell two very
different situations apart. For a *finished* book those files buy nothing but a
faster re-render. For a *stopped* one they **are** the resume, and deleting
them silently costs hours of narration. A bulk "free up space" button built on
cleanup alone would quietly destroy the second while the user was thinking
about the first.

So this module classifies before it deletes, and :func:`free` refuses anything
it has not classified as safe unless told twice.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import settings as app_settings
from .config import paths
from .jobs.models import Stage
from .jobs.store import JobStore

# Why a book's working files may or may not be reclaimed right now.
SAFE = "safe"   # finished, or nothing narrated yet — deleting loses no work
HELD = "held"   # part-narrated — these files are what lets the render resume
BUSY = "busy"   # a worker is writing them at this moment
NONE = "none"   # there is nothing to reclaim


@dataclass
class BookStorage:
    """One book's footprint, split the way a person would want to reason about it."""

    job_id: str
    title: str
    author: str
    stage: str
    # Reclaimable: segment + chapter WAVs, the normalized EPUB, the preview.
    working_bytes: int
    # The finished .m4b, wherever it was filed.
    output_bytes: int
    # Everything else this book owns: the imported ebook, chapters.json, cover.
    keep_bytes: int
    reclaim: str
    reason: str
    rendered_segments: int = 0
    total_segments: int = 0

    @property
    def disk_bytes(self) -> int:
        return self.working_bytes + self.output_bytes + self.keep_bytes

    def to_dict(self) -> dict:
        d = {
            "job_id": self.job_id, "title": self.title, "author": self.author,
            "stage": self.stage, "working_bytes": self.working_bytes,
            "output_bytes": self.output_bytes, "keep_bytes": self.keep_bytes,
            "disk_bytes": self.disk_bytes, "reclaim": self.reclaim,
            "reason": self.reason, "rendered_segments": self.rendered_segments,
            "total_segments": self.total_segments,
        }
        return d


@dataclass
class Survey:
    """Every book, plus the data that belongs to no book (voices, settings)."""

    books: list[BookStorage] = field(default_factory=list)
    extras_bytes: int = 0

    def _sum(self, attr: str, *reclaim: str) -> int:
        return sum(getattr(b, attr) for b in self.books
                   if not reclaim or b.reclaim in reclaim)

    @property
    def safe_bytes(self) -> int:
        """What "Free up space" would actually delete."""
        return self._sum("working_bytes", SAFE)

    @property
    def held_bytes(self) -> int:
        """Working files kept back because they are holding a resume."""
        return self._sum("working_bytes", HELD, BUSY)

    @property
    def working_bytes(self) -> int:
        return self._sum("working_bytes")

    @property
    def output_bytes(self) -> int:
        return self._sum("output_bytes")

    @property
    def keep_bytes(self) -> int:
        return self._sum("keep_bytes") + self.extras_bytes

    @property
    def total_bytes(self) -> int:
        return self.working_bytes + self.output_bytes + self.keep_bytes

    @property
    def safe_books(self) -> list[BookStorage]:
        return [b for b in self.books if b.reclaim == SAFE and b.working_bytes]

    def to_dict(self) -> dict:
        return {
            "books": [b.to_dict() for b in self.books],
            "extras_bytes": self.extras_bytes,
            "safe_bytes": self.safe_bytes,
            "held_bytes": self.held_bytes,
            "working_bytes": self.working_bytes,
            "output_bytes": self.output_bytes,
            "keep_bytes": self.keep_bytes,
            "total_bytes": self.total_bytes,
            "safe_count": len(self.safe_books),
        }


def _tree_bytes(path) -> int:
    try:
        if path is None or not path.exists():
            return 0
        if path.is_file():
            return path.stat().st_size
        return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    except OSError:
        return 0


def _rendered_segment_count(store: JobStore) -> int:
    try:
        return sum(1 for p in store.segments_dir.glob("*.wav"))
    except OSError:
        return 0


def classify(store: JobStore, state, working_bytes: int, busy: bool) -> tuple[str, str, int]:
    """Decide whether a book's working files can go, and say why in the UI's words.

    Returns ``(reclaim, reason, rendered_segments)``.
    """
    if busy:
        return BUSY, "Being narrated right now — these files are in use.", _rendered_segment_count(store)
    if working_bytes <= 0:
        return NONE, "", 0

    rendered = _rendered_segment_count(store)
    if state.stage == Stage.DONE.value:
        return SAFE, "Finished — the audiobook is written and these are only a re-render shortcut.", rendered
    if rendered == 0:
        # A preview and a normalized EPUB, nothing narrated. Re-made in seconds.
        return SAFE, "Nothing narrated yet — this is only a preview and a working copy of the book.", rendered
    if state.stage in (Stage.IMPORTED.value, Stage.EXTRACTED.value):
        # Segments exist, but no full render was ever started — generating a
        # preview caches a handful of them and then puts the stage back. Calling
        # that "a resume worth hours" would be scaremongering over a few seconds
        # of audio, so it is offered like any other finished book's leftovers.
        return SAFE, "Only the audio from a preview — a few seconds' work to make again.", rendered

    total = state.total_segments or 0
    if total > rendered:
        left = total - rendered
        return HELD, (f"Keeping these lets it carry on from where it stopped. "
                      f"Delete them and {rendered:,} of {total:,} sections are narrated again."), rendered
    return HELD, (f"Stopped before the audiobook was written. Deleting means narrating "
                  f"{rendered:,} sections again."), rendered


def survey(busy_job_id: str | None = None) -> Survey:
    """Look at every job on disk. ``busy_job_id`` is excluded from reclaiming."""
    out = Survey()
    for jid in JobStore.list_ids():
        store = JobStore(jid)
        try:
            if not store.exists():
                continue
            book = store.load_book()
            state = store.load_state()
            working = store.intermediate_bytes()
            output = _tree_bytes(store.output_path())
            keep = max(0, store.disk_bytes() - working - output)
            reclaim, reason, rendered = classify(store, state, working, jid == busy_job_id)
            out.books.append(BookStorage(
                job_id=jid, title=book.title, author=book.author, stage=state.stage,
                working_bytes=working, output_bytes=output, keep_bytes=keep,
                reclaim=reclaim, reason=reason,
                rendered_segments=rendered, total_segments=state.total_segments or 0,
            ))
        except Exception:  # noqa: BLE001 - one unreadable job must not hide the rest
            continue
    # Biggest reclaim first: the whole point of the page is "what do I delete".
    out.books.sort(key=lambda b: (b.reclaim != SAFE, -b.working_bytes, b.title))
    p = paths()
    out.extras_bytes = _tree_bytes(p.voices) + _tree_bytes(p.root / "settings.json")
    return out


def free(job_ids, busy_job_id: str | None = None, force: bool = False) -> tuple[int, list[str]]:
    """Delete the working files of the named jobs.

    Skips anything a worker is touching, and — unless ``force`` — anything whose
    files are holding a resume. Returns ``(bytes_freed, skipped_job_ids)``.
    """
    freed = 0
    skipped: list[str] = []
    for jid in job_ids:
        store = JobStore(jid)
        if not store.exists() or jid == busy_job_id:
            skipped.append(jid)
            continue
        state = store.load_state()
        working = store.intermediate_bytes()
        reclaim, _, _ = classify(store, state, working, False)
        if reclaim == NONE:
            continue
        if reclaim != SAFE and not force:
            skipped.append(jid)
            continue
        freed += store.cleanup_intermediates()
    return freed, skipped


def free_after_render(job_id: str) -> int:
    """Reclaim a just-finished book's working files, if the user asked for that.

    Called at the end of a successful full render. Off by default: this app does
    not delete things nobody asked it to.
    """
    if not app_settings.load_settings().auto_free_working_files:
        return 0
    store = JobStore(job_id)
    if not store.exists():
        return 0
    if store.load_state().stage != Stage.DONE.value:
        return 0
    return store.cleanup_intermediates()
