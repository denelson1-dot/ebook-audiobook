#!/usr/bin/env python3
"""Turn source recordings into the reference clips shipped in the package.

Run from the repo root:

    python tools/prepare_voices.py ~/Downloads

Committed rather than done by hand because the choices below are not obvious and
would otherwise live in someone's memory.

What matters, and why
---------------------
Chatterbox reads a reference clip three ways: the first 10 s set timbre
(``DEC_COND_LEN``), the first 6 s set prosody (``ENC_COND_LEN``), and the whole
clip feeds the speaker embedding. So the opening seconds do nearly all the
audible work.

That makes *where a clip starts* a real decision. The first attempt at this
picked the densest run of speech — and on a recording whose noise floor swings
from −59 dB to −46 dB it landed the conditioning window on the noisiest passage,
which is how a hiss got into a shipped voice. Background noise in a reference is
not incidental: it is cloned into every sentence of a ten-hour audiobook.

So the window is chosen for quiet first, and density only among the quiet
candidates. Nothing is denoised: a denoiser's artefacts would be cloned just as
faithfully as the hiss it removed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import librosa
import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfiltfilt

SR = 24_000          # the engine's own S3GEN_SR, so nothing is resampled at load
COND_WINDOW = 12.0   # the 10 s that condition timbre, plus margin
PEAK_DBFS = -3.0     # consistent headroom, so no voice is louder than another
HIGHPASS_HZ = 80     # below this there is no voice, only room
MIN_DENSITY = 0.85   # of the best density available, before quiet decides
QUIET_TOLERANCE_DB = 2.0  # a window this close to the quietest counts as tied

SOURCES = {
    # 60-second narration samples, one reader each. The earlier English clips
    # (from MP3s) are kept under assets/voices/archive/.
    "Mark_F_Smith_North_American_Male_ebook_narration_60s.wav": "male-north-american.flac",
    "Elizabeth_Klett_North_American_Female_ebook_narration_60s.wav": "female-north-american.flac",
    "Peter_Yearsley_British_Male_ebook_narration_60s.wav": "male-british.flac",
    "Ruth_Golding_British_Female_ebook_narration_60s.wav": "female-british.flac",
    "Nadine_Eckert-Boulet_ebook_narration_60s.wav": "female-french.flac",
    "Gilles_G_Le_Blanc_ebook_narration_60s.wav": "male-french.flac",
}


def derumble(y: np.ndarray) -> np.ndarray:
    """Remove everything below the lowest note a human voice makes.

    A male fundamental bottoms out around 85 Hz, so nothing under 80 Hz is
    speech — it is traffic, air conditioning, desk thump and mains hum. One of
    the source recordings carried 4.5% of its total energy down there, against
    0.2% for the cleanest of the four, and the speaker encoder has no way to
    know that is not part of who the man is.

    Zero-phase, so it does not smear the transients the decoder conditions on.
    """
    sos = butter(4, HIGHPASS_HZ, btype="highpass", fs=SR, output="sos")
    return sosfiltfilt(sos, y).astype(np.float32)


def db(v: float) -> float:
    return 20 * np.log10(max(float(v), 1e-12))


def analyse(y: np.ndarray, hop: int = 512):
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=hop)[0]
    return rms, rms > rms.max() * 0.08


def choose_start(y: np.ndarray, hop: int = 512) -> int:
    """The opening the finished audiobook will inherit: quiet first, then busy."""
    rms, voiced = analyse(y, hop)
    frames = int(COND_WINDOW * SR / hop)
    if len(rms) <= frames * 2:
        return 0

    starts, density, floor = [], [], []
    for i in range(0, len(rms) - frames, max(1, int(0.25 * SR / hop))):
        seg, vseg = rms[i:i + frames], voiced[i:i + frames]
        gaps = seg[~vseg]
        if not len(gaps) or vseg.mean() < 0.5:
            continue                       # no gaps to judge, or barely any speech
        starts.append(i)
        density.append(float(vseg.mean()))
        floor.append(db(gaps.mean()))
    if not starts:
        return 0

    density, floor = np.array(density), np.array(floor)
    # Among windows busy enough to be useful, take the quietest — but a window
    # a fraction of a decibel quieter is not worth starting thirty seconds later,
    # because everything before the start is material the speaker embedding never
    # sees. So: quietest first, then the earliest that is essentially as quiet.
    keep = np.arange(len(starts))[density >= density.max() * MIN_DENSITY]
    quietest = floor[keep].min()
    good = keep[floor[keep] <= quietest + QUIET_TOLERANCE_DB]
    idx = int(good[0])

    start = starts[idx] * hop
    # Back off to the nearest gap so the clip does not open mid-word.
    quiet_before = np.where(~voiced[:starts[idx]])[0]
    if len(quiet_before):
        start = int(quiet_before[-1]) * hop
    return start


def prepare(src: Path, dest: Path) -> None:
    y, _ = librosa.load(src, sr=SR, mono=True)
    y = derumble(y)
    start = choose_start(y)
    y = y[start:]

    # Trim silence at the ends, keeping a breath so the first word is not clipped.
    idx = np.where(np.abs(y) > np.abs(y).max() * 0.02)[0]
    if len(idx):
        lead = int(0.05 * SR)
        y = y[max(0, int(idx[0]) - lead): min(len(y), int(idx[-1]) + lead)]

    peak = float(np.abs(y).max()) or 1.0
    y = y * (10 ** (PEAK_DBFS / 20) / peak)

    dest.parent.mkdir(parents=True, exist_ok=True)
    sf.write(dest, y, SR, format="FLAC", subtype="PCM_16")

    rms, voiced = analyse(y)
    head = rms[: int(10 * SR / 512)]
    hv = voiced[: int(10 * SR / 512)]
    gaps = head[~hv]
    print(f"  {dest.name:28} start {start / SR:5.1f}s  {len(y) / SR:5.1f}s  "
          f"first-10s: {hv.mean() * 100:3.0f}% speech, "
          f"floor {db(gaps.mean()) if len(gaps) else float('nan'):6.1f} dB  "
          f"{dest.stat().st_size / 1e6:4.2f} MB")


def main(argv: list[str]) -> int:
    src_dir = Path(argv[1]).expanduser() if len(argv) > 1 else Path.home() / "Downloads"
    out = Path(__file__).resolve().parent.parent / "ebook_audiobook" / "assets" / "voices"
    # Only what is there: a language's clips are added one batch at a time, and
    # the earlier sources need not be re-fetched to prepare a new one.
    present = {s: d for s, d in SOURCES.items() if (src_dir / s).is_file()}
    missing = sorted(set(SOURCES) - set(present))
    if not present:
        print(f"nothing to prepare in {src_dir}: {', '.join(missing)}", file=sys.stderr)
        return 1
    if missing:
        print(f"not in {src_dir}, leaving as shipped: {', '.join(missing)}")
    print(f"preparing {len(present)} voice(s) from {src_dir}")
    for src, dest in present.items():
        prepare(src_dir / src, out / dest)
    print(f"\ntotal {sum(p.stat().st_size for p in out.glob('*.flac')) / 1e6:.1f} MB shipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
