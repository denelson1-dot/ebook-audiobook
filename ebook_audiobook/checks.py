"""Startup checks.

Fail early with actionable guidance rather than deep inside a multi-hour render.
The same results drive ``ebook-audiobook check`` on the command line and the
prerequisite banner in the web UI, so a user is told about a missing tool in
whichever place they happen to be looking.
"""

from __future__ import annotations

from .i18n import _
import platform
import sys
from dataclasses import dataclass

# quiet is imported for its side effect: it filters the engine's import-time
# noise before check_tts_engine() below pulls chatterbox in.
from . import device, quiet, tools  # noqa: F401
from .config import data_root, paths


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    # Copy-pasteable fix for a failing check, when one exists. Shown by the CLI
    # and rendered as guidance in the web UI.
    fix: str | None = None
    # False for checks whose failure only removes capability rather than
    # stopping the app from working at all (e.g. the GPU engine).
    required: bool = True

    def to_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail,
                "fix": self.fix, "required": self.required}


def _version_line(exe, args: list[str]) -> str:
    """First line of a tool's ``--version`` output, or its path if it won't talk."""
    try:
        out = tools.run([exe, *args], timeout=30)
        lines = (out.stdout or out.stderr or "").splitlines()
        return lines[0].strip() if lines else str(exe)
    except Exception:  # noqa: BLE001 - any failure: fall back to reporting the path
        return str(exe)


def check_python() -> CheckResult:
    v = sys.version_info
    ok = v >= (3, 11)
    return CheckResult(
        "python",
        ok,
        f"{v.major}.{v.minor}.{v.micro} on {platform.system()} {platform.machine()}"
        + ("" if ok else _(" (need >= 3.11)")),
        fix=None if ok else _("Install Python 3.11 or newer from https://python.org/downloads"),
    )


def check_ffmpeg() -> CheckResult:
    exe = tools.ffmpeg_path()
    if exe is None:
        return CheckResult(
            "ffmpeg", False,
            _("MISSING — normally installed automatically with this app"),
            fix=tools.install_hint("ffmpeg"),
        )
    origin = _("bundled") if tools.ffmpeg_is_bundled() else _("system")
    return CheckResult("ffmpeg", True, f"{_version_line(exe, ['-version'])}  [{origin}]")


def check_ffprobe() -> CheckResult:
    """Optional: only sharpens post-render validation, never required."""
    exe = tools.ffprobe_path()
    if exe is None:
        return CheckResult(
            _("ffprobe (optional)"), True,
            _("not installed — output is verified with ffmpeg instead"),
            required=False,
        )
    return CheckResult(_("ffprobe (optional)"), True,
                       _version_line(exe, ["-version"]), required=False)


def check_calibre() -> CheckResult:
    exe = tools.ebook_convert_path()
    if exe is None:
        return CheckResult(
            _("calibre (ebook-convert)"), False,
            _("MISSING — required to read ebooks"),
            fix=tools.install_hint("calibre"),
        )
    return CheckResult(_("calibre (ebook-convert)"), True,
                       f"{_version_line(exe, ['--version'])}  [{exe}]")


def check_data_root() -> CheckResult:
    p = paths()
    try:
        p.ensure()
        probe = p.tmp / ".write-probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return CheckResult(_("data folder (writable)"), True, str(p.root))
    except OSError as e:
        return CheckResult(
            _("data folder (writable)"), False, f"{p.root}: {e}",
            fix=_("Set EBAB_DATA_ROOT to a folder you can write to."),
        )


def _torch_too_old(version: str) -> bool:
    """Whether an installed torch predates the one we ship against.

    Below 2.9 the CUDA build has no kernels for RTX 50-series and the ROCm build
    predates RDNA4, so an upgraded app on a stale environment silently loses
    hardware support. Worth naming rather than leaving to be discovered.
    """
    try:
        parts = version.split("+")[0].split(".")
        return (int(parts[0]), int(parts[1])) < (2, 9)
    except (ValueError, IndexError):
        return False


def engine_install_hint() -> str:
    """How to install the speech engine into *this* interpreter's environment.

    Deliberately not ``pip install 'ebook-audiobook[tts]'``: that extra only
    resolves if the project is on PyPI under that name, which it is not, so the
    advice could never work. ``sys.executable -m pip`` also targets the venv the
    app is actually running in, rather than whichever ``pip`` happens to be on
    PATH — which for a user who installed via the one-line installer is usually a
    different Python entirely.
    """
    from .torchbuild import TORCH_PIN

    if intel_mac():
        # There is nothing to re-run: PyTorch stopped building for x86_64 macOS
        # after 2.2.2, so no install can succeed. The installer says the same.
        return _("  PyTorch stopped building for Intel Macs after 2.2.2, so the speech\n"
                 "  engine can't be installed on this Mac. Importing and reading books\n"
                 "  still works; rendering needs an Apple Silicon Mac, Windows or Linux.")
    return (f'  Re-run the installer — it picks the right PyTorch build for\n'
            f'  this machine. By hand, into this environment:\n'
            f'    "{sys.executable}" -m pip install torch=={TORCH_PIN} '
            f'torchaudio=={TORCH_PIN}\n'
            f'  (see the README for which package index to use)')


def intel_mac() -> bool:
    """An x86_64 macOS process — a real Intel Mac, or an Intel Python running
    under Rosetta on Apple Silicon. Either way this interpreter can't have the
    Apple Silicon build of the engine."""
    return sys.platform == "darwin" and platform.machine() == "x86_64"


def check_tts_engine(engine: str = "chatterbox") -> CheckResult:
    """Non-fatal: the pipeline runs with the fake engine without this."""
    if engine == "fake":
        return CheckResult(_("tts engine (fake)"), True, _("no dependencies"), required=False)
    try:
        import torch  # noqa: F401
    except Exception:
        return CheckResult(
            _("tts engine (chatterbox)"), False,
            _("not installed — you can import books, but not render audio"),
            fix=engine_install_hint(),
            required=False,
        )

    dev = device.select_device()
    try:
        import chatterbox  # noqa: F401

        cb = True
    except Exception:
        cb = False
    detail = _("torch %(torch)s, chatterbox=%(cb)s, running on %(device)s", torch=torch.__version__, cb=_("yes") if cb else _("no"), device=dev.describe())
    if _torch_too_old(torch.__version__):
        detail += _("  — this PyTorch predates 2.9 and has no kernels for the "
                    "newest GPUs; re-run the installer to update it")
    # Any device can render; CPU is just slow, so it's not a failure.
    return CheckResult(
        _("tts engine (chatterbox)"), cb, detail,
        fix=None if cb else engine_install_hint(),
        required=False,
    )


def check_narration_languages() -> CheckResult:
    """Which languages a book can be narrated in — never a failure, since
    English is always on offer and the rest is an opt-in download."""
    from . import narration_langs as nl

    ready = [_(lg.name) for lg in nl.LANGUAGES.values()
             if lg.tier == "supported" and nl.language_available(lg.code)]
    waiting = [_(lg.name) for lg in nl.LANGUAGES.values()
               if lg.tier == "supported" and not nl.language_available(lg.code)]
    parts = []
    if ready:
        parts.append(_("installed: %(names)s", names=", ".join(ready)))
    if waiting:
        parts.append(_("not installed: %(names)s (Settings, under Narration languages)",
                       names=", ".join(waiting)))
    return CheckResult(_("narration languages"), True, "; ".join(parts) or _("none"),
                       required=False)


def run_all(engine: str = "chatterbox") -> list[CheckResult]:
    return [
        check_python(),
        check_ffmpeg(),
        check_ffprobe(),
        check_calibre(),
        check_data_root(),
        check_tts_engine(engine),
        check_narration_languages(),
    ]


def blocking_problems(results: list[CheckResult]) -> list[CheckResult]:
    """Failures that actually stop the app being usable."""
    return [r for r in results if r.required and not r.ok]


def format_results(results: list[CheckResult]) -> str:
    lines = [f"data folder: {data_root()}", ""]
    for r in results:
        mark = "ok" if r.ok else "!!"
        lines.append(f"[{mark}] {r.name}: {r.detail}")
        if r.fix and not r.ok:
            for fix_line in r.fix.splitlines():
                lines.append(f"       {fix_line}")
    return "\n".join(lines)
