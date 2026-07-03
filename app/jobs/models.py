"""Plain data records that flow between pipeline stages and are persisted as
JSON in the job directory. Kept deliberately dumb — no behavior, just shape."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum


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
    # measured after a render: chars of text produced per second of wall clock.
    chars_per_render_second: float | None = None
    # history/audit: ISO-8601 UTC timestamps and final output size.
    created_at: str | None = None
    # When the current full render's segment loop began. Used to show an honest
    # "time remaining" estimate that survives a page reload, and cleared once the
    # render leaves the rendering stage.
    render_started_at: str | None = None
    finished_at: str | None = None
    output_bytes: int | None = None
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
