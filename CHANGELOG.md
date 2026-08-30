# Changelog

## 1.3.0 — 2026-08-30

The interface, rebuilt around the two facts the old one never acknowledged: a
render takes hours, and the leftovers are enormous.

**A render is never more than a glance away.** Progress used to live on one page;
navigate away and a three-hour job vanished. The sidebar now carries it on every
screen — which book, which chapter, how much longer, and a way to stop it. Only
one book is narrated at a time, and the app finally says so.

**Working files stopped being a secret.** The raw narration audio is around 85%
of everything stored here and, until now, the only way to reclaim it was a
per-book button you had to already know about. There is a running total in the
sidebar on every screen, a **Free up** button beside the disk figure on the
library page, and a new **Storage** page that breaks the whole folder down and
frees it in bulk.

Crucially, it knows the difference between two things that look identical on
disk. A *finished* book's working files buy nothing you can hear. A *stopped*
render's working files **are** the resume — deleting them costs hours of
narration — so those are held back, unticked, with the price written into the
row. A blunt "delete temporary files" would have destroyed them silently.

**Or never think about it again:** turn on *Free them when a book finishes* and
each book cleans up after itself. Off by default, and it honours the same
setting from the command line.

**The library looks like a library.** Books are shown by their covers — which
were always extracted and never displayed — with how long each one plays and
what it costs on disk. Books with no cover art get their title set on a tinted
ground rather than a grey box.

**The book page is one screen, not six stacked cards.** What gets narrated on the
left, who narrates it on the right, and a bar pinned along the bottom that always
shows what you are about to commit to: length, time to narrate, final size, and
the temporary gigabytes nobody used to warn you about until the confirm dialog.
The engine's own vocabulary — `cfg_weight`, `min_p`, `top_p` — is still there,
one disclosure down, under names you can act on.

**Eleven internal stages, eleven plain sentences.** `cancelled` now reads
"Stopped by you — 34% narrated and kept". The wording lives in one place, so the
sidebar, the library card and `ebook-audiobook list` can no longer describe the
same moment three different ways.

**Five narrators, ready to use.** A new install used to meet the engine's own
untuned voice and a page explaining how to record your own. It now ships five
public-domain reference clips — two North American male, one female, one of each
British — with one chosen as the default and settings tuned to suit it. The
engine's raw voice is no longer offered as a choice, though a book already using
it keeps it.

Preparing those clips is in `tools/prepare_voices.py` rather than anyone's
memory, because it turned out to matter more than choosing them: the engine
takes timbre from a clip's first ten seconds and prosody from its first six, so
where a clip starts decides most of what you hear. One recording was dropped
outright — its noise sat *under* the speech rather than between the words, and a
cloned voice reproduces that faithfully.

**Previews play when they are ready**, rather than waiting to be pressed after a
wait you already asked for. There is a toggle in Settings.

**Estimates are measured, not assumed.** "Time to narrate" and "length of audio"
came from a fixed speaking rate that ignored the pacing setting, the voice and
the reference clip — so the panel could tell you lower pacing reads faster and
then report an unchanged duration. A button beside the figures narrates a few
seconds with the current settings and measures the real rates, excluding the
model load, the slow first segment and any cached audio. Nothing is wasted: what
it renders is reused by the full render.

**The window reopens where you left it**, and paths in the interface open the
folder they name.

Also in this release:

- The interface follows your system's light or dark setting.
- Emoji are gone from the interface — every icon is drawn, so it scales and
  takes the colour of the text beside it instead of whatever the platform felt
  like rendering.
- First launch is a checklist that says what is missing, what is already handled,
  and what your hardware means for how long a book will take.

## 1.2.0 — 2026-08-02

It launches like an application now, instead of like a web server you have to
leave a terminal open for.

**A window of its own.** Starting the app used to park a terminal on screen
telling you to leave it open, and the interface arrived as one more tab in
whatever browser you already had — no dedicated window, no taskbar identity, a
stock system icon. Now you get a chromeless window with its own icon and its own
taskbar entry, and no terminal at all. It borrows a Chromium-family browser you
already have (Chrome, Edge, Brave, Chromium) in `--app` mode, so there is no
bundled runtime to download and no second GUI toolkit sitting next to PyTorch.
If you have no Chromium, it falls back to an ordinary tab exactly as before.

**Closing the window no longer stops a render.** The app keeps running in the
system tray, so an overnight conversion finishes on its own. Open the window
again from the tray, or just launch the app a second time. To stop it properly
there is a **Quit** in the tray menu and at the bottom of the sidebar — and both
warn you first if a render is still going, wording the warning differently for a
six-hour render than for a ten-second voice sample.

**macOS gets a launcher at all.** Previously there was only the command line: no
Dock icon, no Spotlight entry, no way to start it without opening Terminal
first. There is now an `ebook-audiobook.app` in your Applications folder.

Also in this release:

- **Launching twice could corrupt a book's state.** The second launch found port
  5005 taken, quietly bound a different one, and ran a *second* background
  worker over the same job folder — two processes writing state for the same
  job, each believing it owned the GPU. Launching again now finds the running
  instance and opens a window onto it.
- **The diagnostic log never actually deleted anything on Windows**, which
  refuses to unlink a file that is still open. Underneath that was a leak on
  every platform: clearing the log left the old handler attached, so the next
  failure was written **twice**, and every failure after that too.
- **Backups taken through the browser left a full copy behind on Windows**,
  silently doubling the disk cost of the one feature whose whole point is not
  doing that. Archives are also swept if a download is interrupted.
- **Render progress no longer floods the system log.** It printed to stderr
  unconditionally; with no terminal attached that is thousands of lines per
  render into the journal. It now checks whether anyone is actually watching.
- Homebrew's ffmpeg is found when the app is launched from the Dock. macOS gives
  a GUI-launched app a bare `PATH`, so it had been silently falling back to the
  bundled copy — and reporting a different toolchain than the same user saw in
  Terminal.

**Known limitation:** GNOME removed the system tray and needs a user-installed
shell extension to get one back. No library works around that. On GNOME the app
still runs after you close the window; you reopen it by launching the app again,
and quit from the sidebar.

## 1.1.3 — 2026-08-01

Keeping your work safe, knowing when there's a new version, and being able to
say what went wrong.

**Backups that aren't mostly wasted space.** `ebook-audiobook backup` saves your
books, chaptering, voice clips and settings. It leaves rendered audio out by
default, and the difference is stark: on a machine with three books converted,
the backup is **30.8 MB instead of 3.3 GB**, because a single finished book is
3.3 GB of audio wrapped around 2.2 MB of actual work. That audio is
content-addressed and re-renders identically, so storing it buys nothing a
re-render doesn't recreate.

Three profiles (`settings`, `projects`, `full`) plus per-category switches,
`--max-size` to refuse anything bigger than you meant, and `--dry-run` to see
the breakdown before writing. The installed virtualenv — 7.6 GB, and living
inside the data folder — is never included at any setting. `restore` won't
overwrite existing files unless you pass `--force`.

**Update checking, on your terms.** `ebook-audiobook update` asks GitHub for the
latest version; `--apply` re-runs the official installer to upgrade in place, so
upgrading is the same code path as installing rather than a less-tested parallel
one. There is no timer and no start-up poll: the check happens when you ask for
it. Settings has an opt-in for checking on page load, off by default. The README
now lists every network request the app can make, in full.

**A failure log worth reading.** When something fails, one line of JSON records
the version, OS, Python, GPU, operation and traceback. It rotates at ~750 KB
total and deletes itself after two weeks. `ebook-audiobook report` turns the
recent entries into a Markdown bug report — with your home directory replaced by
`~` and book titles removed, so filing a bug doesn't publish your reading
history.

Also in this release:

- **Measuring a backup got 36× faster** (3.2s → 0.09s). The scan walked the
  whole data folder and discarded the virtualenv afterwards — 48,242 of the
  52,915 files there. It now never descends into it.
- **The version number is right in a source checkout.** It came from installed
  package metadata, which goes stale in a working copy, so a checkout reported
  whatever version was last installed — the number that ends up in bug reports
  and backup manifests. In a checkout, `pyproject.toml` now wins.
- **CI proves Metal acceleration works on Apple Silicon.** The installer matrix
  ran with `--no-tts`, so nothing verified the engine on a Mac. A new job
  installs it for real on an arm64 runner and fails the build unless PyTorch has
  the Metal backend, Metal is available, the app selects it, and a tensor
  actually computes on the GPU — a silent fall back to the CPU is a
  several-times-slower render that nothing else would have caught. M1 through M5
  are all arm64 and take this same path.

## 1.1.2 — 2026-08-01

Books that Calibre choked on now import.

- **An EPUB whose reading order contains the cover image is repaired
  automatically.** The spine is a list of documents, but some commercial EPUBs
  put the cover *image* in it. Calibre walks the spine assuming every entry
  parsed into a document tree, calls `.find()` on raw JPEG bytes, and dies
  inside its CSS flattener with a bare `TypeError` — no mention of the spine,
  the cover, or the book. The entry is now dropped before conversion; the cover
  itself stays in the manifest and is still used.

  The repair is deliberately timid. It removes only entries whose media type is
  positively known not to be a document (images, audio, video, fonts, CSS, the
  NCX); anything unknown or unlabelled is left alone, because dropping a real
  chapter would silently shorten an audiobook — a far worse outcome than the
  crash being avoided.

- **Failure messages no longer send you in a circle.** A failed conversion
  advised converting the book to EPUB in Calibre, which is useless when the
  file is already an EPUB and Calibre is what fell over. EPUB inputs now get
  advice that applies to them, and the spine fault above is named in plain
  words instead of surfacing a Python traceback.

## 1.1.1 — 2026-08-01

A clean launch window. Starting the app from the Start Menu, the application
menu, or a terminal produced a screen of alarming-looking errors that were
never ours — GPU driver complaints, component-updater failures, extension
chatter — leaving a new user to conclude the app had crashed when it had in
fact started perfectly.

- **The browser we open no longer reports into our window.** `webbrowser.open`
  spawns the browser with no output redirection, so it inherited our stdout and
  stderr; Chrome writes a wall of startup diagnostics to stderr. The launch is
  now wrapped so the browser gets `devnull` instead.
- **The engine's deprecation warning is suppressed for real.** The filters
  existed but were registered in the TTS adapter, and the startup check imports
  `chatterbox` (and so `perth`) to report whether the engine is present — which
  happens without the adapter ever being loaded. They now live in
  `ebook_audiobook/quiet.py`, imported before either path pulls the engine in.

A normal start is now two lines: where it is running, and how to stop it. Set
`EBAB_VERBOSE=1` to get everything back when debugging the engine.

## 1.1.0 — 2026-08-01

A large release. Hardware support is the headline: current NVIDIA and AMD
GPUs work where they previously could not run at all, Apple Silicon is
properly supported, and a render no longer has to take over the machine.

> **Upgrading:** any book you had part-rendered will re-render once. The
> PyTorch version is now part of each segment's content hash, so cached audio
> from the old engine is correctly discarded rather than being spliced
> together with new audio. Finished `.m4b` files are untouched.

**Current GPUs work.** The app was pinned to PyTorch 2.6.0 — not by choice, but
because Chatterbox declares `torch==2.6.0` and we inherited it. That build's CUDA
kernels stop at `sm_90`, so **RTX 50-series cards could not run at all**, and its
ROCm index predates RDNA4, so **RX 9000-series could not either**. Both are
current, actively-sold hardware.

PyTorch is now 2.9.1, pinned by us. Chatterbox is installed with `--no-deps` and
its dependency list is curated in `ebook_audiobook/torchbuild.py`, which also
drops two packages it never needed: `gradio` (its demo UI, never imported by the
library) and `spacy-pkuseg` (Chinese segmentation, already behind a
`try/except`). `einops` is added, because Chatterbox imports it unguarded in
three modules and never declares it.

Validated before shipping, on an RTX 3070 Ti against a 2.6.0 baseline: identical
transcriptions on every clip, identical durations, identical peak VRAM, and 0.98x
the speed. The audio differs numerically — different float kernels — but not
audibly.

- **NVIDIA now gets CUDA 12.8 or 12.6**, chosen from the card's compute
  capability. 12.8 covers RTX 20-series and newer including Blackwell; older
  GTX 900/1000 cards get 12.6, which 12.8 has no kernels for. On a two-GPU
  machine the older card decides, so both keep working. `--cuda128` /
  `--cuda126` override it.
- **If the wrong build ever lands**, `check` now says so and names the flag that
  fixes it, instead of leaving an opaque CUDA error to surface mid-render. The
  check honours CUDA's minor-version rule, so an RTX 40-series card (`sm_89`) on
  a build listing `sm_86` is correctly treated as fine.
- **AMD moves to ROCm 6.4**, needed for RX 9000-series. It requires a newer
  `amdgpu` kernel driver than 6.2 did.
- **Intel Macs are now told the truth.** PyTorch stopped building for macOS
  x86_64 after 2.2.2, so the speech engine cannot be installed there — and could
  not before this change either; the installer simply failed with pip's "no
  matching distribution". It now says so and installs everything else.
- **One place decides which build to install.** The CUDA index URL had been
  written out in six places and the ROCm one in five. CI now fails the build if
  either installer names an index more than once, and resolves all four builds
  on every run to assert each one is what it claims.
- **The advertised download sizes were all wrong**, and are now measured rather
  than estimated. ROCm was the worst — "about 2 GB" for a 4.65 GB download — and
  the CPU build has claimed "about 250 MB" for a 400 MB download since 1.0.0. CI
  re-measures every build on each run and fails if a figure drifts from reality,
  so nobody on a metered connection is misled again.
- **In-progress books re-render once.** The torch version is now part of
  `engine_version`, which is folded into every segment's content hash — without
  it, a book half-rendered on one PyTorch would resume on another and splice two
  model stacks into a single audiobook with nothing reporting a problem.

**Renders no longer have to take over the machine.** A new render intensity —
Full speed (default), Balanced, or Quiet/background — trades wall-clock time for
a computer you can keep using. Quiet caps PyTorch's CPU threads (which is what
otherwise pins every core and spins the fans), drops the render thread's
scheduling priority, and rests between segments so the silicon can cool. On
Apple Silicon it additionally requests Darwin's background QoS, which moves the
work onto the efficiency cores — the difference between a warm fanless MacBook
and a hot one. Set it globally in Settings, per conversion in the render dialog,
or with `--power quiet` on the command line.

The reported chars/sec deliberately excludes resting time, so choosing a quieter
mode doesn't make the hardware look slower than it is. And because POSIX
niceness is one-way for an unprivileged process, the worker thread is retired
after a quiet render rather than letting its lowered priority leak into every
job that follows.

**AMD Radeon works out of the box on Linux.** The installer detects a discrete
Radeon and installs PyTorch's ROCm build, and cards ROCm doesn't officially list
— the RX 6700/6600 and RX 7600/7700/7800 families, all mainstream parts — get
`HSA_OVERRIDE_GFX_VERSION` worked out and baked into the launcher, so they are
visible rather than silently absent. `--rocm` forces it. Integrated Radeon
graphics stay on the CPU build, which is faster for them than ROCm. On Windows,
where PyTorch has no ROCm wheels, an AMD card is now named and explained instead
of being passed over as "no GPU detected".

A ROCm build of PyTorch impersonates the CUDA API, so an AMD GPU was previously
reported to the user as NVIDIA. Vendor is now determined by `torch.version.hip`,
and if ROCm is installed but can't see the Radeon, that specific situation is
detected and the fix explained.

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

### Installer fixes carried over from the unreleased 1.0.2

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
