# Changelog

## Unreleased

**Apple Silicon is properly supported.** It was nominally handled and would have
broken in practice: PyTorch's Metal backend doesn't implement every operator, and
without `PYTORCH_ENABLE_MPS_FALLBACK` the first uncovered one ends the render —
possibly an hour in, since which operators get hit depends on the text. Metal
memory is now released on unload, macOS below 12.3 (where Metal doesn't work) is
detected and explained by the installer, and `--gpu` on a Mac no longer asks pip
for a CUDA wheel that has never existed for macOS.

- **A GPU running out of memory no longer costs the whole render.** VRAM pressure
  isn't constant across a book, so an out-of-memory now retries with the cache
  flushed and then moves to the CPU to finish, rather than failing the job.
  Cards under 4 GB are called out before the render starts.
- **`EBAB_DEVICE=cuda|mps|cpu`** forces the device when the automatic choice is
  wrong.
- **Queued jobs could be lost.** The web worker exits when idle and restarts on
  demand; a job submitted in the instant it was exiting sat in the queue forever,
  leaving the page stuck on a stage that never advanced and a Stop button that
  did nothing, recoverable only by restarting.
- **Deleting a job now frees the space it claimed to.** The copy of the ebook in
  `imports/` was never removed, and every book uploaded through the browser was
  stored twice — the staging copy was never cleaned up.
- **The disk-space warning checked the wrong disk.** Free space was measured on
  the data folder's volume, but the finished `.m4b` goes to the library folder,
  which for Plex users is very often a different drive.
- The final ffmpeg encode had a flat one-hour timeout, which a long book could
  exceed — discarding the entire render at the last step. It now scales with the
  audio length, and a timeout no longer leaves a truncated `.m4b` looking
  finished.
- A locked output file (open in a player, or being scanned by Plex) and a full
  disk now explain themselves and say that the render resumes rather than
  starting over.
- `check` suggested `pip install 'ebook-audiobook[tts]'`, which could never work,
  and `preview` printed a field a preview never sets — showing `None`, or a
  previous render's `.m4b`.
- `__version__` was hard-coded at `0.1.0` against a 1.0.2 release. It's read from
  the installed metadata now.

## 1.0.2

Every fix here is in the installers. The app itself is unchanged.

- **The CPU-only install was never actually CPU-only.** PyTorch and Chatterbox
  were resolved in two separate pip commands. Chatterbox pins an exact torch
  version, so the second command downgraded the torch the first had just chosen
  — and because no package index was pinned on it, pip fetched the replacement
  from PyPI, silently swapping the 250 MB CPU build for the default CUDA one.
  A `--cpu` install advertised as "about 250 MB" landed 6.4 GB on disk. Both are
  now resolved in a single command against the chosen index.
- **A working GPU could be missed entirely.** The CUDA probe trusted
  `nvidia-smi` alone, but that talks to NVML, which fails independently of CUDA
  — upgrade the driver without rebooting and it reports a version mismatch on a
  machine where PyTorch drives the GPU perfectly well. Such machines were sent
  down the CPU path, making renders roughly 10x slower. The probe now falls back
  to checking for the kernel driver and CUDA libraries, and says so when it does.
- **Added `--gpu` / `-Gpu`** to force the CUDA build. `--cpu` had no counterpart,
  so a bad probe result could not be overridden.
- The installers now print which torch build actually landed. Both bugs above
  were invisible because nothing ever said.
- Dropped `pip install "ebook-audiobook[tts]"`, which could never succeed — the
  name is not registered on PyPI, so it always failed into its fallback with the
  error discarded. It also meant that whoever registered that name first would
  have had their code installed into every user's environment.
- The Linux application-menu entry no longer declares two main categories, which
  could list the app twice in the menu.

## 1.0.1

- The installers now download the wheel themselves before handing it to pip, so
  a failed download produces a clear explanation instead of pip's 404 output.
- Added an authenticated fallback via the `gh` CLI. Release assets of a private
  repository are not publicly readable, so the one-line installer could not work
  at all while the project is private; it now does, for anyone with access.

## 1.0.0

First packaged release. The app itself worked before this; the point of 1.0 is
that anyone can now install and run it on Windows, macOS, or Linux without
building it from source.

### Installation

- **One-line installers** for macOS/Linux (`install.sh`) and Windows
  (`install.ps1`), published with each GitHub Release. They create a private
  virtualenv, pick the PyTorch build that matches the machine (asking before a
  2.5 GB CUDA download), offer to install Calibre through winget/brew/apt, and
  create a launcher command plus a Start Menu or application-menu entry.
- **ffmpeg is now bundled** via `imageio-ffmpeg`. A system ffmpeg is still
  preferred when present. One less thing to install.
- **Matching uninstallers** that remove the program and never touch a user's
  books, settings, or finished audiobooks.
- Debian/Ubuntu systems missing `ensurepip` — the most common Linux install
  failure — are detected and handled instead of dead-ending.

### Cross-platform correctness

- **Data now lives in the per-user OS directory** (`%LOCALAPPDATA%`,
  `~/Library/Application Support`, `~/.local/share`) so it survives upgrades and
  reinstalls. An existing source checkout with a `local-data/` folder keeps using
  it, so no existing setup is disturbed.
- **Calibre is found where its installers actually put it** — inside
  `/Applications/calibre.app` on macOS and `Program Files\Calibre2` on Windows,
  neither of which is necessarily on `PATH`.
- **Subprocess output is decoded as UTF-8**, not the locale code page. An ebook
  with a curly quote or an accented author name no longer risks a
  `UnicodeDecodeError` on Windows.
- **Console output is UTF-8**, so printing a book title can't crash the CLI on a
  cp1252 Windows console.
- **No console windows flash** when the app shells out after being launched from
  a desktop shortcut.
- **Windows filename rules are respected**: reserved device names (`CON`, `NUL`,
  `COM1`, …) are escaped, and the full `Author/Series/Title` path is kept under
  the 260-character `MAX_PATH` limit.
- **Uploads with non-ASCII filenames keep their extension.** Previously a book
  named `Война и мир.epub` lost it and failed to import.

### Packaging

- **Fixed: Jinja templates and static assets were missing from the built wheel.**
  An installed copy would have returned an error for every page. CI now fails the
  build if they're ever absent again.
- Renamed the top-level package `app` → `ebook_audiobook`; a package called `app`
  in site-packages collides with anything else that does the same.
- `ffprobe` is no longer required. Chapter markers are verified with
  `ffmpeg -f ffmetadata` and duration via mutagen, because the bundled ffmpeg
  wheel ships no ffprobe and "the optional checker is missing" must never be
  reported as "your audiobook is broken".
- Served by waitress instead of the Flask development server.
- One entry point does everything: `ebook-audiobook` opens the UI, with `check`,
  `paths`, `convert`, `preview`, `render`, and `list` subcommands.
- New `ebook-audiobook paths` command showing exactly where data is stored.

### Interface

- Missing prerequisites now appear as a banner with the correct install command
  for your platform, instead of surfacing as a failed import later on.
- Removed an unused template.

### Testing

- CI runs the full suite on Windows, macOS, and Linux against Python 3.11, 3.12,
  and 3.13.
- Added smoke tests that run against an *installed wheel*: a real EPUB-to-`.m4b`
  render with tag and chapter verification, and an HTTP check that every page and
  static asset is actually served.
- CI installs no ffmpeg, so the bundled-binary path — what most users will
  actually run — is what gets tested.
- Both installers are executed and then uninstalled on all three OSes on every
  push.
