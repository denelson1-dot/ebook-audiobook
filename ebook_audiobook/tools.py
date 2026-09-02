"""Discovery of, and safe invocation of, the external programs we shell out to.

Three tools matter: **ffmpeg** (encode/mux the .m4b, transcode voice clips),
**ffprobe** (optional, only used to double-check the finished file), and
Calibre's **ebook-convert** (normalize any input format to a clean EPUB).

Finding them is not as simple as ``shutil.which`` on every OS:

* ffmpeg often isn't installed at all, so we fall back to the static binary
  shipped by the ``imageio-ffmpeg`` wheel. A system ffmpeg is always preferred
  when present — it's usually newer and it brings ffprobe with it.
* Calibre's macOS build lives inside ``/Applications/calibre.app`` and puts
  nothing on ``PATH`` unless the user opted into command-line tools; the Windows
  build sometimes isn't on ``PATH`` either, depending on installer choices. Both
  are found here by looking in the places those installers actually use.

Invocation has its own portability traps, all handled by :func:`run`:

* ``text=True`` decodes with the *locale* encoding, which on a Western Windows
  box is cp1252 — so an ebook with a non-Latin-1 character anywhere in ffmpeg's
  or Calibre's output would raise ``UnicodeDecodeError`` and fail the render for
  no good reason. We always decode UTF-8 with ``errors="replace"``.
* On Windows, spawning a console program from a GUI-launched process pops up a
  console window. ``CREATE_NO_WINDOW`` suppresses it.
* An inherited stdin lets a subprocess block forever waiting for input that will
  never come, so it is always ``DEVNULL``.
"""

from __future__ import annotations

from .i18n import _
import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"

# Suppress the console window Windows would otherwise flash for each subprocess.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if IS_WINDOWS else 0


class MissingToolError(RuntimeError):
    """A required external program isn't installed. Message is user-facing."""


# --- invocation --------------------------------------------------------------

def reveal(path: Path) -> bool:
    """Open a folder in the desktop's own file manager. Best effort.

    Deliberately fire-and-forget: a file manager is a long-lived window, not a
    tool that returns an answer, so waiting on it would hang the request. That
    also means the exit status tells us nothing — Windows Explorer exits
    non-zero on success — so this reports only whether the command could be
    launched at all.

    Lives here because this module is the one place allowed to start an external
    program; see CONTRIBUTING. Callers must pass a path the app already knows
    about, never one taken straight from a request.
    """
    path = Path(path)
    if not path.is_dir():
        return False
    if IS_WINDOWS:
        cmd: list[str] = ["explorer", str(path)]
    elif sys.platform == "darwin":
        cmd = ["open", str(path)]
    else:
        # Every mainstream Linux desktop provides this; it dispatches to whatever
        # file manager the user actually has.
        opener = shutil.which("xdg-open")
        if not opener:
            return False
        cmd = [opener, str(path)]
    try:
        subprocess.Popen(  # noqa: S603 - fixed argv, path resolved by the caller
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
            start_new_session=not IS_WINDOWS,  # don't die with us
        )
        return True
    except OSError:
        return False


def run(cmd: list[str | Path], timeout: float | None = None,
        capture: bool = True) -> subprocess.CompletedProcess:
    """Run an external tool with portable, non-surprising defaults.

    Always decodes output as UTF-8 with replacement, never inherits stdin, and
    never flashes a console window on Windows.
    """
    return subprocess.run(
        [str(c) for c in cmd],
        capture_output=capture,
        stdin=subprocess.DEVNULL,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=_NO_WINDOW,
    )


# --- ffmpeg / ffprobe --------------------------------------------------------

def _bundled_ffmpeg() -> Path | None:
    """The static ffmpeg from the ``imageio-ffmpeg`` wheel, if it's usable."""
    try:
        import imageio_ffmpeg
    except ImportError:
        return None
    try:
        exe = Path(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:  # noqa: BLE001 - no binary for this platform/arch
        return None
    if not exe.is_file():
        return None
    # Wheels are not guaranteed to preserve the executable bit on every
    # filesystem; make sure we can actually run it before promising we can.
    if not IS_WINDOWS and not os.access(exe, os.X_OK):
        try:
            exe.chmod(exe.stat().st_mode | 0o755)
        except OSError:
            return None
    return exe


def _on_path_or_in_usual_places(name: str) -> Path | None:
    """Find a command, looking beyond PATH where PATH can't be trusted.

    A macOS app launched from the Dock inherits launchd's PATH — just
    ``/usr/bin:/bin:/usr/sbin:/sbin`` — never the user's shell PATH. So
    Homebrew's ffmpeg is invisible to a double-clicked app while being right
    there for the same user in Terminal. That divergence is worse than simply
    not finding it: ``ebook-audiobook check`` in a terminal would report a
    different toolchain than the app is actually running with.

    Calibre already gets this treatment (see :func:`_calibre_candidates`); this
    extends it to the ffmpeg pair, which mattered less when there was no way to
    launch the app from Finder.
    """
    found = shutil.which(name)
    if found:
        return Path(found)
    if IS_MACOS:
        for prefix in ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin"):
            candidate = Path(prefix) / name
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
    return None


@lru_cache(maxsize=1)
def ffmpeg_path() -> Path | None:
    """Path to an ffmpeg we can run, preferring one the user installed."""
    return _on_path_or_in_usual_places("ffmpeg") or _bundled_ffmpeg()


@lru_cache(maxsize=1)
def ffprobe_path() -> Path | None:
    """Path to ffprobe, or None.

    Only a system install provides this — the bundled ffmpeg wheel ships no
    ffprobe. Everything that uses it degrades gracefully (see
    :mod:`ebook_audiobook.audio.validate`), so its absence is never fatal.
    """
    return _on_path_or_in_usual_places("ffprobe")


def require_ffmpeg() -> Path:
    ff = ffmpeg_path()
    if ff is None:
        raise MissingToolError(
            _("ffmpeg is required to build audio files but no usable copy was "
              "found. It normally installs automatically with this app; "
              "reinstalling should fix it, or install ffmpeg yourself:") + "\n"
            + install_hint("ffmpeg")
        )
    return ff


def ffmpeg_is_bundled() -> bool:
    """True when we're falling back to the wheel's ffmpeg (no system one found)."""
    return (_on_path_or_in_usual_places("ffmpeg") is None
            and _bundled_ffmpeg() is not None)


# --- Calibre (ebook-convert) -------------------------------------------------

def _calibre_candidates() -> list[Path]:
    """Places each platform's Calibre installer actually puts ebook-convert."""
    if IS_WINDOWS:
        roots = [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        out = []
        for r in roots:
            if not r:
                continue
            out.append(Path(r) / "Calibre2" / "ebook-convert.exe")
            out.append(Path(r) / "Calibre" / "ebook-convert.exe")
            # winget installs into a versioned Packages folder for some builds.
            out.append(Path(r) / "Programs" / "Calibre2" / "ebook-convert.exe")
        return out
    if IS_MACOS:
        # The .app bundle is the normal install (drag-to-Applications or
        # `brew install --cask calibre`); it adds nothing to PATH by itself.
        return [
            Path("/Applications/calibre.app/Contents/MacOS/ebook-convert"),
            Path.home() / "Applications/calibre.app/Contents/MacOS/ebook-convert",
            Path("/opt/homebrew/bin/ebook-convert"),
            Path("/usr/local/bin/ebook-convert"),
        ]
    # Linux: distro package, the official binary installer, snap, or flatpak.
    return [
        Path("/usr/bin/ebook-convert"),
        Path("/usr/local/bin/ebook-convert"),
        Path("/opt/calibre/ebook-convert"),
        Path("/snap/bin/ebook-convert"),
        Path("/var/lib/flatpak/exports/bin/com.calibre_ebook.calibre"),
        Path.home() / ".local/bin/ebook-convert",
    ]


@lru_cache(maxsize=1)
def ebook_convert_path() -> Path | None:
    """Path to Calibre's ebook-convert, searching beyond PATH.

    An explicit ``EBAB_EBOOK_CONVERT`` wins, for unusual installs.
    """
    override = os.environ.get("EBAB_EBOOK_CONVERT")
    if override:
        p = Path(override).expanduser()
        return p if p.is_file() else None
    found = shutil.which("ebook-convert")
    if found:
        return Path(found)
    for c in _calibre_candidates():
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return None


def require_ebook_convert() -> Path:
    exe = ebook_convert_path()
    if exe is None:
        raise MissingToolError(
            _("Calibre isn't installed — its 'ebook-convert' command is what reads "
              "your ebook. Install Calibre, then try again:") + "\n" + install_hint("calibre")
        )
    return exe


# --- user-facing install guidance -------------------------------------------

_HINTS = {
    "calibre": {
        "win32": "  winget install --id calibre.calibre\n"
                 "  (or download from https://calibre-ebook.com/download)",
        "darwin": "  brew install --cask calibre\n"
                  "  (or download from https://calibre-ebook.com/download)",
        "linux": "  sudo apt install calibre\n"
                 "  (or see https://calibre-ebook.com/download_linux)",
    },
    "ffmpeg": {
        "win32": "  winget install --id Gyan.FFmpeg",
        "darwin": "  brew install ffmpeg",
        "linux": "  sudo apt install ffmpeg",
    },
}


def install_hint(tool: str) -> str:
    """The copy-pasteable install command for this tool on *this* machine."""
    key = "win32" if IS_WINDOWS else "darwin" if IS_MACOS else "linux"
    return _HINTS.get(tool, {}).get(key, f"  see the {tool} project's download page")


def reset_cache() -> None:
    """Forget discovered tool locations.

    Called by tests, and worth calling after installing a tool mid-session so the
    app notices it without a restart. Tolerates a lookup having been replaced by
    a plain function (as tests do), so cleanup can never itself raise.
    """
    for fn in (ffmpeg_path, ffprobe_path, ebook_convert_path):
        clear = getattr(fn, "cache_clear", None)
        if clear:
            clear()
