"""Assert that a CPU-only PyTorch install really is CPU-only.

Reads a `pip install --dry-run --report` JSON file and fails if the resolution
produced anything other than `+cpu` builds of torch and torchaudio, or dragged
in NVIDIA's CUDA runtime packages.

Why this exists: the installer used to run two pip commands — torch from the
chosen index, then Chatterbox from PyPI. Chatterbox pins an exact torch version,
so the second command downgraded the first one's torch, and with no index pinned
it took the replacement from PyPI, which is the CUDA build. A `--cpu` install
advertised as "about 250 MB" put 6.4 GB on disk with 13 nvidia-* packages, and
nothing failed. Both are resolved in a single command now; this keeps it that
way. See CHANGELOG 1.0.2.
"""

from __future__ import annotations

import json
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <pip-report.json>", file=sys.stderr)
        return 2

    with open(argv[1]) as fh:
        report = json.load(fh)

    resolved = {
        item["metadata"]["name"].lower(): item["metadata"]["version"]
        for item in report.get("install", [])
    }

    problems = []
    for name in ("torch", "torchaudio"):
        version = resolved.get(name)
        if version is None:
            problems.append(f"{name} was not resolved at all")
        elif not version.endswith("+cpu"):
            problems.append(
                f"{name} resolved to {version!r}, which is not a +cpu build — "
                f"a CPU install has picked up the CUDA wheels again"
            )

    cuda_packages = sorted(n for n in resolved if n.startswith("nvidia-"))
    if cuda_packages:
        problems.append(
            f"{len(cuda_packages)} CUDA package(s) pulled into a CPU-only "
            f"install: {', '.join(cuda_packages)}"
        )

    if problems:
        print("CPU resolution is wrong:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(
        f"CPU resolution is clean: torch {resolved['torch']}, "
        f"torchaudio {resolved['torchaudio']}, no CUDA packages"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
