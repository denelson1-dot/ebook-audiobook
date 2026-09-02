"""Single-worker orchestration of the pipeline.

One job at a time; the TTS model is loaded once and reused across every segment.
State is written to disk after each segment, so an interrupted render resumes
from the content-addressed segment cache with no special handling — any segment
whose WAV already exists (its id encodes text+engine+voice) is skipped.

These functions are synchronous and drive the CLI directly; the web UI runs
``render_job`` in a background thread.
"""

from __future__ import annotations

from .i18n import N_, _
from . import narration_langs
import errno
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

from . import config, power, settings
from .audio.wav import is_valid_audio, read_wav, write_wav
from .config import VoiceSettings, paths
from .hashing import file_hash, segment_id, text_hash, voice_key
from .jobs.models import Book, Chapter, JobState, Segment, Stage
from .jobs.store import JobStore
from .pipeline import assemble, extract, layout, package
from .pipeline import cover as cover_mod
from .pipeline.chunk import chunk_structured
from .pipeline.lang import rules_for
from .pipeline.normalize import apply_pronunciation, normalize_text, normalize_title
from .tts import get_adapter

Progress = Callable[[JobState], None]


class OutputDirError(ValueError):
    """The chosen output folder is missing/uncreatable or not writable.

    Raised *before* a render starts so a bad destination never wastes hours of
    synthesis. Carries a plain-language message safe to show the user."""


def resolve_output_dir(raw: str | Path | None) -> Path:
    """Resolve (creating if needed) the folder the final .m4b will be written to,
    and prove we can actually write there before committing to a long render.

    ``None``/empty selects the default ``local-data/outputs``. Raises
    :class:`OutputDirError` if the path isn't a usable, writable folder."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        target = paths().outputs
    else:
        target = Path(raw).expanduser()
    try:
        target = target.resolve()
    except OSError as e:
        raise OutputDirError(_("Invalid output folder: %(path)s (%(e)s)", path=raw, e=e)) from e

    if target.exists() and not target.is_dir():
        raise OutputDirError(_("Output path is not a folder: %(path)s", path=target))
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise OutputDirError(_("Can't create output folder “%(path)s”: %(e)s", path=target, e=e)) from e

    # A real write test — os.access()/W_OK can lie on read-only mounts, ACLs,
    # and some network filesystems, so actually create and remove a probe file.
    try:
        with tempfile.TemporaryFile(dir=str(target)):
            pass
    except OSError as e:
        raise OutputDirError(
            _("No write permission for output folder “%(path)s”: %(e)s", path=target, e=e)
        ) from e
    return target


# Output mode: file the book into the Plex-style library tree, or into a single
# flat folder the user picked.
MODE_LIBRARY = "library"
MODE_FOLDER = "folder"


def default_output_mode() -> str:
    """Library when a library root is configured; otherwise a flat folder."""
    return MODE_LIBRARY if settings.audiobooks_root() else MODE_FOLDER


def resolve_output_target(mode: str, output_dir: str | Path | None, book: Book) -> tuple[Path, Path, bool]:
    """Settle where a full render's .m4b lands, proving the folder is writable
    *before* the render starts. Returns ``(m4b_path, out_dir, write_sidecar)``.

    - ``library``: ``{root}/{Author}/{Title (Year)}/{Title}.m4b`` under the
      configured audiobooks root (raises if none is set). A ``cover.jpg`` sidecar
      is written beside it.
    - ``folder``: ``{chosen_dir}/{Title}.m4b`` in a single flat folder.
    """
    if mode == MODE_LIBRARY:
        root = settings.audiobooks_root()
        if not root:
            raise OutputDirError(
                "No audiobooks library folder is set yet. Set one in Settings, "
                "or switch this render to a specific folder."
            )
        root_dir = resolve_output_dir(root)  # the library root must be usable
        m4b = layout.library_m4b_path(root_dir, book)
        out_dir = resolve_output_dir(m4b.parent)  # create + check the book's folder
        return m4b, out_dir, True
    out_dir = resolve_output_dir(output_dir)
    return out_dir / f"{layout.output_stem(book)}.m4b", out_dir, False


VOICE_SAMPLE_TEXT = (
    "This is a sample of the selected narrator voice. The quiet town slept "
    "beneath a wide and indifferent sky, and somewhere a single bell rang twice."
)


def render_voice_sample(voice_id: str, params: dict | None = None) -> Path:
    """Render a short fixed sentence with a library voice so it can be auditioned
    in the UI. Uses the real engine; output goes to local-data/voices/_sample_<id>.wav."""
    from .voices import VoiceLibrary

    lib = VoiceLibrary()
    clip = lib.clip_path(voice_id)
    voice = VoiceSettings(
        engine="chatterbox",
        reference_clip=str(clip) if clip else None,
        **(params or {}),
    )
    adapter = get_adapter(voice, config.SAMPLE_RATE)
    adapter.load()
    try:
        audio = adapter.synthesize(VOICE_SAMPLE_TEXT)
    finally:
        adapter.unload()
    out = paths().voices / f"_sample_{voice_id}.wav"
    # Written under another name and renamed into place: the Voices page polls
    # for this file and plays it the moment it exists, so it must never exist
    # half-written.
    partial = out.with_name(f"_sample_{voice_id}.partial.wav")
    write_wav(partial, audio.samples, audio.sample_rate)
    partial.replace(out)
    return out


# --- import ------------------------------------------------------------------

# Actionable guidance for common formats we can't read directly.
_UNSUPPORTED_HELP = {
    ".kfx": N_("KFX (newer Kindle) isn't supported directly. Open it in Calibre and "
               "convert/export it to EPUB, then import the EPUB."),
    ".azw8": N_("This is a KFX-era Kindle file. Convert it to EPUB in Calibre first, "
                "then import the EPUB."),
    ".acsm": N_("An .acsm file is an Adobe download token, not the book itself. Open it "
                "in Adobe Digital Editions or Calibre to fetch the actual book, then "
                "import that."),
    ".pdb": N_("Old Palm/PDB ebooks aren't supported. Convert it to EPUB in Calibre first."),
}


def unsupported_format_hint(ext: str) -> str | None:
    """Actionable guidance for a known-but-unsupported ebook extension, else None."""
    hint = _UNSUPPORTED_HELP.get(ext.lower())
    return _(hint) if hint else None


def import_ebook(source_path: str, engine: str = "chatterbox") -> str:
    """Copy a source ebook into local-data and create its job. Returns job_id."""
    src = Path(source_path).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(src)
    ext = src.suffix.lower()
    if ext not in extract.SUPPORTED_INPUT:
        hint = unsupported_format_hint(ext)
        if hint:
            raise ValueError(hint)
        supported = ", ".join(sorted(extract.SUPPORTED_INPUT))
        kind = _("“%(ext)s” files", ext=ext) if ext else _("files with no extension")
        raise ValueError(
            _("%(kind)s aren't supported. Supported formats: %(formats)s. "
              "For best results, convert your book to EPUB in Calibre first.", kind=kind, formats=supported)
        )

    p = paths().ensure()
    job_id = file_hash(src)
    store = JobStore(job_id).ensure()

    imported = p.imports / f"{job_id}{src.suffix.lower()}"
    if not imported.exists():
        shutil.copy2(src, imported)

    store.save_book(Book(job_id=job_id, source_path=str(imported), source_hash=job_id))

    # A new book starts with the shipped narrator rather than the engine's raw
    # default, so the first thing anyone hears is a voice that was chosen.
    from .voices import VoiceLibrary, default_voice_id

    voice = VoiceSettings(engine=engine)
    _choose_narrator(voice, VoiceLibrary(), default_voice_id())
    store.save_voice(voice)
    store.save_state(JobState(job_id=job_id, stage=Stage.IMPORTED.value, created_at=_now_iso()))
    return job_id


def _choose_narrator(voice: VoiceSettings, lib, voice_id: str) -> None:
    """Point ``voice`` at a library voice: its clip, its suggested sliders, its id."""
    clip = lib.clip_path(voice_id)
    chosen = lib.get(voice_id)
    voice.reference_clip = str(clip) if clip else None
    if chosen:
        if chosen.pacing is not None:
            voice.cfg_weight = chosen.pacing
        if chosen.expressiveness is not None:
            voice.exaggeration = chosen.expressiveness
    voice.extra["voice_id"] = voice_id


def set_job_language(store: JobStore, lang: str) -> None:
    """Narrate this job in ``lang``.

    Sets the book's language, the voice's language, and — when the current
    narrator does not speak it — that language's default narrator, so the
    change is one a person can hear rather than a French book read in an
    English accent. Refuses, with a message that says what to install, when
    the language's model is not on this machine. The caller re-extracts: the
    text is prepared differently per language.
    """
    from .voices import DEFAULT_BUNDLED_BY_LANGUAGE, VoiceLibrary, default_voice_id

    narration_langs.require_installed(lang)
    book = store.load_book()
    book.language = lang
    store.save_book(book)

    voice = store.load_voice()
    if voice.language != lang:
        # Each model was tuned with its own repetition penalty. Move the slider
        # only if it still sits at the other model's default, so a value the
        # user chose is never overwritten.
        if lang == "en" and voice.repetition_penalty == config.DEFAULT_REPETITION_PENALTY_MULTILINGUAL:
            voice.repetition_penalty = config.DEFAULT_REPETITION_PENALTY
        elif lang != "en" and voice.repetition_penalty == config.DEFAULT_REPETITION_PENALTY:
            voice.repetition_penalty = config.DEFAULT_REPETITION_PENALTY_MULTILINGUAL
        voice.language = lang
        lib = VoiceLibrary()
        current = lib.get(voice.extra.get("voice_id", ""))
        if not current or current.language != lang:
            wanted = DEFAULT_BUNDLED_BY_LANGUAGE.get(lang) or default_voice_id(lang)
            if not lib.get(wanted):
                wanted = default_voice_id(lang)
            _choose_narrator(voice, lib, wanted)
    store.save_voice(voice)


def narration_notice(store: JobStore) -> str | None:
    """Why a book is not being narrated in its own language, if it isn't.

    Stored nowhere: computed for the page, in the page's language.
    """
    book = store.load_book()
    voice = store.load_voice()
    if book.language == voice.language or narration_langs.language_available(book.language):
        return None  # narrated in its language, or in another one on purpose
    lang = narration_langs.LANGUAGES.get(book.language)
    if lang is None:
        return None
    return _("This book is in %(language)s. Narrating it in %(language)s needs the "
             "additional language model — see Settings, under Narration languages.",
             language=_(lang.name))


# --- extract -----------------------------------------------------------------

_INTRO_CHAPTER_ID = "intro"


def _intro_chapter(book: Book, lang: str = "en") -> Chapter | None:
    """A synthetic opening section that announces the book before chapter one.

    The title is spoken as the standard chapter-title segment (and doubles as the
    .m4b's opening marker); the body announces the author, in the narration
    language. Returns None when there's no usable metadata to announce. Included
    by default; the user can uncheck it like any other section.
    """
    words = rules_for(lang).strings
    title = (book.title or "").strip()
    author = (book.author or "").strip()
    has_title = bool(title) and title.lower() != "unknown title"
    has_author = bool(author) and author.lower() != "unknown author"
    if not has_title and not has_author:
        return None
    if has_title:
        marker, body = title, (words["by_author"] % {"author": author} if has_author else "")
    else:
        marker, body = author, ""  # author-only: just announce the author once
    return Chapter(chapter_id=_INTRO_CHAPTER_ID, sequence=0, title=marker,
                   text=body, char_count=len(body), include=True)


_OUTRO_CHAPTER_ID = "outro"


def _outro_chapter(book: Book, lang: str = "en") -> Chapter | None:
    """A synthetic closing section that signs off the book after the last chapter.

    Set apart by a long lead-in pause (``config.PAUSE_BEFORE_OUTRO``) so a
    listener who isn't looking at a screen clearly hears the book has ended. The
    marker ("The End") is display-only; the body reads one flowing sentence, in
    the narration language. Returns None when there's no usable metadata.
    Included by default.
    """
    words = rules_for(lang).strings
    title = (book.title or "").strip()
    author = (book.author or "").strip()
    has_title = bool(title) and title.lower() != "unknown title"
    has_author = bool(author) and author.lower() != "unknown author"
    if not has_title and not has_author:
        return None
    subject = title if has_title else words["this_book"]
    body = words["concludes"] % {"subject": subject}
    body += words["by_author_tail"] % {"author": author} if has_author else "."
    return Chapter(chapter_id=_OUTRO_CHAPTER_ID, sequence=0, title=words["the_end"],
                   text=body, char_count=len(body), include=True, speak_title=False)


def _plain(s: str) -> str:
    """Lowercase, accents stripped: "TABLE DES MATIÈRES" and "Table des
    matieres" are the same title to a hint list."""
    import unicodedata

    return "".join(c for c in unicodedata.normalize("NFKD", (s or "").strip().lower())
                   if not unicodedata.combining(c))


def _default_included(title: str, lang: str = "en") -> bool:
    """Whether a freshly-extracted section should default to being rendered.

    Front/back matter (copyright, ISBN, TOC, dedication, acknowledgements, …) is
    off by default so the audiobook opens on the real content and doesn't trail
    off into publisher boilerplate. The match is on the section title only — a
    legitimately short chapter still narrates. The user overrides any choice in
    the UI.

    The English hints always apply: a French EPUB is full of English
    boilerplate ("Copyright", "Contents"). The book's own language adds its own.
    """
    t = _plain(title)
    hints = set(rules_for("en").skip_title_hints) | set(rules_for(lang).skip_title_hints)
    return not any(hint in t for hint in hints)


def extract_job(job_id: str, keep_language: bool = False) -> list[Chapter]:
    store = JobStore(job_id)
    store.set_stage(Stage.EXTRACTING, "extracting")
    try:
        return _extract_job(store, keep_language=keep_language)
    except Exception as e:  # noqa: BLE001 - surface the reason to the user
        # Record why extraction stopped so the job page can show it (the web
        # runner otherwise swallows the exception).
        st = store.load_state()
        st.error = str(e)
        st.stage = Stage.ERROR.value
        store.save_state(st)
        raise


def _extract_job(store: JobStore, keep_language: bool = False) -> list[Chapter]:
    """Read the book and prepare its text for the narrator.

    ``keep_language``: the job's narration language was chosen on purpose (a
    re-read after the picker changed it), so the book's own metadata must not
    move it back. On a first read the book's language wins, when its model is
    installed.
    """
    book = store.load_book()
    previous = {c.chapter_id: c.include for c in store.load_chapters()}
    epub = store.dir / "normalized.epub"
    extract.run_ebook_convert(Path(book.source_path), epub)
    raw = extract.parse_epub(epub)
    if not any((c.text or "").strip() for c in raw.chapters):
        raise extract.ExtractionError(
            "No readable text was found in this book. If it's a scanned or image-only "
            "PDF, it has no selectable text to narrate — an OCR'd or EPUB version is "
            "needed."
        )

    # Trust the bytes, not the manifest's extension: covers arrive as WebP,
    # GIF, or PNG-in-a-.jpg, and embedding those under the wrong format flag
    # produces a file that validates fine and shows a blank square in Plex.
    if raw.cover_bytes:
        normalized = cover_mod.normalize_cover(raw.cover_bytes)
        if normalized:
            data, ext = normalized
            cover = store.dir / f"cover{ext}"
            cover.write_bytes(data)
            book.cover_path = str(cover)
    book.title = raw.title
    book.author = raw.author
    book.year = raw.year
    book.description = raw.description
    book.isbn = raw.isbn
    book.series = raw.series
    book.series_index = raw.series_index
    book.language = raw.language
    store.save_book(book)
    # Narrate in the book's own language when its model is installed; when it
    # is not, the voice stays English — what always happened — and the job
    # page says what installing would change.
    if (not keep_language and book.language != "en"
            and narration_langs.language_available(book.language)):
        set_job_language(store, book.language)
    # The text is prepared for whoever will read it: numbers, abbreviations and
    # the app's own announcements follow the *narration* language.
    lang = store.load_voice().language or "en"

    chapters: list[Chapter] = []
    intro = _intro_chapter(book, lang)
    if intro:
        chapters.append(intro)
    for i, rc in enumerate(raw.chapters):
        norm = normalize_text(rc.text, lang)
        title = normalize_title(rc.title, lang)
        chapters.append(
            Chapter(
                chapter_id=f"ch{i:04d}",
                sequence=i,
                title=title,
                text=norm,
                char_count=len(norm),
                include=_default_included(title, book.language),
            )
        )
    outro = _outro_chapter(book, lang)
    if outro:
        chapters.append(outro)
    # A re-read keeps the sections the person switched on or off: the ids are
    # positional, so the same source gives the same ids.
    for ch in chapters:
        if ch.chapter_id in previous:
            ch.include = previous[ch.chapter_id]
    # Renumber the display order so the injected intro/outro don't collide with
    # the real chapters' sequences.
    for seq, ch in enumerate(chapters):
        ch.sequence = seq
    store.save_chapters(chapters)
    store.set_stage(Stage.EXTRACTED, f"extracted {len(chapters)} chapters")
    return chapters


# --- segment building --------------------------------------------------------

def build_segments(chapters: list[Chapter], engine_version: str, vkey: str,
                   overrides: dict[str, str] | None = None) -> list[Segment]:
    segments: list[Segment] = []
    gseq = 0
    for ch in chapters:
        cseq = 0
        title = apply_pronunciation(ch.title, overrides) if overrides else ch.title
        body = apply_pronunciation(ch.text, overrides) if overrides else ch.text

        def add(text: str, boundary: str) -> None:
            nonlocal gseq, cseq
            segments.append(
                Segment(
                    segment_id=segment_id(text, engine_version, vkey),
                    chapter_id=ch.chapter_id,
                    sequence=gseq,
                    chapter_sequence=cseq,
                    text=text,
                    text_hash=text_hash(text),
                    boundary=boundary,
                )
            )
            gseq += 1
            cseq += 1

        # B: speak the chapter title as its own segment, with a longer pause
        # after it. The heading was stripped from the body during extraction, so
        # this doesn't double-read. Some synthetic sections (the outro) keep a
        # display-only marker and opt out of narrating it.
        if title and ch.speak_title:
            add(title, "chapter_title")
        # A/C: paragraph- and scene-aware body chunks carry their own boundaries.
        for chunk, boundary in chunk_structured(body):
            add(chunk, boundary)
    return segments


# --- rendering ---------------------------------------------------------------

class OutOfSpaceError(RuntimeError):
    """The disk filled up mid-render. Message is user-facing."""


def _render_one(adapter, text: str, out_path: Path, retries: int = 2) -> None:
    last_err: Exception | None = None
    for _attempt in range(retries + 1):
        try:
            clip = adapter.synthesize(text)
            write_wav(out_path, clip.samples, clip.sample_rate)
            if is_valid_audio(out_path):
                return
            last_err = RuntimeError("rendered audio failed validation (empty/too short)")
        except OSError as e:
            # A full disk will not fix itself on the next attempt, and retrying
            # twice more just delays the news. Every segment rendered so far
            # stays cached, so this is resumable once space is freed.
            if e.errno == errno.ENOSPC:
                out_path.unlink(missing_ok=True)
                raise OutOfSpaceError(
                    "The disk ran out of space while saving narration. "
                    "Everything rendered so far is kept, so free some space and "
                    "start the render again — it resumes where it stopped."
                ) from e
            last_err = e
        except Exception as e:  # noqa: BLE001 - engine failures are varied
            last_err = e
        out_path.unlink(missing_ok=True)
    raise RuntimeError(f"segment render failed after {retries + 1} attempts: {last_err}")


class JobCancelled(Exception):
    """Raised inside the render loop when a cancel is requested. Handled as a
    clean stop (not an error) — already-rendered segments stay cached."""


def _pick_preview_chapter(chapters: list[Chapter], preview_chapter_id: str | None) -> Chapter:
    """Chapter to sample for a preview: the requested one, else the first with
    real content (front matter / title pages tend to be very short)."""
    if preview_chapter_id:
        for ch in chapters:
            if ch.chapter_id == preview_chapter_id:
                return ch
    # Prefer the first substantial section the user is actually keeping.
    for ch in chapters:
        if ch.include and ch.char_count >= 500:
            return ch
    for ch in chapters:
        if ch.char_count >= 500:
            return ch
    return chapters[0]


def render_job(
    job_id: str,
    preview_max_seconds: float | None = None,
    preview_chapter_id: str | None = None,
    output_dir: str | Path | None = None,
    output_mode: str | None = None,
    progress: Progress | None = None,
    should_cancel: "Callable[[], bool] | None" = None,
    power_mode: str | None = None,
) -> JobState:
    """Render, assemble and package a job.

    If ``preview_max_seconds`` is set, render only a leading excerpt of one
    chapter (``preview_chapter_id`` or the first content chapter) up to that much
    audio, and emit a preview WAV instead of the final .m4b — using the exact
    same engine and voice settings as the full render, so it sounds identical.

    For a full render, ``output_mode`` picks where the final .m4b lands — the
    Plex library tree (``library``) or a single flat ``folder`` (``output_dir``).
    Both fall back to the previously-saved choice. The destination is resolved
    and write-checked up front so a bad one fails immediately rather than after
    hours of synthesis. Previews ignore all of this.

    ``power_mode`` ("full"/"balanced"/"quiet") caps how hard the render is
    allowed to push the machine; see :mod:`ebook_audiobook.power`. Defaults to
    the job's saved choice, then the global setting.

    ``should_cancel`` is polled between segments for cooperative cancellation.
    """
    store = JobStore(job_id).ensure()
    book = store.load_book()
    chapters = store.load_chapters()
    voice = store.load_voice()
    if not chapters:
        raise RuntimeError("no chapters — run extract first")
    # Before any expensive work: a language whose model is not here fails now,
    # with a message about Settings, not after the model load has failed.
    narration_langs.require_installed(voice.language)

    is_preview = preview_max_seconds is not None

    if not is_preview and not any(c.include for c in chapters):
        raise RuntimeError("no sections selected — enable at least one to render")

    # Full render: settle (and prove writable) the destination before doing any
    # expensive work, and remember it so a reload/resume reuses the same choice.
    out_m4b: Path | None = None
    write_sidecar = False
    if not is_preview:
        prior = store.load_state()
        if output_mode is not None:
            mode = output_mode
        elif output_dir is not None:
            mode = MODE_FOLDER  # an explicit folder implies folder mode
        else:
            mode = prior.output_mode or default_output_mode()
        chosen_dir = output_dir if output_dir is not None else prior.output_dir
        out_m4b, out_dir, write_sidecar = resolve_output_target(mode, chosen_dir, book)
        st0 = store.load_state()
        st0.output_mode = mode
        st0.output_dir = str(out_dir)
        store.save_state(st0)
    # Remember the lifecycle stage so a preview can restore it afterwards
    # (a preview must never leave the job looking "done").
    prior_stage = _resumable_stage(store.load_state().stage)

    # Model load can take ~10s — surface a clear "preparing" status first so the
    # UI never shows a stale stage while busy.
    store.set_stage(Stage.PREVIEWING if is_preview else Stage.PREPARING,
                    "loading voice model")
    st = store.load_state()
    st.error = None
    if is_preview:
        # Reset progress up front, not after the model load — otherwise a
        # back-to-back preview shows the PREVIEWING stage with the *previous*
        # preview's progress (~100%) during the ~10s load window.
        st.total_segments = 0
        st.rendered_segments = 0
        st.preview_progress = 0.0
        # Zero or negative would divide the progress by nothing after a full
        # model load; the web route clamps, the CLI does not.
        preview_max_seconds = max(1.0, float(preview_max_seconds))
    store.save_state(st)

    # How hard this render may push the machine. Applied to *this* thread before
    # the model loads, so the load itself is paced too — on a laptop that first
    # ~10s is a noticeable spike. Falls back to the saved per-job choice, then
    # the global setting.
    mode = power_mode
    if mode is None:
        mode = store.load_state().power_mode or settings.default_power_mode()
    pace_profile = power.profile_for(mode)
    for note in power.apply(pace_profile):
        store.set_stage(Stage.PREVIEWING if is_preview else Stage.PREPARING, note)

    adapter = get_adapter(voice, config.SAMPLE_RATE)
    try:
        # Model load and segment building run INSIDE the try so any failure here
        # is recorded as an ERROR on the job. The web runner swallows exceptions,
        # so without this a failed load would leave the UI stuck forever on
        # "Preparing — loading voice model" with no reason shown.
        adapter.load()
        vkey = voice_key(voice, config.SAMPLE_RATE)
        # User pronunciation fixes (e.g. "LOG" -> "log") are folded into segment
        # text here, so changing them re-renders only the affected segments.
        overrides = voice.extra.get("pron") or {}
        segments = build_segments(chapters, adapter.engine_version, vkey, overrides)
        store.save_segments(segments)

        if is_preview:
            chosen = _pick_preview_chapter(chapters, preview_chapter_id)
            todo = [s for s in segments if s.chapter_id == chosen.chapter_id]
        else:
            # Only render sections the user kept selected; excluded front/back
            # matter is skipped here and again at assembly.
            included = {c.chapter_id for c in chapters if c.include}
            todo = [s for s in segments if s.chapter_id in included]

        # Set the stage on the SAME state object the render loop saves each
        # segment. Using store.set_stage() here would update a separate reload,
        # and the loop's save_state(state) would then clobber the stage back to
        # "preparing" every segment — freezing the UI on "loading voice model"
        # for the whole render (previews escaped this only because their prepare
        # and render stages share the value "previewing").
        state = store.load_state()
        state.total_segments = len(todo)
        state.rendered_segments = 0
        state.preview_progress = 0.0
        state.error = None
        # Stamp the wall-clock start of a full render so the UI can show an
        # honest "time remaining" that survives a reload. Previews don't need it.
        state.render_started_at = None if is_preview else _now_iso()
        state.stage = (Stage.PREVIEWING if is_preview else Stage.RENDERING).value
        state.messages.append(f"preview: {chosen.title}" if is_preview else "full render")
        store.save_state(state)
        # Emit the initial 0/N immediately so the terminal/UI show the render has
        # begun the moment the model finishes loading — before the (slow) first
        # segment completes.
        if progress:
            progress(state)

        rendered: list[tuple[Segment, Path]] = []  # (segment, path) in order
        chars_done = 0
        audio_secs = 0.0
        # Time spent actually synthesizing, excluding any deliberate resting, so
        # the chars/sec figure the UI shows stays a measure of the machine rather
        # than of the power mode. Otherwise switching to quiet mode would look
        # like the hardware had got slower.
        work_seconds = 0.0

        for i, seg in enumerate(todo):
            if should_cancel and should_cancel():
                raise JobCancelled()
            path = store.segment_audio_path(seg.segment_id)
            if path.exists() and is_valid_audio(path):
                seg.status = "done"
            else:
                seg_t0 = time.monotonic()
                _render_one(adapter, seg.text, path)
                seg_elapsed = time.monotonic() - seg_t0
                work_seconds += seg_elapsed
                seg.status = "done"
                chars_done += len(seg.text)
                # Rest between segments so a long render leaves the machine
                # usable and cool. No-op at full speed.
                power.pace(pace_profile, seg_elapsed)
            rendered.append((seg, path))

            state.rendered_segments = i + 1
            if chars_done and work_seconds > 0:
                state.chars_per_render_second = round(chars_done / work_seconds, 2)

            # A preview stops once it has enough audio, not once the chapter's
            # segments run out — so drive its progress off seconds produced.
            reached_preview_limit = False
            if is_preview:
                audio_secs += _safe_duration(path)
                state.preview_progress = min(1.0, audio_secs / preview_max_seconds)
                reached_preview_limit = audio_secs >= preview_max_seconds

            store.save_state(state)
            if progress:
                progress(state)

            if reached_preview_limit:
                break

        store.save_segments(segments)

        if is_preview:
            out = _assemble_preview(store, rendered)
            state = store.load_state()
            # Preview output is tracked separately; it must not overwrite the
            # final .m4b path or flip the lifecycle stage to "done".
            state.preview_output = str(out)
            state.preview_at = _now_iso()
            state.stage = prior_stage
            store.save_state(state)
            return store.load_state()

        out = _assemble_and_package(store, book, chapters, segments, out_m4b, write_sidecar)
        state = store.load_state()
        state.output_path = str(out)
        # Full render finished: stamp completion, record size, and drop the
        # now-obsolete preview so it doesn't linger on disk.
        state.finished_at = _now_iso()
        try:
            state.output_bytes = out.stat().st_size
        except OSError:
            state.output_bytes = None
        store.preview_path().unlink(missing_ok=True)
        state.preview_output = None
        # Both, or the page reloads an <audio> pointed at a file that is gone.
        state.preview_at = None
        # The bitrate this file was actually encoded at. The voice's setting can
        # be changed afterwards without re-rendering, and the library's listening
        # time is derived from size ÷ bitrate — so it has to be the real one.
        state.output_bitrate_kbps = int(voice.extra.get("bitrate_kbps", config.DEFAULT_BITRATE_KBPS))
        store.save_state(state)
        store.set_stage(Stage.DONE, f"output: {out}")
        # Opt-in housekeeping: the raw narration audio is several GB and buys
        # nothing once the .m4b exists. Done here rather than in the web layer so
        # a `convert` from the command line honours the same preference. Never
        # allowed to fail a render that has already succeeded.
        try:
            from . import storage as storage_mod

            freed = storage_mod.free_after_render(job_id)
            if freed:
                store.set_stage(Stage.DONE, f"freed {freed} bytes of working files")
        except Exception:  # noqa: BLE001 - the audiobook is written; nothing here is worth failing for
            pass
        return store.load_state()
    except JobCancelled:
        if is_preview:
            state = store.load_state()
            state.stage = prior_stage
            store.save_state(state)
        else:
            store.set_stage(Stage.CANCELLED, "cancelled — rerun to resume")
        return store.load_state()
    except Exception as e:  # noqa: BLE001
        state = store.load_state()
        state.error = str(e)
        # A failed preview shouldn't wreck the render lifecycle.
        state.stage = prior_stage if is_preview else Stage.ERROR.value
        store.save_state(state)
        if not is_preview:
            store.set_stage(Stage.ERROR, f"error: {e}")
        raise
    finally:
        adapter.unload()


def measure_job(job_id: str, progress: "Progress | None" = None,
                should_cancel: "Callable[[], bool] | None" = None,
                power_mode: str | None = None) -> JobState:
    """Narrate a little real audio to find out what this book actually costs.

    The estimates on the job page otherwise assume a fixed speaking rate, so they
    ignore the pacing setting, the voice and the reference clip — every one of
    which changes how long the finished audiobook runs. This renders a short
    stretch with the *current* settings and measures two things from it:

      * characters per second of audio  — drives length, file size, disk space
      * characters per second of work   — drives "time to narrate"

    Two things are deliberately excluded, because including them is how a
    calibration lies:

      * the model load, which happens once and is not part of narrating
      * the first freshly-rendered segment, which is markedly slower while the
        engine warms up — counting it makes fast machines look slow

    Nothing is thrown away. Segments are content-addressed, so everything
    rendered here is exactly what the full render would have produced and is
    reused by it.
    """
    store = JobStore(job_id).ensure()
    chapters = store.load_chapters()
    if not chapters:
        raise RuntimeError("no chapters — read the book first")
    voice = store.load_voice()
    narration_langs.require_installed(voice.language)

    prior_stage = _resumable_stage(store.load_state().stage)

    store.set_stage(Stage.PREVIEWING, "measuring how long this will take")
    st = store.load_state()
    st.preview_progress = 0.0
    st.error = None
    store.save_state(st)

    mode = power_mode or store.load_state().power_mode or settings.default_power_mode()
    pace_profile = power.profile_for(mode)
    power.apply(pace_profile)

    adapter = get_adapter(voice, config.SAMPLE_RATE)
    try:
        adapter.load()  # deliberately outside every timing below
        vkey = voice_key(voice, config.SAMPLE_RATE)
        overrides = voice.extra.get("pron") or {}
        segments = build_segments(chapters, adapter.engine_version, vkey, overrides)

        # Draw from every section that will actually be narrated, starting at the
        # first real chapter — not from that one chapter alone. A chapter can be
        # a single paragraph, and taking only its segments left nothing to
        # measure once the warm-up had been discarded.
        chosen = _pick_preview_chapter(chapters, None)
        included = {c.chapter_id for c in chapters if c.include}
        ordered = [s for s in segments if s.chapter_id in included]
        start = next((i for i, s in enumerate(ordered)
                      if s.chapter_id == chosen.chapter_id), 0)
        ordered = ordered[start:] + ordered[:start]
        # Narrated chapter titles are one short line each and read nothing like
        # the body of a book, so they are poor material for a rate.
        body = [s for s in ordered if s.boundary != "chapter_title"] or ordered
        # The warm-up is the first generation the engine actually performs —
        # not the first segment in the list, which may well be sitting in the
        # cache from an earlier preview and cost nothing. With only one usable
        # segment there is nothing left after discarding a warm-up, and a rough
        # figure beats none at all.
        warmup_pending = len(body) > 1

        chars = 0
        audio_seconds = 0.0
        work_chars = 0
        work_seconds = 0.0
        counted = 0

        for seg in body[: config.MEASURE_MAX_SEGMENTS + 1]:
            if should_cancel and should_cancel():
                raise JobCancelled()
            path = store.segment_audio_path(seg.segment_id)
            fresh = not (path.exists() and is_valid_audio(path))
            t0 = time.monotonic()
            if fresh:
                _render_one(adapter, seg.text, path)
            elapsed = time.monotonic() - t0

            if fresh and warmup_pending:
                # The warm-up generation. Rendered and kept, but not counted.
                warmup_pending = False
                power.pace(pace_profile, elapsed)
                continue

            duration = _safe_duration(path)
            if duration <= 0:
                continue
            chars += len(seg.text)
            audio_seconds += duration
            # A cached segment costs no time, so it informs the audio-length
            # measurement but must not flatter the speed one — neither its
            # seconds nor its characters belong in that ratio.
            if fresh:
                work_chars += len(seg.text)
                work_seconds += elapsed
                counted += 1
                power.pace(pace_profile, elapsed)

            st = store.load_state()
            st.preview_progress = min(1.0, audio_seconds / config.MEASURE_TARGET_AUDIO_SECONDS)
            store.save_state(st)
            if progress:
                progress(st)
            if audio_seconds >= config.MEASURE_TARGET_AUDIO_SECONDS:
                break

        store.save_segments(segments)
        state = store.load_state()
        if chars and audio_seconds > 0:
            state.chars_per_audio_second = round(chars / audio_seconds, 2)
            state.measured_voice_key = vkey
        if counted and work_seconds > 0:
            state.chars_per_render_second = round(work_chars / work_seconds, 2)
        state.preview_progress = 0.0
        state.stage = prior_stage
        store.save_state(state)
        return store.load_state()
    except JobCancelled:
        state = store.load_state()
        state.stage = prior_stage
        state.preview_progress = 0.0
        store.save_state(state)
        return store.load_state()
    except Exception as e:  # noqa: BLE001
        state = store.load_state()
        state.error = str(e)
        state.stage = prior_stage
        state.preview_progress = 0.0
        store.save_state(state)
        raise
    finally:
        adapter.unload()


def _resumable_stage(stage: str) -> str:
    """The stage a job goes back to after a preview or a measurement.

    Anything transient means "working", which the job is about to stop being.
    A prior *error* is mapped the same way: previewing clears ``state.error``
    (a preview is a fresh attempt), so restoring ``error`` afterwards would leave
    the job saying "Stopped by a problem" with no problem to show.
    """
    if stage in (Stage.PREPARING.value, Stage.PREVIEWING.value, Stage.RENDERING.value,
                 Stage.ASSEMBLING.value, Stage.PACKAGING.value, Stage.ERROR.value):
        return Stage.EXTRACTED.value
    return stage


def _safe_duration(path: Path) -> float:
    try:
        from .audio.wav import duration_seconds

        return duration_seconds(path)
    except Exception:
        return 0.0


def _assemble_preview(store: JobStore, rendered: list[tuple[Segment, Path]]) -> Path:
    import numpy as np
    import soundfile as sf

    out = paths().outputs / f"{store.job_id}_preview.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    with sf.SoundFile(str(out), "w", samplerate=config.SAMPLE_RATE, channels=1, subtype="PCM_16") as f:
        for i, (seg, p) in enumerate(rendered):
            data, _sr = read_wav(p)
            f.write(data)
            if i < len(rendered) - 1:
                gap = np.zeros(int(assemble.gap_for(seg.boundary) * config.SAMPLE_RATE), dtype=np.float32)
                f.write(gap)
    return out


def _assemble_and_package(store: JobStore, book: Book, chapters: list[Chapter], segments: list[Segment],
                          out_path: Path | None = None, write_sidecar: bool = False) -> Path:
    from .audio import tag as tagmod
    from .audio import validate as validatemod

    store.set_stage(Stage.ASSEMBLING, "assembling chapters")
    by_chapter: dict[str, list[Segment]] = {}
    for seg in segments:
        by_chapter.setdefault(seg.chapter_id, []).append(seg)

    chapter_audios: list[package.ChapterAudio] = []
    for ch in chapters:
        if not ch.include:
            continue  # excluded section — no audio was rendered for it
        segs = by_chapter.get(ch.chapter_id)
        if not segs:
            continue
        specs = [(store.segment_audio_path(s.segment_id), assemble.gap_for(s.boundary)) for s in segs]
        cpath = store.chapter_audio_path(ch.chapter_id)
        # The closing outro gets a long lead-in silence so the ending is
        # unmistakable when listening away from a screen.
        lead = config.PAUSE_BEFORE_OUTRO if ch.chapter_id == _OUTRO_CHAPTER_ID else 0.0
        assemble.assemble_chapter(cpath, specs, lead_pause=lead)
        chapter_audios.append(package.ChapterAudio(title=ch.title, path=cpath))

    store.set_stage(Stage.PACKAGING, "packaging .m4b")
    out = out_path or (paths().outputs / f"{layout.output_stem(book)}.m4b")
    voice = store.load_voice()
    bitrate = int(voice.extra.get("bitrate_kbps", config.DEFAULT_BITRATE_KBPS))
    cover = Path(book.cover_path) if book.cover_path else None
    package.package_m4b(
        out, chapter_audios, book.title, book.author,
        cover_path=cover, bitrate_kbps=bitrate, workdir=store.dir,
        language=voice.language,
    )

    # Plex-friendly tags (stik=2, album artist, covr, year/desc/ISBN) as a
    # post-mux pass; ffmpeg can't set several of these atoms well.
    tagmod.tag_m4b(out, book)
    if write_sidecar and cover and cover.exists():
        ext = ".png" if cover.suffix.lower() == ".png" else ".jpg"
        shutil.copy2(cover, out.parent / f"cover{ext}")

    problems = validatemod.validate_m4b(out, book)
    if problems:
        raise RuntimeError("output failed Plex validation: " + "; ".join(problems))
    return out
