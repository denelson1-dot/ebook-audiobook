"""Command-line entry point — the headless path through the whole pipeline.

The web UI is a thin wrapper over these same functions, and is launched from
here too (``ebook-audiobook web``) so there is exactly one command to remember.

Examples:
    ebook-audiobook web                             # start the UI (default)
    ebook-audiobook check
    ebook-audiobook convert book.epub --engine fake
    ebook-audiobook convert book.epub --voice-ref voices/narrator.wav
    ebook-audiobook preview <job_id> --seconds 30
"""

from __future__ import annotations

import argparse
import os
import sys

from . import checks, config, power
from .audio import estimate
from .config import VoiceSettings
from .jobs.models import JobState
from .jobs.store import JobStore
from . import worker


def _use_utf8_console() -> None:
    """Make the output streams safe to print to, whatever launched us.

    Two Windows-specific hazards, both of which turn an innocent ``print`` into a
    crash:

    * Windows consoles still default to a legacy code page (cp1252 in the US and
      much of Europe), and Python encodes stdout with it. A single curly quote or
      accented author name — both everywhere in ebook metadata — raises
      ``UnicodeEncodeError``. Re-encoding as UTF-8 with replacement means the
      worst case is a mangled character rather than a failed render.
    * Launched through ``pythonw.exe`` (which is how the desktop and Start Menu
      shortcuts run, so no console window appears), ``sys.stdout`` and
      ``sys.stderr`` are ``None``. Anything that prints then dies with
      ``AttributeError: 'NoneType' object has no attribute 'write'``. Pointing
      them at the null device keeps every print harmless.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass  # redirected to something that can't be reconfigured; fine


def _progress(state: JobState) -> None:
    if state.total_segments:
        pct = 100 * state.rendered_segments / state.total_segments
        rate = f" | {state.chars_per_render_second} ch/s" if state.chars_per_render_second else ""
        sys.stdout.write(
            f"\r  rendering {state.rendered_segments}/{state.total_segments} ({pct:4.1f}%){rate}   "
        )
        sys.stdout.flush()


def _apply_voice(job_id: str, args) -> None:
    store = JobStore(job_id)
    voice = store.load_voice()
    if getattr(args, "engine", None):
        voice.engine = args.engine
    if getattr(args, "voice_ref", None):
        voice.reference_clip = str(args.voice_ref)
    if getattr(args, "bitrate", None):
        voice.extra["bitrate_kbps"] = int(args.bitrate)
    store.save_voice(voice)


def cmd_check(args) -> int:
    results = checks.run_all(engine=args.engine)
    print(checks.format_results(results))
    from . import settings as app_settings

    print(f"\nrender intensity: {power.describe(app_settings.default_power_mode())}")
    problems = checks.blocking_problems(results)
    if problems:
        print(f"\n{len(problems)} problem(s) must be fixed before converting a book.")
        return 1
    print("\nEverything needed is installed.")
    return 0


def cmd_web(args) -> int:
    """Start the local web UI (the way almost everyone uses this)."""
    from .web.server import serve

    serve(host=args.host, port=args.port, open_browser=not args.no_browser)
    return 0


def cmd_paths(args) -> int:
    """Show where the app keeps things — the first question every support
    conversation starts with."""
    from .config import data_root
    from . import settings as app_settings

    p = config.paths()
    print(f"data folder:      {data_root()}")
    print(f"  imported books: {p.imports}")
    print(f"  job workspace:  {p.jobs}")
    print(f"  voice clips:    {p.voices}")
    print(f"  outputs:        {p.outputs}")
    print(f"  settings file:  {data_root() / 'settings.json'}")
    root = app_settings.audiobooks_root()
    print(f"audiobooks library: {root or '(not set — choose one in Settings)'}")
    print("\nOverride the data folder by setting EBAB_DATA_ROOT.")
    return 0


def cmd_convert(args) -> int:
    job_id = worker.import_ebook(args.source, engine=args.engine)
    print(f"job: {job_id}")
    _apply_voice(job_id, args)

    print("extracting...")
    chapters = worker.extract_job(job_id)
    total_chars = sum(c.char_count for c in chapters)
    book = JobStore(job_id).load_book()
    print(f'  "{book.title}" by {book.author} — {len(chapters)} chapters')

    est = estimate.estimate(total_chars, _bitrate(job_id))
    print(f"  estimate: {est.human()}")

    if args.preview_seconds and args.engine != "fake":
        print(f"preview (~{args.preview_seconds}s)...")
        st = worker.render_job(job_id, preview_max_seconds=args.preview_seconds,
                               power_mode=args.power, progress=_progress)
        print()
        # preview_output, not output_path: a preview must never claim to be the
        # finished audiobook, so it's tracked separately. Reading output_path
        # here printed None, or worse, a previous full render's .m4b.
        print(f"  preview: {st.preview_output}")
        if not args.yes:
            resp = input("proceed with full render? [y/N] ").strip().lower()
            if resp != "y":
                print("stopped after preview.")
                return 0

    print("rendering full audiobook...")
    try:
        state = worker.render_job(
            job_id, output_dir=args.output_dir,
            output_mode=worker.MODE_FOLDER if args.output_dir else None,
            power_mode=args.power, progress=_progress,
        )
    except worker.OutputDirError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print()
    print(f"done: {state.output_path}")
    return 0


def cmd_extract(args) -> int:
    job_id = args.job_or_source
    if not JobStore(job_id).exists():
        job_id = worker.import_ebook(args.job_or_source, engine=args.engine)
        print(f"job: {job_id}")
    chapters = worker.extract_job(job_id)
    book = JobStore(job_id).load_book()
    print(f'"{book.title}" by {book.author}')
    for c in chapters:
        print(f"  [{c.sequence:3d}] {c.title}  ({c.char_count:,} chars)")
    est = estimate.estimate(sum(c.char_count for c in chapters), _bitrate(job_id))
    print(f"estimate: {est.human()}")
    return 0


def cmd_preview(args) -> int:
    _apply_voice(args.job_id, args)
    state = worker.render_job(args.job_id, preview_max_seconds=args.seconds,
                              power_mode=args.power, progress=_progress)
    print()
    print(f"preview: {state.preview_output}")
    return 0


def cmd_render(args) -> int:
    _apply_voice(args.job_id, args)
    try:
        state = worker.render_job(
            args.job_id, output_dir=args.output_dir,
            output_mode=worker.MODE_FOLDER if args.output_dir else None,
            power_mode=args.power, progress=_progress,
        )
    except worker.OutputDirError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print()
    print(f"done: {state.output_path}")
    return 0


def cmd_list(args) -> int:
    ids = JobStore.list_ids()
    if not ids:
        print("no jobs")
        return 0
    for jid in ids:
        store = JobStore(jid)
        try:
            book = store.load_book()
            state = store.load_state()
            print(f"{jid}  {state.stage:12s}  {book.title} — {book.author}")
        except Exception:
            print(f"{jid}  (unreadable)")
    return 0


def _bitrate(job_id: str) -> int:
    return int(JobStore(job_id).load_voice().extra.get("bitrate_kbps", config.DEFAULT_BITRATE_KBPS))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ebook-audiobook",
        description="Turn a DRM-free ebook you own into a narrated .m4b audiobook, "
                    "entirely on this machine.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run with no arguments to open the web interface.",
    )
    sub = p.add_subparsers(dest="command")

    c = sub.add_parser("web", help="open the web interface (default)")
    c.add_argument("--host", default=None, help="bind address (default 127.0.0.1)")
    c.add_argument("--port", type=int, default=None, help="port (default: first free from 5005)")
    c.add_argument("--no-browser", action="store_true", help="don't open a browser window")
    c.set_defaults(func=cmd_web)

    c = sub.add_parser("paths", help="show where books, jobs, and settings are stored")
    c.set_defaults(func=cmd_paths)

    def power_flag(sp):
        sp.add_argument(
            "--power", choices=list(power.MODES), default=None,
            help="how hard to push this machine: full (default), balanced, "
                 "or quiet (background — cooler and slower, good on a laptop)")

    def voice_flags(sp):
        sp.add_argument("--engine", choices=["chatterbox", "fake"], default="chatterbox")
        sp.add_argument("--voice-ref", dest="voice_ref", help="local rights-cleared reference clip")
        sp.add_argument("--bitrate", type=int, help=f"AAC kbps (default {config.DEFAULT_BITRATE_KBPS})")

    c = sub.add_parser("check", help="run startup checks")
    c.add_argument("--engine", default="chatterbox")
    c.set_defaults(func=cmd_check)

    c = sub.add_parser("convert", help="import -> extract -> (preview) -> full render")
    c.add_argument("source", help="path to .epub/.mobi/.azw3/.pdf")
    voice_flags(c)
    c.add_argument("--output-dir", dest="output_dir", help="folder for the final .m4b (default: local-data/outputs)")
    c.add_argument("--preview-seconds", type=float, default=0, help="render a preview and confirm first")
    c.add_argument("-y", "--yes", action="store_true", help="skip preview confirmation")
    power_flag(c)
    c.set_defaults(func=cmd_convert)

    c = sub.add_parser("extract", help="import (if needed) and extract chapters")
    c.add_argument("job_or_source")
    voice_flags(c)
    c.set_defaults(func=cmd_extract)

    c = sub.add_parser("preview", help="render a short preview for a job")
    c.add_argument("job_id")
    c.add_argument("--seconds", type=float, default=30)
    voice_flags(c)
    power_flag(c)
    c.set_defaults(func=cmd_preview)

    c = sub.add_parser("render", help="full render for a job")
    c.add_argument("job_id")
    voice_flags(c)
    c.add_argument("--output-dir", dest="output_dir", help="folder for the final .m4b (default: local-data/outputs)")
    power_flag(c)
    c.set_defaults(func=cmd_render)

    c = sub.add_parser("list", help="list jobs")
    c.set_defaults(func=cmd_list)
    return p


def main(argv: list[str] | None = None) -> int:
    _use_utf8_console()
    parser = build_parser()
    # Bare `ebook-audiobook` (a desktop shortcut, or someone who just installed
    # it) opens the UI rather than printing usage and exiting non-zero.
    args = parser.parse_args(argv if argv is not None else (sys.argv[1:] or ["web"]))
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted (progress saved; rerun to resume)")
        return 130


def main_gui() -> int:
    """Entry point for desktop/Start-menu shortcuts.

    Registered as a ``gui_script`` so Windows launches it with ``pythonw.exe``
    and no console window appears behind the browser. There is no console to
    print to in that case, so failures have to be surfaced in a dialog rather
    than on a stream nobody will ever see.
    """
    try:
        return main(["web"])
    except Exception as e:  # noqa: BLE001 - last resort: tell the user *something*
        _report_gui_error(e)
        return 1


def _report_gui_error(exc: BaseException) -> None:
    message = f"ebook-audiobook could not start:\n\n{exc}"
    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror("ebook-audiobook", message)
        root.destroy()
    except Exception:  # noqa: BLE001 - no display / no tkinter
        print(message, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
