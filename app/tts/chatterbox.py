"""Chatterbox engine (Resemble AI). Primary narrator for real renders.

All heavy imports are deferred to ``load()`` so importing this module (and thus
the whole app) never requires torch. The model is loaded once and kept resident
on the GPU for the life of the worker; segments are rendered one at a time.
"""

from __future__ import annotations

import contextlib
import logging
import os
import warnings

import numpy as np

from .adapter import AudioClip, TTSAdapter, VoiceConfig

# The ML stack is noisy: a "loaded PerthNet" stdout line, tqdm sampling bars, and
# a handful of import-time deprecation warnings from perth/diffusers/HF. Silence
# them by default so our own progress line is the only output. Set EBAB_VERBOSE=1
# to restore everything (useful when debugging the engine).
_VERBOSE = os.environ.get("EBAB_VERBOSE") == "1"
if not _VERBOSE:
    os.environ.setdefault("TQDM_DISABLE", "1")  # kills the sampling progress bars
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    warnings.filterwarnings("ignore", message=r".*pkg_resources is deprecated.*")


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


def _select_device():
    """Prefer CUDA, then Apple Silicon (MPS), then CPU. Same model and output
    quality on every device — only speed differs."""
    import torch

    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


class ChatterboxAdapter(TTSAdapter):
    def __init__(self, voice: VoiceConfig):
        super().__init__(voice)
        self._model = None
        self._model_sr: int | None = None
        self._device: str | None = None
        self._version: str | None = None

    @property
    def engine_version(self) -> str:
        # engine + model + device. Voice params are content-addressed separately
        # via hashing.voice_key, so they don't need to be repeated here.
        return self._version or "chatterbox"

    def load(self) -> None:
        if self._model is not None:
            return
        import sys

        _hush_loggers()
        self._device = _select_device()
        # Progress bars are disabled, so give a heads-up: load is ~10s, and the
        # very first run also downloads the model (~1 GB) from Hugging Face.
        print(f"loading TTS model on {self._device} (first run downloads ~1 GB)...",
              file=sys.stderr, flush=True)
        with _quiet_io():
            from chatterbox.tts import ChatterboxTTS

            try:
                import chatterbox as _cb

                pkg_ver = getattr(_cb, "__version__", "unknown")
            except Exception:
                pkg_ver = "unknown"
            self._model = ChatterboxTTS.from_pretrained(device=self._device)
            # Embed the reference voice ONCE here (not per chunk). Subsequent
            # generate() calls reuse self._model.conds, which is both faster and
            # more consistent than re-embedding the clip every segment. With no
            # reference clip, the model's built-in default voice is used.
            if self.voice.reference_clip:
                self._model.prepare_conditionals(
                    self.voice.reference_clip, exaggeration=self.voice.exaggeration
                )
        self._model_sr = int(getattr(self._model, "sr", 24_000))
        self._version = f"chatterbox-{pkg_ver}-{self._device}"

    def unload(self) -> None:
        if self._model is None:
            return
        try:
            import torch

            del self._model
            self._model = None
            if self._device == "cuda":
                torch.cuda.empty_cache()
        except Exception:
            self._model = None

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
        with _quiet_io():
            wav = self._model.generate(text, **gen_kwargs)

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
