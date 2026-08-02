"""Thin Flask UI over the pipeline functions. Localhost only.

Nothing slow happens in a request handler: extraction, rendering, previews, and
voice-sample audition are all handed to the single background ``runner``; the
browser polls ``/job/<id>/status`` and ``/api/status``.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

from .. import checks, config, power, settings as app_settings, tools, worker
from ..audio import estimate
from ..config import VoiceSettings, paths
from ..jobs.models import Stage
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
    if n is None:
        return "—"
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.0f} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} TB"


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
    for _ in range(64):  # bounded: a symlink cycle must not hang a request
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
    p = paths().ensure()

    # A prior process may have been killed mid-render, leaving jobs frozen in a
    # transient stage with no worker behind them. Reconcile at boot so the
    # library and job pages don't show a dead "Preparing"/Stop.
    for _jid in JobStore.list_ids():
        try:
            _reconcile_stale(JobStore(_jid))
        except Exception:
            continue

    @app.context_processor
    def inject_globals():
        s = app_settings.load_settings()
        return {
            "home_dir": str(Path.home()),
            "audiobooks_root": s.audiobooks_root,
            # First-run nudge: no library folder chosen and not yet dismissed.
            "setup_needed": not s.audiobooks_root and not s.setup_dismissed,
            "power_mode": s.power_mode,
            "power_modes": [
                {"id": m, "label": power.MODE_LABELS[m],
                 "description": power.MODE_DESCRIPTIONS[m]}
                for m in power.MODES
            ],
            "check_for_updates": s.check_for_updates,
            "app_version": _app_version(),
        }

    # ----- pages ------------------------------------------------------------

    @app.get("/")
    def index():
        jobs = []
        for jid in JobStore.list_ids():
            store = JobStore(jid)
            try:
                jobs.append({
                    "id": jid,
                    "book": store.load_book(),
                    "state": _reconcile_stale(store),
                    "bytes": store.disk_bytes(),
                })
            except Exception:
                continue
        jobs.sort(key=lambda j: (j["state"].created_at or ""), reverse=True)  # newest first
        total = sum(j["bytes"] for j in jobs)
        return render_template("library.html", jobs=jobs, total_bytes=total)

    @app.get("/new")
    def new_page():
        return render_template("new.html", start=str(Path.home()))

    @app.get("/voices")
    def voices_page():
        return render_template(
            "voices.html",
            voices=[v.to_dict() for v in VoiceLibrary().list()],
            start=str(Path.home()),
        )

    @app.get("/settings")
    def settings_page():
        return render_template("settings.html")

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
        s.setup_dismissed = True  # user has engaged with setup either way
        app_settings.save_settings(s)
        return {"ok": True, "audiobooks_root": s.audiobooks_root,
                "power_mode": s.power_mode,
                "check_for_updates": s.check_for_updates}

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
                {"key": key, "label": label,
                 "files": est.by_category.get(key, (0, 0))[0],
                 "human": backup_mod.human_bytes(est.by_category.get(key, (0, 0))[1]),
                 "included": getattr(selection, key, False)}
                for key, label in backup_mod.CATEGORY_LABELS.items()
                if est.by_category.get(key, (0, 0))[0]
            ],
        }

    @app.post("/backup")
    def backup_download():
        """Build the archive in the data root's tmp/ and stream it back."""
        from flask import after_this_request

        from .. import backup as backup_mod

        profile = request.form.get("profile", backup_mod.DEFAULT_PROFILE)
        try:
            selection = backup_mod.resolve_selection(profile)
        except ValueError as e:
            return {"ok": False, "error": str(e)}, 400

        stamp = __import__("time").strftime("%Y%m%d-%H%M%S")
        name = f"ebook-audiobook-{profile}-{stamp}.zip"
        tmp_path = paths().ensure().tmp / name
        try:
            backup_mod.create(tmp_path, selection)
        except backup_mod.BackupError as e:
            return {"ok": False, "error": str(e)}, 400

        @after_this_request
        def _cleanup(response):
            # The archive is a copy; keeping it would double the disk cost of
            # every backup taken through the browser.
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            return response

        return send_file(tmp_path, as_attachment=True, download_name=name)

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
        est = estimate.estimate(total_chars, bitrate, state.chars_per_render_second) if total_chars else None
        default_ch = worker._pick_preview_chapter(chapters, None).chapter_id if chapters else ""
        book = store.load_book()
        root = app_settings.audiobooks_root()
        return render_template(
            "job.html",
            job_id=job_id,
            book=book,
            chapters=chapters,
            voice=voice,
            voices=[v.to_dict() for v in VoiceLibrary().list()],
            selected_voice_id=voice.extra.get("voice_id", "default"),
            default_chapter_id=default_ch,
            bitrate=bitrate,
            pron_text=pron_text,
            state=state,
            est=est,
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
                if not e.is_file():
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
                return fail(f"Couldn't save the uploaded file — is the disk full?\n\n{e}")
            src_path = str(staged)
        elif request.form.get("path"):
            # Local path import is intentional for a single-user localhost tool.
            src_path = request.form["path"].strip()
        if not src_path:
            return fail("Choose an ebook file first.")
        try:
            job_id = worker.import_ebook(src_path, engine=engine)
        except FileNotFoundError:
            return fail(f"That file no longer exists:\n{src_path}")
        except ValueError as e:
            # Unsupported/again-actionable format problems — show the guidance inline.
            return fail(str(e))
        except OSError as e:
            # Out of space, unreadable source, permission denied on the data dir.
            return fail(f"Couldn't import that book:\n\n{e}")
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
        runner.submit(
            job_id, "preview",
            seconds=_f(request.form, "seconds", 30),
            chapter_id=request.form.get("chapter_id") or None,
            power_mode=power.normalize_mode(
                request.form.get("power_mode")
                or JobStore(job_id).load_state().power_mode
                or app_settings.default_power_mode()),
        )
        return {"ok": True}

    @app.post("/job/<job_id>/render")
    def start_render(job_id):
        store = _job_or_404(job_id)
        if not any(c.include for c in store.load_chapters()):
            return {"ok": False, "error": "Select at least one section to render."}, 400
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
        d = _reconcile_stale(store).to_dict()
        d["busy"] = runner.is_busy(job_id)
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
            return {"ok": False, "error": "job is busy — stop it first"}, 409
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
        return {"ok": True, "freed_bytes": freed}

    @app.post("/job/<job_id>/delete")
    def delete_job(job_id):
        _job_or_404(job_id)
        if runner.is_busy(job_id):
            abort(409, "job is busy — stop it first")
        JobStore(job_id).delete()
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
            abort(400, "name required")
        upload = request.files.get("file")
        path = request.form.get("path", "").strip()
        try:
            if upload and upload.filename:
                if Path(upload.filename).suffix.lower() not in AUDIO_EXTS:
                    abort(400, "unsupported audio format")
                lib.add(name, file_storage=upload, orig_filename=upload.filename)
            elif path:
                lib.add(name, src_path=path)
            else:
                abort(400, "provide an audio file or path")
        except (ValueError, OSError) as e:
            abort(400, str(e))
        return redirect(url_for("voices_page"))

    @app.post("/voices/<voice_id>/delete")
    def voices_delete(voice_id):
        VoiceLibrary().delete(voice_id)
        return redirect(url_for("voices_page"))

    @app.post("/voices/<voice_id>/test")
    def voices_test(voice_id):
        if not VoiceLibrary().get(voice_id):
            abort(404)
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
        return {"busy": runner.is_busy(), "current": runner.current}

    # Every page asks for this on load, and answering means running
    # `ffmpeg -version` and `ebook-convert --version` as subprocesses. Process
    # spawning isn't free — noticeably so on Windows — and the answer changes
    # only when the user installs something, so cache it briefly. The TTL (rather
    # than caching forever) means installing a missing tool clears the banner
    # within a minute instead of needing a restart.
    _prereq_cache: dict = {"at": 0.0, "value": None}
    _PREREQ_TTL = 60.0

    @app.get("/api/prereqs")
    def api_prereqs():
        """What's installed and what isn't.

        Surfaced as a banner so a missing prerequisite is visible the moment the
        UI opens, rather than as a failed import ten minutes later.
        """
        import time

        now = time.monotonic()
        if _prereq_cache["value"] is None or now - _prereq_cache["at"] > _PREREQ_TTL:
            tools.reset_cache()  # notice a tool installed since the last look
            results = checks.run_all()
            blocking = checks.blocking_problems(results)
            _prereq_cache["value"] = {
                "checks": [r.to_dict() for r in results],
                "blocking": [r.to_dict() for r in blocking],
                "ok": not blocking,
            }
            _prereq_cache["at"] = now
        return _prereq_cache["value"]

    return app
