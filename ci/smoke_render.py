"""End-to-end smoke test of an *installed* copy: EPUB in, valid .m4b out.

Run against the venv the wheel was installed into, on every OS in CI. Unlike the
unit tests this exercises the real installed artifact — entry points, packaged
data files, the bundled ffmpeg, and the per-user data directory — so it catches
the class of bug that only appears after packaging.

Deliberately uses the ``fake`` TTS engine: no GPU, no model download, no
network, but every other stage of the pipeline is the real one.
"""

from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from pathlib import Path

FAILURES: list[str] = []


def check(condition: bool, description: str) -> None:
    status = "ok  " if condition else "FAIL"
    print(f"  [{status}] {description}")
    if not condition:
        FAILURES.append(description)


def write_epub(path: Path) -> None:
    """A minimal, valid EPUB2. Text written for this test; nothing copyrighted."""
    def doc(title: str, paragraphs: list[str]) -> bytes:
        body = "".join(f"<p>{p}</p>" for p in paragraphs)
        return (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml">'
            f"<head><title>{title}</title></head>"
            f"<body><h1>{title}</h1>{body}</body></html>"
        ).encode("utf-8")

    opf = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>The Smoke Test</dc:title>
    <dc:creator>CI Runner</dc:creator>
    <dc:date>2024-06-01</dc:date>
    <dc:identifier id="id">urn:uuid:smoke</dc:identifier>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/>
    <item id="c2" href="c2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx"><itemref idref="c1"/><itemref idref="c2"/></spine>
</package>"""

    ncx = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>
  <navPoint id="n1"><navLabel><text>The Beginning</text></navLabel><content src="c1.xhtml"/></navPoint>
  <navPoint id="n2"><navLabel><text>The Ending</text></navLabel><content src="c2.xhtml"/></navPoint>
</navMap></ncx>"""

    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/toc.ncx", ncx)
        z.writestr("OEBPS/c1.xhtml",
                   doc("The Beginning", ["It was the year 1999 and the road ran on."] * 8))
        z.writestr("OEBPS/c2.xhtml",
                   doc("The Ending", ["She walked 3 miles home and paid $5 for tea."] * 8))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ebab-smoke-"))
    # Isolate: never touch whatever else is on the machine.
    import os

    os.environ["EBAB_DATA_ROOT"] = str(tmp / "data")

    from ebook_audiobook import tools, worker
    from ebook_audiobook.audio import validate
    from ebook_audiobook.config import VoiceSettings
    from ebook_audiobook.jobs.models import Book, Chapter
    from ebook_audiobook.jobs.store import JobStore

    tools.reset_cache()
    print(f"platform: {sys.platform}  python: {sys.version.split()[0]}")
    print(f"ffmpeg:   {tools.ffmpeg_path()} "
          f"({'bundled' if tools.ffmpeg_is_bundled() else 'system'})")
    print(f"ffprobe:  {tools.ffprobe_path() or 'not installed (fine)'}")
    print(f"calibre:  {tools.ebook_convert_path() or 'not installed'}")
    print()

    print("ffmpeg availability")
    check(tools.ffmpeg_path() is not None,
          "an ffmpeg is available (bundled wheel counts)")
    if FAILURES:
        return report()

    # Calibre isn't installed on CI runners, so build the job directly rather
    # than going through ebook-convert. Extraction from a real EPUB is covered
    # by the Calibre-marked unit tests.
    epub = tmp / "smoke.epub"
    write_epub(epub)
    check(epub.stat().st_size > 0, "test EPUB written")

    job_id = "smoke"
    store = JobStore(job_id).ensure()
    book = Book(job_id=job_id, source_path=str(epub), source_hash=job_id,
                title="The Smoke Test", author="CI Runner", year="2024")
    store.save_book(book)
    store.save_chapters([
        Chapter(chapter_id="ch0001", sequence=0, title="The Beginning",
                text="It was the year nineteen ninety-nine and the road ran on. " * 6,
                char_count=348),
        Chapter(chapter_id="ch0002", sequence=1, title="The Ending",
                text="She walked three miles home and paid five dollars for tea. " * 6,
                char_count=354),
    ])
    store.save_voice(VoiceSettings(engine="fake"))

    print("\nrender")
    out_dir = tmp / "audiobooks"
    state = worker.render_job(job_id, output_dir=str(out_dir),
                              output_mode=worker.MODE_FOLDER)
    check(state.stage == "done", f"job reached 'done' (got {state.stage!r}, error={state.error!r})")

    out = Path(state.output_path) if state.output_path else None
    check(out is not None and out.exists(), "an .m4b file was produced")
    if not out or not out.exists():
        return report()
    check(out.suffix == ".m4b", "output is named .m4b")
    check(out.stat().st_size > 10_000, f"output is a real size ({out.stat().st_size:,} bytes)")

    print("\ncontainer")
    probe = validate.probe_container(out)
    check(probe is not None, "container is readable")
    if probe:
        check(probe["chapters"] == 2, f"two chapter markers (got {probe['chapters']})")
        check(bool(probe["duration"]) and probe["duration"] > 5,
              f"non-trivial duration ({probe['duration']}s)")

    print("\nPlex/Audnexus tags")
    from ebook_audiobook.audio.tag import read_tags

    tags = read_tags(out)
    check(tags["stik"] == 2, "marked as an Audiobook (stik=2)")
    check(tags["title"] == "The Smoke Test", "title tag")
    check(tags["album_artist"] == "CI Runner", "album-artist tag (author matching)")

    print("\nfull validation")
    problems = validate.validate_m4b(out, book)
    check(problems == [], f"no validation problems ({problems})")

    print("\nresume / content addressing")
    # A second render must reuse every cached segment rather than redo the work.
    seg = store.load_segments()[0]
    before = store.segment_audio_path(seg.segment_id).stat().st_mtime_ns
    worker.render_job(job_id, output_dir=str(out_dir), output_mode=worker.MODE_FOLDER)
    after = store.segment_audio_path(seg.segment_id).stat().st_mtime_ns
    check(before == after, "cached segment audio was reused, not re-rendered")

    return report()


def report() -> int:
    print()
    if FAILURES:
        print(f"SMOKE TEST FAILED — {len(FAILURES)} problem(s):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("SMOKE TEST PASSED — installed copy renders a valid, Plex-ready audiobook.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
