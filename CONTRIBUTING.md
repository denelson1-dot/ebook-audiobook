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
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
# Apple Silicon, or CPU-only:
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu

pip install -e '.[tts]'
python -m ebook_audiobook.cli check     # expect device=cuda|mps|cpu, chatterbox=yes
```

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
- Anything that changes rendered audio belongs in `VoiceSettings._RENDER_FIELDS`;
  anything that only affects the container (bitrate, etc.) belongs in `extra`, so
  that changing it never triggers a re-render.

## Cutting a release

1. Bump `version` in `pyproject.toml`.
2. Update `CHANGELOG.md`.
3. Commit, then tag and push:
   ```bash
   git tag v1.2.3 && git push origin main --tags
   ```

The release workflow verifies the tag matches `pyproject.toml`, builds the wheel
and sdist, checks the wheel really contains its templates, smoke-tests it, runs
both installers on all three operating systems, then publishes the GitHub
Release with the installers stamped with that version.

A tag that disagrees with `pyproject.toml` fails the build rather than
publishing a release whose installer points at a wheel that doesn't exist.
