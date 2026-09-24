"""Which way the engine runs on a given card, and what happens when it's wrong.

The machines that matter here are the ones nobody on the project owns: a 4 GB
laptop card, a 3 GB one, an RTX 20-series without bfloat16. So the card is a
number and the engine is a stub. What was learned on real hardware (an RTX
3070 Ti held to smaller budgets, and a CUDA out-of-memory injected mid-render)
is in ebook_audiobook/tiers.py; these pin the decisions it led to.
"""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from ebook_audiobook import device, tiers
from ebook_audiobook.tiers import COMPACT, ENGLISH, FULL, MULTILINGUAL, Probe, Rung

GiB = tiers.GiB
CPU = Rung("cpu", FULL)
GPU_FULL = Rung("cuda", FULL)
GPU_COMPACT = Rung("cuda", COMPACT)


@pytest.fixture(autouse=True)
def no_budget_env(monkeypatch):
    monkeypatch.delenv(tiers.BUDGET_ENV, raising=False)


def card(free_gb, total_gb=None, bf16=True):
    return Probe("cuda", int(free_gb * GiB), int((total_gb or free_gb + 0.5) * GiB), bf16)


# --- choosing a rung ------------------------------------------------------------

def test_a_roomy_card_runs_at_full_precision_with_compact_to_fall_back_on():
    assert tiers.ladder(card(7.0)) == [GPU_FULL, GPU_COMPACT, CPU]


@pytest.mark.parametrize("model", [ENGLISH, MULTILINGUAL])
def test_the_4gb_laptop_card_runs_compact(model):
    """The machine that started this: an RTX 3050 4 GB Laptop GPU. Full
    precision loads there and then overflows, which on Windows meant a silent
    crawl in system RAM."""
    assert tiers.ladder(card(3.5, total_gb=4.0), model=model) == [GPU_COMPACT, CPU]


def test_a_3gb_card_still_tries_compact_before_the_cpu():
    # Short of compact's generous narrating figure, but it loads.
    assert tiers.ladder(card(2.7, total_gb=3.0)) == [GPU_COMPACT, CPU]


def test_a_2gb_card_goes_straight_to_the_cpu():
    assert tiers.ladder(card(1.8, total_gb=2.0)) == [CPU]


def test_the_multilingual_model_needs_more_room_at_full_precision():
    free = 4.5  # enough for English at full precision, not for the other model
    assert tiers.ladder(card(free), model=ENGLISH)[0] == GPU_FULL
    assert tiers.ladder(card(free), model=MULTILINGUAL)[0] == GPU_COMPACT


def test_without_bfloat16_there_is_no_compact_rung():
    """RTX 20-series and older: bf16 there is emulated, and slower than fp32."""
    assert tiers.ladder(card(6.0, bf16=False)) == [GPU_FULL, CPU]
    assert tiers.ladder(card(3.5, total_gb=4.0, bf16=False)) == [CPU]


def test_a_bigger_rung_is_never_a_fallback_for_a_smaller_one():
    """Compact fits; full would only just load. Stepping *up* after compact ran
    out of memory would be pointless."""
    probe = card(3.3 + tiers.HEADROOM / GiB)   # budget 3.3: full loads, compact fits
    assert tiers.ladder(probe) == [GPU_COMPACT, CPU]


def test_a_card_that_cannot_be_read_is_treated_as_roomy():
    assert tiers.ladder(Probe("cuda", bf16=True)) == [GPU_FULL, GPU_COMPACT, CPU]


def test_apple_silicon_and_cpu_machines_have_no_tiers():
    assert tiers.ladder(Probe("mps")) == [Rung("mps", FULL), CPU]
    assert tiers.ladder(Probe("cpu")) == [CPU]


def test_the_budget_leaves_headroom_for_the_cuda_context():
    assert card(4.0).budget == int(4.0 * GiB) - tiers.HEADROOM


def test_the_environment_can_cap_the_budget(monkeypatch):
    monkeypatch.setenv(tiers.BUDGET_ENV, "3")
    assert tiers.ladder(card(7.0)) == [GPU_COMPACT, CPU]
    monkeypatch.setenv(tiers.BUDGET_ENV, "lots")   # nonsense is ignored
    assert tiers.ladder(card(7.0))[0] == GPU_FULL


def test_the_allocator_cap_is_the_budget_as_a_share_of_the_card():
    probe = card(3.5, total_gb=4.0)
    assert tiers.fraction_for(probe) == pytest.approx(probe.budget / probe.total)
    assert tiers.fraction_for(Probe("cpu")) is None


# --- pinning ------------------------------------------------------------------------

def test_a_compact_book_never_runs_above_compact():
    """Its cached audio is compact audio; running full would re-render it."""
    assert tiers.ladder(card(7.0), pinned=COMPACT) == [GPU_COMPACT, CPU]


def test_a_full_book_on_a_small_card_runs_compact_but_stays_full():
    """Resuming shouldn't throw away hours of audio because the card is busier
    today. It runs smaller, and its audio keeps the key it was made under."""
    assert tiers.ladder(card(3.5, total_gb=4.0), pinned=FULL) == [GPU_COMPACT, CPU]
    assert tiers.identity(FULL, GPU_COMPACT) == FULL


def test_an_unpinned_book_takes_what_it_loads_as():
    assert tiers.identity(None, GPU_COMPACT) == COMPACT
    assert tiers.identity("nonsense", CPU) == FULL


def test_only_compact_changes_the_cache_key():
    """Full precision adds nothing, so audio cached before tiers existed is
    still found after the upgrade."""
    assert tiers.version_suffix(FULL) == ""
    assert tiers.version_suffix(COMPACT) == "-bf16"


# --- probing a real torch's answers ------------------------------------------------

def _torch_with(free, total, bf16=True, capability=(8, 6), bf16_takes_arg=True, calls=None):
    calls = calls if calls is not None else []
    cuda = types.SimpleNamespace(
        mem_get_info=lambda: calls.append("mem_get_info") or (free, total),
        empty_cache=lambda: calls.append("empty_cache"),
        get_device_capability=lambda _i=0: capability,
    )
    if bf16_takes_arg:
        cuda.is_bf16_supported = lambda including_emulation=True: bf16
    else:
        def old(*a, **k):
            if a or k:
                raise TypeError("unexpected argument")
            return True
        cuda.is_bf16_supported = old
    mod = types.ModuleType("torch")
    mod.cuda = cuda
    return mod


def test_probe_hands_back_the_pool_before_measuring(monkeypatch):
    """What an earlier task left behind is released and then measured, not
    assumed free: memory still held by live tensors is not room."""
    calls = []
    monkeypatch.setitem(sys.modules, "torch", _torch_with(3 * GiB, 8 * GiB, calls=calls))
    p = tiers.probe("cuda")
    assert (p.free, p.total, p.bf16) == (3 * GiB, 8 * GiB, True)
    assert calls == ["empty_cache", "mem_get_info"]


def test_probe_asks_for_native_bfloat16_only(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _torch_with(8 * GiB, 8 * GiB, bf16=False))
    assert tiers.probe("cuda").bf16 is False


def test_probe_on_an_older_torch_reads_the_compute_capability(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", _torch_with(8 * GiB, 8 * GiB, capability=(7, 5),
                                                          bf16_takes_arg=False))
    assert tiers.probe("cuda").bf16 is False


def test_probe_never_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)
    assert tiers.probe("cuda") == Probe("cuda")


# --- the adapter walking the ladder ------------------------------------------------

class _OOM(RuntimeError):
    pass


def _oom():
    return _OOM("CUDA out of memory. Tried to allocate 20.00 MiB")


@pytest.fixture
def engine(monkeypatch):
    """ChatterboxAdapter on a pretend 8 GB card, with _load_rung replaced so the
    test decides which rungs load and what generate() does on each."""
    torch = types.ModuleType("torch")
    torch.__version__ = "2.9.1"
    torch.manual_seed = lambda s: None
    torch.cuda = types.SimpleNamespace(reset_peak_memory_stats=lambda: None,
                                       max_memory_reserved=lambda: 2 * GiB,
                                       empty_cache=lambda: None)
    pkg = types.ModuleType("chatterbox")
    pkg.__version__ = "0.1.7"
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "chatterbox", pkg)
    monkeypatch.setattr(device, "select_device",
                        lambda: device.Device("cuda", "Fake GPU", None, backend="cuda"))
    monkeypatch.setattr(tiers, "probe", lambda kind: card(7.0, total_gb=8.0))

    from ebook_audiobook.tts import chatterbox as cb
    from ebook_audiobook.tts.adapter import VoiceConfig

    class Model:
        sr = 24_000

        def __init__(self, rung, script):
            self.rung, self.script = rung, script

        def generate(self, text, **kw):
            step = self.script.get(self.rung, "ok")
            if step == "oom":
                raise _oom()
            if step == "boom":
                raise ValueError("not a memory problem")
            return np.zeros(2_400, dtype="float32")

    def make(tier=None, load_fails=(), script=None):
        script = script or {}
        adapter = cb.ChatterboxAdapter(VoiceConfig(language="en"), tier=tier)
        loads = []

        def load_rung(rung):
            loads.append(rung)
            if rung in load_fails:
                raise _oom()
            adapter._model = Model(rung, script)
            adapter._rung = rung
            adapter._model_sr = 24_000

        adapter._load_rung = load_rung
        adapter.loads = loads
        return adapter

    return make


def test_a_roomy_card_loads_full_precision(engine):
    a = engine()
    a.load()
    assert a.loads == [GPU_FULL]
    assert a.engine_version == "chatterbox-0.1.7-torch2.9-cuda"
    assert a.identity_tier == FULL and a.reason is None


def test_running_out_while_loading_tries_the_next_rung(engine):
    a = engine(load_fails={GPU_FULL})
    a.load()
    assert a.loads == [GPU_FULL, GPU_COMPACT]
    assert a.engine_version.endswith("-cuda-bf16")
    assert a.identity_tier == COMPACT and a.reason == "ran_out"


def test_a_pinned_full_book_keeps_its_key_when_it_has_to_run_compact(engine):
    a = engine(tier=FULL, load_fails={GPU_FULL})
    a.load()
    assert a.runtime["tier"] == COMPACT
    assert a.engine_version == "chatterbox-0.1.7-torch2.9-cuda"


def test_running_out_mid_passage_retries_then_steps_down(engine):
    a = engine(script={GPU_FULL: "oom"})
    a.load()
    version = a.engine_version
    clip = a.synthesize("A sentence.")
    assert clip.samples.size
    assert a.loads == [GPU_FULL, GPU_COMPACT]
    assert a.runtime["tier"] == COMPACT and a.reason == "ran_out"
    # The key stays what the book was loaded as: nothing already made is lost.
    assert a.engine_version == version


def test_it_steps_all_the_way_down_to_the_cpu(engine):
    a = engine(script={GPU_FULL: "oom", GPU_COMPACT: "oom"})
    a.load()
    a.synthesize("A sentence.")
    assert a.loads == [GPU_FULL, GPU_COMPACT, CPU]
    assert a.runtime["device"] == "cpu"


def test_a_rung_that_cannot_load_on_the_way_down_is_skipped(engine):
    a = engine(load_fails={GPU_COMPACT}, script={GPU_FULL: "oom"})
    a.load()
    a.synthesize("A sentence.")
    assert a.loads == [GPU_FULL, GPU_COMPACT, CPU]


def test_out_of_memory_with_nowhere_lower_is_raised(engine, monkeypatch):
    monkeypatch.setattr(tiers, "probe", lambda kind: Probe("cpu"))
    monkeypatch.setattr(device, "select_device",
                        lambda: device.Device("cpu", "CPU", None, backend="cpu"))
    a = engine(script={CPU: "oom"})
    a.load()
    with pytest.raises(_OOM):
        a.synthesize("A sentence.")


def test_other_failures_are_not_mistaken_for_memory(engine):
    a = engine(script={GPU_FULL: "boom"})
    a.load()
    with pytest.raises(ValueError, match="not a memory problem"):
        a.synthesize("A sentence.")
    assert a.loads == [GPU_FULL]


def test_a_card_with_no_room_says_so(engine, monkeypatch):
    monkeypatch.setattr(tiers, "probe", lambda kind: card(1.5, total_gb=2.0))
    a = engine()
    a.load()
    assert a.loads == [CPU]
    assert a.runtime == {"device": "cpu", "name": device.cpu_name(), "backend": "cpu",
                         "tier": FULL, "vram_gb": 2.0, "budget_gb": 1.2, "reason": "no_room"}
    # Keyed to the machine's GPU, as a fallback partway through always was:
    # a book begun on the card resumes on a busy day without starting over.
    assert a.engine_version == "chatterbox-0.1.7-torch2.9-cuda"


def test_compact_keeps_the_rotary_position_tables_at_full_precision():
    torch = pytest.importorskip("torch")
    from ebook_audiobook.tts.chatterbox import _compact

    class Rotary(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.register_buffer("inv_freq", torch.rand(8), persistent=False)

    class T3(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.proj = torch.nn.Linear(4, 4)
            self.tfmr = torch.nn.Module()
            self.tfmr.rotary_emb = Rotary()

        def inference(self):
            return None

    model = types.SimpleNamespace(t3=T3())
    before = model.t3.tfmr.rotary_emb.inv_freq.clone()
    _compact(model)
    assert model.t3.proj.weight.dtype == torch.bfloat16
    assert model.t3.tfmr.rotary_emb.inv_freq.dtype == torch.float32
    assert torch.equal(model.t3.tfmr.rotary_emb.inv_freq, before)


# --- loading without the network ------------------------------------------------------

@pytest.fixture
def stub_models(monkeypatch, tmp_path):
    """Chatterbox with both loaders recorded, on a CPU-only machine."""
    calls = []

    class Model:
        sr = 24_000

        @classmethod
        def from_local(cls, ckpt_dir, device):
            calls.append(("local", str(ckpt_dir), device))
            return cls()

        @classmethod
        def from_pretrained(cls, device):
            calls.append(("network", device))
            return cls()

    torch = types.ModuleType("torch")
    torch.__version__ = "2.9.1"
    pkg = types.ModuleType("chatterbox")
    pkg.__version__ = "0.1.7"
    tts = types.ModuleType("chatterbox.tts")
    tts.ChatterboxTTS = Model
    for name, mod in (("torch", torch), ("chatterbox", pkg), ("chatterbox.tts", tts)):
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setattr(device, "select_device",
                        lambda: device.Device("cpu", "CPU", None, backend="cpu"))
    return calls


def test_an_installed_model_loads_from_disk_without_asking_the_network(stub_models, monkeypatch, tmp_path):
    """from_pretrained checks Hugging Face on every load, even with every file
    on disk; on a captive or slow network that held a preview at "loading"."""
    from ebook_audiobook import narration_langs
    from ebook_audiobook.tts.adapter import VoiceConfig
    from ebook_audiobook.tts.chatterbox import ChatterboxAdapter

    monkeypatch.setattr(narration_langs, "is_installed", lambda pack_id, root=None: True)
    monkeypatch.setattr(narration_langs, "snapshot_dir", lambda root=None: tmp_path)
    ChatterboxAdapter(VoiceConfig(language="en")).load()
    assert stub_models == [("local", str(tmp_path), "cpu")]


def test_a_voice_that_fails_to_prepare_leaves_no_model_behind(stub_models, monkeypatch, tmp_path):
    """Loaded with the built-in voice and then failed on the book's own: a
    retry of the passage must not narrate in the built-in voice."""
    from ebook_audiobook import narration_langs
    from ebook_audiobook.tts.adapter import VoiceConfig
    from ebook_audiobook.tts.chatterbox import ChatterboxAdapter

    monkeypatch.setattr(narration_langs, "is_installed", lambda pack_id, root=None: True)
    monkeypatch.setattr(narration_langs, "snapshot_dir", lambda root=None: tmp_path)
    Model = sys.modules["chatterbox.tts"].ChatterboxTTS

    def broken(self, *a, **k):
        raise FileNotFoundError("the reference clip has moved")

    monkeypatch.setattr(Model, "prepare_conditionals", broken, raising=False)
    adapter = ChatterboxAdapter(VoiceConfig(language="en", reference_clip=str(tmp_path / "gone.wav")))
    with pytest.raises(FileNotFoundError):
        adapter.load()
    assert adapter._model is None


def test_a_model_not_yet_downloaded_still_downloads(stub_models, monkeypatch):
    from ebook_audiobook import narration_langs
    from ebook_audiobook.tts.adapter import VoiceConfig
    from ebook_audiobook.tts.chatterbox import ChatterboxAdapter

    monkeypatch.setattr(narration_langs, "is_installed", lambda pack_id, root=None: False)
    ChatterboxAdapter(VoiceConfig(language="en")).load()
    assert stub_models == [("network", "cpu")]
