"""Command-line entry point — the headless path through the whole pipeline.

The web UI is a thin wrapper over these same functions.

Examples:
    python -m app.cli check
    python -m app.cli convert book.epub --engine fake
    python -m app.cli convert book.epub --voice-ref voices/narrator.wav
    python -m app.cli preview <job_id> --seconds 30
"""

from __future__ import annotations

import argparse
import sys

from . import checks, config
from .audio import estimate
from .config import VoiceSettings
from .jobs.models import JobState
from .jobs.store import JobStore
from . import worker


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
    # Only the fake engine is required to be functional for the pipeline itself.
    essential = [r for r in results if not r.name.startswith("tts engine")]
    return 0 if all(r.ok for r in essential) else 1


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
        worker.render_job(job_id, preview_max_seconds=args.preview_seconds, progress=_progress)
        print()
        st = JobStore(job_id).load_state()
        print(f"  preview: {st.output_path}")
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
            progress=_progress,
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
    state = worker.render_job(args.job_id, preview_max_seconds=args.seconds, progress=_progress)
    print()
    print(f"preview: {state.output_path}")
    return 0


def cmd_render(args) -> int:
    _apply_voice(args.job_id, args)
    try:
        state = worker.render_job(
            args.job_id, output_dir=args.output_dir,
            output_mode=worker.MODE_FOLDER if args.output_dir else None,
            progress=_progress,
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
    p = argparse.ArgumentParser(prog="ebook-audiobook", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

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
    c.set_defaults(func=cmd_convert)

    c = sub.add_parser("extract", help="import (if needed) and extract chapters")
    c.add_argument("job_or_source")
    voice_flags(c)
    c.set_defaults(func=cmd_extract)

    c = sub.add_parser("preview", help="render a short preview for a job")
    c.add_argument("job_id")
    c.add_argument("--seconds", type=float, default=30)
    voice_flags(c)
    c.set_defaults(func=cmd_preview)

    c = sub.add_parser("render", help="full render for a job")
    c.add_argument("job_id")
    voice_flags(c)
    c.add_argument("--output-dir", dest="output_dir", help="folder for the final .m4b (default: local-data/outputs)")
    c.set_defaults(func=cmd_render)

    c = sub.add_parser("list", help="list jobs")
    c.set_defaults(func=cmd_list)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted (progress saved; rerun to resume)")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
