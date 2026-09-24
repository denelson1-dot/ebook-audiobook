"""Chatterbox engine (Resemble AI). Primary narrator for real renders.

All heavy imports are deferred to ``load()`` so importing this module (and thus
the whole app) never requires torch. The model is loaded once per task and kept
resident on its device; segments are rendered one at a time.

How it runs is chosen at load from what the machine has room for (see
:mod:`ebook_audiobook.tiers`): full precision, a compact half-precision model,
or the CPU. When the card runs out of memory it steps down a rung rather than
losing the render.
"""

from __future__ import annotations

import contextlib
import gc
import logging
import os
import sys
import time

import numpy as np

from .. import debuglog, device, narration_langs, quiet, tiers
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
    # UTF-8 explicitly: the platform default on Windows is cp1252, and a
    # library echoing Japanese text into a cp1252 stream would raise.
    with open(os.devnull, "w", encoding="utf-8", errors="replace") as devnull, contextlib.redirect_stdout(devnull), \
            contextlib.redirect_stderr(devnull):
        yield


# --- placing the model -------------------------------------------------------
#
# These reach into chatterbox-tts 0.1.7's internals (t3, s3gen, s3gen.flow,
# s3gen.tokenizer, ve, conds). torchbuild pins that version exactly and CI loads
# it for real, so a release that moved them would be caught there.

_LEAN_CLASSES: dict = {}


def _lean_class(cls: type) -> type:
    """A subclass of S3Gen whose ``device`` follows its flow network.

    Upstream reads S3Gen's device off its speech tokenizer, and every tensor of
    a narration is put on that device. With the tokenizer moved to the CPU, that
    would drag the whole decoder there too.
    """
    if getattr(cls, "_ebab_lean", False):
        return cls
    if cls not in _LEAN_CLASSES:
        _LEAN_CLASSES[cls] = type(cls.__name__, (cls,), {
            "_ebab_lean": True,
            "device": property(lambda self: next(self.flow.parameters()).device),
        })
    return _LEAN_CLASSES[cls]


def _offload_speech_tokenizer(model) -> None:
    """Move the speech tokenizer (0.46 GB) off the card.

    It turns the reference clip into speech tokens once, in
    prepare_conditionals, and is never used while narrating, so moving it
    afterwards changes nothing that is heard: the audio is bit-for-bit the
    same, and 0.46 GB is often the difference between fitting and not.
    """
    s3gen = getattr(model, "s3gen", None)
    if s3gen is None or not hasattr(s3gen, "tokenizer") or not hasattr(s3gen, "flow"):
        return
    s3gen.__class__ = _lean_class(type(s3gen))
    s3gen.tokenizer.to("cpu")


def _compact(model) -> None:
    """Put the main model (T3) in bfloat16, about a gigabyte smaller.

    Cast while it is still on the CPU, so the card never holds the
    full-precision copy: loading straight onto a 3 GB card and casting there
    runs out of memory before the cast. Autocast is scoped to T3 alone. Around
    the whole of generate() it also casts S3Gen, whose cached bf16 weights then
    cost back most of the saving, and S3Gen's flow in bf16 fails outright.
    """
    import torch

    # The rotary position tables stay float32. Rounded to bfloat16 they are
    # 0.3% off, which by position 1200 (a long passage) turns into an angle
    # error of a radian: the model loses track of where it is in the sentence
    # exactly where it is already most prone to wander. Hugging Face's own bf16
    # loading keeps them at full precision for the same reason.
    keep = {name: buf.detach().clone() for name, buf in model.t3.named_buffers()
            if name.endswith("inv_freq")}
    model.t3.to(dtype=torch.bfloat16)
    for name, buf in keep.items():
        owner, _, attr = name.rpartition(".")
        model.t3.get_submodule(owner)._buffers[attr] = buf
    run = model.t3.inference

    def inference(*args, **kwargs):
        with torch.autocast("cuda", dtype=torch.bfloat16):
            return run(*args, **kwargs)

    model.t3.inference = inference


def _move(model, device_kind: str) -> None:
    model.t3.to(device_kind)
    model.s3gen.to(device_kind)
    model.ve.to(device_kind)
    if getattr(model, "conds", None) is not None:
        model.conds = model.conds.to(device_kind)
    model.device = device_kind


def _say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


class ChatterboxAdapter(TTSAdapter):
    def __init__(self, voice: VoiceConfig, tier: str | None = None):
        super().__init__(voice)
        self._model = None
        self._model_sr: int | None = None
        # The book's pinned tier: what its cached audio is. None for a book
        # that has never been narrated, which then takes whatever loads.
        self._pin = tier if tier in tiers.TIERS else None
        self._gpu: device.Device | None = None
        self._probe: tiers.Probe | None = None
        # Every way to run on this machine, best first (see tiers.ladder).
        self._ladder: list[tiers.Rung] = []
        # The rung running *now*. Moves down the ladder mid-render when the
        # card runs out of memory (see _step_down).
        self._rung: tiers.Rung | None = None
        # Device and tier the model was first loaded with. These, not _rung,
        # are what engine_version reports, so falling back partway through a
        # render doesn't change every segment's content hash and silently
        # invalidate hours of already-rendered audio.
        self._load_device: str | None = None
        self._identity: str | None = None
        self._version: str | None = None
        # Why this is running below a GPU machine's best rung, if it is:
        # "no_room" (nothing fitted in the card's free memory) or "ran_out"
        # (it ran out of memory, loading or narrating).
        self.reason: str | None = None
        # PyTorch's peak on the card during the last passage, for the debug log.
        self.last_peak: int | None = None

    @property
    def engine_version(self) -> str:
        # engine + model + device + precision. Voice params are
        # content-addressed separately via hashing.voice_key.
        return self._version or "chatterbox"

    @property
    def identity_tier(self) -> str | None:
        """The tier this book's audio belongs to, for the job to pin."""
        return self._identity

    @property
    def active_device(self) -> str | None:
        """Device currently in use. May differ from the one in engine_version
        after an out-of-memory fallback."""
        return self._rung.device if self._rung else None

    @property
    def runtime(self) -> dict | None:
        """Where narration is happening, for the job page."""
        if self._rung is None:
            return None
        on_gpu = self._rung.device != "cpu" and self._gpu is not None
        total = self._probe.total if self._probe else None
        budget = self._probe.budget if self._probe else None
        return {
            "device": self._rung.device,
            "name": self._gpu.name if on_gpu else device.cpu_name(),
            "backend": self._gpu.backend if on_gpu else "cpu",
            "tier": self._rung.tier,
            "vram_gb": round(total / tiers.GiB, 1) if total else None,
            # What the choice was made from: the card's free memory, less
            # headroom, less any EBAB_VRAM_BUDGET_GB. Not the card's size: a
            # big card that another program is using is short of room too.
            "budget_gb": round(budget / tiers.GiB, 1) if budget else None,
            "reason": self.reason,
        }

    # --- loading --------------------------------------------------------------

    def _model_class(self):
        # Two models, one interface: the English weights the app has always
        # used, and the multilingual ones for everything else. Chosen by the
        # voice's language, so an English book never pays for the switch.
        if self.voice.language == "en":
            from chatterbox.tts import ChatterboxTTS as Model
        else:
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS as Model
        return Model

    def _checkpoint(self):
        """The local copy of the weights, when it is complete.

        Loaded from there directly, because from_pretrained asks Hugging Face
        about every file on every load, even when all of it is on disk. On a
        slow or captive network that alone could hold a render at "loading"
        for minutes, in an app that promises to work offline.
        """
        pack = narration_langs.pack_for(self.voice.language)
        if narration_langs.is_installed(pack.id):
            return narration_langs.snapshot_dir()
        return None

    def _build(self, device_kind: str):
        Model = self._model_class()
        source = self._checkpoint()
        if source is not None and hasattr(Model, "from_local"):
            return Model.from_local(source, device_kind)
        return Model.from_pretrained(device=device_kind)

    def _cap_memory(self) -> None:
        """Hold PyTorch to the budget the ladder was built from.

        Without a cap, Windows' NVIDIA driver meets an overflow by borrowing
        system RAM, and narration carries on at a crawl with no error for the
        fallback to catch. With one, PyTorch refuses first, with an
        out-of-memory error _generate knows what to do with.
        """
        if self._probe is None or self._probe.kind != "cuda":
            return
        # 1.0 when the card couldn't be read: undo an earlier task's cap
        # rather than inherit it.
        fraction = tiers.fraction_for(self._probe) or 1.0
        try:
            import torch

            torch.cuda.set_per_process_memory_fraction(fraction)
        except Exception:  # noqa: BLE001 - without a cap, the old behaviour
            pass

    def _load_rung(self, rung: tiers.Rung) -> None:
        """Load (or reload) the model onto one rung of the ladder."""
        t0 = time.monotonic()
        if rung.device == "cuda":
            import torch

            torch.cuda.reset_peak_memory_stats()
        try:
            with _quiet_io():
                if rung.tier == tiers.COMPACT:
                    model = self._build("cpu")
                    _compact(model)
                    _move(model, rung.device)
                else:
                    model = self._build(rung.device)
                # Held before the voice is prepared, so an out-of-memory there
                # still lets _release free what is on the card.
                self._model = model
                # Embed the reference voice ONCE here (not per chunk). Later
                # generate() calls reuse self._model.conds, which is both faster
                # and more consistent than re-embedding the clip every segment.
                # With no reference clip, the built-in default voice is used.
                if self.voice.reference_clip:
                    model.prepare_conditionals(
                        self.voice.reference_clip, exaggeration=self.voice.exaggeration
                    )
                if rung.device == "cuda":
                    _offload_speech_tokenizer(model)
        except BaseException:
            # Never leave a model behind that loaded but has no voice: it
            # already holds the built-in one, and a retry of the passage would
            # narrate in that voice and file the audio under this book's key.
            self._model = None
            raise
        if rung.device == "cuda":
            device.empty_cache("cuda")
        self._rung = rung
        self._model_sr = int(getattr(model, "sr", 24_000))
        fields = {"device": rung.device, "tier": rung.tier, "language": self.voice.language,
                  "load_seconds": round(time.monotonic() - t0, 1)}
        if rung.device == "cuda":
            try:
                import torch

                fields["reserved_gb"] = round(torch.cuda.memory_reserved() / tiers.GiB, 2)
                fields["load_peak_gb"] = round(torch.cuda.max_memory_reserved() / tiers.GiB, 2)
            except Exception:  # noqa: BLE001
                pass
        debuglog.event("engine_loaded", **fields)

    def _release(self) -> None:
        """Drop the model and hand its memory back.

        gc.collect() is not optional here: the compact wrapper closes over T3's
        own method, a reference cycle that would otherwise hold a gigabyte on
        the card until the collector happened to run.
        """
        self._model = None
        gc.collect()
        for kind in {self._rung.device if self._rung else None,
                     self._probe.kind if self._probe else None} - {None}:
            device.empty_cache(kind)

    def load(self) -> None:
        if self._model is not None:
            return
        _hush_loggers()
        dev = device.select_device()
        self._gpu = dev
        self._probe = tiers.probe(dev.kind)
        self._ladder = tiers.ladder(self._probe, self._pin,
                                    model=tiers.model_for(self.voice.language))
        first = self._ladder[0]
        if dev.kind == "cuda" and first.device == "cpu":
            self.reason = "no_room"
        debuglog.event(
            "engine_plan", gpu=dev.describe(), pinned=self._pin,
            free_gb=tiers.gb(self._probe.free), total_gb=tiers.gb(self._probe.total),
            budget_gb=tiers.gb(self._probe.budget), bf16=self._probe.bf16,
            capped_by_env=self._probe.capped_by_env,
            ladder=[tiers.label(r) for r in self._ladder])

        # Progress bars are disabled, so give a heads-up: load is ~10s, and the
        # very first run also downloads the model from Hugging Face.
        where = dev.describe() if first.device == dev.kind else f"{device.cpu_name()} (cpu)"
        if first.tier == tiers.COMPACT:
            where += (f", compact ({tiers.gb(self._probe.total)} GB card, "
                      f"{tiers.gb(self._probe.free)} GB free)")
        elif self.reason == "no_room":
            where += f" (the {tiers.gb(self._probe.total)} GB card hasn't room for the model)"
        pack = narration_langs.pack_for(self.voice.language)
        if narration_langs.is_installed(pack.id):
            _say(f"loading TTS model on {where}...")
        else:
            _say(f"loading TTS model on {where} (first run downloads about "
                 f"{pack.size_bytes / 1e9:.1f} GB)...")
        try:
            import chatterbox as _cb

            pkg_ver = getattr(_cb, "__version__", "unknown")
        except Exception:
            pkg_ver = "unknown"

        self._cap_memory()
        for i, rung in enumerate(self._ladder):
            below = self._ladder[i + 1] if i + 1 < len(self._ladder) else None
            try:
                self._load_rung(rung)
                break
            except Exception as e:  # noqa: BLE001 - a card that can't hold the model
                if below is None or not device.is_out_of_memory(e):
                    raise
                # Without its traceback, whose frames hold the half-loaded model.
                failure = e.with_traceback(None)
            # Detected, but hasn't the memory to load this way. The rung below
            # always has more room, and the CPU always can. Released here,
            # outside the except block, so the attempt is gone from the card.
            _say(f"  {tiers.label(rung)} ran out of memory loading the model — "
                 f"trying {tiers.label(below)}")
            debuglog.event("step_down", during="load", came_from=tiers.label(rung),
                           to=tiers.label(below), error=str(failure)[:300])
            self.reason = "ran_out"
            self._release()

        # The device this machine narrates on, not where this load landed. A
        # card that is short of room today, or ran out, keeps the key its
        # audio was made under, exactly as a fallback partway through a render
        # always has; otherwise a busy day would re-render a whole book.
        self._load_device = self._gpu.kind if self.reason else self._rung.device
        self._identity = tiers.identity(self._pin, self._rung)
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
        # The multilingual model is a different model, so its segments are
        # different segments. The language itself is in the voice key. Full
        # precision adds nothing, so audio cached before tiers is still found.
        model = "chatterbox" if self.voice.language == "en" else "chatterbox-mtl"
        self._version = (f"{model}-{pkg_ver}-{torch_tag}-{self._load_device}"
                         f"{tiers.version_suffix(self._identity)}")

    # --- running out of memory --------------------------------------------------

    def _step_down(self, error: BaseException) -> bool:
        """Carry on one rung down after the card ran out of memory.

        A long render is hours of work; losing all of it because one unusually
        long segment wouldn't fit is a bad trade when a smaller tier, or the
        CPU, can finish the job. Returns False when there is nowhere lower.
        """
        if self._rung is None or self._rung not in self._ladder:
            return False
        below = self._ladder[self._ladder.index(self._rung) + 1:]
        failed = self._rung
        for rung in below:
            _say(f"  {tiers.label(failed)} ran out of memory — continuing on "
                 f"{tiers.label(rung)} for the rest of this render (same voice)")
            debuglog.event("step_down", during="narrating", came_from=tiers.label(failed),
                           to=tiers.label(rung), error=str(error)[:300])
            self._release()
            try:
                self._load_rung(rung)
            except Exception as e:  # noqa: BLE001
                if rung.device == "cpu" or not device.is_out_of_memory(e):
                    raise
                failed, error = rung, e.with_traceback(None)
                continue
            self.reason = "ran_out"
            return True
        return False

    def unload(self) -> None:
        if self._model is None:
            return
        self._release()

    def _generate(self, text: str, gen_kwargs: dict):
        """One generation, surviving a card that runs out of memory.

        VRAM pressure is not constant across a book: a long paragraph, or a
        stretch the model decides to sample for longer, can exhaust a card that
        rendered the previous thousand segments fine. So an out-of-memory is
        retried once with the cache flushed (usually enough: fragmentation
        rather than a genuine shortfall), and only then does narration move a
        rung down, which gets the same single retry.
        """
        retried = False
        while True:
            try:
                with _quiet_io():
                    return self._model.generate(text, **gen_kwargs)
            except Exception as e:  # noqa: BLE001 - only OOM is handled; rest re-raise
                if not device.is_out_of_memory(e):
                    raise
                # Without its traceback. The traceback's frames hold the failed
                # passage's tensors, and while they live, emptying the cache
                # frees nothing and the next tier loads into a full card.
                failure = e.with_traceback(None)
            if not retried:
                retried = True
                device.empty_cache(self._rung.device)
                continue
            if not self._step_down(failure):
                raise failure
            retried = False

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
        if self.voice.language != "en":
            gen_kwargs["language_id"] = self.voice.language
        if self.active_device == "cuda":
            torch.cuda.reset_peak_memory_stats()
        wav = self._generate(text, gen_kwargs)
        # Read after, so a step-down to the CPU mid-passage reports nothing.
        self.last_peak = (torch.cuda.max_memory_reserved()
                          if self.active_device == "cuda" else None)

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
