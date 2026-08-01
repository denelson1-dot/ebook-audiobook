#!/usr/bin/env bash
# ebook-audiobook installer for macOS and Linux.
#
#   curl -fsSL https://github.com/denelson1-dot/ebook-audiobook/releases/latest/download/install.sh | bash
#
# What it does, in order:
#   1. finds a Python 3.11+ interpreter
#   2. creates a private virtualenv under your user data directory
#   3. installs the app, plus the right PyTorch build for this machine
#   4. checks for Calibre and offers to install it
#   5. puts an `ebook-audiobook` command on your PATH and a desktop launcher
#
# It never uses sudo without asking, never touches your system Python, and never
# writes outside the app directory, ~/.local/bin, and (optionally) your desktop.
#
# Options:
#   --version X.Y.Z    install a specific release (default: latest)
#   --dir PATH         install somewhere other than the default
#   --cpu              force the CPU-only PyTorch build (small download)
#   --no-tts           skip PyTorch entirely (import books, can't render yet)
#   --yes              accept all prompts (for scripted installs)
#   --uninstall        remove the app (your books and settings are kept)

set -euo pipefail

REPO="denelson1-dot/ebook-audiobook"
VERSION="latest"
# The release workflow rewrites this line in the published copy of this script,
# so the installer always knows exactly which wheel it belongs to. A wheel's
# filename must contain its version to be installable, so a fixed
# "latest/download/…" asset name is not an option; baking the version in beats
# calling the GitHub API, which is rate-limited for unauthenticated users.
PINNED_VERSION="__EBAB_VERSION__"
ASSUME_YES=0
FORCE_CPU=0
SKIP_TTS=0
DO_UNINSTALL=0
INSTALL_DIR=""

# --- pretty output -----------------------------------------------------------
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  B=$'\033[1m'; DIM=$'\033[2m'; GRN=$'\033[32m'; YLW=$'\033[33m'; RED=$'\033[31m'; N=$'\033[0m'
else
  B=""; DIM=""; GRN=""; YLW=""; RED=""; N=""
fi
say()  { printf '%s\n' "$*"; }
step() { printf '\n%s==>%s %s%s%s\n' "$GRN" "$N" "$B" "$*" "$N"; }
ok()   { printf '  %s✓%s %s\n' "$GRN" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$YLW" "$N" "$*"; }
die()  { printf '\n%serror:%s %s\n' "$RED" "$N" "$*" >&2; exit 1; }

have_sudo() { command -v sudo >/dev/null 2>&1; }

# Install packages with apt, keeping sudo's own failure noise away from the user.
#
# Whether sudo can prompt for a password is genuinely not predictable from here
# (a readable /dev/tty is not sufficient), so rather than guess, run it and
# interpret the outcome: on success say so, on failure print one clear line and
# let the caller fall back. Interactive runs still get sudo's real prompt,
# because stderr is only swallowed after an authentication attempt fails.
apt_install() { # apt_install pkg...
  have_sudo || { warn "sudo isn't available, so this can't be installed for you"; return 1; }
  local log
  log="$(mktemp)"
  if sudo -n true >/dev/null 2>&1; then
    sudo apt-get update >/dev/null 2>&1 || true
    sudo apt-get install -y "$@" >"$log" 2>&1 && { rm -f "$log"; return 0; }
  else
    # Needs a password: let sudo own the terminal so its prompt is visible.
    if sudo apt-get update >/dev/null 2>"$log" && sudo apt-get install -y "$@" >>"$log" 2>&1; then
      rm -f "$log"; return 0
    fi
  fi
  if grep -qi "terminal is required\|password is required\|no askpass" "$log" 2>/dev/null; then
    warn "couldn't ask for your password here, so nothing was installed"
  else
    warn "installing $* failed (see below)"
    tail -3 "$log" 2>/dev/null | sed 's/^/      /'
  fi
  rm -f "$log"
  return 1
}

# Prompts must read from the terminal, not stdin: this script is normally piped
# in from curl, so stdin is the script itself and `read` would consume it.
ask() { # ask "question" [default y|n] -> 0 for yes
  local q="$1" default="${2:-y}" reply hint
  [ "$default" = "y" ] && hint="[Y/n]" || hint="[y/N]"
  if [ "$ASSUME_YES" = "1" ]; then say "  $q $hint y (auto)"; [ "$default" = "y" ]; return; fi
  if [ ! -t 0 ] && [ ! -r /dev/tty ]; then say "  $q $hint $default (no terminal)"; [ "$default" = "y" ]; return; fi
  printf '  %s %s ' "$q" "$hint"
  read -r reply < /dev/tty || reply=""
  reply="$(printf '%s' "${reply:-$default}" | tr '[:upper:]' '[:lower:]')"
  [ "$reply" = "y" ] || [ "$reply" = "yes" ]
}

while [ $# -gt 0 ]; do
  case "$1" in
    --version) VERSION="${2:?--version needs a value}"; shift 2 ;;
    --dir)     INSTALL_DIR="${2:?--dir needs a value}"; shift 2 ;;
    --cpu)     FORCE_CPU=1; shift ;;
    --no-tts)  SKIP_TTS=1; shift ;;
    --yes|-y)  ASSUME_YES=1; shift ;;
    --uninstall) DO_UNINSTALL=1; shift ;;
    # Printed inline rather than read back out of "$0": piped from curl, "$0" is
    # "bash" and there is no file to read the comment header from.
    -h|--help)
      cat <<'HELP'
ebook-audiobook installer (macOS and Linux)

  curl -fsSL https://github.com/denelson1-dot/ebook-audiobook/releases/latest/download/install.sh | bash

Creates a private Python environment under your user data directory, installs
the app and a suitable PyTorch build, checks for Calibre, and adds an
`ebook-audiobook` command. Nothing is installed system-wide.

Options:
  --version X.Y.Z   install a specific release (default: latest)
  --dir PATH        install somewhere other than the default
  --cpu             force the CPU-only PyTorch build (small download)
  --no-tts          skip PyTorch entirely (import books, can't render yet)
  --yes, -y         accept all prompts (for scripted installs)
  --uninstall       remove the app (your books and settings are kept)
  -h, --help        show this
HELP
      exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
done

# --- platform ----------------------------------------------------------------
OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS" in
  Darwin) PLATFORM="macos"; DATA_DIR="$HOME/Library/Application Support/ebook-audiobook" ;;
  Linux)  PLATFORM="linux"; DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/ebook-audiobook" ;;
  *) die "unsupported system: $OS (this installer handles macOS and Linux; use install.ps1 on Windows)" ;;
esac
APP_DIR="${INSTALL_DIR:-$DATA_DIR}"
VENV="$APP_DIR/venv"
BIN_DIR="$HOME/.local/bin"

# --- uninstall ---------------------------------------------------------------
if [ "$DO_UNINSTALL" = "1" ]; then
  step "Uninstalling ebook-audiobook"
  rm -rf "$VENV"
  rm -f "$BIN_DIR/ebook-audiobook"
  rm -f "$HOME/.local/share/applications/ebook-audiobook.desktop"
  rm -f "$HOME/Desktop/ebook-audiobook.desktop"
  ok "program removed"
  say ""
  say "  Your books, settings, and audiobooks were NOT deleted. They're in:"
  say "    $DATA_DIR"
  say "  Delete that folder yourself if you want them gone."
  exit 0
fi

say ""
say "${B}ebook-audiobook installer${N}"
say "${DIM}Turns ebooks you own into narrated audiobooks, entirely offline.${N}"

# --- 1. Python ---------------------------------------------------------------
step "Looking for Python 3.11 or newer"
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)' 2>/dev/null; then
      PYTHON="$(command -v "$candidate")"; break
    fi
  fi
done

if [ -z "$PYTHON" ]; then
  warn "no Python 3.11+ found"
  if [ "$PLATFORM" = "macos" ] && command -v brew >/dev/null 2>&1; then
    if ask "Install Python 3.12 with Homebrew?" y; then
      brew install python@3.12 || die "Homebrew couldn't install Python"
      PYTHON="$(command -v python3.12 || command -v python3)"
    fi
  elif [ "$PLATFORM" = "linux" ] && command -v apt-get >/dev/null 2>&1 && have_sudo; then
    if ask "Install Python with 'sudo apt-get install python3 python3-venv'?" y; then
      apt_install python3 python3-venv python3-pip || true
      PYTHON="$(command -v python3 || true)"
    fi
  fi
fi
[ -n "$PYTHON" ] || die "Python 3.11+ is required. Install it from https://python.org/downloads and re-run this installer."
ok "$($PYTHON -V) at $PYTHON"

# Debian and Ubuntu split venv/ensurepip out of the base python3 package, so a
# stock system Python cannot create a working virtualenv. This is the single most
# common reason a Linux install fails, so handle it properly: offer the one apt
# command that fixes it, and if that isn't possible (no sudo, not Debian-based),
# fall back to bootstrapping pip by hand rather than dead-ending.
NEED_PIP_BOOTSTRAP=0
if ! "$PYTHON" -c 'import venv' >/dev/null 2>&1; then
  warn "Python's 'venv' module is missing"
  if command -v apt-get >/dev/null 2>&1 && have_sudo \
     && ask "Install it with 'sudo apt-get install python3-venv'?" y; then
    apt_install python3-venv || true
  fi
  "$PYTHON" -c 'import venv' >/dev/null 2>&1 \
    || die "Python's venv module is required.
       On Debian/Ubuntu:  sudo apt install python3-venv
       Then re-run this installer."
fi
if ! "$PYTHON" -c 'import ensurepip' >/dev/null 2>&1; then
  warn "Python's 'ensurepip' module is missing (common on Debian/Ubuntu)"
  if command -v apt-get >/dev/null 2>&1 && have_sudo \
     && ask "Install it with 'sudo apt-get install python3-venv'?" y; then
    apt_install python3-venv || true
  fi
  if ! "$PYTHON" -c 'import ensurepip' >/dev/null 2>&1; then
    NEED_PIP_BOOTSTRAP=1
    warn "will install pip into the environment directly instead"
  fi
fi

# --- 2. virtualenv -----------------------------------------------------------
step "Creating a private environment"
say "  ${DIM}$VENV${N}"
mkdir -p "$APP_DIR" || die "couldn't create $APP_DIR"
VPY="$VENV/bin/python"
if [ -x "$VPY" ]; then
  ok "reusing the existing environment (upgrading in place)"
elif [ "$NEED_PIP_BOOTSTRAP" = "1" ]; then
  "$PYTHON" -m venv --without-pip "$VENV" || die "couldn't create a virtualenv at $VENV"
  # Official pip bootstrap, same source pip itself documents.
  GETPIP="$APP_DIR/.get-pip.py"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL -o "$GETPIP" https://bootstrap.pypa.io/get-pip.py || die "couldn't download get-pip.py"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "$GETPIP" https://bootstrap.pypa.io/get-pip.py || die "couldn't download get-pip.py"
  else
    die "need curl or wget to bootstrap pip. Install python3-venv instead: sudo apt install python3-venv"
  fi
  "$VPY" "$GETPIP" --quiet || die "couldn't install pip into the environment"
  rm -f "$GETPIP"
  ok "created (with pip bootstrapped manually)"
else
  "$PYTHON" -m venv "$VENV" || die "couldn't create a virtualenv at $VENV"
  ok "created"
fi
[ -x "$VPY" ] || die "the environment at $VENV looks broken; delete it and re-run"
"$VPY" -m pip install --quiet --upgrade pip setuptools wheel || die "couldn't upgrade pip"

# --- 3. the app --------------------------------------------------------------
step "Installing ebook-audiobook"

resolve_version() {
  # An explicit --version always wins.
  if [ "$VERSION" != "latest" ]; then printf '%s' "$VERSION"; return; fi
  # The published installer has its version baked in (see PINNED_VERSION).
  case "$PINNED_VERSION" in
    __EBAB_*) ;;                       # still the placeholder: fall through
    *) printf '%s' "$PINNED_VERSION"; return ;;
  esac
  # Running an unreleased copy of this script: ask GitHub what's current.
  local tag=""
  if command -v curl >/dev/null 2>&1; then
    tag="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
           | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
  fi
  printf '%s' "${tag#v}"
}

# A checkout next to this script installs from source; that's how CI smoke-tests
# the installer, and how you test a change before cutting a release.
# Piped from curl, BASH_SOURCE is unset and this resolves to the *current
# directory* — so requiring a pyproject.toml alone would happily install whatever
# unrelated Python project the user happened to be standing in. Require that it
# is specifically this project, and that the script was really read from a file.
SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || echo "")"
fi
IS_SOURCE_TREE=0
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/pyproject.toml" ] \
   && [ -d "$SCRIPT_DIR/ebook_audiobook" ] \
   && grep -q '^name = "ebook-audiobook"' "$SCRIPT_DIR/pyproject.toml" 2>/dev/null; then
  IS_SOURCE_TREE=1
fi

if [ "$IS_SOURCE_TREE" = "1" ]; then
  say "  ${DIM}installing from the source tree at $SCRIPT_DIR${N}"
  "$VPY" -m pip install --quiet "$SCRIPT_DIR" || die "install from source failed"
else
  RESOLVED="$(resolve_version)"
  [ -n "$RESOLVED" ] || die "couldn't work out which version to install.
       Pass one explicitly, e.g. --version 1.0.0
       Releases: https://github.com/$REPO/releases"
  WHEEL="ebook_audiobook-${RESOLVED}-py3-none-any.whl"
  WHEEL_URL="https://github.com/$REPO/releases/download/v${RESOLVED}/${WHEEL}"
  say "  ${DIM}$WHEEL_URL${N}"
  # Download into a scratch subdirectory, NOT as a dot-prefixed file: pip parses
  # the package name and version out of a wheel's filename, so ".ebook_audiobook-
  # 1.0.0-…whl" is read as a package literally named "-ebook-audiobook".
  DL_DIR="$APP_DIR/.download"
  mkdir -p "$DL_DIR"
  TMP_WHEEL="$DL_DIR/$WHEEL"

  # Download first, install second, so a private repo (or any HTTP problem) gives
  # a clear message instead of pip's wall of 404 text. Release assets of a
  # private repo are not publicly readable, so fall back to an authenticated
  # fetch via the gh CLI when one is available — that makes the same one-liner
  # work for the repo owner before the project is made public.
  DOWNLOADED=0
  if command -v curl >/dev/null 2>&1 \
     && curl -fsSL -o "$TMP_WHEEL" "$WHEEL_URL" 2>/dev/null; then
    DOWNLOADED=1
  elif command -v wget >/dev/null 2>&1 && wget -qO "$TMP_WHEEL" "$WHEEL_URL" 2>/dev/null; then
    DOWNLOADED=1
  elif command -v gh >/dev/null 2>&1 \
       && gh release download "v${RESOLVED}" -R "$REPO" -p "$WHEEL" -O "$TMP_WHEEL" --clobber >/dev/null 2>&1; then
    say "  ${DIM}(downloaded with your GitHub credentials — the repo isn't public yet)${N}"
    DOWNLOADED=1
  fi

  if [ "$DOWNLOADED" != "1" ]; then
    rm -rf "$DL_DIR"
    die "couldn't download $WHEEL.
       If the release exists, this usually means the repository is still private.
       Check https://github.com/$REPO/releases — and if it is private, either make
       it public or install the wheel by hand:
         <venv>/bin/pip install /path/to/$WHEEL"
  fi

  "$VPY" -m pip install --quiet --upgrade "$TMP_WHEEL" || {
    rm -rf "$DL_DIR"; die "the downloaded package failed to install"; }
  rm -rf "$DL_DIR"
fi
ok "installed $("$VPY" -c 'import importlib.metadata as m; print(m.version("ebook-audiobook"))' 2>/dev/null || echo "")"

# --- 4. PyTorch --------------------------------------------------------------
if [ "$SKIP_TTS" = "1" ]; then
  step "Skipping the speech engine (--no-tts)"
  warn "you can import books, but rendering audio needs the engine"
  say "  add it later with: $VENV/bin/pip install 'ebook-audiobook[tts]'"
else
  step "Setting up the speech engine"
  TORCH_INDEX=""
  if [ "$FORCE_CPU" = "1" ]; then
    DEVICE_DESC="CPU only (forced with --cpu)"
    [ "$PLATFORM" = "linux" ] && TORCH_INDEX="https://download.pytorch.org/whl/cpu"
    SIZE="about 250 MB"
  elif [ "$PLATFORM" = "macos" ]; then
    if [ "$ARCH" = "arm64" ]; then
      DEVICE_DESC="Apple Silicon GPU (MPS) — a novel takes several hours"
    else
      DEVICE_DESC="Intel Mac, CPU only — a novel can take a very long time"
    fi
    SIZE="about 250 MB"
  elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    GPU="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo "NVIDIA GPU")"
    DEVICE_DESC="$GPU via CUDA — a novel takes roughly 2-3 hours"
    TORCH_INDEX="https://download.pytorch.org/whl/cu124"
    SIZE="about 2.5 GB"
  else
    DEVICE_DESC="no GPU detected, CPU only — a novel can take many hours"
    TORCH_INDEX="https://download.pytorch.org/whl/cpu"
    SIZE="about 250 MB"
  fi

  say "  Detected: ${B}${DEVICE_DESC}${N}"
  say "  Download: ${B}${SIZE}${N}"
  if ask "Download and install the speech engine now?" y; then
    if [ -n "$TORCH_INDEX" ]; then
      "$VPY" -m pip install --quiet torch torchaudio --index-url "$TORCH_INDEX" \
        || die "PyTorch install failed. Re-run with --cpu, or install torch manually."
    else
      "$VPY" -m pip install --quiet torch torchaudio \
        || die "PyTorch install failed."
    fi
    "$VPY" -m pip install --quiet "ebook-audiobook[tts]" 2>/dev/null \
      || "$VPY" -m pip install --quiet chatterbox-tts 'setuptools<81' \
      || warn "the Chatterbox engine didn't install; run '$VENV/bin/pip install chatterbox-tts' to retry"
    ok "speech engine ready"
    say "  ${DIM}The ~1 GB voice model downloads the first time you render.${N}"
  else
    warn "skipped — add it later with: $VENV/bin/pip install 'ebook-audiobook[tts]'"
  fi
fi

# --- 5. Calibre --------------------------------------------------------------
step "Checking for Calibre (needed to read ebook files)"
if "$VPY" -c 'from ebook_audiobook import tools; raise SystemExit(0 if tools.ebook_convert_path() else 1)' 2>/dev/null; then
  ok "Calibre found"
else
  warn "Calibre is not installed"
  INSTALLED_CALIBRE=0
  if [ "$PLATFORM" = "macos" ] && command -v brew >/dev/null 2>&1; then
    if ask "Install it now with 'brew install --cask calibre'?" y; then
      brew install --cask calibre && INSTALLED_CALIBRE=1 || warn "Homebrew install failed"
    fi
  elif [ "$PLATFORM" = "linux" ] && command -v apt-get >/dev/null 2>&1 && have_sudo; then
    if ask "Install it now with 'sudo apt-get install calibre'?" y; then
      apt_install calibre && INSTALLED_CALIBRE=1 || true
    fi
  fi
  if [ "$INSTALLED_CALIBRE" = "1" ]; then
    ok "Calibre installed"
  else
    warn "install Calibre before converting a book:"
    if [ "$PLATFORM" = "macos" ]; then
      say "      brew install --cask calibre"
    else
      say "      sudo apt install calibre"
    fi
    say "      ${DIM}or download it from https://calibre-ebook.com/download${N}"
  fi
fi

# --- 6. launchers ------------------------------------------------------------
step "Creating the launcher"
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/ebook-audiobook" <<LAUNCHER
#!/usr/bin/env bash
# Generated by the ebook-audiobook installer.
exec "$VENV/bin/ebook-audiobook" "\$@"
LAUNCHER
chmod +x "$BIN_DIR/ebook-audiobook"
ok "command: ebook-audiobook"

# A self-contained uninstaller, so removing the app never requires re-fetching
# this script or remembering which directories it touched.
cat > "$BIN_DIR/ebook-audiobook-uninstall" <<UNINSTALL
#!/usr/bin/env bash
# Generated by the ebook-audiobook installer.
set -euo pipefail
echo "Removing the ebook-audiobook program from:"
echo "  $VENV"
rm -rf "$VENV"
rm -f "$HOME/.local/share/applications/ebook-audiobook.desktop"
rm -f "$BIN_DIR/ebook-audiobook"
echo
echo "Done. Your books, settings, and audiobooks were kept, in:"
echo "  $DATA_DIR"
echo "Delete that folder yourself if you want them gone too."
rm -f "$BIN_DIR/ebook-audiobook-uninstall"
UNINSTALL
chmod +x "$BIN_DIR/ebook-audiobook-uninstall"
ok "command: ebook-audiobook-uninstall"

if [ "$PLATFORM" = "linux" ]; then
  DESKTOP_DIR="$HOME/.local/share/applications"
  mkdir -p "$DESKTOP_DIR"
  cat > "$DESKTOP_DIR/ebook-audiobook.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=ebook-audiobook
Comment=Turn ebooks you own into narrated audiobooks
Exec=$BIN_DIR/ebook-audiobook web
Icon=media-optical-audio
Terminal=true
Categories=AudioVideo;Audio;Utility;
DESKTOP
  chmod +x "$DESKTOP_DIR/ebook-audiobook.desktop"
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
  ok "application menu entry"
fi

# PATH advice, only when it's actually needed.
NEEDS_PATH=0
case ":$PATH:" in *":$BIN_DIR:"*) ;; *) NEEDS_PATH=1 ;; esac

# --- done --------------------------------------------------------------------
step "Verifying the install"
if "$VENV/bin/ebook-audiobook" check --engine fake >/dev/null 2>&1; then
  ok "all required components are working"
else
  warn "some checks failed — run 'ebook-audiobook check' for details"
fi

say ""
say "${GRN}${B}Installed.${N}"
say ""
say "  Start it with:   ${B}ebook-audiobook${N}"
say "  Your books live in: ${DIM}$DATA_DIR${N}"
say "  Check setup:     ${DIM}ebook-audiobook check${N}"
say "  Uninstall:       ${DIM}ebook-audiobook-uninstall${N} (or re-run this script with --uninstall)"
say ""
if [ "$NEEDS_PATH" = "1" ]; then
  warn "$BIN_DIR isn't on your PATH yet."
  say "  Add this line to your ~/.bashrc or ~/.zshrc, then open a new terminal:"
  say "      ${B}export PATH=\"\$HOME/.local/bin:\$PATH\"${N}"
  say "  Until then, start it with: ${B}$VENV/bin/ebook-audiobook${N}"
  say ""
fi
