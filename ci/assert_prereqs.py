"""Assert the prerequisite state CI should have, and fail loudly on anything else.

CI runners have no Calibre, so ``ebook-audiobook check`` correctly exits
non-zero there. Treating that as a build failure would be wrong — but so would
ignoring the exit code entirely, because then a genuinely broken ffmpeg or an
unwritable data directory would sail through unnoticed.

So: Calibre may be missing. Nothing else may be. And ffmpeg must resolve, since
nothing on the runner installs one — that is the check that proves the binary
bundled in the wheel actually works on this platform.

Pass ``--require-calibre`` where Calibre *is* installed (the integration job) to
demand a completely clean bill of health.
"""

from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    require_calibre = "--require-calibre" in argv

    from ebook_audiobook import checks, tools

    results = checks.run_all(engine="fake")
    for r in results:
        print(f"  [{'ok' if r.ok else '!!'}] {r.name}: {r.detail}")

    problems = checks.blocking_problems(results)
    if require_calibre:
        unexpected = problems
    else:
        unexpected = [r for r in problems if "calibre" not in r.name.lower()]

    print()
    failed = False

    if unexpected:
        print("FAIL: unexpected failing prerequisites:")
        for r in unexpected:
            print(f"  - {r.name}: {r.detail}")
        failed = True

    if tools.ffmpeg_path() is None:
        print("FAIL: no ffmpeg resolved — the bundled binary is not working here")
        failed = True
    else:
        origin = "bundled with the wheel" if tools.ffmpeg_is_bundled() else "system"
        print(f"ffmpeg: {tools.ffmpeg_path()}  ({origin})")

    if failed:
        return 1
    if not require_calibre and any("calibre" in r.name.lower() for r in problems):
        print("Calibre absent, as expected on a CI runner.")
    print("prerequisite state is as expected.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
