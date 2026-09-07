# Bundled narrator voices

Twelve reference clips shipped with the application, so a new install has
usable voices without anyone having to find or record one: four English,
two French, four Spanish, two Japanese. Each carries the language it was recorded in, and
each language has its own default narrator (`DEFAULT_BUNDLED_BY_LANGUAGE` in
`ebook_audiobook/voices.py`).

## Provenance and licence

Public domain. No attribution required, no copyright restriction, no
licence text to reproduce. Supplied by the project maintainer as 60-second
narration samples (WAV), one reader each, converted here to 24 kHz mono FLAC.

- **English** — `male-north-american`: Mark F. Smith; `female-north-american`:
  Elizabeth Klett; `male-british`: Peter Yearsley; `female-british`:
  Ruth Golding. These replaced the original five English clips on 2026-09-02;
  those are kept under `archive/` and are not shipped.
- **French** — `female-french`: Nadine Eckert-Boulet; `male-french`:
  Gilles G. Le Blanc. The female voice is the default narrator when the
  interface is in French.
- **Spanish** — `female-spanish-latin-american`: Karen Savage;
  `male-spanish-latin-american`: Mario Pineda; `female-spanish-european`:
  Marian (dreamvoz); `male-spanish-european`: "tux". Added 2026-09-07.
- **Japanese** — `female-japanese`: kaseumin; `male-japanese`: ekzemplaro.
  Added 2026-09-07.

The "floor" figure `tools/prepare_voices.py` prints is not a noise measure for
any of these: it is the level of the quietest frames, and a continuous
60-second narration has no silence to measure, only breaths and word tails. To
judge a recording, compare the quietest *sustained* window against the clips
already here — the shipped ten sit between −63 dB and −97 dB by that measure.

Spanish ships two accent regions rather than one "neutral" reader, because
there is no such thing: a Mexican and a Peninsular narrator are audibly
different within a sentence. The display names say the region — matching how
the English clips say "North American" and "British" rather than naming a
country — while the readers above are the actual individuals. The Latin
American female is the default, on speaker numbers.

Recording the provenance is the point of this file: audio shipped with no
note of where it came from is very hard to reconstruct later, and "we
believe it was fine" is not an answer anyone wants to give afterwards.

## Why these files look like this

- **FLAC, 24 kHz, mono.** 24 kHz is the engine's own `S3GEN_SR`, so nothing
  is resampled when a clip is loaded. FLAC is lossless and roughly half the
  size of the equivalent WAV, which matters because these ride in every
  wheel.
- **Leading and trailing silence trimmed, peaks normalised to −3 dBFS.**
- **Full length kept.** Chatterbox reads a reference clip three ways: the
  first 10 s set timbre (`DEC_COND_LEN`), the first 6 s set prosody
  (`ENC_COND_LEN`), and the *whole* clip feeds the speaker embedding. So the
  opening seconds do most of the work and the rest still contributes
  identity — which is also why the silence trim matters more than it looks.

## One that did not make it

A male British recording was prepared and then dropped. Its noise was
*continuous and under the speech*, not just in the gaps — 2.9% digital
silence against 23–29% for the three kept here, and a −61 dB gap floor
against −95 dB. The other three had been noise-gated in production; that
one had not.

Chatterbox clones what it is given, so that noise became part of the
speaker's timbre: the generated voice hissed whenever it spoke and fell
completely silent in the pauses the model invented. No filtering fixes
that, because the noise cannot be separated from the speech it sits under.

When choosing a replacement, the numbers that mattered were the gap floor
(−95 dB, not −61 dB) and continuous noise under speech. `tools/prepare_voices.py`
reports both.

## Adding or replacing one

Drop a clip in, add an entry to `BUNDLED` in `ebook_audiobook/voices.py`,
and make sure the first ten seconds are clean, representative, continuous
speech. That is the part listeners will hear in the finished audiobook.

The 60-second source WAVs are **inputs, not artefacts**: `tools/prepare_voices.py`
reads them from a local directory and writes the FLACs here, and only the
FLACs are committed and shipped. The sources are named in that script's
`SOURCES` map so it is clear which file produced which clip, but they are
not in the repository — they are several times the size of the output and
re-deriving a clip from one is a deliberate act, not part of a build. Keep
them somewhere durable if a clip may need re-cutting.
