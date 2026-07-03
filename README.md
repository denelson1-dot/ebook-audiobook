# ebook-audiobook

Offline, local converter that turns a DRM-free ebook you own into a single
high-quality narrated audiobook (`.m4b`) you can drop into Plex and listen to on
your phone. No cloud APIs, no telemetry, nothing leaves the machine.

## How it works

A linear, content-addressed, resumable pipeline:

```
ebook → extract → normalize → chunk → render → assemble → package → .m4b
        (Calibre)  (spoken     (TTS-    (Chatterbox: (ffmpeg)   (ffmpeg,
                    form)       safe)    GPU/CPU/MPS)            chapters+cover)
```

Each segment's identity is `hash(text + voice settings + engine version)`, so an
interrupted render **resumes automatically** (finished segments are cached) and
changing a voice setting or a normalization rule re-renders only what changed.

Everything except the TTS engine is pure Python and runs without a GPU; the
`fake` engine renders the whole pipeline to a real `.m4b` for testing.

## Requirements

Runs on **Linux, Windows, and macOS**. You need three things:

- **Python ≥ 3.11** (developed against 3.12)
- **[Calibre](https://calibre-ebook.com/)** (provides `ebook-convert`)
  - Linux: `sudo apt install calibre` · macOS: `brew install --cask calibre` · Windows: [installer](https://calibre-ebook.com/download) (ensure `ebook-convert` is on `PATH`)
- **[ffmpeg](https://ffmpeg.org/)**
  - Linux: `sudo apt install ffmpeg` · macOS: `brew install ffmpeg` · Windows: `choco install ffmpeg` or [download](https://ffmpeg.org/download.html)

The TTS engine picks the best available device automatically — see the
performance table below. `python -m app.cli check` verifies all of the above.

## Setup

```bash
cd repo
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e '.[dev]'          # core pipeline + tests, no GPU needed
python -m app.cli check --engine fake
```

> On Debian/Ubuntu you may need `sudo apt install python3-venv` first (the
> `venv` module ships separately there).

### Install the real TTS engine

The `[tts]` extra brings torch, torchaudio, and `chatterbox-tts` (and pins
`setuptools<81`, because Chatterbox's watermarker dep `resemble-perth` still
imports `pkg_resources`, which newer setuptools removed). The Chatterbox model
(~1 GB) downloads from Hugging Face on first render and is cached.

**Install torch for your platform first, then the extra:**

```bash
# NVIDIA GPU (Linux/Windows) — CUDA build:
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
# Apple Silicon (macOS) or CPU-only (any OS) — default wheel:
pip install torch torchaudio

pip install -e '.[tts]'
python -m app.cli check          # shows device=cuda|mps|cpu, chatterbox=yes
```

(On Linux the default PyPI `torch` wheel is already the CUDA build, so the plain
`pip install -e '.[tts]'` suffices there.)

### Performance — same quality on every device, only speed differs

The model, weights, and audio quality are identical everywhere; only render
speed changes:

| Platform | Device | Speed |
|---|---|---|
| Linux / Windows + NVIDIA | `cuda` | Fast — ~49 chars/sec on an RTX 3070 Ti (~2.2× real-time); a ~110k-word novel is a **2–3 h** render |
| macOS (Apple Silicon) | `mps` | Workable |
| Any OS, no GPU | `cpu` | Works, but slow (a full novel can take many hours) |

The first segment of a run is slower (warmup); the preview reports a live rate
that refines the full-book estimate. Set `EBAB_VERBOSE=1` to see the engine's
own progress bars.

## Usage

### Web UI (recommended)

One command from the repo root:

```bash
./run            # Linux/macOS   (Windows: run.bat)
```

It picks a free port automatically (so it won't clash with anything already on
localhost) and opens your browser. Then:

1. **New conversion** → browse your filesystem and pick a DRM-free ebook.
2. On the book page, choose a **voice** and adjust **settings** (expressiveness &
   pacing up front; temperature / repetition penalty / min_p / top_p / seed under
   *Advanced*).
3. **Generate a preview** of any chapter — it uses the exact same engine and
   settings as the full render, so it sounds like the final result.
4. **Render full audiobook** — choose where it lands:
   - **Plex library** (default): filed into your audiobooks folder as a
     Plex-ready `Author / Title (Year) / Title.m4b` tree, with a `cover.jpg`
     beside each book. Set the library folder once in **Settings** (you're
     prompted on first run).
   - **Specific folder**: a single flat folder you pick.

   Either way the destination is write-checked *before* the render starts, so a
   bad path fails immediately rather than after hours. Has a **Stop** button;
   interrupted renders resume.
5. The `.m4b` is tagged for Plex/Audnexus — marked as an Audiobook (`stik=2`),
   with album-artist = author, embedded cover, native chapter markers, and
   year/ISBN when the ebook provides them. Point Plex's library at your
   audiobooks folder and refresh.

**Voices** tab: add your own rights-cleared reference clips, audition them, and
switch between them per book. **Settings** tab: set the audiobooks library
folder. (`EBAB_PORT`, `EBAB_HOST`, and `EBAB_NO_BROWSER=1` override the launch
behavior.)

### CLI (scriptable alternative)

```bash
python -m app.cli convert /path/to/book.epub --preview-seconds 30   # preview, then confirm
python -m app.cli convert /path/to/book.epub -y --bitrate 64        # straight through
python -m app.cli convert /path/to/book.epub --voice-ref clip.wav   # clone a voice
python -m app.cli convert /path/to/book.epub --engine fake -y       # no-GPU plumbing test
python -m app.cli convert /path/to/book.epub -y --output-dir ~/Audiobooks  # choose destination
```

By default the CLI files into your configured Plex library folder (if set),
otherwise `local-data/outputs/`. `--output-dir` forces a single flat folder.
Every destination is write-checked before the render begins.

## Data layout

All private/generated data lives under `local-data/` (git-ignored):

```
local-data/
  imports/   copied source ebooks
  jobs/      per-book state (JSON) + cached segment/chapter audio
  voices/    your rights-cleared reference clips
  outputs/   final .m4b files when no library folder is set (and *_preview.wav)
  models/    model cache (if configured)
  tmp/       scratch
  settings.json   app-wide preferences (e.g. your audiobooks library folder)
```

Finished audiobooks are written to your **Plex library folder** (set in
Settings), not `local-data/` — only the preview WAVs and, when no library folder
is configured, the flat-folder outputs live here.

Override the root with `EBAB_DATA_ROOT=/some/path`.

### History & storage cleanup

The **Library** is your history: every conversion, newest first, with its status,
export date, and disk footprint (plus a total-usage summary). Two cleanup actions
keep things from piling up:

- **Free up space** (per job) — deletes the large regenerable render artifacts
  (segment/chapter WAVs, the normalized EPUB, the preview) but keeps the finished
  `.m4b` and metadata, so the entry stays in your history. These intermediates are
  the bulk of the footprint; the `.m4b` is small by comparison.
- **Delete** (per job) — removes the whole conversion: its files and its `.m4b`.

Previews don't accumulate: there's only ever one per book (each new preview
replaces it), and it's **deleted automatically once a full render finishes**.
Voice audition clips are likewise one-per-voice and removed when the voice is
deleted. You can't clean up or delete a job while it's actively rendering.

## Tests

```bash
pytest                 # unit + fake-engine integration
pytest -m "not calibre and not ffmpeg"   # skip external-tool integration tests
```

## Scope (v1)

Single narrator, one book in → one `.m4b` out. **Not** in v1: multiple/character
voices, DRM removal, cloud anything, batch library conversion. The `TTSAdapter`
seam and per-segment model leave room for a future multi-voice Plan 2.

## Responsible use & privacy

This tool is for **ebooks you own and are legally allowed to convert**. It does
**not** remove DRM and will not open DRM-protected files — bring your own
DRM-free source. Generated audiobooks are for **personal use**; converting and
redistributing copyrighted work is on you, not this tool.

Voice cloning from a reference clip is opt-in and local only. Use a voice you
have the **rights and consent** to use (your own, or a rights-cleared / public
clip). Don't clone someone's voice without permission.

**Watermarking:** Chatterbox embeds an inaudible [Resemble
Perth](https://github.com/resemble-ai/chatterbox) watermark in all generated
audio, which allows AI-generated speech to be identified after the fact. This is
a deliberate responsible-AI feature and is present in every rendered file.

**Privacy:** everything runs locally. No cloud APIs, no telemetry, no network
egress except the one-time model download from Hugging Face. Source text, voice
clips, model files, and generated audio all stay under `local-data/` and are
git-ignored — nothing copyrighted is ever committed.

## Security

The web UI has **no authentication** and is bound to `127.0.0.1` on purpose. It
is a single-user local tool. Do **not** expose it to a network or bind it to
`0.0.0.0` — the "import by local path" feature reads arbitrary local files, so
an exposed instance would be a local-file-disclosure risk. Uploaded filenames
are sanitized, but the localhost-only boundary is the real protection.

## License

[MIT](LICENSE) — free to use, modify, and distribute. Chatterbox is likewise
MIT-licensed. (The copyright line in `LICENSE` currently reads `denelson1`; swap
in your legal name before publishing if you prefer.)
