# Contributing / running from source

## Development setup

```bash
git clone https://github.com/denelson1-dot/ebook-audiobook.git
cd ebook-audiobook
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
python -m ebook_audiobook.cli check --engine fake
```

On Debian/Ubuntu you may need `sudo apt install python3-venv` first — the `venv`
and `ensurepip` modules ship separately there.

Run it:

```bash
python -m ebook_audiobook.cli web     # or just: ebook-audiobook
./run                                 # convenience wrapper (run.bat on Windows)
```

### Adding the real TTS engine

Core development doesn't need it — the `fake` engine exercises the entire
pipeline through to a real `.m4b`. When you do want real audio, install the
PyTorch build that matches your hardware *first*, then the extra:

```bash
# NVIDIA GPU (Linux/Windows):
pip install torch==2.9.1 torchaudio==2.9.1 --index-url https://download.pytorch.org/whl/cu128
pip install --no-deps chatterbox-tts==0.1.7
python -c "from ebook_audiobook.torchbuild import CHATTERBOX_DEPS as d; print(' '.join(d))" | xargs pip install
# Apple Silicon, or CPU-only:
pip install torch==2.9.1 torchaudio==2.9.1 --index-url https://download.pytorch.org/whl/cpu

pip install -e '.[tts]'
python -m ebook_audiobook.cli check     # expect device=cuda|mps|cpu, chatterbox=yes
```

The torch version is pinned **exactly**, and must stay that way: PyPI's torch is
far ahead of the pinned indexes, and PEP 440 ranks a plain `2.13.0` above
`2.9.1+cu128`, so a `>=` constraint silently fetches the wrong build from PyPI.
Chatterbox goes in with `--no-deps` because it declares `torch==2.6.0`, which has
no kernels for current GPUs; its dependency list lives in
`ebook_audiobook/torchbuild.py` and CI installs it for real.

`[tts]` pins `setuptools<81` because Chatterbox's watermarker dependency
(`resemble-perth`) still imports `pkg_resources`, which newer setuptools removed.

## Data directory

A checkout that already contains `local-data/` keeps using it, so an existing
setup is never disturbed by an upgrade. A fresh checkout uses the same per-user
OS directory an installed copy does. Either way, `EBAB_DATA_ROOT` overrides it —
which is what the test suite does, so tests never touch real data.

```bash
EBAB_DATA_ROOT=/tmp/scratch python -m ebook_audiobook.cli paths
```

## Tests

```bash
pytest                                    # everything available on this machine
pytest -m "not calibre and not ffmpeg"    # skip external-tool integration tests
```

Tests that need Calibre or ffmpeg are marked and skip cleanly when the tool
isn't there. ffmpeg-marked tests still run without a *system* ffmpeg, because
they fall back to the binary bundled with `imageio-ffmpeg` — the same one users
get.

Worth doing before a release, since it's the configuration most users are in:

```bash
mkdir -p /tmp/emptybin
PATH=/tmp/emptybin:$VIRTUAL_ENV/bin pytest     # no system ffmpeg/ffprobe/calibre
```

### The CI smoke tests

`ci/smoke_render.py` and `ci/smoke_web.py` run against an *installed wheel*
rather than a source checkout. They exist to catch the failures that only appear
after packaging — most importantly a wheel built without its Jinja templates,
which imports perfectly and then 500s on every page. Run them the way CI does:

```bash
python -m build --wheel --outdir dist
python -m venv /tmp/fresh && /tmp/fresh/bin/pip install dist/*.whl
/tmp/fresh/bin/python ci/smoke_render.py
/tmp/fresh/bin/python ci/smoke_web.py
```

## Architecture notes

- `tools.py` is the only place that locates or runs an external program. It
  handles the differences that break things on other platforms: Calibre not
  being on PATH on macOS, Windows consoles decoding subprocess output as cp1252,
  and console windows flashing up when launched from a GUI. **Don't call
  `subprocess.run` or `shutil.which` directly elsewhere.**
- `platform_dirs.py` owns every per-OS path decision.
- `tts/adapter.py` is the engine seam. An engine's `engine_version` participates
  in each segment's content hash, so upgrading one correctly invalidates cached
  audio.
- `storage.py` classifies before it deletes. `JobStore.cleanup_intermediates`
  cannot tell a finished book's leftovers from a stopped render's resume cache —
  they are the same files on disk — so anything that frees space in bulk goes
  through `storage.survey()`/`storage.free()`, which can. **Don't call
  `cleanup_intermediates` from a loop.**
- Stage wording lives in `jobs/models.STAGE_LABELS`, and nothing renders a raw
  stage name. The web UI, the job page's client-side poller and `cli list` all
  read the same table so they cannot drift apart.
- Anything that changes rendered audio belongs in `VoiceSettings._RENDER_FIELDS`;
  anything that only affects the container (bitrate, etc.) belongs in `extra`, so
  that changing it never triggers a re-render.

## Translations

The interface is translated with plain `gettext`: strings are wrapped in `_()`
(or `ngettext()` for plurals) in Python, templates and JavaScript alike, looked
up by their English text. `ebook_audiobook/i18n.py` chooses the language —
`EBAB_LANG`, then the setting, then the browser or desktop, then English — and
`tools/i18n.py` maintains the catalogs:

```bash
python tools/i18n.py update      # pull new strings into locale/<lang>/LC_MESSAGES/messages.po
python tools/i18n.py compile     # .po -> .mo, which is what the app reads
python tools/i18n.py check       # stale .mo, bad placeholders, untranslated: what CI runs
python tools/i18n.py report      # what a translator still has to do
```

Rules that keep it working:

- **Wrap where the sentence is built**, in the request that will show it. The
  worker thread and the CLI never set a language, so they stay English.
- **Shared label tables** (`STAGE_LABELS`, `MODE_LABELS`, …) keep English values
  marked `N_()`; whoever renders one calls `_()` on it then. Anything cached
  across requests (the prerequisite check, the storage survey) must store the
  English and translate on the way out, or the next request gets the wrong
  language.
- Placeholders are `%(name)s` in all three layers, passed as keyword arguments —
  `_("No folder at %(root)s.", root=path)` in Python and in a template,
  `_("…", { root: path })` in JavaScript — and every layer formats the result,
  so a literal `%` is always written `%%`.
- Don't put a string literal inside a dynamic `_()` argument (`_(b["name"])`):
  the extractor would read `"name"` as a message. Assign it to a local first.
- Never call `locale.setlocale`: it is process-global and thread-unsafe.
- Commit the `.po` and the `.mo` together. A translator edits the `.po` in
  Poedit; `compile` then `check` before committing what comes back.
- Not translated, on purpose: CLI output, the bug report from `ebook-audiobook
  report`, the "Unknown Title"/"Unknown Author" fallbacks (they name folders and
  tags), raw exception text, and the shell commands in "how to fix it" hints.

## Cutting a release

1. Bump `version` in `pyproject.toml`.
2. Update `CHANGELOG.md`.
3. Run the [macOS pre-flight](#macos-pre-flight-the-part-ci-cannot-do) if the
   desktop shell, the icons or the installer changed.
4. Commit, then tag and push:
   ```bash
   git tag v1.2.3 && git push origin main --tags
   ```

The release workflow verifies the tag matches `pyproject.toml`, builds the wheel
and sdist, checks the wheel really contains its templates, smoke-tests it, runs
both installers on all three operating systems, then publishes the GitHub
Release with the installers stamped with that version.

A tag that disagrees with `pyproject.toml` fails the build rather than
publishing a release whose installer points at a wheel that doesn't exist.

### macOS pre-flight: the part CI cannot do

CI runs the test suite, the wheel smoke test and `bash -n install.sh` on
`macos-latest`. It does **not** run `install.sh`, so the `.app` bundle, the
`.icns` and the Dock behaviour have no automated coverage at all. Ten minutes on
a real Mac before tagging, once per release that touches any of it:

```bash
curl -fsSL .../install-macos-linux.sh | bash     # or: bash install.sh from a checkout
```

| # | Check | What "wrong" looks like |
|---|---|---|
| 1 | `~/Applications/ebook-audiobook.app` exists and shows **our** icon in Finder | Generic blank-document icon → `icon.icns` missing or malformed |
| 2 | Icon looks right in Finder's **list and column views**, not just the large grid | Scrambled pixels at small sizes → an `icp4`/`icp5`/`icp6` PNG slot crept back into `ICNS_TYPES` |
| 3 | Double-click it. A chromeless window opens | Falls back to a Safari tab → no Chromium-family browser found |
| 4 | Menu-bar icon appears, with **Open** and **Quit** | No icon → pyobjc missing, or `_macos_has_gui_session()` said no |
| 5 | **No Dock tile appears**, and ⌘-Tab does not list "Python" | A Python rocket in the Dock → `setActivationPolicy_` didn't take |
| 6 | Close the window. Server keeps running; menu-bar icon stays | Server dies → the tray isn't holding the main thread |
| 7 | Launch the app again from Finder → reopens on the **same** server | Two processes, two ports → `runtime.json` probe failed |
| 8 | Start a render, open the menu bar → Quit reads **"Quit — stops the running render"** | Plain "Quit" → the busy watcher isn't refreshing the menu |
| 9 | Quit from the menu bar. Process, window and `runtime.json` all go | Window left showing "can't be reached" → `close_windows()` didn't get a live child |
| 10 | With Homebrew ffmpeg installed, Settings → Diagnostics reports the **same** ffmpeg as `ebook-audiobook check` in Terminal | They disagree → a Finder launch isn't finding `/opt/homebrew/bin` |
| 11 | `ebook-audiobook-uninstall`, then confirm the `.app` is gone | A surviving `.app` bounces once and dies forever |

Known and accepted, so don't chase them:

- **Ctrl-C from a terminal skips cleanup.** pystray's SIGINT handler calls
  `NSApp.terminate_`, which never returns, so `serve()`'s `finally` doesn't run:
  the window is orphaned and `runtime.json` is left behind (the next launch
  clears it). Quit from the menu bar instead.
- **The menu-bar icon is slightly soft on Retina**, and is coloured rather than a
  monochrome template. pystray sizes it in pixels rather than points.
- **App Nap** may throttle a long render when no window is open. Untested.

### If a release build fails

Nothing is published unless every job passes, so a failure leaves you with a tag
and no release. Fix the problem, then move the tag rather than bumping the
version — the released code should be exactly what the tag points at:

```bash
git tag -d v1.2.3 && git push origin :refs/tags/v1.2.3   # remove the bad tag
git tag -a v1.2.3 -m "..." && git push origin v1.2.3     # re-tag at the fix
```

Only do this while the release is unpublished. Once people can download it, cut
a new patch version instead.
