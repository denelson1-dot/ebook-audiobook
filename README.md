# ebook-audiobook

Turn a DRM-free ebook you own into a single, high-quality narrated audiobook
(`.m4b`) — chaptered, tagged, and filed ready for your Plex library. See
[Listening to it](#listening-to-it) for which players show the chapters.

Everything runs **on your own machine**. No cloud APIs, no accounts, no
telemetry, nothing leaves the computer.

---

## Install

One command. It sets up its own private Python environment, works out which
PyTorch build suits your hardware, and offers to install anything missing.
Nothing is installed system-wide and you don't need administrator rights.

**macOS and Linux**

```bash
curl -fsSL https://github.com/denelson1-dot/ebook-audiobook/releases/latest/download/install.sh | bash
```

**Windows** — open PowerShell and run:

```powershell
irm https://github.com/denelson1-dot/ebook-audiobook/releases/latest/download/install.ps1 | iex
```

Then start it:

```bash
ebook-audiobook
```

> **While this repository is private**, GitHub does not serve release assets to
> anonymous requests, so the plain one-liner can't fetch the wheel. The installer
> falls back to the [`gh` CLI](https://cli.github.com/) when it's installed and
> signed in, which makes the same command work for anyone with repo access. Once
> the repository is public, no such fallback is needed.

That opens the web interface in your browser. On Windows there's also a Start
Menu entry; on Linux, an application-menu entry.

<details>
<summary>Installer options</summary>

| Option | Effect |
|---|---|
| `--cpu` / `-Cpu` | Force the CPU-only PyTorch build (~250 MB instead of ~2.5 GB) |
| `--gpu` / `-Gpu` | Force the CUDA build when the GPU probe comes up empty (e.g. a broken `nvidia-smi`) |
| `--rocm` / `--amd` | Force the AMD ROCm build (Linux + Radeon) |
| `--no-tts` / `-NoTts` | Skip PyTorch for now — import books, add the engine later |
| `--version X.Y.Z` | Install a specific release |
| `--dir PATH` / `-InstallDir` | Install somewhere other than the default |
| `--yes` / `-Yes` | Accept all prompts (scripted installs) |
| `--uninstall` / `-Uninstall` | Remove the program, keeping your books and settings |

</details>

### What you need

| | |
|---|---|
| **Python 3.11+** | The installer offers to install it if it's missing |
| **[Calibre](https://calibre-ebook.com/download)** | Required — reads your ebook files. The installer offers to install it |
| **ffmpeg** | **Included.** Nothing to do |
| A GPU | Optional. See [how long it takes](#how-long-it-takes) |

### Uninstalling

```bash
ebook-audiobook-uninstall            # macOS/Linux
```
```powershell
iex "& { $(irm https://github.com/denelson1-dot/ebook-audiobook/releases/latest/download/install.ps1) } -Uninstall"
```

Your books, settings, and finished audiobooks are **never** deleted by an
uninstall. `ebook-audiobook paths` shows you where they live.

---

## Using it

### The web interface

Run `ebook-audiobook` and your browser opens. Then:

1. **New conversion** → pick a DRM-free ebook from your computer.
2. Choose a **voice** and adjust the settings — expressiveness and pacing up
   front, the finer generation knobs under *Advanced*.
3. **Generate a preview** of any chapter. It uses the exact same engine and
   settings as the full render, so it sounds like the finished article.
4. **Render full audiobook**, and choose where it goes:
   - **Plex library** — filed as a Plex-ready `Author / Title (Year) / Title.m4b`
     tree with a `cover.jpg` beside it. Set the folder once in **Settings**.
   - **A specific folder** — one flat folder you pick.

   Either way the destination is checked for write access *before* the render
   starts, so a bad path fails in a second rather than after three hours.
   There's a **Stop** button, and interrupted renders resume where they left off.
5. The `.m4b` comes out tagged for Plex/Audnexus: marked as an Audiobook
   (`stik=2`), album-artist set to the author, cover embedded, one chapter
   marker per chapter, plus year and ISBN when the ebook provides them.
   ([Not every player shows the chapters](#listening-to-it) — Plex's own don't.)

**Voices** — add your own rights-cleared reference clips, audition them, and
switch between them per book.
**Library** — every conversion you've done, with its status, size, and cleanup
actions.

### The command line

```bash
ebook-audiobook                                   # open the web UI
ebook-audiobook check                             # is everything installed?
ebook-audiobook paths                             # where is my data?
ebook-audiobook convert book.epub --preview-seconds 30   # preview, then confirm
ebook-audiobook convert book.epub -y --bitrate 64        # straight through
ebook-audiobook convert book.epub --voice-ref clip.wav   # clone a voice
ebook-audiobook convert book.epub --engine fake -y       # no-GPU plumbing test
ebook-audiobook list                              # past conversions
```

---

## Listening to it

Every book is written with a real chapter marker per chapter, stored **twice** —
as a QuickTime chapter track and as a Nero `chpl` atom — so any player that reads
chapters at all will find them. A render that somehow lost its markers is failed
rather than shipped.

> **Plex Media Server does not read chapters from audio files.** Only from video.
> In Plex and Plexamp your book appears as one unbroken ten-hour track with no
> chapter list and no skip-chapter buttons — which makes the scrub bar genuinely
> hazardous in a car. This is a Plex limitation, not a problem with the file; it
> has been [an open request since 2018][plex-req] with no implementation, and the
> maintainer of the main Plex audiobook guide [says the same][plex-guide].

Use a player that reads the chapters itself. These keep Plex as the library, so
there's no second server to run:

| Platform | Player | Notes |
|---|---|---|
| Android | [Chronicle Epilogue](https://play.google.com/store/apps/details?id=local.oss.chronicle) | Free, [open source](https://github.com/mattttvaughn/chronicle). Chapters, offline downloads, Android Auto, 0.5–3× speed, sleep timer, progress syncs back to Plex. Currently an open beta — join from the Play listing. Needs Android 13+; Android Auto support is basic (no voice control). |
| Android | [Bookcamp](https://play.google.com/store/apps/details?id=app.bookcamp.android) | Chapters, offline, Android Auto — but subscription-only, and reviews report Android Auto and chapter-playback glitches. |
| iOS | [Prologue](https://prologue.audio/) | Chapters, CarPlay (with a chapter list on the now-playing screen), Apple Watch, Siri, bookmarks, sleep timer, voice boost. Free; one-time $5 unlock for offline downloads. Also speaks Audiobookshelf, so it survives a later switch. |
| iOS | [Bookcamp](https://apps.apple.com/us/app/bookcamp/id1523540165) | Chapters, offline, cross-device sync — but subscription-only. |

Not committed to Plex? [Audiobookshelf](https://www.audiobookshelf.org/) reads
these chapters natively on both server and client, and the library tree this tool
writes (`{Author}/{Title} (Year)/`, or `{Author}/{Series}/{NN} - {Title} (Year)/`)
already matches its expected `{Author}/{Series}/{Book}` layout — point it at the
same folder and nothing needs re-rendering.

<details>
<summary>Checking the markers yourself</summary>

If a player shows no chapters, confirm where the fault lies before blaming the
file:

```bash
ffprobe -v error -print_format json -show_chapters "Your Book.m4b" \
  | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["chapters"]))'
```

A number greater than zero means the markers are there and the player is what's
ignoring them.

</details>

[plex-req]: https://forums.plex.tv/t/support-for-reading-chapters-in-m4b-files/741874
[plex-guide]: https://github.com/seanap/Plex-Audiobook-Guide/discussions/92

---

## How long it takes

The model, the weights, and the audio quality are **identical on every device**.
Only the speed changes.

| Your hardware | Runs on | A ~110,000-word novel takes |
|---|---|---|
| NVIDIA GPU (Windows/Linux) | CUDA | **2–3 hours** (~49 chars/sec on an RTX 3070 Ti) |
| AMD Radeon (Linux) | ROCm | 3–4 hours |
| Apple Silicon Mac, macOS 12.3+ | Metal | A few hours |
| Intel Mac, or macOS below 12.3 | CPU | Many hours |
| AMD Radeon (Windows) | CPU | Many hours — PyTorch's ROCm builds are Linux-only |
| No GPU (any OS) | CPU | Many hours — works, but leave it overnight |

The installer picks the right build for your machine on its own — CUDA, ROCm,
Metal, or CPU-only. `ebook-audiobook check` tells you which one you ended up on
and why. The first segment is slower (warm-up); generating a preview measures
your machine's real rate and refines the estimate shown for the full book.

**AMD on Linux** gets the ROCm build automatically when a discrete Radeon is
found. Some consumer cards (RX 6700/6600, RX 7600/7700/7800) aren't on ROCm's
supported list and need `HSA_OVERRIDE_GFX_VERSION` set to be visible at all —
the installer works this out and bakes it into the launcher, so there's nothing
to configure. Integrated Radeon graphics are deliberately left on the CPU build,
which is faster for them than ROCm would be.

**Apple Silicon** is used automatically — there's one Mac build of PyTorch and it
has Metal support built in, so `--cpu` and `--gpu` don't change what's
downloaded. Metal needs macOS 12.3 or newer; below that the same install quietly
runs on the CPU, so the installer says so rather than letting you discover it
from the render time.

**If a GPU runs out of memory** partway through, the render doesn't die — it
retries once, then moves to the CPU and finishes. Everything already rendered
stays cached either way.

<details>
<summary>Environment variables</summary>

| Variable | Effect |
|---|---|
| `EBAB_DEVICE` | Force `cuda`, `mps`, or `cpu` instead of the automatic choice |
| `EBAB_DATA_ROOT` | Put all stored data somewhere other than the default |
| `EBAB_VERBOSE=1` | Show the engine's own progress bars and warnings |
| `EBAB_PORT` / `EBAB_HOST` | Bind the web UI somewhere other than `127.0.0.1:5005` |
| `EBAB_NO_BROWSER=1` | Don't open a browser window on start |
| `EBAB_EBOOK_CONVERT` | Path to Calibre's `ebook-convert`, for unusual installs |

</details>

---

## How it works

A linear, content-addressed, resumable pipeline:

```
ebook → extract → normalize → chunk  →  render   → assemble → package → .m4b
        (Calibre)  (spoken     (TTS-    (Chatterbox  (ffmpeg)   (ffmpeg,
                    form)       safe)    GPU/CPU/MPS)            chapters+cover)
```

Each segment's identity is `hash(text + voice settings + engine version)`. That
one idea gives you three things for free: an interrupted render **resumes
automatically**, changing a voice setting **re-renders only what changed**, and
changing something that doesn't affect audio (like bitrate) re-renders
**nothing**.

Everything except the TTS engine is pure Python and runs without a GPU. The
`fake` engine renders the whole pipeline to a real `.m4b` for testing.

---

## Where your files live

Everything the app stores lives in one folder — run `ebook-audiobook paths` to
see exactly where. By default:

| OS | Location |
|---|---|
| Windows | `%LOCALAPPDATA%\ebook-audiobook` |
| macOS | `~/Library/Application Support/ebook-audiobook` |
| Linux | `~/.local/share/ebook-audiobook` |

```
imports/    copies of your source ebooks
jobs/       per-book state + cached segment/chapter audio
voices/     your reference clips
outputs/    previews, and finished files when no library folder is set
settings.json
```

Finished audiobooks go to your **Plex library folder** (set in Settings), not
here. Override the whole location with `EBAB_DATA_ROOT=/some/path`.

> Running from a source checkout that already has a `local-data/` folder? That
> keeps working exactly as before — it takes precedence, so an existing setup is
> never orphaned.

### Keeping disk usage down

The **Library** tab shows every conversion with its footprint and a running
total. Two cleanup actions:

- **Free up space** — deletes the big regenerable artifacts (segment and chapter
  WAVs, the normalized EPUB, the preview) but keeps the finished `.m4b` and its
  metadata, so the entry stays in your history. Those intermediates are the bulk
  of the space; the `.m4b` is small by comparison.
- **Delete** — removes the whole conversion, `.m4b` included.

Previews don't pile up: there's only ever one per book, and it's deleted
automatically once a full render finishes. Neither action is available while a
job is rendering.

---

## Troubleshooting

**"Calibre isn't installed"** — install it from
[calibre-ebook.com](https://calibre-ebook.com/download), or
`winget install calibre.calibre` / `brew install --cask calibre` /
`sudo apt install calibre`. On macOS you do **not** need to add it to your PATH;
the app looks inside `/Applications/calibre.app` itself.

**The `ebook-audiobook` command isn't found** — on macOS/Linux add
`~/.local/bin` to your PATH (the installer tells you the exact line). On Windows,
open a **new** terminal — PATH changes don't affect already-open ones.

**"This book appears to be DRM-protected"** — this tool doesn't remove DRM and
won't open protected files. Bring a DRM-free copy.

**A scanned PDF produces nothing** — an image-only PDF has no text to narrate.
You need an OCR'd or EPUB version.

**Renders are very slow** — check `ebook-audiobook check`. If it says
`device=cpu` and you have an NVIDIA card, the CPU-only PyTorch build got
installed; re-run the installer to get the CUDA one.

Run `ebook-audiobook check` first for anything else — it reports the state of
every prerequisite and how to fix what's missing.

---

## Responsible use and privacy

This tool is for **ebooks you own and are legally allowed to convert**. It does
**not** remove DRM and will not open DRM-protected files. Generated audiobooks
are for **personal use**; converting and redistributing copyrighted work is on
you, not this tool.

Voice cloning from a reference clip is opt-in and local only. Use a voice you
have the **rights and consent** to use — your own, or a rights-cleared clip.
Don't clone someone's voice without their permission.

**Watermarking:** Chatterbox embeds an inaudible
[Resemble Perth](https://github.com/resemble-ai/chatterbox) watermark in all
generated audio so AI-generated speech can be identified after the fact. This is
a deliberate responsible-AI feature and is present in every file this produces.

**Privacy:** everything runs locally. No cloud APIs, no telemetry, and no network
traffic at all except two one-time downloads: the app itself, and the ~1 GB voice
model from Hugging Face on your first render. Your books, voice clips, and
generated audio never leave the machine.

**Security:** the web interface has **no authentication** and binds to
`127.0.0.1` deliberately. It's a single-user local tool. Don't expose it to a
network or bind it to `0.0.0.0` — the "import by local path" feature reads
arbitrary local files, so an exposed instance would leak them.

---

## Contributing / running from source

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup, the test
suite, and how releases are cut.

## License

[MIT](LICENSE) — free to use, modify, and distribute. Chatterbox is likewise MIT.
The bundled ffmpeg binary (via `imageio-ffmpeg`) is licensed under the GPL by its
own authors; it is invoked as a separate program, not linked into this code.
