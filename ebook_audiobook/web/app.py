"""Thin Flask UI over the pipeline functions. Localhost only.

Nothing slow happens in a request handler: extraction, rendering, previews, and
voice-sample audition are all handed to the single background ``runner``; the
browser polls ``/job/<id>/status`` and ``/api/status``.
"""

from __future__ import annotations

import re
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

from .. import checks, config, settings as app_settings, worker
from ..audio import estimate
from ..config import VoiceSettings, paths
from ..jobs.models import Stage
from ..jobs.store import JobStore
from ..pipeline import extract as extract_mod, layout
from ..voices import AUDIO_EXTS, VoiceLibrary
from .runner import runner


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
        raw = (request.form.get("audiobooks_root") or "").strip()
        s = app_settings.load_settings()
        if raw:
            try:
                out = worker.resolve_output_dir(raw)  # must be a writable folder
            except worker.OutputDirError as e:
                return {"ok": False, "error": str(e)}, 400
            s.audiobooks_root = str(out)
        else:
            s.audiobooks_root = None  # cleared
        s.setup_dismissed = True  # user has engaged with setup either way
        app_settings.save_settings(s)
        return {"ok": True, "audiobooks_root": s.audiobooks_root}

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
        src_path = None
        upload = request.files.get("file")
        if upload and upload.filename:
            dest = p.imports / _safe_upload_name(upload.filename)
            upload.save(dest)
            src_path = str(dest)
        elif request.form.get("path"):
            # Local path import is intentional for a single-user localhost tool.
            src_path = request.form["path"].strip()
        if not src_path:
            return render_template("new.html", start=str(Path.home()),
                                   error="Choose an ebook file first."), 400
        try:
            job_id = worker.import_ebook(src_path, engine=engine)
        except FileNotFoundError:
            return render_template("new.html", start=str(Path.home()),
                                   error=f"That file no longer exists:\n{src_path}"), 400
        except ValueError as e:
            # Unsupported/again-actionable format problems — show the guidance inline.
            return render_template("new.html", start=str(Path.home()), error=str(e)), 400
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
        runner.submit(job_id, "render", output_mode=mode, output_dir=raw)
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
        """Free/total bytes on the filesystem that holds the working data. All
        jobs share one data root, so this is a global figure the render-plan
        dialog uses to warn before a large render fills the disk."""
        import shutil as _shutil

        root = paths().root
        try:
            usage = _shutil.disk_usage(str(root))
            return {"free_bytes": usage.free, "total_bytes": usage.total}
        except OSError:
            return {"free_bytes": None, "total_bytes": None}

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

    @app.get("/api/prereqs")
    def api_prereqs():
        """What's installed and what isn't.

        Surfaced as a banner so a missing prerequisite is visible the moment the
        UI opens, rather than as a failed import ten minutes later.
        """
        results = checks.run_all()
        return {
            "checks": [r.to_dict() for r in results],
            "blocking": [r.to_dict() for r in checks.blocking_problems(results)],
            "ok": not checks.blocking_problems(results),
        }

    return app
