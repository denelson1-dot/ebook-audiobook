# Bundled narrator voices

Five reference clips shipped with the application, so a new install has
usable voices without anyone having to find or record one.

## Provenance and licence

Public domain. No attribution required, no copyright restriction, no
licence text to reproduce. Supplied by the project maintainer, converted
here from the original MP3s to 24 kHz mono FLAC.

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
