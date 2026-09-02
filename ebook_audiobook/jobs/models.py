"""Plain data records that flow between pipeline stages and are persisted as
JSON in the job directory. Kept deliberately dumb — no behavior, just shape."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum

from ..i18n import N_, _


class Stage(str, Enum):
    IMPORTED = "imported"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    PREPARING = "preparing"      # loading the voice model before a render
    PREVIEWING = "previewing"    # rendering a preview excerpt
    RENDERING = "rendering"
    ASSEMBLING = "assembling"
    PACKAGING = "packaging"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"

    # Stages that reflect a finished or idle job (not actively working).
    @property
    def is_terminal(self) -> bool:
        return self in (Stage.IMPORTED, Stage.EXTRACTED, Stage.DONE,
                        Stage.ERROR, Stage.CANCELLED)

    @property
    def label(self) -> str:
        """What a person is shown instead of the enum's name.

        Kept here so the sidebar, the library card, the job page and the command
        line cannot end up describing the same moment three different ways. Two
        of these say *who* stopped the render, because "cancelled" answers a
        question nobody asked and leaves the one they did.
        """
        return stage_label(self.value)


STAGE_LABELS = {
    Stage.IMPORTED.value:   N_("Imported"),
    Stage.EXTRACTING.value: N_("Reading the book"),
    Stage.EXTRACTED.value:  N_("Ready to narrate"),
    Stage.PREPARING.value:  N_("Warming up"),
    Stage.PREVIEWING.value: N_("Making a preview"),
    Stage.RENDERING.value:  N_("Narrating"),
    Stage.ASSEMBLING.value: N_("Stitching chapters together"),
    Stage.PACKAGING.value:  N_("Packaging"),
    Stage.DONE.value:       N_("Finished"),
    Stage.ERROR.value:      N_("Stopped by a problem"),
    Stage.CANCELLED.value:  N_("Stopped by you"),
}


def stage_label(value: str) -> str:
    """Label for a raw stage string, tolerating one this build doesn't know.

    Translated here, at the moment of use: inside a web request this is the
    request's language, and everywhere else (the CLI, a stored copy) English.
    """
    return _(STAGE_LABELS.get(value, value))


@dataclass
class Book:
    job_id: str
    source_path: str
    source_hash: str
    title: str = "Unknown Title"
    author: str = "Unknown Author"
    cover_path: str | None = None
    # Optional bibliographic metadata parsed from the source (used for Plex tags
    # and library foldering). Any may be absent for a given ebook.
    year: str | None = None          # publication year, "YYYY"
    description: str | None = None   # long-form synopsis
    isbn: str | None = None          # ISBN identifier, if present
    series: str | None = None        # series name (rarely available)
    series_index: str | None = None  # position in series, e.g. "2"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Book":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})


@dataclass
class Chapter:
    chapter_id: str
    sequence: int
    title: str
    text: str  # normalized, speakable text for the whole chapter
    char_count: int = 0
    # Whether this section is rendered into the audiobook. Front/back matter
    # (copyright, ISBN, table of contents, dedication, acknowledgements, …) is
    # defaulted off at extraction; the user can toggle any section in the UI.
    include: bool = True
    # Whether the title is narrated as its own spoken segment. Normally yes; the
    # synthetic outro sets this off so its .m4b marker ("The End") stays a
    # display-only nav label while the body reads one flowing closing sentence.
    speak_title: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Chapter":
        # Only pass keys present so dataclass defaults apply (e.g. `include`
        # for chapters.json written before that field existed).
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})


@dataclass
class Segment:
    segment_id: str  # content address: text + engine + voice
    chapter_id: str
    sequence: int  # global order across the whole book
    chapter_sequence: int  # order within the chapter
    text: str
    text_hash: str
    status: str = "pending"  # pending | done | failed
    # Structural boundary that FOLLOWS this segment; picks the pause the
    # assembler inserts after it. One of: sentence | paragraph | scene |
    # chapter_title. Not part of the content hash (assembly-only metadata).
    boundary: str = "sentence"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Segment":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})


@dataclass
class JobState:
    job_id: str
    stage: str = Stage.IMPORTED.value
    total_segments: int = 0
    rendered_segments: int = 0
    error: str | None = None
    output_path: str | None = None
    # Where the final .m4b is written, chosen at full-render time. ``output_mode``
    # is "library" (Plex tree under the configured root) or "folder" (a single
    # flat folder); ``output_dir`` is the resolved destination folder.
    output_mode: str | None = None
    output_dir: str | None = None
    # How hard this job's render may push the machine: "full", "balanced" or
    # "quiet". None means fall back to the global setting. Per-job because the
    # right answer differs by book — an overnight run wants full speed, one
    # started at 9am on a laptop does not.
    power_mode: str | None = None
    # measured after a render: chars of text produced per second of wall clock.
    chars_per_render_second: float | None = None
    # measured: characters of text per second of *audio produced*. Drives the
    # length, file-size and working-space estimates, all of which otherwise
    # assume a fixed speaking rate and so ignore the pacing setting entirely.
    chars_per_audio_second: float | None = None
    # The voice settings the two figures above were measured with. When this no
    # longer matches the job's current settings the estimates are stale, which
    # is what lets the UI offer to remeasure rather than quietly misreport.
    measured_voice_key: str | None = None
    # history/audit: ISO-8601 UTC timestamps and final output size.
    created_at: str | None = None
    # When the most recent full render's segment loop began. Used to show an
    # honest "time remaining" estimate that survives a page reload. Deliberately
    # never cleared: its presence is what tells a job that was reset after a
    # crash apart from one that only ever previewed — see storage.classify.
    render_started_at: str | None = None
    finished_at: str | None = None
    output_bytes: int | None = None
    # The bitrate the finished file was encoded at. The voice's bitrate can be
    # changed after the render without re-rendering; this one cannot.
    output_bitrate_kbps: int | None = None
    # Preview is tracked separately from the render lifecycle so it never
    # clobbers `stage`/`output_path` (the final .m4b).
    preview_output: str | None = None
    preview_at: str | None = None
    # 0.0–1.0 completion of the in-flight preview, measured as audio seconds
    # produced toward the requested excerpt length. A preview renders only a
    # few of a chapter's many segments, so the segment ratio would barely
    # move; this fills the bar honestly for the preview's own scope.
    preview_progress: float = 0.0
    messages: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "JobState":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__ if k in d})
