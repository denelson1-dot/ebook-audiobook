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
#   --gpu, --cuda      force the CUDA build when the GPU probe comes up empty
#   --rocm, --amd      force the AMD ROCm build (Linux + Radeon)
#   --cuda128 / --cuda126  pick a specific CUDA build (see --help)
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
FORCE_GPU=0
FORCE_ROCM=0
FORCE_CUDA=""
SKIP_TTS=0
DO_UNINSTALL=0
# Set only when an AMD card needs it; the launcher exports it when non-empty.
HSA_OVERRIDE=""
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

# --- BEGIN macos-metal-gate (extracted verbatim by CI; keep the markers) ------
macos_version() {
  sw_vers -productVersion 2>/dev/null || echo "unknown"
}

# Can this Mac use its GPU for rendering?
#
# PyTorch's Metal (MPS) backend requires macOS 12.3 or newer, on Apple Silicon.
# Below that the same wheel installs fine and then quietly runs on the CPU, so
# checking here is the difference between telling someone their render will take
# three hours and letting them find out it takes thirty.
macos_supports_metal() {
  ver="$(macos_version)"
  major="${ver%%.*}"
  rest="${ver#*.}"
  minor="${rest%%.*}"
  case "$major" in ''|*[!0-9]*) return 1 ;; esac
  [ "$major" -gt 12 ] && return 0
  [ "$major" -lt 12 ] && return 1
  case "$minor" in ''|*[!0-9]*) return 1 ;; esac
  [ "$minor" -ge 3 ]
}
# --- END macos-metal-gate -----------------------------------------------------

# Is there an AMD GPU that ROCm can drive?
#
# The kernel's DRM nodes are the authority: PCI vendor 0x1002 is AMD, and that
# is true whether or not ROCm is installed — which matters, because we want to
# offer the ROCm build to someone who hasn't got it yet. `rocminfo` is consulted
# only for the architecture name, which decides whether the card needs
# HSA_OVERRIDE_GFX_VERSION to be visible at all.
#
# Deliberately skips integrated graphics: nearly every AMD *CPU* also presents a
# Radeon iGPU, and steering those users to a 2 GB ROCm download that then renders
# slower than their CPU would be a worse default than the CPU build. A discrete
# card has its own VRAM, so that is what we test for.
detect_amd() {
  [ "$PLATFORM" = "linux" ] || return 1
  found=1
  for card in /sys/class/drm/card[0-9]*; do
    [ -r "$card/device/vendor" ] || continue
    [ "$(cat "$card/device/vendor" 2>/dev/null)" = "0x1002" ] || continue
    # mem_info_vram_total exists only for a real VRAM pool (discrete cards).
    if [ -r "$card/device/mem_info_vram_total" ]; then
      vram="$(cat "$card/device/mem_info_vram_total" 2>/dev/null || echo 0)"
      # Integrated parts carve out a small aperture; require >2 GB to be sure.
      case "$vram" in ''|*[!0-9]*) continue ;; esac
      [ "$vram" -gt 2147483648 ] || continue
    else
      continue
    fi
    found=0
    break
  done
  [ "$found" = "0" ] || return 1
  GPU_NAME="AMD Radeon"
  if command -v rocminfo >/dev/null 2>&1; then
    GFX_ARCH="$(rocminfo 2>/dev/null | grep -om1 'gfx[0-9a-f]*' | head -1)"
  fi
  return 0
}

# Cards ROCm won't enumerate without being told which architecture to pretend to
# be. These are ordinary consumer Radeons, so leaving a user to discover this
# from a forum thread is not acceptable. Echoes the value, or nothing.
hsa_override_for() {
  case "$1" in
    gfx1031|gfx1032|gfx1033|gfx1034|gfx1035|gfx1036) echo "10.3.0" ;;
    gfx1101|gfx1102|gfx1103) echo "11.0.0" ;;
    *) echo "" ;;
  esac
}

# Is there an NVIDIA GPU that CUDA can actually use?
#
# nvidia-smi is the friendly answer — it hands us the model name — but it is not
# the authority. It talks to NVML, which fails independently of CUDA: upgrade the
# driver without rebooting and nvidia-smi dies with "Driver/library version
# mismatch" on a machine where torch still runs on the GPU perfectly well.
# Treating that as "no GPU" silently costs the user a 10x slower render, so when
# NVML is unhappy, ask a lower-level question instead: is the kernel driver
# loaded (a device node exists) and is the CUDA userspace library installed?
# Sets GPU_NAME on success, and NVML_BROKEN=1 if we got there the hard way.
detect_nvidia() {
  if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    GPU_NAME="$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"
    [ -n "$GPU_NAME" ] || GPU_NAME="NVIDIA GPU"
    # One line per GPU, e.g. "8.6". Decides which CUDA build has kernels for
    # this card — CUDA 12.8 dropped everything below sm_70. Supported since
    # driver 510; older drivers simply report nothing and we fall back to
    # matching the model name.
    COMPUTE_CAPS="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null \
                    | tr -d ' ' | grep -E '^[0-9]+\.[0-9]+$' | paste -sd, - || true)"
    return 0
  fi
  if [ -e /dev/nvidiactl ] || [ -e /dev/nvidia0 ]; then
    if ldconfig -p 2>/dev/null | grep -q 'libcuda\.so\.1'; then
      GPU_NAME="NVIDIA GPU"
      NVML_BROKEN=1
      return 0
    fi
  fi
  return 1
}

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
    --gpu|--cuda) FORCE_GPU=1; shift ;;
    --rocm|--amd) FORCE_ROCM=1; shift ;;
    --cuda126) FORCE_CUDA="cuda126"; shift ;;
    --cuda128) FORCE_CUDA="cuda128"; shift ;;
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
  --gpu, --cuda     force the CUDA PyTorch build, even if this script's GPU
                    probe came up empty (e.g. a broken nvidia-smi)
  --rocm, --amd     force the AMD ROCm build (Linux + Radeon)
  --cuda128         force the CUDA 12.8 build (RTX 20-series and newer)
  --cuda126         force the CUDA 12.6 build (GTX 900/1000-series and older)
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
  # Keep this list identical to the generated ebook-audiobook-uninstall below.
  # The banner tells people the two are equivalent, and a divergence here is
  # invisible until someone is left with a launcher that bounces once and dies
  # because the program it points at is gone.
  rm -rf "$VENV"
  rm -f "$BIN_DIR/ebook-audiobook"
  rm -f "$BIN_DIR/ebook-audiobook-uninstall"
  rm -f "$HOME/.local/share/applications/ebook-audiobook.desktop"
  rm -f "$HOME/.local/share/icons/hicolor"/*/apps/ebook-audiobook.png
  rm -f "$HOME/.local/share/icons/hicolor/scalable/apps/ebook-audiobook.svg"
  rm -rf "$HOME/Applications/ebook-audiobook.app"
  rm -rf "$DATA_DIR/browser-profile"
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
  say "  add it later with:"
  say "    $VENV/bin/pip install torch torchaudio chatterbox-tts 'setuptools<81'"
else
  step "Setting up the speech engine"
  TORCH_INDEX=""
  GPU_NAME=""
  GFX_ARCH=""
  COMPUTE_CAPS=""
  INTEL_MAC=0
  NVML_BROKEN=0
  MAC_NOTE=""
  # macOS is settled first, and deliberately ahead of --cpu/--gpu, because it is
  # the one platform where those flags cannot change what gets downloaded: there
  # is exactly one Mac wheel on PyPI, it already contains Metal (MPS) support,
  # and no CUDA build exists for any Mac. Sending --gpu down the CUDA branch
  # asked pip for a cu124 wheel that has never existed for macOS, so the install
  # failed outright on the flag that was supposed to help.
  if [ "$PLATFORM" = "macos" ]; then
    SIZE="about 250 MB"
    if [ "$ARCH" = "arm64" ]; then
      if macos_supports_metal; then
        DEVICE_DESC="Apple Silicon GPU (Metal) — a novel takes a few hours"
      else
        DEVICE_DESC="Apple Silicon, but macOS $(macos_version) is too old for Metal — CPU only"
        MAC_NOTE="Metal needs macOS 12.3 or newer; updating macOS makes renders several times faster."
      fi
    else
      # PyTorch stopped shipping macOS x86_64 wheels after 2.2.2, so there is
      # no build of the speech engine an Intel Mac can install at all. This was
      # already true before the version bump — the old path simply failed with
      # pip's "no matching distribution" instead of saying so.
      INTEL_MAC=1
      DEVICE_DESC="Intel Mac — the speech engine can't be installed"
      MAC_NOTE="PyTorch stopped building for Intel Macs after 2.2.2. Everything except rendering works; on an Apple Silicon Mac it all does."
    fi
    if [ "$FORCE_CPU" = "1" ]; then
      MAC_NOTE="There is only one Mac build of PyTorch, so --cpu doesn't change this download. To keep a render off the GPU, run the app with EBAB_DEVICE=cpu."
    elif [ "$FORCE_GPU" = "1" ]; then
      MAC_NOTE="--gpu means CUDA, which no Mac has. Apple Silicon is already used automatically via Metal."
    fi
  fi

  # Which PyTorch build belongs here is decided by the app, not by this script.
  # The index URLs used to be written out in six places across the two
  # installers with no single source of truth, so a bump could land in some and
  # not others. ebook_audiobook.torchbuild is importable by now because the app
  # was installed in section 3, above.
  VENDOR=""
  if [ "$PLATFORM" != "macos" ]; then
    if detect_nvidia; then VENDOR="nvidia"; elif detect_amd; then VENDOR="amd"; fi
  fi
  FORCED=""
  [ "$FORCE_CPU" = "1" ]  && FORCED="cpu"
  [ "$FORCE_GPU" = "1" ]  && FORCED="gpu"
  [ "$FORCE_ROCM" = "1" ] && FORCED="rocm"
  [ -n "$FORCE_CUDA" ]    && FORCED="$FORCE_CUDA"

  TORCH_ID=""; TORCH_INDEX=""; TORCH_LABEL=""; SIZE=""; TORCH_NOTE=""; TORCH_PIN=""
  CHATTERBOX_PIN="$("$VPY" -c 'from ebook_audiobook.torchbuild import CHATTERBOX_PIN; print(CHATTERBOX_PIN)' 2>/dev/null || echo "chatterbox-tts")"
  CHATTERBOX_DEPS="$("$VPY" -c 'from ebook_audiobook.torchbuild import CHATTERBOX_DEPS; print(" ".join(CHATTERBOX_DEPS))' 2>/dev/null || true)"
  TORCH_VARS="$("$VPY" -m ebook_audiobook.torchbuild --platform "$PLATFORM" \
      --arch "$ARCH" --vendor "$VENDOR" --forced "$FORCED" \
      --gpu-name "$GPU_NAME" --compute-caps "$COMPUTE_CAPS" 2>/dev/null || true)"
  # Read into variables rather than eval: the values contain spaces, and this
  # script must not execute anything the subprocess happens to print.
  while IFS='=' read -r _k _v; do
    case "$_k" in
      EBAB_TORCH_ID)    TORCH_ID="$_v" ;;
      EBAB_TORCH_INDEX) TORCH_INDEX="$_v" ;;
      EBAB_TORCH_LABEL) TORCH_LABEL="$_v" ;;
      EBAB_TORCH_SIZE)  SIZE="$_v" ;;
      EBAB_TORCH_NOTE)  TORCH_NOTE="$_v" ;;
      EBAB_TORCH_PIN)   TORCH_PIN="$_v" ;;
    esac
  done <<TORCHVARS
$TORCH_VARS
TORCHVARS

  if [ -z "$TORCH_ID" ]; then
    # Only reachable if the app failed to import (e.g. --version pinned to a
    # release predating this module). The CPU build always works; say so
    # loudly rather than guessing at hardware.
    warn "couldn't ask the app which PyTorch build to use; falling back to CPU-only"
    TORCH_ID="cpu"; TORCH_INDEX="https://download.pytorch.org/whl/cpu"
    SIZE="about 250 MB"; TORCH_LABEL="CPU only"; TORCH_PIN="2.9.1"
  fi

  # The human-facing line: the module supplies the facts, this supplies the
  # phrasing, including the GPU's own name and what the wait will feel like.
  case "$TORCH_ID" in
    cu128|cu126)
           DEVICE_DESC="${GPU_NAME:-NVIDIA GPU} via ${TORCH_LABEL} — a novel takes roughly 2-3 hours" ;;
    rocm)  DEVICE_DESC="${GPU_NAME:-AMD Radeon} via ROCm — a novel takes roughly 3-4 hours" ;;
    mac)   : ;;  # already set above, with the Metal/Intel distinction
    *)     if [ -n "$FORCED" ]; then
             DEVICE_DESC="CPU only (forced with --$FORCED)"
           else
             DEVICE_DESC="no GPU detected, CPU only — a novel can take many hours"
           fi ;;
  esac

  say "  Detected: ${B}${DEVICE_DESC}${N}"
  say "  Download: ${B}${SIZE}${N}"
  [ -n "$MAC_NOTE" ] && say "  ${DIM}${MAC_NOTE}${N}"
  # A Radeon whose architecture ROCm doesn't list needs one environment
  # variable to be visible at all. Work it out here and bake it into the
  # launcher, so the user never has to find this out from a forum thread.
  HSA_OVERRIDE=""
  if [ "$TORCH_ID" = "rocm" ] && [ -n "$GFX_ARCH" ]; then
    HSA_OVERRIDE="$(hsa_override_for "$GFX_ARCH")"
    if [ -n "$HSA_OVERRIDE" ]; then
      say "  ${DIM}$GFX_ARCH needs HSA_OVERRIDE_GFX_VERSION=$HSA_OVERRIDE; the${N}"
      say "  ${DIM}launcher will set it for you.${N}"
    fi
  elif [ "$TORCH_ID" = "rocm" ]; then
    say "  ${DIM}If ROCm reports no device later, install rocminfo and re-run,${N}"
    say "  ${DIM}or set HSA_OVERRIDE_GFX_VERSION (10.3.0 for RX 6000, 11.0.0 for RX 7000).${N}"
  fi
  [ "$NVML_BROKEN" = "1" ] && \
    say "  ${DIM}(nvidia-smi is broken on this machine — usually a driver upgrade${N}"
  [ "$NVML_BROKEN" = "1" ] && \
    say "  ${DIM} without a reboot — but the driver and CUDA libraries are present.)${N}"
  if [ "$FORCE_CPU" != "1" ] && [ "$FORCE_GPU" != "1" ] && [ -z "$GPU_NAME" ] \
     && [ "$PLATFORM" = "linux" ]; then
    say "  ${DIM}If this machine does have an NVIDIA GPU, re-run with --gpu.${N}"
  fi
  if [ "$INTEL_MAC" = "1" ]; then
    warn "skipping the speech engine — no PyTorch build exists for Intel Macs"
    say "  ${DIM}You can still import books, browse chapters and manage voices.${N}"
  elif ask "Download and install the speech engine now?" y; then
    # Three pip commands, in this order, and the order is load-bearing.
    #
    # 1. torch, pinned exactly, from the chosen index. The pin must be exact:
    #    PyPI's torch is far ahead of the pinned indexes and PEP 440 ranks a
    #    plain 2.13.0 above 2.9.1+cu128, so a floor would quietly fetch the
    #    default CUDA build from PyPI and undo the choice entirely.
    # 2. Chatterbox with --no-deps. It declares torch==2.6.0, which has no
    #    kernels for current GPUs; letting it resolve would drag the pinned
    #    build back down.
    # 3. Chatterbox's dependencies, curated by us (see torchbuild.py), with the
    #    torch pins repeated so nothing in that list can replace the build.
    ENGINE_MANUAL="$VENV/bin/pip install torch==$TORCH_PIN torchaudio==$TORCH_PIN"
    ENGINE_FAILED=0
    if [ -n "$TORCH_INDEX" ]; then
      IDX="--index-url $TORCH_INDEX --extra-index-url https://pypi.org/simple"
    else
      IDX=""
    fi
    # shellcheck disable=SC2086  # IDX is a deliberate multi-word flag list
    "$VPY" -m pip install --quiet $IDX "torch==$TORCH_PIN" "torchaudio==$TORCH_PIN" \
      || ENGINE_FAILED=1
    if [ "$ENGINE_FAILED" = "0" ]; then
      "$VPY" -m pip install --quiet --no-deps "$CHATTERBOX_PIN" || ENGINE_FAILED=1
    fi
    if [ "$ENGINE_FAILED" = "0" ]; then
      # shellcheck disable=SC2086
      "$VPY" -m pip install --quiet $IDX "torch==$TORCH_PIN" "torchaudio==$TORCH_PIN" \
          $CHATTERBOX_DEPS || ENGINE_FAILED=1
    fi
    [ "$ENGINE_FAILED" = "0" ] || die "the speech engine failed to install. Re-run with --cpu, or by hand:
         $ENGINE_MANUAL"

    # Report the build that actually landed. The whole bug above was invisible
    # precisely because nothing ever said which torch you ended up with.
    TORCH_BUILD="$("$VPY" -c 'import torch; print(torch.__version__)' 2>/dev/null || true)"
    ok "speech engine ready${TORCH_BUILD:+ (torch $TORCH_BUILD)}"
    say "  ${DIM}The ~1 GB voice model downloads the first time you render.${N}"
  else
    warn "skipped — add it later by re-running this installer."
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
${HSA_OVERRIDE:+export HSA_OVERRIDE_GFX_VERSION=$HSA_OVERRIDE
}exec "$VENV/bin/ebook-audiobook" "\$@"
LAUNCHER
chmod +x "$BIN_DIR/ebook-audiobook"
ok "command: ebook-audiobook"

# Where the wheel keeps the icons, so the desktop entry and the .app bundle can
# point at real files rather than a stock system icon.
ASSETS="$("$VPY" -c 'import ebook_audiobook, pathlib; print(pathlib.Path(ebook_audiobook.__file__).parent / "assets")' 2>/dev/null || true)"
# What actually got installed. $VERSION is the *requested* version and is
# usually the literal "latest", which macOS will not accept as a bundle version.
# CFBundleVersion must be period-separated integers, and __version__ degrades to
# "0.0.0+unknown" when the dist metadata can't be read — which macOS rejects.
APP_VERSION="$("$VPY" -c 'import ebook_audiobook; print(ebook_audiobook.__version__)' 2>/dev/null || echo "")"
case "$APP_VERSION" in
  ''|*[!0-9.]*) APP_VERSION="1.0" ;;
esac

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
rm -f "$HOME/.local/share/icons/hicolor"/*/apps/ebook-audiobook.png
rm -f "$HOME/.local/share/icons/hicolor/scalable/apps/ebook-audiobook.svg"
rm -rf "$HOME/Applications/ebook-audiobook.app"
# The app window's own browser profile. Regenerated on next launch; holds no
# books, settings or audiobooks, only Chromium's window-size cache.
rm -rf "$DATA_DIR/browser-profile"
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
  # Install the icon into the hicolor theme, which is where the panel, the
  # window manager and the application menu all look it up by name.
  if [ -n "$ASSETS" ] && [ -d "$ASSETS" ]; then
    ICON_DIR="$HOME/.local/share/icons/hicolor"
    for SIZE in 16 24 32 48 64 128 256 512; do
      if [ -f "$ASSETS/icon-$SIZE.png" ]; then
        mkdir -p "$ICON_DIR/${SIZE}x${SIZE}/apps"
        cp "$ASSETS/icon-$SIZE.png" "$ICON_DIR/${SIZE}x${SIZE}/apps/ebook-audiobook.png"
      fi
    done
    if [ -f "$ASSETS/icon.svg" ]; then
      mkdir -p "$ICON_DIR/scalable/apps"
      cp "$ASSETS/icon.svg" "$ICON_DIR/scalable/apps/ebook-audiobook.svg"
    fi
    command -v gtk-update-icon-cache >/dev/null 2>&1 \
      && gtk-update-icon-cache -qtf "$ICON_DIR" 2>/dev/null || true
    ok "application icon"
  fi

  DESKTOP_DIR="$HOME/.local/share/applications"
  mkdir -p "$DESKTOP_DIR"
  # Terminal=false: the app opens its own window and, when the desktop has a
  # tray, keeps running there after that window is closed. There is no longer a
  # terminal the user has to leave open, so putting one on screen would be
  # showing them a window whose only content is log output.
  #
  # StartupWMClass matches the --class the app window is launched with, which is
  # what makes the window group under this icon rather than the browser's.
  cat > "$DESKTOP_DIR/ebook-audiobook.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=ebook-audiobook
Comment=Turn ebooks you own into narrated audiobooks
Exec=$BIN_DIR/ebook-audiobook web
Icon=ebook-audiobook
Terminal=false
StartupWMClass=ebook-audiobook
StartupNotify=true
Categories=AudioVideo;Audio;
DESKTOP
  chmod +x "$DESKTOP_DIR/ebook-audiobook.desktop"
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
  ok "application menu entry"
fi

if [ "$PLATFORM" = "macos" ]; then
  # Until now macOS got only the CLI: no Dock icon, no Spotlight entry, no way
  # to start this without opening Terminal first. A bundle is the minimum that
  # makes it an application — a plist, a shell stub, and an icon.
  MAC_APP="$HOME/Applications/ebook-audiobook.app"
  mkdir -p "$MAC_APP/Contents/MacOS" "$MAC_APP/Contents/Resources"
  cat > "$MAC_APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>              <string>ebook-audiobook</string>
  <key>CFBundleDisplayName</key>       <string>ebook-audiobook</string>
  <key>CFBundleIdentifier</key>        <string>io.github.denelson1.ebook-audiobook</string>
  <key>CFBundleVersion</key>           <string>$APP_VERSION</string>
  <key>CFBundleShortVersionString</key><string>$APP_VERSION</string>
  <key>CFBundlePackageType</key>       <string>APPL</string>
  <key>CFBundleExecutable</key>        <string>ebook-audiobook</string>
  <key>CFBundleIconFile</key>          <string>icon</string>
  <key>NSHighResolutionCapable</key>   <true/>
  <key>CFBundleInfoDictionaryVersion</key> <string>6.0</string>
  <key>LSApplicationCategoryType</key> <string>public.app-category.productivity</string>
  <!-- Menu-bar app: no Dock tile reserved at launch. The running process sets
       the same policy itself (see desktop/tray.py), because by then it has
       exec'd out of this bundle and no longer reads this file. -->
  <key>LSUIElement</key>               <true/>
</dict>
</plist>
PLIST
  # The stub checks before it execs. A Finder-launched process has no terminal,
  # so without this an uninstalled or half-installed app just bounces once in
  # the Dock and dies with the reason buried in the unified log.
  cat > "$MAC_APP/Contents/MacOS/ebook-audiobook" <<MACSTUB
#!/usr/bin/env bash
# Generated by the ebook-audiobook installer.
BIN="$BIN_DIR/ebook-audiobook"
if [ ! -x "\$BIN" ]; then
  osascript -e 'display alert "ebook-audiobook is not installed" message "The program this app points at is missing. Re-run the installer to fix it." as critical' >/dev/null 2>&1
  exit 1
fi
exec "\$BIN" web
MACSTUB
  chmod +x "$MAC_APP/Contents/MacOS/ebook-audiobook"
  if [ -n "$ASSETS" ] && [ -f "$ASSETS/icon.icns" ]; then
    cp "$ASSETS/icon.icns" "$MAC_APP/Contents/Resources/icon.icns"
  else
    warn "no icon.icns found; the app will use the generic bundle icon"
  fi
  # Bump the bundle's mtime or Finder and Dock keep showing the previous icon.
  touch "$MAC_APP"
  ok "application: $MAC_APP"
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
