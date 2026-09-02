"""Thin Flask UI over the pipeline functions. Localhost only.

Nothing slow happens in a request handler: extraction, rendering, previews, and
voice-sample audition are all handed to the single background ``runner``; the
browser polls ``/job/<id>/status`` and ``/api/status``.
"""

from __future__ import annotations

import re
import threading
import uuid
from pathlib import Path

from flask import (Flask, Response, abort, current_app, g, jsonify, redirect,
                   render_template, request, send_file, url_for)
from werkzeug.utils import secure_filename

from .. import (checks, config, power, settings as app_settings, storage as storage_mod,
                tools, worker)
from .. import hashing, i18n
from ..i18n import _
from ..config import VoiceSettings, paths
from ..desktop import runtime
from ..jobs.models import STAGE_LABELS, Stage, stage_label
from ..jobs.store import JobStore
from ..pipeline import extract as extract_mod, layout
from ..voices import AUDIO_EXTS, VoiceLibrary
from .runner import runner


def _app_version() -> str:
    from .. import __version__

    return __version__


def _f(form, key, default):
    """Parse a float form field, falling back to the current value."""
    try:
        return float(form.get(key, default))
    except (TypeError, ValueError):
        return default


def _parse_pron(text: str) -> dict:
    """Parse the pronunciation-fixes textarea (one ``FROM=TO`` per line) into a
    dict. Blank lines and lines without a left-hand side are ignored."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        if "=" not in line:
            continue
        src, dst = line.split("=", 1)
        src = src.strip()
        if src:
            out[src] = dst.strip()
    return out


def _safe_upload_name(filename: str) -> str:
    """A filesystem-safe name for an uploaded ebook that keeps its extension.

    ``secure_filename`` strips non-ASCII entirely, so a book named
    ``Война и мир.epub`` or ``日本語.epub`` collapses to the empty string — and the
    old fallback of ``"upload"`` lost the extension too, making the import fail
    with the confusing "files with no extension aren't supported". The extension
    decides which importer runs, so it is preserved separately from the stem.
    """
    src = Path(filename)
    ext = src.suffix.lower()
    if re.fullmatch(r"\.[A-Za-z0-9]{1,6}", ext or ""):
        stem = secure_filename(src.stem)
    else:
        # Not a plausible extension (".something-long", or none at all). Keep the
        # whole name rather than silently amputating part of it; import will then
        # report the unsupported format clearly.
        stem, ext = secure_filename(src.name), ""
    return f"{stem or 'upload'}{ext}"


def human_bytes(n) -> str:
    """In the request's language: "1.5 MB" here, "1,5 Mo" in French."""
    return i18n.human_bytes(n)


# Muted, deliberately unsaturated grounds for books whose ebook carries no cover
# image. Picked by a stable hash of the title so a given book always looks the
# same, and kept dark enough that the serif title stays legible on top.
COVER_TINTS = ("#244A52", "#2A2F52", "#6E4A22", "#7A3B2E", "#4E5A44",
               "#6B2733", "#3A3E2C", "#26221F", "#3F3350", "#1F4740")


def cover_tint(title: str) -> str:
    """A stable colour for a coverless book. Not decoration: it is what makes a
    shelf of fallback covers scannable instead of a wall of identical grey."""
    import zlib

    return COVER_TINTS[zlib.crc32((title or "").encode("utf-8")) % len(COVER_TINTS)]


def fmt_listening(output_bytes: int | None, kbps: int) -> str | None:
    """How long a finished audiobook plays — "8h 04m" — in the request's language."""
    return i18n.fmt_listening(output_bytes, kbps)


def fmt_dt(iso) -> str:
    if not iso:
        return ""
    try:
        from datetime import datetime

        return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return str(iso)


def _nearest_existing(path: Path) -> Path:
    """Walk up to the first directory that exists.

    A destination folder is usually created only when the render starts, and
    ``disk_usage`` on a path that doesn't exist yet raises — but its parent
    volume is what we actually want to measure. Falls back to the path itself
    when nothing in the chain exists (an unmounted drive), which then reports
    unknown rather than lying.
    """
    p = path
    for _hop in range(64):  # bounded: a symlink cycle must not hang a request
        if p.exists():
            return p
        if p.parent == p:
            break
        p = p.parent
    return path


def _disk_usage(path: Path) -> dict:
    import shutil as _shutil

    try:
        usage = _shutil.disk_usage(str(_nearest_existing(path)))
        return {"path": str(path), "free_bytes": usage.free, "total_bytes": usage.total}
    except OSError:
        # An unreachable network share, or a drive letter with nothing in it.
        return {"path": str(path), "free_bytes": None, "total_bytes": None}


def _same_volume(a: Path, b: Path) -> bool:
    """Whether two paths live on the same filesystem.

    ``st_dev`` is the reliable answer on POSIX *and* Windows (where it's the
    volume serial number). When either path can't be stat'd, assume they differ
    — reporting one figure for two volumes is the failure worth avoiding.
    """
    try:
        return _nearest_existing(a).stat().st_dev == _nearest_existing(b).stat().st_dev
    except OSError:
        return False


def _offerable_voices(current_id: str | None = None) -> list[dict]:
    """The voices a person may pick from.

    The engine's own untuned voice is deliberately not among them: it exists as
    a fallback, not as a choice, and the shipped narrators are better in every
    case. It is still shown when a job already uses it, because silently
    swapping a book's voice would silently re-render the book.
    """
    return [v.to_dict() for v in VoiceLibrary().list()
            if not v.is_default or v.id == current_id]


def _busy_job_id() -> str | None:
    """Which job the single worker is on, if any. ``runner.current`` is
    "<job_id>:<kind>"; everywhere outside the runner only the id matters."""
    cur = runner.current
    return cur.split(":", 1)[0] if cur else None


def _measured_here(store: JobStore, state) -> bool:
    """Whether the job's measured rates were taken with its current voice
    settings. Estimates measured with different settings are no longer about
    this book as it is now, which is what the UI offers to put right."""
    if not state.measured_voice_key:
        return False
    current = hashing.voice_key(store.load_voice(), config.SAMPLE_RATE)
    return state.measured_voice_key == current


def _job_or_404(job_id: str) -> JobStore:
    store = JobStore(job_id)
    if not store.exists():
        abort(404)
    return store


def _reconcile_stale(store: JobStore):
    """Recover a job left mid-flight by a killed/crashed process.

    A stored stage like "preparing"/"rendering" means "working", but after a
    restart no worker is actually running it — yet the UI would show a Stop
    button that does nothing. If the stage is transient and the runner has no
    work for this job, reset it to a resumable stage (a re-render resumes from
    the content-addressed segment cache). Returns the (possibly updated) state.
    """
    st = store.load_state()
    try:
        transient = not Stage(st.stage).is_terminal
    except ValueError:
        transient = False
    if transient and not runner.is_busy(store.job_id):
        st.stage = Stage.EXTRACTED.value if store.load_chapters() else Stage.IMPORTED.value
        st.error = None
        st.messages.append("interrupted (process stopped) — reset; re-render to resume")
        store.save_state(st)
    return st


def create_app() -> Flask:
    app = Flask(__name__)
    app.jinja_env.filters["humanbytes"] = human_bytes
    app.jinja_env.filters["dt"] = fmt_dt
    app.jinja_env.filters["stage"] = stage_label
    app.jinja_env.filters["tint"] = cover_tint
    # For _(msgid, **params) where a value is a count: "12 000" in French.
    app.jinja_env.filters["nums"] = lambda d: {
        k: (i18n.fmt_int(v) if isinstance(v, int) and not isinstance(v, bool) else v)
        for k, v in d.items()}
    # Interface language: Jinja's own i18n extension over stdlib gettext. The
    # callables look the language up per request, so one environment serves
    # every language. newstyle matters: _("… %(n)s …", n=x) formats with the
    # variables escaped and the msgid trusted as markup, which is what lets a
    # paragraph keep its inline <code> as one translatable piece — the price is
    # that a literal % in a template string must be written %%.
    app.jinja_env.add_extension("jinja2.ext.i18n")
    app.jinja_env.install_gettext_callables(
        i18n.translate, i18n.translate_plural, newstyle=True,
        pgettext=i18n.translate_context, npgettext=i18n.translate_context_plural)
    app.jinja_env.policies["ext.i18n.trimmed"] = True
    p = paths().ensure()

    # A prior process may have been killed mid-render, leaving jobs frozen in a
    # transient stage with no worker behind them. Reconcile at boot so the
    # library and job pages don't show a dead "Preparing"/Stop.
    for _jid in JobStore.list_ids():
        try:
            _reconcile_stale(JobStore(_jid))
        except Exception:
            continue

    @app.before_request
    def _choose_language():
        # EBAB_LANG, then the saved setting, then what the browser asks for.
        s = app_settings.load_settings()
        g.settings = s
        g.lang = i18n.resolve(
            s.language, request.accept_languages.best_match(list(i18n.SUPPORTED)))

    @app.after_request
    def _declare_language(resp):
        if resp.mimetype == "text/html":
            resp.headers["Content-Language"] = getattr(g, "lang", i18n.DEFAULT)
        return resp

    @app.context_processor
    def inject_globals():
        s = getattr(g, "settings", None) or app_settings.load_settings()
        lang = getattr(g, "lang", None) or i18n.DEFAULT
        return {
            "lang": lang,
            "language_setting": s.language,
            "languages": i18n.language_choices(),
            "js_catalog": i18n.js_catalog(lang),
            "home_dir": str(Path.home()),
            "data_root": str(paths().root),
            "audiobooks_root": s.audiobooks_root,
            # First-run nudge: no library folder chosen and not yet dismissed.
            "setup_needed": not s.audiobooks_root and not s.setup_dismissed,
            # True only when there is a real server to shut down, so the Quit
            # control doesn't appear under `flask run` or in tests, where it
            # could not work. Read at render time, after serve() sets it.
            "can_quit": bool(current_app.config.get("EBAB_SHUTDOWN")),
            "power_mode": s.power_mode,
            "power_modes": [
                {"id": m, "label": _(power.MODE_LABELS[m]),
                 "description": _(power.MODE_DESCRIPTIONS[m])}
                for m in power.MODES
            ],
            "check_for_updates": s.check_for_updates,
            "auto_free_working_files": s.auto_free_working_files,
            "autoplay_preview": s.autoplay_preview,
            "app_version": _app_version(),
        }

    # ----- pages ------------------------------------------------------------

    @app.get("/")
    def index():
        busy_id = _busy_job_id()
        jobs = []
        for jid in JobStore.list_ids():
            store = JobStore(jid)
            try:
                book = store.load_book()
                state = _reconcile_stale(store)
                jobs.append({
                    "id": jid,
                    "book": book,
                    "state": state,
                    "bytes": store.disk_bytes(),
                    # The shelf shows covers; without this every card would have
                    # to guess and then 404 an <img>.
                    "has_cover": bool(book.cover_path and Path(book.cover_path).is_file()),
                    "tint": cover_tint(book.title),
                    # The bitrate the file was encoded at, when the render
                    # recorded it; the voice's current setting is only a
                    # fallback for books rendered before it was stamped.
                    "duration": fmt_listening(
                        state.output_bytes,
                        int(state.output_bitrate_kbps
                            or store.load_voice().extra.get("bitrate_kbps", config.DEFAULT_BITRATE_KBPS)),
                    ) if state.stage == Stage.DONE.value else None,
                    "working": state.stage == Stage.DONE.value,
                    "busy": jid == busy_id,
                })
            except Exception:
                continue
        jobs.sort(key=lambda j: (j["state"].created_at or ""), reverse=True)  # newest first

        # Three shelves, because the answer to "what is this app doing" differs
        # completely between them: one book may be mid-render, some are finished
        # and listenable, the rest are waiting on a decision.
        running = [j for j in jobs if j["busy"]]
        finished = [j for j in jobs if not j["busy"] and j["state"].stage == Stage.DONE.value]
        waiting = [j for j in jobs if not j["busy"] and j["state"].stage != Stage.DONE.value]
        total = sum(j["bytes"] for j in jobs)
        return render_template("library.html", jobs=jobs, total_bytes=total,
                               running=running, finished=finished, waiting=waiting)

    @app.get("/new")
    def new_page():
        return render_template("new.html", start=str(Path.home()))

    @app.get("/voices")
    def voices_page():
        return _voices_page()

    def _voices_page(error: str | None = None, code: int = 200):
        from ..voices import default_voice_id

        return render_template(
            "voices.html",
            voices=_offerable_voices(),
            default_voice_id=default_voice_id(),
            start=str(Path.home()),
            error=error,
        ), code

    @app.get("/settings")
    def settings_page():
        return render_template("settings.html")

    # Surveying walks every job's segment tree — tens of thousands of stat()
    # calls for a long book — and every open page asks for it every few seconds
    # to keep the sidebar figure current. The figure changes when a render
    # finishes or files are freed, not every four seconds, so it is cached and
    # dropped whenever something here deletes.
    _storage_cache: dict = {"at": 0.0, "value": None}
    _STORAGE_TTL = 20.0

    def _survey(fresh: bool = False):
        import time

        now = time.monotonic()
        cached = _storage_cache["value"]
        if fresh or cached is None or now - _storage_cache["at"] > _STORAGE_TTL:
            cached = storage_mod.survey(_busy_job_id(), is_busy=runner.is_busy)
            _storage_cache.update(at=now, value=cached)
        return cached

    def _forget_survey() -> None:
        _storage_cache["value"] = None

    @app.get("/storage")
    def storage_page():
        """What is on disk, and which of it is only there to speed up a re-render.

        The whole page exists because the biggest thing this app stores is also
        the most disposable, and until now the only way to reclaim it was a
        per-book button you had to already know about.
        """
        return render_template("storage.html",
                               survey=_survey(fresh=True),
                               safe=storage_mod.SAFE, held=storage_mod.HELD,
                               busy=storage_mod.BUSY, none=storage_mod.NONE)

    @app.get("/api/storage")
    def api_storage():
        """The same survey as JSON.

        Fetched by every page to fill the sidebar's working-files figure. It
        walks each job's tree, so it is deliberately not computed during a page
        render — same reasoning as /api/prereqs — and it is cached briefly, for
        the same reason again.
        """
        return _survey().to_dict()

    @app.post("/storage/free")
    def storage_free():
        """Delete the working files of the named jobs (default: every safe one).

        ``force=1`` reaches past the safety check, which is only ever sent after
        the user has been told, in the row itself, how many sections deleting
        that book's files would make it narrate again.
        """
        busy = _busy_job_id()
        ids = request.form.getlist("job_id")
        force = request.form.get("force") == "1"
        if not ids:
            ids = [b.job_id for b in _survey(fresh=True).safe_books]
        freed, skipped = storage_mod.free(ids, busy_job_id=busy, force=force,
                                          is_busy=runner.is_busy)
        _forget_survey()
        return {"ok": True, "freed_bytes": freed, "skipped": skipped}

    @app.post("/settings")
    def settings_save():
        s = app_settings.load_settings()
        # Each field is only touched when the form actually carries it. The page
        # saves settings independently, and an absent field must mean "leave it
        # alone" rather than "clear it" — otherwise saving the render mode would
        # silently wipe the library folder.
        if "audiobooks_root" in request.form:
            raw = (request.form.get("audiobooks_root") or "").strip()
            if raw:
                try:
                    out = worker.resolve_output_dir(raw)  # must be writable
                except worker.OutputDirError as e:
                    return {"ok": False, "error": str(e)}, 400
                s.audiobooks_root = str(out)
            else:
                s.audiobooks_root = None  # explicitly cleared
        if request.form.get("power_mode") is not None:
            s.power_mode = power.normalize_mode(request.form.get("power_mode"))
        if "check_for_updates" in request.form:
            s.check_for_updates = request.form.get("check_for_updates") == "1"
        if "autoplay_preview" in request.form:
            s.autoplay_preview = request.form.get("autoplay_preview") == "1"
        if "auto_free_working_files" in request.form:
            s.auto_free_working_files = request.form.get("auto_free_working_files") == "1"
        if "language" in request.form:
            s.language = i18n.normalize(request.form.get("language"))
        if "audiobooks_root" in request.form:
            # Choosing (or clearing) the folder answers the first-run question.
            # Flipping an unrelated switch does not, and must not silently
            # dismiss a prompt the user never saw.
            s.setup_dismissed = True
        app_settings.save_settings(s)
        # The tray has no browser to ask; it follows the setting, or the desktop.
        i18n.set_process_language(i18n.resolve(s.language, i18n.detect_os_language()))
        return {"ok": True, "audiobooks_root": s.audiobooks_root,
                "power_mode": s.power_mode,
                "check_for_updates": s.check_for_updates,
                "auto_free_working_files": s.auto_free_working_files,
                "autoplay_preview": s.autoplay_preview,
                "language": s.language}

    # ----- updates, backup, diagnostics -------------------------------------

    @app.post("/updates/check")
    def updates_check():
        """Ask GitHub for the latest release.

        POST, and only ever from a button: this is the one request in the app
        that leaves the machine, so it happens when the user asks and at no
        other time. There is no polling and nothing on page load.
        """
        from .. import update as update_mod

        available, release, message = update_mod.status()
        return {
            "ok": True,
            "available": available,
            "message": message,
            "current": update_mod.current_version(),
            "latest": release.version if release else None,
            "notes_url": release.notes_url if release else None,
            "command": update_mod.install_command(),
        }

    @app.get("/backup/estimate")
    def backup_estimate():
        from .. import backup as backup_mod

        profile = request.args.get("profile", backup_mod.DEFAULT_PROFILE)
        try:
            selection = backup_mod.resolve_selection(profile)
        except ValueError as e:
            return {"ok": False, "error": str(e)}, 400
        est = backup_mod.estimate(selection)
        return {
            "ok": True,
            "profile": profile,
            "bytes": est.selected_bytes,
            "files": est.selected_files,
            "human": backup_mod.human_bytes(est.selected_bytes),
            "excluded_human": backup_mod.human_bytes(est.excluded_bytes),
            "categories": [
                {"key": key, "label": _(label),
                 "files": est.by_category.get(key, (0, 0))[0],
                 "human": backup_mod.human_bytes(est.by_category.get(key, (0, 0))[1]),
                 "included": getattr(selection, key, False)}
                for key, label in backup_mod.CATEGORY_LABELS.items()
                if est.by_category.get(key, (0, 0))[0]
            ],
        }

    # A backup this old is not being downloaded any more; whatever request built
    # it is long gone.
    _STALE_ARCHIVE_SECONDS = 3600

    def _sweep_stale_archives() -> None:
        """Clear out archives a previous request failed to clean up.

        Belt and braces for the cleanup below: if a download is abandoned
        halfway, or the process is killed mid-stream, nothing else would ever
        remove the copy. Left alone that silently doubles the disk cost of every
        backup, which is the one thing this feature must not do.
        """
        import time as _time

        cutoff = _time.time() - _STALE_ARCHIVE_SECONDS
        for old in paths().tmp.glob("ebook-audiobook-*.zip"):
            try:
                if old.stat().st_mtime < cutoff:
                    old.unlink()
            except OSError:
                pass

    @app.post("/backup")
    def backup_download():
        """Build the archive in the data root's tmp/ and stream it back."""
        from .. import backup as backup_mod

        profile = request.form.get("profile", backup_mod.DEFAULT_PROFILE)
        try:
            selection = backup_mod.resolve_selection(profile)
        except ValueError as e:
            return {"ok": False, "error": str(e)}, 400

        stamp = __import__("time").strftime("%Y%m%d-%H%M%S")
        name = f"ebook-audiobook-{profile}-{stamp}.zip"
        paths().ensure()
        _sweep_stale_archives()
        tmp_path = paths().tmp / name
        try:
            backup_mod.create(tmp_path, selection)
        except backup_mod.BackupError as e:
            return {"ok": False, "error": str(e)}, 400

        # Stream it ourselves rather than handing the path to send_file.
        #
        # The archive is a copy, so it has to be deleted once it has been sent
        # or keeping it would double the disk cost of every backup. Doing that
        # from after_this_request — as this used to — runs the delete while
        # send_file still holds the file open, and Windows refuses to unlink an
        # open file, so the error was swallowed and the copy stayed. call_on_close
        # is the documented alternative and does not fire under Flask's test
        # client at all, which would have made this untestable.
        #
        # A generator settles it: the `with` closes the handle before the
        # `finally` deletes, in that order, on every platform. It also covers the
        # client that disconnects halfway, because closing a partly-consumed
        # generator raises GeneratorExit through the same `finally`.
        size = tmp_path.stat().st_size

        def _stream_and_delete():
            try:
                with open(tmp_path, "rb") as fh:
                    while chunk := fh.read(256 * 1024):
                        yield chunk
            finally:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass

        return Response(
            _stream_and_delete(),
            mimetype="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{name}"',
                "Content-Length": str(size),
            },
        )

    @app.get("/diagnostics")
    def diagnostics():
        from .. import errorlog

        errorlog.prune()
        found = errorlog.entries(limit=20)
        return {
            "ok": True,
            "log_path": str(errorlog.log_path()),
            "bytes": errorlog.total_bytes(),
            "max_bytes": errorlog.MAX_BYTES * (errorlog.BACKUP_COUNT + 1),
            "max_age_days": errorlog.MAX_AGE_DAYS,
            "errors": [
                {"ts": e.get("ts"), "op": e.get("op"), "job_id": e.get("job_id"),
                 "error": e.get("error"), "message": e.get("message")}
                for e in reversed(found)
            ],
        }

    @app.get("/diagnostics/report")
    def diagnostics_report():
        """The Markdown bug report, as a download."""
        from .. import errorlog

        text = errorlog.issue_report(limit=int(request.args.get("limit", 5)))
        return app.response_class(
            text, mimetype="text/markdown",
            headers={"Content-Disposition":
                     'attachment; filename="ebook-audiobook-report.md"'})

    @app.post("/diagnostics/clear")
    def diagnostics_clear():
        from .. import errorlog

        return {"ok": True, "removed": errorlog.clear()}

    @app.post("/settings/dismiss-setup")
    def settings_dismiss():
        s = app_settings.load_settings()
        s.setup_dismissed = True
        app_settings.save_settings(s)
        return {"ok": True}

    @app.get("/job/<job_id>")
    def job_page(job_id):
        store = _job_or_404(job_id)
        chapters = store.load_chapters()
        total_chars = sum(c.char_count for c in chapters)
        voice = store.load_voice()
        bitrate = int(voice.extra.get("bitrate_kbps", config.DEFAULT_BITRATE_KBPS))
        # Pronunciation fixes render back into the textarea as "FROM=TO" lines.
        pron_text = "\n".join(f"{k}={v}" for k, v in (voice.extra.get("pron") or {}).items())
        # Reset a stage stranded by a killed process before rendering the page.
        state = _reconcile_stale(store)
        measured_here = _measured_here(store, state)
        default_ch = worker._pick_preview_chapter(chapters, None).chapter_id if chapters else ""
        book = store.load_book()
        root = app_settings.audiobooks_root()
        return render_template(
            "job.html",
            job_id=job_id,
            book=book,
            has_cover=bool(book.cover_path and Path(book.cover_path).is_file()),
            tint=cover_tint(book.title),
            # The client renders a stage into words too (it polls faster than a
            # page reload), so it gets the same table rather than a second copy.
            stage_labels=STAGE_LABELS,
            chapters=chapters,
            voice=voice,
            voices=_offerable_voices(voice.extra.get("voice_id")),
            selected_voice_id=voice.extra.get("voice_id", "default"),
            default_chapter_id=default_ch,
            bitrate=bitrate,
            pron_text=pron_text,
            state=state,
            output_mode=state.output_mode or worker.default_output_mode(),
            job_power_mode=state.power_mode or app_settings.default_power_mode(),
            folder_dir=state.output_dir if state.output_mode == worker.MODE_FOLDER else str(paths().outputs),
            library_target=str(layout.library_m4b_path(Path(root), book)) if root else None,
            output_filename=layout.output_stem(book) + ".m4b",
            busy=runner.is_busy(job_id),
            disk_bytes=store.disk_bytes(),
            intermediate_bytes=store.intermediate_bytes(),
            # Constants the client uses to recompute the render estimate live as
            # sections are toggled or the bitrate changes.
            chars_per_audio_second=config.CHARS_PER_AUDIO_SECOND,
            sample_rate=config.SAMPLE_RATE,
            size_warn_bytes=config.SIZE_WARN_BYTES,
            render_rate=state.chars_per_render_second,
            audio_rate=state.chars_per_audio_second,
            measured_here=measured_here,
        )

    # ----- filesystem browser ----------------------------------------------

    @app.get("/api/fs")
    def api_fs():
        raw = request.args.get("path")
        kind = request.args.get("kind", "ebook")
        # "dir" mode is a folder picker (choosing a render destination): show
        # subfolders only, no files to select.
        if kind == "dir":
            allowed = set()
        elif kind == "audio":
            allowed = AUDIO_EXTS
        else:
            allowed = extract_mod.SUPPORTED_INPUT
        base = (Path(raw).expanduser() if raw else Path.home())
        try:
            base = base.resolve()
            if not base.is_dir():
                base = base.parent
            entries = sorted(base.iterdir(), key=lambda x: x.name.lower())
            dirs = [{"name": e.name, "path": str(e)} for e in entries
                    if e.is_dir() and not e.name.startswith(".")]
            files = []
            for e in entries:
                # Hidden files are noise at best. At worst they are macOS
                # AppleDouble sidecars (``._Book.epub`` on any external or
                # network volume), which carry the extension of a real ebook
                # and none of its content, so they list as books and then
                # fail to import.
                if not e.is_file() or e.name.startswith("."):
                    continue
                ext = e.suffix.lower()
                if ext in allowed:
                    files.append({"name": e.name, "path": str(e)})
                elif kind == "ebook":
                    # Surface known-but-unsupported ebooks (e.g. .kfx) as disabled
                    # rows with the reason, so they don't just vanish from the list.
                    reason = worker.unsupported_format_hint(ext)
                    if reason:
                        files.append({"name": e.name, "path": str(e),
                                      "disabled": True, "reason": reason})
            parent = None if base.parent == base else str(base.parent)
            return {"cwd": str(base), "parent": parent, "dirs": dirs, "files": files}
        except (OSError, PermissionError) as e:
            return {"cwd": str(base), "parent": str(base.parent), "dirs": [], "files": [],
                    "error": str(e)}

    # ----- import -----------------------------------------------------------

    @app.post("/import")
    def import_ebook():
        engine = request.form.get("engine", "chatterbox")

        def fail(message: str, code: int = 400):
            return render_template("new.html", start=str(Path.home()),
                                   error=message), code

        src_path = None
        # An upload lands in a staging file that is always removed below:
        # import_ebook() takes its own content-addressed copy, so keeping this
        # one would silently store every uploaded book twice, forever.
        staged: Path | None = None
        upload = request.files.get("file")
        if upload and upload.filename:
            staged = p.tmp / f"upload-{uuid.uuid4().hex}-{_safe_upload_name(upload.filename)}"
            try:
                upload.save(staged)
            except OSError as e:
                staged.unlink(missing_ok=True)
                return fail(_("Couldn't save the uploaded file — is the disk full?\n\n%(e)s", e=e))
            src_path = str(staged)
        elif request.form.get("path"):
            # Local path import is intentional for a single-user localhost tool.
            src_path = request.form["path"].strip()
        if not src_path:
            return fail(_("Choose an ebook file first."))
        try:
            job_id = worker.import_ebook(src_path, engine=engine)
        except FileNotFoundError:
            return fail(_("That file no longer exists:\n%(path)s", path=src_path))
        except ValueError as e:
            # Unsupported/again-actionable format problems — show the guidance inline.
            return fail(str(e))
        except OSError as e:
            # Out of space, unreadable source, permission denied on the data dir.
            return fail(_("Couldn't import that book:\n\n%(e)s", e=e))
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)
        runner.submit(job_id, "extract")
        return redirect(url_for("job_page", job_id=job_id))

    # ----- job actions ------------------------------------------------------

    @app.post("/job/<job_id>/settings")
    def save_settings(job_id):
        store = _job_or_404(job_id)
        voice = store.load_voice()
        f = request.form
        voice.engine = f.get("engine", voice.engine)

        voice_id = f.get("voice_id")
        if voice_id is not None:
            clip = VoiceLibrary().clip_path(voice_id)
            voice.reference_clip = str(clip) if clip else None
            voice.extra["voice_id"] = voice_id

        voice.exaggeration = _f(f, "exaggeration", voice.exaggeration)
        voice.cfg_weight = _f(f, "cfg_weight", voice.cfg_weight)
        voice.temperature = _f(f, "temperature", voice.temperature)
        voice.repetition_penalty = _f(f, "repetition_penalty", voice.repetition_penalty)
        voice.min_p = _f(f, "min_p", voice.min_p)
        voice.top_p = _f(f, "top_p", voice.top_p)
        voice.seed = int(_f(f, "seed", voice.seed))
        voice.extra["bitrate_kbps"] = int(_f(f, "bitrate", config.DEFAULT_BITRATE_KBPS))
        voice.extra["pron"] = _parse_pron(f.get("pron", ""))
        store.save_voice(voice)
        return {"ok": True}

    @app.post("/job/<job_id>/preview")
    def start_preview(job_id):
        _job_or_404(job_id)
        if runner.is_busy(job_id):
            return {"ok": False, "error": _("Already working on this book — stop it first.")}, 409
        # Bounded: zero would divide the progress by nothing after a full model
        # load, and an hour-long "preview" is a render by another name.
        seconds = min(300.0, max(5.0, _f(request.form, "seconds", 30)))
        runner.submit(
            job_id, "preview",
            seconds=seconds,
            chapter_id=request.form.get("chapter_id") or None,
            power_mode=power.normalize_mode(
                request.form.get("power_mode")
                or JobStore(job_id).load_state().power_mode
                or app_settings.default_power_mode()),
        )
        return {"ok": True}

    @app.post("/job/<job_id>/measure")
    def start_measure(job_id):
        """Narrate a little real audio to calibrate this book's estimates.

        Cheaper than a preview and produces nothing to listen to — its whole
        purpose is the two rates it measures. The audio it does render is
        content-addressed, so the full render reuses every second of it.
        """
        store = _job_or_404(job_id)
        if not store.load_chapters():
            return {"ok": False, "error": _("The book hasn't been read yet.")}, 409
        if runner.is_busy():
            return {"ok": False, "error": _("Something else is running.")}, 409
        runner.submit(job_id, "measure")
        return {"ok": True}

    @app.post("/job/<job_id>/render")
    def start_render(job_id):
        store = _job_or_404(job_id)
        if runner.is_busy(job_id):
            # A double-click, a stale tab, a retried request: without this the
            # second submit queues a second full pass — re-assembling and
            # re-packaging the book for nothing — and its state writes below
            # land under a render that is already saving its own copy.
            return {"ok": False, "error": _("Already working on this book — stop it first.")}, 409
        if not any(c.include for c in store.load_chapters()):
            return {"ok": False, "error": _("Select at least one section to render.")}, 400
        mode = request.form.get("output_mode") or worker.default_output_mode()
        raw = (request.form.get("output_dir") or "").strip() or None
        # Resolve + write-check the real destination now, so a bad folder (or an
        # unset library root) is rejected before the render starts — not hours
        # later at packaging time.
        try:
            _m4b, out_dir, _sidecar = worker.resolve_output_target(mode, raw, store.load_book())
        except worker.OutputDirError as e:
            return {"ok": False, "error": str(e)}, 400
        st = store.load_state()
        st.output_mode = mode
        st.output_dir = str(out_dir)
        store.save_state(st)
        # Remember the intensity with the job so a resume after a restart
        # doesn't silently go back to full speed on someone's laptop.
        chosen_power = power.normalize_mode(
            request.form.get("power_mode") or st.power_mode
            or app_settings.default_power_mode())
        st.power_mode = chosen_power
        store.save_state(st)
        runner.submit(job_id, "render", output_mode=mode, output_dir=raw,
                      power_mode=chosen_power)
        return {"ok": True}

    @app.post("/job/<job_id>/cancel")
    def cancel_job(job_id):
        _job_or_404(job_id)
        runner.cancel(job_id)
        return {"ok": True}

    @app.get("/job/<job_id>/status")
    def status(job_id):
        store = _job_or_404(job_id)
        # Self-heal a job whose worker died mid-flight (killed process, dead
        # thread) so the UI stops showing a stuck stage with a dead Stop button.
        st = _reconcile_stale(store)
        d = st.to_dict()
        d["busy"] = runner.is_busy(job_id)
        # The page polls this far more often than it reloads, so it is where it
        # learns that a re-measurement has made its figures current again.
        d["measured_here"] = _measured_here(store, st)
        return d

    @app.get("/job/<job_id>/chapters")
    def job_chapters(job_id):
        store = _job_or_404(job_id)
        book = store.load_book()
        chapters = store.load_chapters()
        default_ch = worker._pick_preview_chapter(chapters, None).chapter_id if chapters else ""
        root = app_settings.audiobooks_root()
        return {
            "title": book.title,
            "author": book.author,
            "output_filename": layout.output_stem(book) + ".m4b",
            # Library target reflects the freshly-extracted title/author, so the
            # job page can refresh the "Unknown Author/…" placeholder in place.
            "library_target": str(layout.library_m4b_path(Path(root), book)) if root else None,
            "default_chapter_id": default_ch,
            "chapters": [
                {"chapter_id": c.chapter_id, "sequence": c.sequence,
                 "title": c.title, "char_count": c.char_count,
                 "include": c.include}
                for c in chapters
            ],
        }

    @app.get("/job/<job_id>/chapter/<chapter_id>/text")
    def job_chapter_text(job_id, chapter_id):
        """A leading excerpt of one chapter's normalized text, so the user can
        eyeball whether a section is real book content or front/back matter
        without rendering an audio preview. Capped so a huge chapter can't send
        megabytes to the browser."""
        store = _job_or_404(job_id)
        limit = 4000
        for c in store.load_chapters():
            if c.chapter_id == chapter_id:
                text = c.text or ""
                excerpt = text[:limit]
                return {"chapter_id": c.chapter_id, "sequence": c.sequence,
                        "title": c.title, "char_count": c.char_count,
                        "include": c.include, "excerpt": excerpt,
                        "truncated": len(text) > limit}
        abort(404)

    @app.post("/job/<job_id>/chapters/include")
    def set_chapter_includes(job_id):
        """Persist which sections render. Body: {"includes": {chapter_id: bool}}.
        Only listed chapters are changed; the rest keep their current setting."""
        store = _job_or_404(job_id)
        if runner.is_busy(job_id):
            return {"ok": False, "error": _("Already working on this book — stop it first.")}, 409
        includes = (request.get_json(silent=True) or {}).get("includes", {})
        if not isinstance(includes, dict):
            abort(400, "includes must be an object of chapter_id -> bool")
        chapters = store.load_chapters()
        for ch in chapters:
            if ch.chapter_id in includes:
                ch.include = bool(includes[ch.chapter_id])
        store.save_chapters(chapters)
        return {"ok": True,
                "included": sum(1 for c in chapters if c.include),
                "total": len(chapters)}

    @app.get("/job/<job_id>/storage")
    def job_storage(job_id):
        store = _job_or_404(job_id)
        return {"disk_bytes": store.disk_bytes(),
                "intermediate_bytes": store.intermediate_bytes()}

    @app.post("/api/window")
    def save_window():
        """Remember where the app window is, so a relaunch reopens it there.

        Values come from the page's own ``screenX``/``outerWidth``. They are
        bounds-checked before being stored — not to keep the window on screen,
        which Chromium does itself when it places a window whose saved position
        is on a monitor that has gone, but so a garbage or hostile value can
        never become a command-line flag handed to the browser.
        """
        try:
            g = {k: int(float(request.form[k])) for k in ("x", "y", "width", "height")}
        except (KeyError, TypeError, ValueError, OverflowError):
            return {"ok": False, "error": "bad geometry"}, 400
        if not (320 <= g["width"] <= 20000 and 240 <= g["height"] <= 20000):
            return {"ok": False, "error": "implausible size"}, 400
        if not (-20000 < g["x"] < 20000 and -20000 < g["y"] < 20000):
            return {"ok": False, "error": "implausible position"}, 400

        s = app_settings.load_settings()
        if s.window_geometry != g:
            s.window_geometry = g
            app_settings.save_settings(s)
        return {"ok": True}

    @app.post("/reveal")
    def reveal_folder():
        """Open one of the app's own folders in the system file manager.

        Takes a *name*, not a path. This route ends in starting a program with a
        path argument, so accepting one from the request would turn a convenience
        into a way to point the file manager anywhere on the machine. The caller
        says which folder it means and this resolves it.
        """
        what = (request.form.get("what") or "").strip()
        job_id = (request.form.get("job_id") or "").strip()

        target: Path | None = None
        if job_id:
            store = _job_or_404(job_id)
            out = store.output_path()
            # The folder the audiobook was actually written to, when there is one.
            target = out.parent if out and out.exists() else None
            if target is None:
                state = store.load_state()
                target = Path(state.output_dir) if state.output_dir else None
        elif what == "data":
            target = paths().root
        elif what == "outputs":
            target = paths().outputs
        elif what == "library":
            root = app_settings.audiobooks_root()
            target = Path(root) if root else None

        if target is None or not target.is_dir():
            return {"ok": False, "error": _("That folder doesn't exist yet.")}, 404
        if not tools.reveal(target):
            return {"ok": False, "error": _("Couldn't open a file manager on this system.")}, 501
        return {"ok": True, "path": str(target)}

    @app.get("/api/space")
    def api_space():
        """Free space for a render — on *both* filesystems it can touch.

        The bulky temporary WAVs go to the data root, but the finished .m4b goes
        wherever the user chose, and for a Plex library that is very often a
        different volume: a NAS mount, a USB drive. Reporting only the data root
        meant a dialog that cheerfully showed 400 GB free while the destination
        drive had 200 MB. ``?path=`` asks about a specific destination; without
        it only the working volume is reported.
        """
        work = _disk_usage(paths().root)
        out = {
            # Kept at the top level: this is the working-files figure, which is
            # what the existing plan dialog reads.
            "free_bytes": work["free_bytes"],
            "total_bytes": work["total_bytes"],
            "work": work,
        }
        raw = (request.args.get("path") or "").strip()
        if raw:
            dest = _disk_usage(_nearest_existing(Path(raw).expanduser()))
            dest["same_volume"] = _same_volume(paths().root, Path(raw).expanduser())
            out["output"] = dest
        return out

    @app.get("/job/<job_id>/cover.jpg")
    def job_cover(job_id):
        """The book's own cover, as extracted from the ebook.

        A library of books should look like one, and this is the only thing that
        makes a shelf scannable. Absent for plenty of ebooks, so callers must
        cope with a 404 rather than assume an image.
        """
        store = _job_or_404(job_id)
        raw = store.load_book().cover_path
        if not raw:
            abort(404)
        f = Path(raw)
        if not f.is_file():
            abort(404)
        mime = "image/png" if f.suffix.lower() == ".png" else "image/jpeg"
        return send_file(f, mimetype=mime)

    @app.get("/job/<job_id>/preview.wav")
    def preview_audio(job_id):
        f = paths().outputs / f"{job_id}_preview.wav"
        if not f.exists():
            abort(404)
        return send_file(f, mimetype="audio/wav")

    @app.get("/job/<job_id>/download")
    def download(job_id):
        state = JobStore(job_id).load_state()
        if not state.output_path or not Path(state.output_path).exists():
            abort(404)
        return send_file(state.output_path, as_attachment=True)

    @app.post("/job/<job_id>/cleanup")
    def cleanup_job(job_id):
        _job_or_404(job_id)
        if runner.is_busy(job_id):
            abort(409, "job is busy")
        freed = JobStore(job_id).cleanup_intermediates()
        _forget_survey()
        return {"ok": True, "freed_bytes": freed}

    @app.post("/job/<job_id>/delete")
    def delete_job(job_id):
        _job_or_404(job_id)
        if runner.is_busy(job_id):
            abort(409, "job is busy — stop it first")
        JobStore(job_id).delete()
        _forget_survey()
        return redirect(url_for("index"))

    # ----- voices -----------------------------------------------------------

    @app.get("/api/voices")
    def api_voices():
        return jsonify([v.to_dict() for v in VoiceLibrary().list()])

    @app.post("/voices/add")
    def voices_add():
        lib = VoiceLibrary()
        name = request.form.get("name", "").strip()
        if not name:
            return _voices_page(_("Give the voice a name."), 400)
        upload = request.files.get("file")
        path = request.form.get("path", "").strip()
        try:
            if upload and upload.filename:
                if Path(upload.filename).suffix.lower() not in AUDIO_EXTS:
                    return _voices_page(_("That isn't an audio format this can read."), 400)
                lib.add(name, file_storage=upload, orig_filename=upload.filename)
            elif path:
                lib.add(name, src_path=path)
            else:
                return _voices_page(_("Choose a clip first."), 400)
        except (ValueError, OSError) as e:
            # A clip ffmpeg couldn't decode, a path that has gone, a folder macOS
            # wouldn't let us read: all shown on the page, with a way back.
            return _voices_page(_("Couldn't add that voice: %(e)s", e=e), 400)
        return redirect(url_for("voices_page"))

    @app.post("/voices/<voice_id>/default")
    def voices_set_default(voice_id):
        """Which narrator a newly imported book starts with.

        Only new books: a book's voice is part of its own settings, and changing
        it would re-render the book.
        """
        if not VoiceLibrary().get(voice_id):
            abort(404)
        s = app_settings.load_settings()
        s.default_voice_id = voice_id
        app_settings.save_settings(s)
        return redirect(url_for("voices_page"))

    @app.post("/voices/<voice_id>/delete")
    def voices_delete(voice_id):
        VoiceLibrary().delete(voice_id)
        return redirect(url_for("voices_page"))

    @app.post("/voices/<voice_id>/test")
    def voices_test(voice_id):
        if not VoiceLibrary().get(voice_id):
            abort(404)
        # The page waits for the sample by polling for the file, which only
        # means "this audition is done" if the previous audition's file is not
        # still sitting there. Auditions queue behind a render, so waiting on
        # the worker being idle instead — as this used to — meant four minutes
        # of spinner and then playing whatever was there before.
        try:
            (paths().voices / f"_sample_{voice_id}.wav").unlink(missing_ok=True)
        except OSError:
            pass
        runner.submit(f"voicetest-{voice_id}", "voice_test", voice_id=voice_id)
        return {"ok": True}

    @app.get("/voices/<voice_id>/sample.wav")
    def voices_sample(voice_id):
        f = paths().voices / f"_sample_{voice_id}.wav"
        if not f.exists():
            abort(404)
        return send_file(f, mimetype="audio/wav")

    # ----- global status ----------------------------------------------------

    @app.get("/api/status")
    def api_status():
        out = {
            # An open port proves something is listening, not that it is us.
            # A second launch identifies the instance recorded in runtime.json
            # by this marker before handing the user its window.
            "app": runtime.APP_ID,
            "busy": runner.is_busy(),
            "current": runner.current,
            # "render" | "preview" | "extract" | "voice_test" | None — the Quit
            # control words its warning differently for a six-hour render than
            # for a ten-second voice sample.
            "kind": runner.current_kind(),
            "job": None,
        }
        # Enough about the running job for the sidebar dock to show it on every
        # page. A render lasts hours; making the user navigate back to the job
        # page to find out how it is going was the single worst thing about the
        # old layout.
        jid = _busy_job_id()
        if jid and JobStore(jid).exists():  # a voice audition is busy, but is no book
            try:
                store = JobStore(jid)
                st = store.load_state()
                out["job"] = {
                    "job_id": jid,
                    "title": store.load_book().title,
                    "stage": st.stage,
                    "stage_label": stage_label(st.stage),
                    "rendered_segments": st.rendered_segments,
                    "total_segments": st.total_segments,
                    "preview_progress": st.preview_progress,
                    "render_started_at": st.render_started_at,
                }
            except Exception:  # noqa: BLE001 - the dock is not worth a 500
                out["job"] = None
        return out

    @app.post("/quit")
    def quit_app():
        """Shut the whole application down.

        Refuses while work is in flight unless asked twice. The window is a
        browser window and the server outlives it, so this is the only
        deliberate way out other than the tray — and it is reachable by a stray
        click, which a multi-hour render should survive.
        """
        shutdown = current_app.config.get("EBAB_SHUTDOWN")
        if shutdown is None:
            # Running under the test client or a dev server, where there is no
            # server object to stop.
            return {"ok": False, "error": "not running as an application"}, 501

        kind = runner.current_kind()
        force = request.args.get("force") == "1"
        if runner.is_busy() and not force:
            return {"ok": False, "busy": True, "kind": kind}, 409

        if runner.current:
            runner.cancel(runner.current.split(":", 1)[0])
        # Answer before stopping, or the browser sees a dropped connection and
        # shows its own error page in what is supposed to be our app window.
        threading.Timer(0.25, shutdown).start()
        return {"ok": True}

    # Every page asks for this on load, and answering means running
    # `ffmpeg -version` and `ebook-convert --version` as subprocesses. Process
    # spawning isn't free — noticeably so on Windows — and the answer changes
    # only when the user installs something, so cache it briefly. The TTL (rather
    # than caching forever) means installing a missing tool clears the banner
    # within a minute instead of needing a restart.
    # One entry per interface language: the answer carries sentences.
    _prereq_cache: dict = {}
    _PREREQ_TTL = 60.0

    @app.get("/api/prereqs")
    def api_prereqs():
        """What's installed and what isn't.

        Surfaced as a banner so a missing prerequisite is visible the moment the
        UI opens, rather than as a failed import ten minutes later.
        """
        import time

        now = time.monotonic()
        entry = _prereq_cache.setdefault(g.lang, {"at": 0.0, "value": None})
        if entry["value"] is None or now - entry["at"] > _PREREQ_TTL:
            tools.reset_cache()  # notice a tool installed since the last look
            results = checks.run_all()
            blocking = checks.blocking_problems(results)
            entry["value"] = {
                "checks": [r.to_dict() for r in results],
                "blocking": [r.to_dict() for r in blocking],
                "ok": not blocking,
            }
            entry["at"] = now
        return entry["value"]

    return app
