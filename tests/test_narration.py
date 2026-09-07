"""Narrating a book in its own language: the book's language, the voice's
language, the model that speaks it, and what happens when it isn't there."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from ebook_audiobook import narration_langs as nl
from ebook_audiobook.config import (DEFAULT_REPETITION_PENALTY,
                                    DEFAULT_REPETITION_PENALTY_MULTILINGUAL, VoiceSettings)
from ebook_audiobook.jobs.models import Book
from ebook_audiobook.jobs.store import JobStore
from ebook_audiobook.web import create_app


# --- the data ----------------------------------------------------------------------

def test_an_old_book_json_gets_the_english_default():
    old = {"job_id": "j", "source_path": "/x.epub", "source_hash": "h", "title": "T",
           "author": "A"}
    book = Book.from_dict(old)
    assert book.language == "en"
    assert book.cover_path is None


def test_english_render_keys_are_unchanged_by_the_language_field():
    """Every render made before the field existed was English; its cached
    audio survives only if the hashed payload is byte-identical."""
    v = VoiceSettings(engine="fake")
    assert "language" not in v.render_key()
    assert "language" in VoiceSettings(engine="fake", language="fr").render_key()
    assert VoiceSettings.from_dict({"engine": "fake"}).language == "en"
    assert VoiceSettings.from_dict({"engine": "fake", "language": "fr"}).language == "fr"
    assert VoiceSettings(language="fr").to_dict()["language"] == "fr"


def test_the_fake_engine_reports_its_language():
    from ebook_audiobook.tts import get_adapter

    assert get_adapter(VoiceSettings(engine="fake"), 24_000).engine_version == "fake-1"
    assert get_adapter(VoiceSettings(engine="fake", language="fr"), 24_000).engine_version == "fake-1-fr"


# --- the engine seam ------------------------------------------------------------------

def _stub_chatterbox(monkeypatch, calls: dict):
    """A chatterbox package that records which model was loaded and what
    generate() was asked for, so the seam is testable without torch."""
    def make(name):
        class Model:
            sr = 24_000

            @classmethod
            def from_pretrained(cls, device):
                calls["model"] = name
                return cls()

            def prepare_conditionals(self, *a, **kw):
                calls["conditioned"] = True

            def generate(self, text, **kw):
                calls["generate"] = kw
                import numpy as np
                return np.zeros(240, dtype="float32")
        return Model

    pkg = types.ModuleType("chatterbox"); pkg.__version__ = "0.1.7"
    tts = types.ModuleType("chatterbox.tts"); tts.ChatterboxTTS = make("english")
    mtl = types.ModuleType("chatterbox.mtl_tts"); mtl.ChatterboxMultilingualTTS = make("multilingual")
    torch = types.ModuleType("torch"); torch.__version__ = "2.9.1"
    torch.manual_seed = lambda s: None
    for name, mod in (("chatterbox", pkg), ("chatterbox.tts", tts), ("chatterbox.mtl_tts", mtl),
                      ("torch", torch)):
        monkeypatch.setitem(sys.modules, name, mod)
    from ebook_audiobook import device
    monkeypatch.setattr(device, "select_device",
                        lambda: device.Device("cpu", "CPU", None, backend="cpu"))
    monkeypatch.setattr(nl, "is_installed", lambda pack_id, root=None: True)


@pytest.mark.parametrize("language, model, has_language_id", [
    ("en", "english", False), ("fr", "multilingual", True)])
def test_the_adapter_picks_the_model_for_the_language(monkeypatch, language, model, has_language_id):
    from ebook_audiobook.tts.adapter import VoiceConfig
    from ebook_audiobook.tts.chatterbox import ChatterboxAdapter

    calls: dict = {}
    _stub_chatterbox(monkeypatch, calls)
    adapter = ChatterboxAdapter(VoiceConfig(language=language))
    adapter.load()
    assert calls["model"] == model
    adapter.synthesize("Bonjour.")
    assert ("language_id" in calls["generate"]) == has_language_id
    if has_language_id:
        assert calls["generate"]["language_id"] == "fr"
        assert adapter.engine_version.startswith("chatterbox-mtl-")
    else:
        assert adapter.engine_version.startswith("chatterbox-0.1.7")


# --- reading the book's language --------------------------------------------------------

def _epub_with_language(tmp_path, lang):
    import zipfile

    from tests.conftest import _xhtml

    path = tmp_path / f"book-{lang or 'none'}.epub"
    lang_tag = f"<dc:language>{lang}</dc:language>" if lang else ""
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Livre</dc:title><dc:creator>Auteur</dc:creator>{lang_tag}
    <dc:identifier id="id">urn:uuid:t</dc:identifier>
  </metadata>
  <manifest><item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="c1"/></spine>
</package>"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", '<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        z.writestr("content.opf", opf)
        z.writestr("ch1.xhtml", _xhtml("Chapitre premier", ["Il était une fois un livre assez long pour compter."]))
    return path


@pytest.mark.parametrize("tag, expected", [("fr", "fr"), ("fr-FR", "fr"), ("fra", "fr"),
                                           ("en-US", "en"), (None, "en"), ("xx", "en")])
def test_the_epubs_language_is_read_and_normalised(tmp_path, tag, expected):
    from ebook_audiobook.pipeline import extract

    raw = extract.parse_epub(_epub_with_language(tmp_path, tag))
    assert raw.language == expected


# --- the job's language ---------------------------------------------------------------

def _job_with_french_text(client, synthetic_epub, monkeypatch, tmp_path):
    """Import the fixture book, then declare it French by hand — the fixture
    OPF has no dc:language, and Calibre is not part of a unit test."""
    r = client.post("/import", data={"path": str(synthetic_epub), "engine": "fake"})
    job = r.headers["Location"].rstrip("/").split("/")[-1]
    # The import queues an extraction; let it finish, or it races the test's
    # own writes to book.json and the busy check answers 409 for that instead.
    import time

    from ebook_audiobook.web.runner import runner

    deadline = time.monotonic() + 30
    while runner.is_busy() and time.monotonic() < deadline:
        time.sleep(0.05)
    return JobStore(job)


def test_switching_language_swaps_the_narrator_and_the_penalty(monkeypatch, synthetic_epub):
    from ebook_audiobook import worker

    monkeypatch.setattr(nl, "is_installed", lambda pack_id, root=None: True)
    client = create_app().test_client()
    store = _job_with_french_text(client, synthetic_epub, monkeypatch, None)
    voice = store.load_voice()
    assert voice.language == "en" and voice.extra["voice_id"] == "male-north-american"
    assert voice.repetition_penalty == DEFAULT_REPETITION_PENALTY

    worker.set_job_language(store, "fr")
    book, voice = store.load_book(), store.load_voice()
    assert book.language == "fr" and voice.language == "fr"
    assert voice.extra["voice_id"] == "female-french"
    assert voice.reference_clip.endswith("female-french.flac")
    assert voice.repetition_penalty == DEFAULT_REPETITION_PENALTY_MULTILINGUAL

    # A value the user chose is theirs; only the other model's default moves.
    voice.repetition_penalty = 1.7
    store.save_voice(voice)
    worker.set_job_language(store, "en")
    voice = store.load_voice()
    assert voice.language == "en" and voice.repetition_penalty == 1.7
    assert voice.extra["voice_id"] == "male-north-american"


def test_a_missing_model_is_refused_before_anything_starts(monkeypatch, synthetic_epub):
    from ebook_audiobook import worker

    monkeypatch.setattr(nl, "is_installed", lambda pack_id, root=None: pack_id == "english")
    client = create_app().test_client()
    store = _job_with_french_text(client, synthetic_epub, monkeypatch, None)
    job = store.job_id

    r = client.post(f"/job/{job}/language", data={"language": "fr"})
    assert r.status_code == 409
    assert r.get_json()["install_pack"] == "multilingual"
    assert "Settings" in r.get_json()["error"]
    assert store.load_voice().language == "en", "a refused switch changes nothing"

    # Force the voice to French behind the app's back, as an older cache might.
    voice = store.load_voice(); voice.language = "fr"; store.save_voice(voice)
    # A section to render, so the routes get as far as the language check on a
    # machine without Calibre (CI's test job), where the import reads nothing.
    if not store.load_chapters():
        from ebook_audiobook.jobs.models import Chapter

        store.save_chapters([Chapter(chapter_id="ch0000", sequence=0, title="Un",
                                     text="Bonjour.", char_count=8, include=True)])
    for path in (f"/job/{job}/render", f"/job/{job}/preview", f"/job/{job}/measure"):
        r = client.post(path, data={"seconds": "5", "output_mode": "folder",
                                    "output_dir": str(Path(store.dir) / "out")})
        assert r.status_code == 409, path
        assert r.get_json()["install_pack"] == "multilingual"
    with pytest.raises(nl.LanguagePackMissing):
        worker.render_job(job, preview_max_seconds=5)
    assert worker.narration_notice(store) is None  # book and voice agree (both fr)


def test_the_job_page_says_when_the_book_cannot_be_narrated_in_its_language(monkeypatch, synthetic_epub):
    from ebook_audiobook import worker

    monkeypatch.setattr(nl, "is_installed", lambda pack_id, root=None: pack_id == "english")
    client = create_app().test_client()
    store = _job_with_french_text(client, synthetic_epub, monkeypatch, None)
    book = store.load_book(); book.language = "fr"; store.save_book(book)
    assert "French" in worker.narration_notice(store)
    body = client.get(f"/job/{store.job_id}").data.decode()
    assert "additional language model" in body
    assert 'id="narrationLang"' in body


def test_the_voice_picker_shows_the_narrators_of_the_books_language(monkeypatch, synthetic_epub):
    from ebook_audiobook import worker

    monkeypatch.setattr(nl, "is_installed", lambda pack_id, root=None: True)
    client = create_app().test_client()
    store = _job_with_french_text(client, synthetic_epub, monkeypatch, None)
    body = client.get(f"/job/{store.job_id}").data.decode()
    assert 'value="male-north-american"' in body and 'value="female-french"' not in body
    worker.set_job_language(store, "fr")
    body = client.get(f"/job/{store.job_id}").data.decode()
    assert 'value="female-french"' in body and 'value="male-french"' in body
    assert 'value="male-north-american"' not in body


# --- the download ------------------------------------------------------------------------

def test_the_languages_api_reports_what_is_on_disk(monkeypatch):
    monkeypatch.setattr(nl, "is_installed", lambda pack_id, root=None: pack_id == "english")
    d = create_app().test_client().get("/api/languages").get_json()
    packs = {p["id"]: p for p in d["packs"]}
    assert packs["english"]["installed"] and not packs["multilingual"]["installed"]
    langs = {lg["code"]: lg for lg in d["languages"]}
    assert langs["en"]["available"] and not langs["fr"]["available"]
    assert langs["fr"]["tier"] == "supported" and langs["de"]["tier"] == "experimental"
    assert d["download"] is None


def test_install_goes_through_the_worker_and_refuses_while_busy(monkeypatch):
    from ebook_audiobook.web import app as web_app

    submitted = []
    monkeypatch.setattr(web_app, "_engine_present", lambda: True)
    monkeypatch.setattr(nl, "is_installed", lambda pack_id, root=None: False)
    monkeypatch.setattr(nl, "free_space_ok", lambda pack, root=None: (True, 0))
    monkeypatch.setattr(web_app.runner, "submit", lambda job_id, kind, **kw: submitted.append((job_id, kind, kw)))
    client = create_app().test_client()
    r = client.post("/api/languages/install", data={"pack": "multilingual"})
    assert r.status_code == 200, r.get_json()
    assert submitted == [("langpack-multilingual", "model_download", {"pack": "multilingual"})]

    monkeypatch.setattr(web_app.runner, "is_busy", lambda job_id=None: True)
    r = client.post("/api/languages/install", data={"pack": "multilingual"})
    assert r.status_code == 409
    assert client.post("/api/languages/install", data={"pack": "nope"}).status_code == 400


def test_install_needs_the_engine_and_room(monkeypatch):
    from ebook_audiobook.web import app as web_app

    client = create_app().test_client()
    monkeypatch.setattr(web_app, "_engine_present", lambda: False)
    r = client.post("/api/languages/install", data={"pack": "multilingual"})
    assert r.status_code == 409 and "engine" in r.get_json()["error"]
    monkeypatch.setattr(web_app, "_engine_present", lambda: True)
    monkeypatch.setattr(nl, "is_installed", lambda pack_id, root=None: False)
    monkeypatch.setattr(nl, "free_space_ok", lambda pack, root=None: (False, 3_000_000_000))
    r = client.post("/api/languages/install", data={"pack": "multilingual"})
    assert r.status_code == 400 and "free space" in r.get_json()["error"]


def test_the_runner_dispatches_a_download(monkeypatch):
    from ebook_audiobook.web.runner import Runner, _Task

    seen = {}
    monkeypatch.setattr(nl, "install", lambda pack, should_cancel=None, root=None: seen.update(pack=pack, cancel=should_cancel))
    Runner()._run(_Task(job_id="langpack-multilingual", kind="model_download", kwargs={"pack": "multilingual"}))
    assert seen["pack"] == "multilingual" and callable(seen["cancel"])


def test_quit_warns_about_a_download(monkeypatch):
    from ebook_audiobook.web import app as web_app

    monkeypatch.setattr(web_app.runner, "is_busy", lambda job_id=None: True)
    monkeypatch.setattr(web_app.runner, "current_kind", lambda: "model_download")
    app = create_app()
    app.config["EBAB_SHUTDOWN"] = lambda: None
    r = app.test_client().post("/quit")
    assert r.status_code == 409 and r.get_json()["kind"] == "model_download"
    body = app.test_client().get("/").data.decode()
    assert "model_download" in body  # the warning table knows the kind


# --- auditioning a narrator in its own language ------------------------------

def test_the_audition_sentence_follows_the_voices_language():
    """Each language auditions with its own sentence, not a translation of the
    English one and not the English one itself."""
    from ebook_audiobook.worker import voice_sample_text

    said = {c: voice_sample_text(c) for c in ("en", "fr", "es")}
    assert len({*said.values()}) == 3
    assert "narrator voice" in said["en"]
    assert "voix" in said["fr"]
    assert "voz" in said["es"]
    # A language with no rules module of its own still gets a sentence.
    assert voice_sample_text("de") == said["en"]


def test_auditioning_a_voice_uses_its_own_language_and_model(monkeypatch, tmp_path):
    """The bug this covers: render_voice_sample built VoiceSettings without a
    language, so every audition loaded the English model and read English —
    a French narrator's clip reading English tells you nothing useful."""
    from ebook_audiobook import worker

    seen = {}

    class FakeAdapter:
        def load(self): pass
        def unload(self): pass
        def synthesize(self, text):
            seen["text"] = text
            import numpy as np
            return types.SimpleNamespace(samples=np.zeros(240, dtype="float32"),
                                         sample_rate=24000)

    def fake_get_adapter(voice, sr):
        seen["language"] = voice.language
        return FakeAdapter()

    monkeypatch.setattr(worker, "get_adapter", fake_get_adapter)
    monkeypatch.setattr(nl, "require_installed", lambda lang: None)
    worker.render_voice_sample("female-french")

    assert seen["language"] == "fr"
    assert "voix" in seen["text"]


def test_auditioning_without_the_model_is_refused_before_it_queues(monkeypatch):
    """409 with the pack to install, rather than four minutes of spinner and
    then a failure inside the worker."""
    monkeypatch.setattr(nl, "language_available", lambda code: code == "en")
    client = create_app().test_client()
    r = client.post("/voices/female-spanish-european/test")
    assert r.status_code == 409
    assert r.get_json()["install_pack"] == "multilingual"

    # English still works: its model is the one that is there.
    assert client.post("/voices/male-north-american/test").status_code == 200


# --- the voices page is per language -----------------------------------------

def test_the_voices_page_shows_one_languages_narrators():
    client = create_app().test_client()
    es = client.get("/voices?lang=es").data.decode()
    assert "female-spanish-latin-american" in es
    assert "female-french" not in es
    assert "male-north-american" not in es

    fr = client.get("/voices?lang=fr").data.decode()
    assert "female-french" in fr
    assert "female-spanish-latin-american" not in fr


def test_the_voices_page_falls_back_for_a_language_it_cannot_narrate():
    """German is a language the engine speaks but the app has no rules or
    voices for, so asking for it lands on English rather than an empty page."""
    client = create_app().test_client()
    body = client.get("/voices?lang=de").data.decode()
    assert "male-north-american" in body


def test_an_audition_uses_the_penalty_its_model_was_tuned_with():
    """At the English model's 1.2 the multilingual one stops a few words in: a
    nine-second sentence came out at two. The audition builds its VoiceSettings
    from scratch, so it has to ask for the right default rather than inherit
    the dataclass one."""
    from ebook_audiobook import config, worker

    seen = {}

    class FakeAdapter:
        def load(self): pass
        def unload(self): pass
        def synthesize(self, text):
            import numpy as np
            return types.SimpleNamespace(samples=np.zeros(240, dtype="float32"),
                                         sample_rate=24000)

    def fake_get_adapter(voice, sr):
        seen["penalty"] = voice.repetition_penalty
        return FakeAdapter()

    import pytest as _pytest
    monkey = _pytest.MonkeyPatch()
    try:
        monkey.setattr(worker, "get_adapter", fake_get_adapter)
        monkey.setattr(nl, "require_installed", lambda lang: None)
        worker.render_voice_sample("female-spanish-european")
        assert seen["penalty"] == config.DEFAULT_REPETITION_PENALTY_MULTILINGUAL
        worker.render_voice_sample("male-north-american")
        assert seen["penalty"] == config.DEFAULT_REPETITION_PENALTY
    finally:
        monkey.undo()


def test_the_penalty_default_is_decided_in_one_place():
    from ebook_audiobook import config

    # An unspecified language means English, which is what every caller that
    # predates the field is asking for.
    for code in ("en", None, ""):
        assert config.default_repetition_penalty(code) == config.DEFAULT_REPETITION_PENALTY
    for code in ("fr", "es", "ja"):
        assert config.default_repetition_penalty(code) == \
            config.DEFAULT_REPETITION_PENALTY_MULTILINGUAL


# --- changing the language without reading the book again ---------------------------------

def _import_with_a_countable_read(client, epub, monkeypatch):
    """Import ``epub`` with Calibre's conversion replaced by a copy that counts
    itself, and wait for the extraction the import queues.

    The conversion is the expensive half of reading a book — minutes on a real
    one — so counting it is how "it didn't read the book again" is checked.
    """
    import shutil
    import time

    from ebook_audiobook import worker
    from ebook_audiobook.web.runner import runner

    reads = []

    def convert(src, out_epub, timeout=1800):
        reads.append(str(src))
        shutil.copyfile(src, out_epub)
        return out_epub

    monkeypatch.setattr(worker.extract, "run_ebook_convert", convert)
    r = client.post("/import", data={"path": str(epub), "engine": "fake"})
    job = r.headers["Location"].rstrip("/").split("/")[-1]
    deadline = time.monotonic() + 30
    while runner.is_busy() and time.monotonic() < deadline:
        time.sleep(0.05)
    return job, reads


def _wait_for_the_worker():
    import time

    from ebook_audiobook.web.runner import runner

    deadline = time.monotonic() + 30
    while runner.is_busy() and time.monotonic() < deadline:
        time.sleep(0.05)


def test_changing_the_language_prepares_the_text_without_reading_the_book_again(
        monkeypatch, synthetic_epub):
    monkeypatch.setattr(nl, "is_installed", lambda pack_id, root=None: True)
    client = create_app().test_client()
    job, reads = _import_with_a_countable_read(client, synthetic_epub, monkeypatch)
    store = JobStore(job)
    assert len(reads) == 1, "the import reads the book once"
    english = {c.chapter_id: c.text for c in store.load_chapters()}
    assert english, "nothing was extracted, so there is nothing to re-prepare"

    r = client.post(f"/job/{job}/language", data={"language": "fr"})
    assert r.status_code == 200 and r.get_json()["language"] == "fr"
    _wait_for_the_worker()

    assert len(reads) == 1, "changing the language read the book again"
    assert store.load_voice().language == "fr"
    french = {c.chapter_id: c.text for c in store.load_chapters()}
    assert set(french) == set(english), "the sections themselves changed"
    # The text really was prepared afresh: the app's own closing line is now
    # in French, and the body text was normalised by French rules.
    assert "de" in french["outro"] and french["outro"] != english["outro"]
    assert french["ch0000"] != english["ch0000"]


def test_the_sections_you_switched_off_survive_a_language_change(monkeypatch, synthetic_epub):
    monkeypatch.setattr(nl, "is_installed", lambda pack_id, root=None: True)
    client = create_app().test_client()
    job, _reads = _import_with_a_countable_read(client, synthetic_epub, monkeypatch)
    store = JobStore(job)
    chapters = store.load_chapters()
    chapters[-1].include = False
    store.save_chapters(chapters)

    client.post(f"/job/{job}/language", data={"language": "fr"})
    _wait_for_the_worker()
    assert store.load_chapters()[-1].include is False


def test_a_book_imported_before_the_cache_existed_falls_back_to_a_full_read(
        monkeypatch, synthetic_epub):
    """No parse on disk is not an error — it is the old behaviour, once."""
    monkeypatch.setattr(nl, "is_installed", lambda pack_id, root=None: True)
    client = create_app().test_client()
    job, reads = _import_with_a_countable_read(client, synthetic_epub, monkeypatch)
    store = JobStore(job)
    (store.dir / "raw_chapters.json").unlink()

    client.post(f"/job/{job}/language", data={"language": "fr"})
    _wait_for_the_worker()

    assert len(reads) == 2, "with no cached parse the book has to be read again"
    assert store.load_voice().language == "fr"
    assert store.load_chapters(), "the fallback produced no sections"
    # And it wrote the cache, so the next change of language is the fast one.
    client.post(f"/job/{job}/language", data={"language": "en"})
    _wait_for_the_worker()
    assert len(reads) == 2


def test_a_cache_from_a_different_book_is_not_used(monkeypatch, synthetic_epub):
    """The parse is keyed to the source it came from, so a job whose book was
    replaced re-reads rather than narrating the previous one's text."""
    monkeypatch.setattr(nl, "is_installed", lambda pack_id, root=None: True)
    client = create_app().test_client()
    job, reads = _import_with_a_countable_read(client, synthetic_epub, monkeypatch)
    store = JobStore(job)
    book = store.load_book()
    assert store.load_raw_chapters(book.source_hash)
    assert store.load_raw_chapters("some-other-hash") is None

    book.source_hash = "some-other-hash"
    store.save_book(book)
    client.post(f"/job/{job}/language", data={"language": "fr"})
    _wait_for_the_worker()
    assert len(reads) == 2


# --- each narrator keeps its own expressiveness and pacing -------------------------------

def test_switching_language_gives_a_voice_back_the_settings_it_was_left_at(
        monkeypatch, synthetic_epub):
    from ebook_audiobook import worker

    monkeypatch.setattr(nl, "is_installed", lambda pack_id, root=None: True)
    client = create_app().test_client()
    store = _job_with_french_text(client, synthetic_epub, monkeypatch, None)
    voice = store.load_voice()
    assert voice.extra["voice_id"] == "male-north-american"

    # Tune the English narrator to something no clip would suggest.
    voice.exaggeration, voice.cfg_weight = 0.85, 0.35
    store.save_voice(voice)
    worker.remember_voice_tuning(voice)
    store.save_voice(voice)

    worker.set_job_language(store, "fr")
    voice = store.load_voice()
    assert voice.extra["voice_id"] == "female-french"
    assert voice.cfg_weight == 0.42, "the French clip's own pacing, it has never been tuned"
    voice.exaggeration, voice.cfg_weight = 0.20, 0.70
    worker.remember_voice_tuning(voice)
    store.save_voice(voice)

    # Back to English: the settings that voice was left at, not the clip's.
    worker.set_job_language(store, "en")
    voice = store.load_voice()
    assert voice.extra["voice_id"] == "male-north-american"
    assert (voice.exaggeration, voice.cfg_weight) == (0.85, 0.35)

    # And French is still where it was left too.
    worker.set_job_language(store, "fr")
    voice = store.load_voice()
    assert (voice.exaggeration, voice.cfg_weight) == (0.20, 0.70)


def test_saving_files_the_sliders_under_the_voice_they_were_set_for(synthetic_epub):
    from ebook_audiobook import worker

    job_id = worker.import_ebook(str(synthetic_epub), engine="fake")
    store = JobStore(job_id)
    client = create_app().test_client()

    r = client.post(f"/job/{job_id}/settings", data={
        "voice_id": "male-north-american", "exaggeration": "0.85", "cfg_weight": "0.35"})
    assert r.status_code == 200
    tuning = store.load_voice().extra["voice_tuning"]
    assert tuning["male-north-american"] == {"exaggeration": 0.85, "cfg_weight": 0.35}

    # The page also carries what it remembers for voices tried without a save.
    client.post(f"/job/{job_id}/settings", data={
        "voice_id": "female-british", "exaggeration": "0.5", "cfg_weight": "0.42",
        "voice_tuning": '{"male-british": {"exaggeration": 0.1, "cfg_weight": 0.9}}'})
    tuning = store.load_voice().extra["voice_tuning"]
    assert tuning["male-british"] == {"exaggeration": 0.1, "cfg_weight": 0.9}
    assert tuning["female-british"] == {"exaggeration": 0.5, "cfg_weight": 0.42}
    assert tuning["male-north-american"] == {"exaggeration": 0.85, "cfg_weight": 0.35}, \
        "a save merged with what was already on file, rather than replacing it"


def test_the_job_page_hands_the_browser_what_each_voice_was_left_at(synthetic_epub):
    from ebook_audiobook import worker

    job_id = worker.import_ebook(str(synthetic_epub), engine="fake")
    client = create_app().test_client()
    client.post(f"/job/{job_id}/settings", data={
        "voice_id": "male-north-american", "exaggeration": "0.85", "cfg_weight": "0.35"})
    body = client.get(f"/job/{job_id}").data.decode()
    assert '"male-north-american": {"cfg_weight": 0.35, "exaggeration": 0.85}' in body
    assert 'id="voiceTuning"' in body


def test_a_nonsense_tuning_payload_cannot_corrupt_the_settings(synthetic_epub):
    """The map is posted by the page, so it is parsed defensively: bad shapes
    are dropped and out-of-range values clamped to what the sliders allow."""
    from ebook_audiobook import worker

    job_id = worker.import_ebook(str(synthetic_epub), engine="fake")
    store = JobStore(job_id)
    client = create_app().test_client()
    for payload in ("not json", "[1, 2, 3]", '{"v": 3}', '{"v": {"exaggeration": "x"}}'):
        r = client.post(f"/job/{job_id}/settings", data={
            "voice_id": "male-north-american", "exaggeration": "0.5",
            "cfg_weight": "0.42", "voice_tuning": payload})
        assert r.status_code == 200, payload
        assert set(store.load_voice().extra["voice_tuning"]) == {"male-north-american"}, payload

    client.post(f"/job/{job_id}/settings", data={
        "voice_id": "male-north-american", "exaggeration": "0.5", "cfg_weight": "0.42",
        "voice_tuning": '{"v": {"exaggeration": 42, "cfg_weight": -5}}'})
    assert store.load_voice().extra["voice_tuning"]["v"] == {"exaggeration": 1.0, "cfg_weight": 0.0}
