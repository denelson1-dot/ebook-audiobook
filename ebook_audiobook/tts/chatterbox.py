"""Chatterbox engine (Resemble AI). Primary narrator for real renders.

All heavy imports are deferred to ``load()`` so importing this module (and thus
the whole app) never requires torch. The model is loaded once and kept resident
on the GPU for the life of the worker; segments are rendered one at a time.
"""

from __future__ import annotations

import contextlib
import logging
import os

import numpy as np

from .. import device, quiet
from .adapter import AudioClip, TTSAdapter, VoiceConfig

# Imported for its side effect: the engine's import-time noise is filtered out
# before we pull the library in below. See ebook_audiobook/quiet.py.
_VERBOSE = quiet.VERBOSE


def _hush_loggers() -> None:
    if _VERBOSE:
        return
    for name in ("huggingface_hub", "hf_xet", "transformers", "diffusers"):
        logging.getLogger(name).setLevel(logging.ERROR)


@contextlib.contextmanager
def _quiet_io():
    """Silence stdout+stderr for the wrapped engine call. Safe here because
    progress bars are disabled (nothing useful streams during the call) and
    exceptions still propagate normally once the block exits."""
    if _VERBOSE:
        yield
        return
    with open(os.devnull, "w") as devnull, contextlib.redirect_stdout(devnull), \
            contextlib.redirect_stderr(devnull):
        yield


class ChatterboxAdapter(TTSAdapter):
    def __init__(self, voice: VoiceConfig):
        super().__init__(voice)
        self._model = None
        self._model_sr: int | None = None
        # Device the model is running on *now*. Can change mid-render if the GPU
        # runs out of memory (see _fall_back_to_cpu).
        self._device: str | None = None
        # Device the model was originally loaded on. This — not _device — is what
        # engine_version reports, so an out-of-memory fallback partway through a
        # render doesn't change every segment's content hash and silently
        # invalidate hours of already-rendered audio.
        self._load_device: str | None = None
        self._version: str | None = None

    @property
    def engine_version(self) -> str:
        # engine + model + device. Voice params are content-addressed separately
        # via hashing.voice_key, so they don't need to be repeated here.
        return self._version or "chatterbox"

    def _load_on(self, device_kind: str) -> None:
        """Load (or reload) the model onto one specific device."""
        with _quiet_io():
            from chatterbox.tts import ChatterboxTTS

            self._model = ChatterboxTTS.from_pretrained(device=device_kind)
            # Embed the reference voice ONCE here (not per chunk). Subsequent
            # generate() calls reuse self._model.conds, which is both faster and
            # more consistent than re-embedding the clip every segment. With no
            # reference clip, the model's built-in default voice is used.
            if self.voice.reference_clip:
                self._model.prepare_conditionals(
                    self.voice.reference_clip, exaggeration=self.voice.exaggeration
                )
        self._device = device_kind
        self._model_sr = int(getattr(self._model, "sr", 24_000))

    def load(self) -> None:
        if self._model is not None:
            return
        import sys

        _hush_loggers()
        dev = device.select_device()
        # Progress bars are disabled, so give a heads-up: load is ~10s, and the
        # very first run also downloads the model (~1 GB) from Hugging Face.
        print(f"loading TTS model on {dev.describe()} (first run downloads ~1 GB)...",
              file=sys.stderr, flush=True)
        try:
            import chatterbox as _cb

            pkg_ver = getattr(_cb, "__version__", "unknown")
        except Exception:
            pkg_ver = "unknown"

        try:
            self._load_on(dev.kind)
        except Exception as e:  # noqa: BLE001 - a GPU that can't hold the model
            if dev.kind == "cpu" or not device.is_out_of_memory(e):
                raise
            # The card was detected but hasn't the memory to load the model at
            # all. The CPU always can, so say what happened and use it rather
            # than refusing to render.
            print(f"  {dev.kind} ran out of memory loading the model — "
                  f"continuing on the CPU (slower)", file=sys.stderr, flush=True)
            self._model = None
            device.empty_cache(dev.kind)
            self._load_on("cpu")

        self._load_device = self._device
        # The torch version belongs in here. engine_version is folded into every
        # segment's content hash, so without it a book half-rendered on one torch
        # would resume on another and splice two model stacks into a single
        # audiobook with nothing reporting a problem. Major.minor only: a patch
        # release isn't worth re-rendering a whole book for.
        try:
            import torch

            torch_tag = "torch" + ".".join(torch.__version__.split(".")[:2])
        except Exception:  # noqa: BLE001
            torch_tag = "torch?"
        self._version = f"chatterbox-{pkg_ver}-{torch_tag}-{self._load_device}"

    def _fall_back_to_cpu(self) -> bool:
        """Move the model to the CPU after the GPU ran out of memory.

        A long render is hours of work; losing all of it because one unusually
        long segment wouldn't fit is a bad trade when the CPU can finish the job.
        Returns False if we're already on the CPU (nothing left to fall back to).
        """
        import sys

        if self._device == "cpu":
            return False
        print(f"  {self._device} out of memory — moving the model to the CPU for "
              f"the rest of this render (slower, same audio)",
              file=sys.stderr, flush=True)
        failed = self._device
        self._model = None
        device.empty_cache(failed)
        self._load_on("cpu")
        return True

    @property
    def active_device(self) -> str | None:
        """Device currently in use — may differ from the one in engine_version
        if an out-of-memory fallback happened."""
        return self._device

    def unload(self) -> None:
        if self._model is None:
            return
        released = self._device
        try:
            del self._model
        except Exception:  # noqa: BLE001
            pass
        self._model = None
        if released:
            device.empty_cache(released)

    def _generate(self, text: str, gen_kwargs: dict):
        """One generation, surviving a GPU that runs out of memory.

        VRAM pressure is not constant across a book: a long paragraph, or a
        stretch the model decides to sample for longer, can exhaust a card that
        rendered the previous thousand segments fine. Dropping three hours of
        work at that point is the wrong answer, so an out-of-memory is retried
        once with the cache flushed (which is usually enough — fragmentation
        rather than a genuine shortfall), and only then does the model move to
        the CPU for the rest of the render.
        """
        try:
            with _quiet_io():
                return self._model.generate(text, **gen_kwargs)
        except Exception as e:  # noqa: BLE001 - only OOM is handled; rest re-raise
            if not device.is_out_of_memory(e):
                raise
            device.empty_cache(self._device)

        try:
            with _quiet_io():
                return self._model.generate(text, **gen_kwargs)
        except Exception as e:  # noqa: BLE001
            if not device.is_out_of_memory(e) or not self._fall_back_to_cpu():
                raise

        with _quiet_io():
            return self._model.generate(text, **gen_kwargs)

    def synthesize(self, text: str) -> AudioClip:
        if self._model is None:
            self.load()
        import torch

        if self.voice.seed:
            torch.manual_seed(self.voice.seed)

        # Reference conditionals were prepared once in load(), so we do NOT pass
        # audio_prompt_path here — generate() reuses self._model.conds.
        gen_kwargs = {
            "exaggeration": self.voice.exaggeration,
            "cfg_weight": self.voice.cfg_weight,
            "temperature": self.voice.temperature,
            "repetition_penalty": self.voice.repetition_penalty,
            "min_p": self.voice.min_p,
            "top_p": self.voice.top_p,
        }
        wav = self._generate(text, gen_kwargs)

        # Normalize to mono float32 numpy.
        if hasattr(wav, "detach"):
            wav = wav.detach().to("cpu").float()
            arr = wav.numpy()
        else:
            arr = np.asarray(wav, dtype=np.float32)
        arr = np.squeeze(arr)
        if arr.ndim > 1:
            arr = arr.mean(axis=0)
        arr = arr.astype(np.float32)

        target_sr = self.voice.sample_rate
        if self._model_sr and self._model_sr != target_sr:
            import torchaudio

            resampled = torchaudio.functional.resample(
                torch.from_numpy(arr), self._model_sr, target_sr
            )
            arr = resampled.numpy().astype(np.float32)
            out_sr = target_sr
        else:
            out_sr = self._model_sr or target_sr

        return AudioClip(samples=arr, sample_rate=out_sr)
