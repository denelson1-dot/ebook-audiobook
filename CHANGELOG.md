# Changelog

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
