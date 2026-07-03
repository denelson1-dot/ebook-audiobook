"""Startup checks.

Fail early with actionable guidance rather than deep inside a multi-hour render.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass

from .config import paths


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _cmd_version(exe: str, args: list[str]) -> str | None:
    path = shutil.which(exe)
    if not path:
        return None
    try:
        out = subprocess.run(
            [exe, *args], capture_output=True, text=True, timeout=20
        )
        first = (out.stdout or out.stderr).splitlines()
        return first[0].strip() if first else path
    except (subprocess.SubprocessError, OSError):
        return path


def check_python() -> CheckResult:
    v = sys.version_info
    ok = v >= (3, 11)
    return CheckResult(
        "python",
        ok,
        f"{v.major}.{v.minor}.{v.micro}" + ("" if ok else " (need >= 3.11)"),
    )


def check_ffmpeg() -> CheckResult:
    ver = _cmd_version("ffmpeg", ["-version"])
    return CheckResult(
        "ffmpeg",
        ver is not None,
        ver or "MISSING — install with: sudo apt install ffmpeg",
    )


def check_calibre() -> CheckResult:
    ver = _cmd_version("ebook-convert", ["--version"])
    return CheckResult(
        "calibre (ebook-convert)",
        ver is not None,
        ver or "MISSING — install with: sudo apt install calibre",
    )


def check_data_root() -> CheckResult:
    p = paths()
    try:
        p.ensure()
        probe = p.tmp / ".write-probe"
        probe.write_text("ok")
        probe.unlink()
        return CheckResult("data root (writable)", True, str(p.root))
    except OSError as e:
        return CheckResult("data root (writable)", False, f"{p.root}: {e}")


def check_tts_engine(engine: str = "chatterbox") -> CheckResult:
    """Non-fatal: the pipeline runs with the fake engine without this."""
    if engine == "fake":
        return CheckResult("tts engine (fake)", True, "no dependencies")
    try:
        import torch  # noqa: F401

        if torch.cuda.is_available():
            device = "cuda"
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"
        try:
            import chatterbox  # noqa: F401

            cb = True
        except Exception:
            cb = False
        detail = f"torch {torch.__version__}, device={device}, chatterbox={'yes' if cb else 'no'}"
        if device == "cpu":
            detail += " (CPU works but is slow — an NVIDIA GPU or Apple Silicon is recommended)"
        # Any device can render; CPU is just slow, so it's not a failure.
        return CheckResult("tts engine (chatterbox)", cb, detail)
    except Exception:
        return CheckResult(
            "tts engine (chatterbox)",
            False,
            "not installed — run: pip install -e '.[tts]' (see README for CUDA torch)",
        )


def run_all(engine: str = "chatterbox") -> list[CheckResult]:
    return [
        check_python(),
        check_ffmpeg(),
        check_calibre(),
        check_data_root(),
        check_tts_engine(engine),
    ]


def format_results(results: list[CheckResult]) -> str:
    lines = []
    for r in results:
        mark = "OK " if r.ok else "!! "
        lines.append(f"[{mark}] {r.name}: {r.detail}")
    return "\n".join(lines)
