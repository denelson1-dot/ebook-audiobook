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

import time
from dataclasses import dataclass, field
from pathlib import Path

from . import settings as app_settings
from .config import paths
from .jobs.models import Stage
from .jobs.store import JobStore, is_junk

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
    """Every book, plus the data that belongs to no book (voices, settings).

    ``app_bytes`` and ``other_bytes`` are what else sits in the data folder —
    the installed program itself (the installer puts its virtualenv there, and
    that alone is several gigabytes) and leftovers no book claims. They are kept
    out of ``total_bytes``, which is the *books'* footprint, and shown on the
    Storage page so that its "in your folder" figure is the one Finder or
    Explorer would give for the same folder.
    """

    books: list[BookStorage] = field(default_factory=list)
    extras_bytes: int = 0
    app_bytes: int = 0
    other_bytes: int = 0

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
        """The books, their outputs, and the small things around them."""
        return self.working_bytes + self.output_bytes + self.keep_bytes

    @property
    def books_bytes(self) -> int:
        """Just the books: what "N books · X on disk" should say."""
        return self._sum("disk_bytes")

    @property
    def folder_bytes(self) -> int:
        """Everything in the data folder, the way a file manager would count it."""
        return self.total_bytes + self.app_bytes + self.other_bytes

    @property
    def safe_books(self) -> list[BookStorage]:
        return [b for b in self.books if b.reclaim == SAFE and b.working_bytes]

    def to_dict(self) -> dict:
        return {
            "books": [b.to_dict() for b in self.books],
            "extras_bytes": self.extras_bytes,
            "app_bytes": self.app_bytes,
            "other_bytes": self.other_bytes,
            "safe_bytes": self.safe_bytes,
            "held_bytes": self.held_bytes,
            "working_bytes": self.working_bytes,
            "output_bytes": self.output_bytes,
            "keep_bytes": self.keep_bytes,
            "total_bytes": self.total_bytes,
            "books_bytes": self.books_bytes,
            "folder_bytes": self.folder_bytes,
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
    """How many segments are on disk. Not "how many .wav files": on an exFAT
    or SMB volume macOS writes a ``._name.wav`` sidecar beside each real one,
    and counting those would double the figure and, at the boundary, turn
    "nothing narrated" into "something narrated"."""
    try:
        return sum(1 for p in store.segments_dir.glob("*.wav") if not is_junk(p.name))
    except OSError:
        return 0


# What the installer puts in the data folder that is not the user's data: the
# program's own virtualenv and the engine's model cache. Walked rarely — the
# venv is ~100k files and changes only when the app is reinstalled.
_APP_DIRS = ("venv", "models")
_APP_BYTES_TTL = 600.0
_app_cache: dict = {"at": 0.0, "root": None, "value": 0}

# Everything a book accounts for lives here; anything else at the top of the
# data folder is "other" (temporary files, the app window's browser profile).
_BOOK_DIRS = ("jobs", "imports", "voices", "outputs")


def _app_bytes(root: Path) -> int:
    now = time.monotonic()
    if _app_cache["root"] == root and now - _app_cache["at"] < _APP_BYTES_TTL:
        return _app_cache["value"]
    value = sum(_tree_bytes(root / d) for d in _APP_DIRS)
    _app_cache.update(at=now, root=root, value=value)
    return value


def _other_bytes(root: Path, claimed: set[Path]) -> int:
    """Bytes in the data folder that no book and no known purpose accounts for.

    Two kinds: top-level entries that are none of the known folders, and files
    in ``imports/`` or ``outputs/`` that no job refers to any more — a job
    deleted by hand, or an output left by a version that filed things
    differently. Reported so the folder total is honest, not so it can be
    deleted from here: nothing in this module touches them.
    """
    total = 0
    try:
        for entry in root.iterdir():
            if entry.name in _BOOK_DIRS or entry.name in _APP_DIRS or entry.name == "settings.json":
                continue
            total += _tree_bytes(entry)
        for folder in ("imports", "outputs"):
            base = root / folder
            if not base.is_dir():
                continue
            for entry in base.iterdir():
                if entry.is_file() and entry not in claimed:
                    total += _tree_bytes(entry)
    except OSError:
        pass
    return total


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
    total = state.total_segments or 0
    if (state.stage in (Stage.IMPORTED.value, Stage.EXTRACTED.value)
            and not (state.render_started_at and total > rendered)):
        # Segments exist, but no full render was ever started — generating a
        # preview caches a handful of them and then puts the stage back. Calling
        # that "a resume worth hours" would be scaremongering over a few seconds
        # of audio, so it is offered like any other finished book's leftovers.
        #
        # The stage alone cannot say that, though. A render interrupted by a
        # crash or a force-quit is reset to "extracted" on the next launch
        # (web.app._reconcile_stale), and on disk it looks exactly like this —
        # except that a full render stamps ``render_started_at`` and a preview
        # never does. Twelve thousand narrated sections are a resume, whatever
        # the stage says, so those fall through to the held case below.
        return SAFE, "Only the audio from a preview — a few seconds' work to make again.", rendered

    if total > rendered:
        left = total - rendered
        return HELD, (f"Keeping these lets it carry on from where it stopped. "
                      f"Delete them and {rendered:,} of {total:,} sections are narrated again."), rendered
    return HELD, (f"Stopped before the audiobook was written. Deleting means narrating "
                  f"{rendered:,} sections again."), rendered


def _is_busy(jid: str, busy_job_id: str | None, is_busy) -> bool:
    """Whether a worker owns this job right now — running *or queued*.

    ``busy_job_id`` is the one being rendered this instant. ``is_busy`` is the
    runner's own answer, which also counts work that is queued and about to
    start; between a submit and the worker picking it up the first is None and
    the second is True, and deleting the job's files in that window means the
    render that begins a moment later re-narrates everything it had.
    """
    if jid == busy_job_id:
        return True
    if is_busy is None:
        return False
    try:
        return bool(is_busy(jid))
    except Exception:  # noqa: BLE001 - never let a busy check hide the survey
        return False


def survey(busy_job_id: str | None = None, is_busy=None) -> Survey:
    """Look at every job on disk. Busy jobs are excluded from reclaiming.

    ``is_busy`` is a ``job_id -> bool`` callable (the web runner's) that also
    knows about queued work; see :func:`_is_busy`.
    """
    out = Survey()
    claimed: set[Path] = set()
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
            for owned in (store.imported_source(), store.preview_path(), store.output_path()):
                if owned is not None:
                    claimed.add(owned)
            reclaim, reason, rendered = classify(store, state, working,
                                                 _is_busy(jid, busy_job_id, is_busy))
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
    out.app_bytes = _app_bytes(p.root)
    out.other_bytes = _other_bytes(p.root, claimed)
    return out


def free(job_ids, busy_job_id: str | None = None, force: bool = False,
         is_busy=None) -> tuple[int, list[str]]:
    """Delete the working files of the named jobs.

    Skips anything a worker is touching or about to, and — unless ``force`` —
    anything whose files are holding a resume. Returns
    ``(bytes_freed, skipped_job_ids)``.
    """
    freed = 0
    skipped: list[str] = []
    for jid in job_ids:
        store = JobStore(jid)
        if not store.exists() or _is_busy(jid, busy_job_id, is_busy):
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
