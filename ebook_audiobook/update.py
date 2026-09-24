"""Checking for, and installing, a newer release.

This is the one part of the app that talks to the network for its own sake, so
it is the one part that has to be careful about it. The promise on the tin is
that nothing leaves your machine; a background version check would quietly make
that untrue, and a version check is a ping to GitHub carrying your IP and rough
usage pattern.

So: **nothing in this module ever runs on its own.** There is no timer and no
start-up poll here. A check happens when someone runs ``ebook-audiobook
update``, or presses the button in Settings. :func:`check` is the only
function that opens a socket, and this module never calls it from import time
or from a request handler the user did not trigger.

``ebook_audiobook.web.update_watch`` builds an *opt-in* timer on top of this
module — off by default (``check_for_updates``), and even once turned on it
only ever calls :func:`check`, the exact same function, on a schedule instead
of a click. Turning that setting on **is** the consent for those calls, the
same way running ``ebook-audiobook update`` by hand is; nothing here treats
the two differently. Installing what is found stays a separate, explicit step
either way — the setting only automates asking, never applying.

Applying an update re-runs the official installer, which is the same code path a
new user gets — so an upgrade is never a second, less-tested install route.

On Windows the installer cannot run *beside* the app it is upgrading, so there
it runs after the app has quit, in a window of its own; see
:func:`closes_to_install`.
"""

from __future__ import annotations

from .i18n import _
import base64
import json
import os
import platform
import re
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

REPO = "denelson1-dot/ebook-audiobook"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
INSTALL_SH = f"https://github.com/{REPO}/releases/latest/download/install-macos-linux.sh"
INSTALL_PS1 = f"https://github.com/{REPO}/releases/latest/download/install-windows.ps1"

TIMEOUT_SECONDS = 10


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class Release:
    version: str
    tag: str
    url: str

    @property
    def notes_url(self) -> str:
        return self.url or RELEASES_PAGE


def parse_version(text: str) -> tuple[int, ...]:
    """``"v1.2.3"`` -> ``(1, 2, 3)``.

    Trailing suffixes (``1.2.3.dev0``, ``1.2.3+local``) are cut at the first
    non-numeric part, so a locally-built copy compares as its base version
    rather than sorting unpredictably.
    """
    cleaned = (text or "").strip().lstrip("vV")
    parts: list[int] = []
    for chunk in re.split(r"[.\-+]", cleaned):
        if chunk.isdigit():
            parts.append(int(chunk))
        else:
            break
    return tuple(parts) or (0,)


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)


def current_version() -> str:
    from . import __version__

    return __version__


def check(timeout: float = TIMEOUT_SECONDS) -> Release:
    """Ask GitHub for the latest release. Opens a network connection.

    Only ever called in response to something the user did.
    """
    req = urllib.request.Request(
        LATEST_API,
        headers={
            "Accept": "application/vnd.github+json",
            # GitHub rejects requests without one, and an honest agent is
            # better than pretending to be a browser.
            "User-Agent": f"ebook-audiobook/{current_version()}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=ssl.create_default_context()) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError(_("No published releases yet.")) from e
        if e.code in (403, 429):
            raise UpdateError(_("GitHub rate-limited the version check. Try again later, or see %(url)s", url=RELEASES_PAGE)) from e
        raise UpdateError(_("GitHub returned HTTP %(code)s for the version check.", code=e.code)) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError(_("Couldn't reach GitHub to check for updates (%(e)s). "
                            "You're offline, or a firewall is in the way.", e=e)) from e
    except ValueError as e:
        raise UpdateError(_("GitHub's reply wasn't valid JSON.")) from e

    tag = str(payload.get("tag_name") or "").strip()
    if not tag:
        raise UpdateError(_("GitHub's reply had no release tag."))
    return Release(version=tag.lstrip("vV"), tag=tag,
                   url=str(payload.get("html_url") or RELEASES_PAGE))


def install_command() -> str:
    """The command that upgrades this machine, for showing to the user."""
    if sys.platform.startswith("win"):
        return f'irm {INSTALL_PS1} | iex'
    return f"curl -fsSL {INSTALL_SH} | bash"


def closes_to_install() -> bool:
    """Whether the app has to quit before its own update can be installed.

    True on Windows, where a running copy is in the way of the installer three
    times over. Windows won't let pip move a folder holding a loaded DLL, and a
    running copy has numpy's, lxml's and soundfile's loaded (torch's too, after
    a render), so a release that changes any of those pins fails and rolls back.
    A process started without a console, as the app is, would give the
    installer's PowerShell a console window of its own anyway, and closing that
    window kills pip halfway. And the installer, asked to accept its prompts,
    would install whatever it would offer a new user.

    So there the app starts the installer in a window of its own and quits; the
    installer waits for it to be gone, upgrades only what is already installed
    (``-Update``), and starts it again. See :func:`start_windows_update`.
    Elsewhere a loaded library doesn't pin its file, and the installer runs in
    the background while the app carries on.
    """
    return sys.platform.startswith("win")


def apply_update(yes: bool = False, timeout: float = 3_600, *,
                 update_only: bool = False) -> int:
    """Download and run the official installer, in place.

    Deliberately the same script a new user runs: an upgrade path that isn't the
    install path is an upgrade path nobody tests. Returns the installer's exit
    code. Requires curl (macOS/Linux) or PowerShell (Windows), both of which
    were needed to install in the first place.

    *yes* is the CLI's ``--yes``: accept every prompt, as a scripted install
    would. *update_only* is what the app's own "Install" asks for: upgrade what
    is installed and add nothing (the installers' ``--update`` / ``-Update``),
    since a confirmed update is not consent to a Homebrew cask, a sudo prompt,
    or a multi-gigabyte speech engine the install was set up without.

    On Windows (see :func:`closes_to_install`) this process — ``ebook-audiobook
    update --apply`` has numpy loaded as well — can't be running while the
    installer does, so the installer starts in a new window that waits for this
    one to exit, and 0 means only that it started.
    """
    if closes_to_install():
        # --yes means unattended, and unattended means update only: -Yes would
        # also install Calibre, shortcuts and the speech engine, unasked.
        start_windows_update(installer_args=["-Update"] if yes or update_only else [],
                             relaunch=False)
        return 0

    # `bash -s --`, not `bash --`: without -s, bash takes the first argument
    # after -- for a script file to run, and exits 127 on "--yes". pipefail,
    # or a download that fails hands bash an empty script, which "succeeds".
    flags = " -s -- --update" if update_only else " -s -- --yes" if yes else ""
    cmd = ["bash", "-c", f"set -o pipefail; curl -fsSL {INSTALL_SH} | bash{flags}"]
    try:
        # Inherits stdout/stderr on purpose: the installer's progress is the
        # only feedback during a multi-gigabyte download.
        return subprocess.run(cmd, timeout=timeout).returncode
    except FileNotFoundError as e:
        raise UpdateError("Couldn't find the tool needed to run the installer (bash/curl).") from e
    except subprocess.TimeoutExpired as e:
        raise UpdateError("The installer took too long and was stopped.") from e


# --- Windows: install after the app has quit ---------------------------------

# How long the installer's window waits for the app to finish quitting before
# it closes it itself. Quitting normally takes a few seconds.
APP_EXIT_TIMEOUT_SECONDS = 60

# Fail's marker in install.ps1: it has already printed the reason in red.
_INSTALL_STOPPED = "ebook-audiobook: install stopped"

CREATE_NEW_CONSOLE = 0x00000010
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def installed_layout() -> tuple[str, str]:
    """``(install_dir, gui_exe)`` for the copy that is running, on Windows.

    The installer puts its virtualenv at ``<install_dir>\\venv``. Handing that
    folder back with ``-InstallDir`` upgrades the copy that is actually
    running, even one installed somewhere other than the default. A copy that
    doesn't look installer-made (a source checkout's own venv) gets ``""``,
    the installer's default location.
    """
    prefix = Path(sys.prefix)
    gui_exe = prefix / "Scripts" / "ebook-audiobook-gui.exe"
    if prefix.name.lower() == "venv" and (prefix.parent / "bin" / "ebook-audiobook.cmd").exists():
        return str(prefix.parent), str(gui_exe)
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    default = Path(local) / "ebook-audiobook"
    return "", str(default / "venv" / "Scripts" / "ebook-audiobook-gui.exe")


def _ps_literal(text: str) -> str:
    """*text* as a single-quoted PowerShell string.

    PowerShell also closes a single-quoted string on a typographic quote
    (U+2018-U+201B), and French puts one in every elision, so each of those
    is doubled too, which PowerShell reads as that character itself.
    """
    out = []
    for ch in text:
        out.append(ch + ch if ch in "'\u2018\u2019\u201a\u201b" else ch)
    return "'" + "".join(out) + "'"


def windows_update_script(*, wait_for: int | None, installer: str = INSTALL_PS1,
                          installer_args: Sequence[str] = (), relaunch: str = "",
                          workdir: str = "") -> str:
    """The PowerShell that finishes an update from outside the app.

    Waits for process *wait_for* to exit (closing it after
    :data:`APP_EXIT_TIMEOUT_SECONDS`), runs the installer the way
    ``irm … | iex`` does, then starts *relaunch* if it exists — whether or not
    the installer succeeded, since an upgrade pip rolled back leaves the old
    copy working. *installer* is the URL of install-windows.ps1, or a local
    path to one (CI uses that to run this against the checkout's copy).

    The messages are translated here, in whatever language the caller is in,
    and travel inside the script: nothing on the command line to mangle.
    """
    q = _ps_literal
    if re.match(r"https?://", installer):
        # Bytes, decoded as UTF-8 here: GitHub names no charset, and Windows
        # PowerShell would otherwise read the French and Spanish as Latin-1.
        source = ("[Text.Encoding]::UTF8.GetString((Invoke-WebRequest -UseBasicParsing "
                  f"-Uri {q(installer)}).RawContentStream.ToArray())")
    else:
        source = f"Get-Content -Raw -Encoding UTF8 -LiteralPath {q(installer)}"
    args = " ".join(a if re.fullmatch(r"-[A-Za-z]\w*", a) else q(a) for a in installer_args)
    retry = _("The update didn't finish. To try again, run this in PowerShell:")
    lines = [
        "# ebook-audiobook: finishing an update. The app started this window and quit,",
        "# so that nothing of it is loaded while the installer replaces it.",
        "$ErrorActionPreference = 'Stop'",
        "$ProgressPreference = 'SilentlyContinue'",
        "try { [Net.ServicePointManager]::SecurityProtocol = "
        "[Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12 } catch { }",
        "try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) } catch { }",
        f"try {{ $Host.UI.RawUI.WindowTitle = {q(_('Updating ebook·audiobook'))} }} catch {{ }}",
        "Write-Host ''",
        f"Write-Host {q(_('Updating ebook·audiobook'))} -ForegroundColor White",
        f"Write-Host {q(_('Keep this window open until the update finishes. Closing it stops the update partway.'))} -ForegroundColor DarkGray",
    ]
    if wait_for:
        lines += [
            f"Write-Host {q(_('Waiting for ebook·audiobook to close…'))}",
            # The handle is taken before waiting, so a process id Windows
            # recycles in the meantime can't be mistaken for the app.
            "$app = $null",
            f"try {{ $app = Get-Process -Id {int(wait_for)} -ErrorAction Stop; $null = $app.Handle }} catch {{ $app = $null }}",
            # This window can take seconds to start. If the app is already
            # gone and its id reused, the process found is younger than this
            # window, which the app, having started it, can never be.
            "try { if ($app -and $app.StartTime -gt (Get-Process -Id $PID).StartTime) { $app = $null } } catch { }",
            f"if ($app -and -not $app.WaitForExit({APP_EXIT_TIMEOUT_SECONDS * 1000})) {{",
            "    try { $app.Kill(); $null = $app.WaitForExit(10000) } catch { }",
            "}",
            # The entry-point .exe and the venv's python.exe stub that started
            # it go a moment later; the installer closes any that linger.
            "Start-Sleep -Seconds 1",
        ]
    lines += [
        "$ok = $false",
        "try {",
        f"    $text = {source}",
        f"    & ([scriptblock]::Create($text)) {args}".rstrip(),
        "    $ok = $true",
        "} catch {",
        f'    if ("$_" -ne {q(_INSTALL_STOPPED)}) {{ Write-Host "error: $_" -ForegroundColor Red }}',
        "}",
        "Write-Host ''",
        "if ($ok) {",
        f"    Write-Host {q(_('The update is installed.'))} -ForegroundColor Green",
        "} else {",
        f"    Write-Host {q(retry)} -ForegroundColor Yellow",
        f"    Write-Host {q('    ' + install_command())}",
        "}",
    ]
    if relaunch:
        lines += [
            f"$exe = {q(relaunch)}",
            "if (Test-Path -LiteralPath $exe) {",
            f"    Write-Host {q(_('Starting ebook·audiobook…'))}",
            f"    try {{ Start-Process -FilePath $exe -WorkingDirectory {q(workdir or _install_folder(relaunch))} }} "
            'catch { Write-Host "  $_" -ForegroundColor Yellow }',
            "}",
        ]
    lines += [
        # Left open on failure, so the reason can be read; closed on success.
        "if ($ok) { Start-Sleep -Seconds 5 } else {",
        f"    $null = Read-Host {q(_('Press Enter to close this window'))}",
        "}",
        "exit [int](-not $ok)",
    ]
    return "\n".join(lines) + "\n"


def _install_folder(gui_exe: str) -> str:
    """``<install_dir>`` for ``<install_dir>\\venv\\Scripts\\<exe>``: outside the venv,
    which an update may delete and rebuild, and can't while a process has its
    working directory in it."""
    parents = Path(gui_exe).parents
    return str(parents[2] if len(parents) > 2 else parents[0])


# One installer at a time: a second "Install" before the app has quit must not
# start a second window, and two installers, on the same venv.
_handoff_lock = threading.Lock()
_handoff_started = False


def encode_command(script: str) -> str:
    """*script* as PowerShell's ``-EncodedCommand`` takes it: base64 of UTF-16LE,
    which carries any language's text past the console's code page intact."""
    return base64.b64encode(script.encode("utf-16-le")).decode("ascii")


def start_windows_update(*, installer_args: Sequence[str] = ("-Update",), relaunch: bool = True,
                         lang: str = "", installer: str = INSTALL_PS1) -> bool:
    """Start the installer in a console window of its own, to run once this
    process has exited. The caller must then quit — promptly, or the window
    closes it after :data:`APP_EXIT_TIMEOUT_SECONDS`.

    ``-Update`` by default: what "Install update" confirmed is this release of
    what's installed, not whatever else the installer would offer a new user.

    False, and nothing started, if this process already started one.

    The script travels as ``-EncodedCommand`` rather than as a file: nothing
    to clean up, and nothing on the command line that names the virtualenv,
    which install.ps1 uses to recognise a running copy of the app — it would
    otherwise take this window for one and close it.
    """
    global _handoff_started
    with _handoff_lock:
        if _handoff_started:
            return False
        _start_windows_update(installer_args, relaunch, lang, installer)
        _handoff_started = True
        return True


def _powershell() -> str:
    """Windows PowerShell by its full path: a bare name is looked up in the
    working directory too."""
    root = os.environ.get("SystemRoot") or os.environ.get("windir")
    if root:
        exe = Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        if exe.is_file():
            return str(exe)
    return "powershell"


def _start_windows_update(installer_args, relaunch, lang, installer) -> None:
    install_dir, gui_exe = installed_layout()
    workdir = install_dir or _install_folder(gui_exe)
    args = list(installer_args)
    if install_dir:
        args += ["-InstallDir", install_dir]
    if lang:
        args += ["-Lang", lang]
    script = windows_update_script(
        wait_for=os.getpid(), installer=installer, installer_args=args,
        relaunch=gui_exe if relaunch else "", workdir=workdir)
    cmd = [_powershell(), "-NoProfile", "-ExecutionPolicy", "Bypass",
           "-EncodedCommand", encode_command(script)]
    # Out of any job object this process is in, where the job allows it: the
    # launcher .exe that started the app may close its job, and everything
    # in it, as soon as the app exits — the installer included.
    for flags in (CREATE_NEW_CONSOLE | CREATE_BREAKAWAY_FROM_JOB, CREATE_NEW_CONSOLE):
        try:
            # Not the app's working directory, which may be inside the venv.
            subprocess.Popen(cmd, creationflags=flags, close_fds=True,
                             cwd=workdir if os.path.isdir(workdir) else None)
            return
        except FileNotFoundError as e:
            raise UpdateError(_("Couldn't find PowerShell to run the installer.")) from e
        except OSError as e:
            error = e
    raise UpdateError(_("Couldn't start the installer: %(e)s", e=error)) from error


def status(timeout: float = TIMEOUT_SECONDS) -> tuple[bool, Release | None, str]:
    """``(update_available, release, human_message)``. Opens a connection."""
    current = current_version()
    try:
        latest = check(timeout=timeout)
    except UpdateError as e:
        return False, None, str(e)
    if is_newer(latest.version, current):
        return True, latest, (
            _("%(latest)s is available (you have %(current)s).", latest=latest.version, current=current))
    if is_newer(current, latest.version):
        # A source checkout mid-release, or a locally-built wheel. Claiming
        # "you're on the latest" would be a lie in the one situation where the
        # person reading it is most likely to be checking something specific.
        return False, latest, (
            _("You're on %(current)s, ahead of the latest release (%(latest)s) — an unreleased build.", current=current, latest=latest.version))
    return False, latest, _("You're on the latest version (%(current)s).", current=current)


def platform_hint() -> str:
    """A short description of what this machine would install, for `check`."""
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin":
        if machine in ("arm64", "aarch64"):
            return "macOS on Apple Silicon — PyTorch with Metal (MPS) acceleration"
        return "macOS on Intel — CPU only (PyTorch stopped building for Intel Macs)"
    if system == "Windows":
        return "Windows — CUDA build if an NVIDIA GPU is present, otherwise CPU"
    return "Linux — CUDA or ROCm build if a supported GPU is present, otherwise CPU"
