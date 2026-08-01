"""Assert that a PyTorch install resolved to the build we actually asked for.

Reads a ``pip install --dry-run --report`` JSON file and checks that torch and
torchaudio carry the expected local version tag (``+cpu``, ``+rocm6.2.4``, or a
CUDA build), and that no foreign accelerator packages came along for the ride.

Why this exists: the installer used to run two pip commands — torch from the
chosen index, then Chatterbox from PyPI. Chatterbox pins an exact torch version,
so the second command downgraded the first one's torch, and with no index pinned
it took the replacement from PyPI, which is the CUDA build. A ``--cpu`` install
advertised as "about 250 MB" put 6.4 GB on disk with 13 nvidia-* packages, and
nothing failed.

That same pin is why the AMD index is not interchangeable. Chatterbox requires
torch==2.6.0, and ``2.6.0+rocm`` wheels exist only on the rocm6.2.4 index — the
rocm6.3 and rocm6.4 indexes start at torch 2.7. Bumping the index without
re-checking would resolve to a plain PyPI torch and quietly put AMD users on the
CPU. This asserts it doesn't.

Usage:
    assert_torch_build.py <report.json> <flavour>
where flavour is "cpu", "rocm", or "cuda".
"""

from __future__ import annotations

import json
import sys

# What each flavour must look like once resolved. The local version tag is
# exact, not a prefix: "+cu" would happily accept cu124 when cu128 was asked
# for, which is the whole class of bug this script exists to catch.
FLAVOURS = {
    # name: (required local-version tag, forbidden package prefixes)
    "cpu": ("+cpu", ("nvidia-", "pytorch-triton-rocm")),
    "rocm": ("+rocm6.4", ("nvidia-",)),
    "cu126": ("+cu126", ("pytorch-triton-rocm",)),
    "cu128": ("+cu128", ("pytorch-triton-rocm",)),
}

# Below this, the CUDA build has no kernels for RTX 50-series and the ROCm build
# predates RDNA4 — the entire reason for the upgrade.
MIN_TORCH = (2, 9)

# Installed by Chatterbox's own resolution but deliberately excluded from ours:
# gradio is never imported by the library, and spacy-pkuseg is Chinese-only and
# already behind a try/except. Their presence means --no-deps was bypassed.
FORBIDDEN_PACKAGES = ("gradio", "spacy-pkuseg")


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[2] not in FLAVOURS:
        print(f"usage: {argv[0]} <pip-report.json> <{'|'.join(FLAVOURS)}>",
              file=sys.stderr)
        return 2
    report_path, flavour = argv[1], argv[2]
    wanted, forbidden = FLAVOURS[flavour]

    with open(report_path) as fh:
        report = json.load(fh)

    resolved = {
        item["metadata"]["name"].lower(): item["metadata"]["version"]
        for item in report.get("install", [])
    }
    if not resolved:
        print("the pip report is empty — nothing was resolved. Re-run the "
              "resolution with --ignore-installed.", file=sys.stderr)
        return 1

    problems = []
    for name in ("torch", "torchaudio"):
        version = resolved.get(name)
        if version is None:
            problems.append(f"{name} was not resolved at all")
            continue
        if wanted not in version:
            problems.append(
                f"{name} resolved to {version!r}, which is not a {wanted} build — "
                f"the {flavour} index did not win, so this install would run on "
                f"the wrong hardware"
            )
        # A floor rather than an exact pin resolves to PyPI's much newer torch,
        # which has no local version tag at all. Catch that explicitly.
        try:
            base = version.split("+")[0].split(".")
            if (int(base[0]), int(base[1])) < MIN_TORCH:
                problems.append(
                    f"{name} resolved to {version!r}, older than the "
                    f"{MIN_TORCH[0]}.{MIN_TORCH[1]} needed for current GPUs")
        except (ValueError, IndexError):
            problems.append(f"{name} version {version!r} is unparseable")

    for pkg in FORBIDDEN_PACKAGES:
        if pkg in resolved:
            problems.append(
                f"{pkg} was resolved, so Chatterbox's own dependency list ran "
                f"instead of the curated one")

    for prefix in forbidden:
        stowaways = sorted(n for n in resolved if n.startswith(prefix))
        if stowaways:
            problems.append(
                f"{len(stowaways)} {prefix}* package(s) pulled into a {flavour} "
                f"install: {', '.join(stowaways[:6])}"
            )

    if problems:
        print(f"{flavour} resolution is wrong:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"{flavour} resolution is clean: torch {resolved['torch']}, "
          f"torchaudio {resolved['torchaudio']}, "
          f"chatterbox-tts {resolved.get('chatterbox-tts', '(absent)')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
