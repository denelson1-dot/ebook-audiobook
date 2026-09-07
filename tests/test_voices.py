import subprocess

import numpy as np
import pytest
import soundfile as sf

from ebook_audiobook.voices import BUNDLED, DEFAULT_VOICE_ID, VoiceLibrary

from ebook_audiobook import tools

HAVE_FFMPEG = tools.ffmpeg_path() is not None


@pytest.fixture
def clip(tmp_path):
    p = tmp_path / "sample.wav"
    sf.write(str(p), np.zeros(2400, dtype=np.float32), 24000, subtype="PCM_16")
    return str(p)


def test_default_voice_always_present():
    lib = VoiceLibrary()
    ids = [v.id for v in lib.list()]
    assert DEFAULT_VOICE_ID in ids
    assert lib.get(DEFAULT_VOICE_ID).is_default
    assert lib.clip_path(DEFAULT_VOICE_ID) is None


def test_add_list_get_delete(clip):
    lib = VoiceLibrary()
    v = lib.add("Warm Baritone", src_path=clip)
    assert v.id == "warm-baritone"
    assert not v.is_default
    assert lib.get(v.id).name == "Warm Baritone"
    assert lib.clip_path(v.id).exists()

    assert v.id in [x.id for x in lib.list()]
    assert lib.delete(v.id) is True
    assert lib.get(v.id) is None
    assert not lib.clip_path(v.id) if lib.clip_path(v.id) else True


def test_duplicate_names_get_unique_ids(clip):
    lib = VoiceLibrary()
    a = lib.add("Voice", src_path=clip)
    b = lib.add("Voice", src_path=clip)
    assert a.id != b.id


def test_cannot_delete_default():
    assert VoiceLibrary().delete(DEFAULT_VOICE_ID) is False


def test_unsupported_format_rejected(tmp_path):
    bad = tmp_path / "notaudio.txt"
    bad.write_text("nope")
    with pytest.raises(ValueError):
        VoiceLibrary().add("Bad", src_path=str(bad))


def test_wav_input_stored_as_wav(clip):
    v = VoiceLibrary().add("Plain Wav", src_path=clip)
    assert v.clip_filename.endswith(".wav")


@pytest.mark.ffmpeg
@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_mp4_audio_import_transcodes_to_wav(tmp_path, clip):
    # Build a real .mp4 (AAC audio) from the wav, then import it.
    mp4 = tmp_path / "voice.mp4"
    subprocess.run([str(tools.ffmpeg_path()), "-y", "-i", clip, "-c:a", "aac", str(mp4)],
                   capture_output=True, check=True)
    v = VoiceLibrary().add("From MP4", src_path=str(mp4))
    clip_path = VoiceLibrary().clip_path(v.id)
    assert clip_path.suffix == ".wav" and clip_path.exists()
    # And it's a readable WAV.
    assert sf.info(str(clip_path)).frames > 0


# --- the voices shipped with the application --------------------------------

def test_the_shipped_voices_are_present_and_readable():
    """They ride in the package, so a fresh install has usable narrators."""
    from ebook_audiobook.voices import BUNDLED, BUNDLED_DIR

    # Named explicitly, so dropping one is a decision someone has to make here
    # rather than something that quietly happens to a glob. Adding one is a
    # decision too — hence the count.
    assert [b["id"] for b in BUNDLED] == [
        "male-north-american", "female-north-american", "male-british", "female-british",
        "female-french", "male-french",
        "female-spanish-latin-american", "male-spanish-latin-american",
        "female-spanish-european", "male-spanish-european"]
    for b in BUNDLED:
        clip = BUNDLED_DIR / b["file"]
        assert clip.is_file(), f"{b['file']} is missing from the package"
        assert clip.stat().st_size > 100_000, f"{b['file']} looks truncated"


def test_shipped_voices_lead_the_picker_and_carry_their_pacing():
    lib = VoiceLibrary()
    voices = lib.list()
    n = len(BUNDLED)
    assert [v.id for v in voices[:n]] == [b["id"] for b in BUNDLED]
    assert all(v.bundled and v.pacing for v in voices[:n])
    # The engine's own voice is still there, just not first.
    assert any(v.is_default for v in voices)


def test_a_shipped_clip_resolves_into_the_package_not_the_user_library():
    from ebook_audiobook.voices import BUNDLED_DIR

    clip = VoiceLibrary().clip_path("male-north-american")
    assert clip is not None and clip.parent == BUNDLED_DIR
    assert clip.is_file()


def test_shipped_voices_cannot_be_deleted():
    """Not merely hidden from the UI: a hand-made request must not corrupt the
    index or leave the picker pointing at a clip that no longer exists."""
    lib = VoiceLibrary()
    for b in BUNDLED:
        assert lib.delete(b["id"]) is False
    assert not any(v.removable for v in lib.list() if v.bundled)
    assert VoiceLibrary().clip_path("male-north-american") is not None


def test_a_users_voice_cannot_shadow_a_shipped_one(clip):
    v = VoiceLibrary().add("Male North American", src_path=clip)
    assert v.id != "male-north-american"
    assert VoiceLibrary().clip_path("male-north-american").parent.name == "voices"


def test_a_new_book_starts_with_the_shipped_default():
    from ebook_audiobook.voices import DEFAULT_BUNDLED_ID, default_voice_id

    assert default_voice_id() == DEFAULT_BUNDLED_ID == "male-north-american"


def test_the_default_can_be_changed_and_falls_back_if_it_vanishes():
    from ebook_audiobook import settings as app_settings
    from ebook_audiobook.voices import default_voice_id

    s = app_settings.load_settings()
    s.default_voice_id = "female-british"
    app_settings.save_settings(s)
    assert default_voice_id() == "female-british"

    # A voice the user deleted, or a stale name from an older build.
    s.default_voice_id = "no-such-voice"
    app_settings.save_settings(s)
    assert default_voice_id() == "male-north-american"


def test_importing_a_book_adopts_the_default_voice_and_its_pacing(synthetic_epub):
    from ebook_audiobook import worker
    from ebook_audiobook.jobs.store import JobStore

    job_id = worker.import_ebook(str(synthetic_epub), engine="fake")
    voice = JobStore(job_id).load_voice()

    assert voice.extra["voice_id"] == "male-north-american"
    assert voice.reference_clip and voice.reference_clip.endswith("male-north-american.flac")
    # The default voice carries its own pacing hint, not the engine's generic
    # one — a book should sound right before anybody touches a slider.
    assert voice.cfg_weight == 0.42


def test_the_engine_s_own_voice_is_not_offered_as_a_choice():
    """It is a fallback, not a narrator. Every shipped voice beats it."""
    from ebook_audiobook.web.app import _offerable_voices

    assert not any(v["is_default"] for v in _offerable_voices())
    assert any(v["bundled"] for v in _offerable_voices())


def test_but_a_book_already_using_it_still_sees_it():
    """Dropping it from that book's picker would silently move the book to
    another voice on the next save — and re-render the whole thing."""
    from ebook_audiobook.web.app import _offerable_voices

    offered = _offerable_voices(DEFAULT_VOICE_ID)
    assert any(v["id"] == DEFAULT_VOICE_ID for v in offered)


def test_a_book_on_the_engine_voice_keeps_it_across_a_save(synthetic_epub):
    from ebook_audiobook import worker
    from ebook_audiobook.jobs.store import JobStore
    from ebook_audiobook.web import create_app

    job_id = worker.import_ebook(str(synthetic_epub), engine="fake")
    store = JobStore(job_id)
    v = store.load_voice()
    v.extra["voice_id"] = DEFAULT_VOICE_ID
    v.reference_clip = None
    store.save_voice(v)

    client = create_app().test_client()
    body = client.get(f"/job/{job_id}").data.decode()
    assert "Default narrator" in body, "the picker dropped the voice this book uses"


def test_a_voice_may_suggest_settings_or_leave_them_alone():
    lib = VoiceLibrary()
    # Every shipped clip carries a pacing hint; expressiveness stays at the
    # engine's unless a clip is tuned for one (none of the current six are).
    shipped = [v for v in lib.list() if v.bundled]
    assert shipped and all(v.pacing and v.expressiveness is None for v in shipped)
    # The engine's own voice suggests nothing.
    assert lib.get("default").pacing is None


# The display name leads with the language, so a picker showing several reads
# as a list rather than a jumble. Extend this when a language is added — which
# is the point: the name is part of shipping a voice, not an afterthought.
LANGUAGE_NAME_PREFIX = {"en": "English — ", "fr": "French — ", "es": "Spanish — "}


def test_every_bundled_voice_names_its_language():
    from ebook_audiobook import narration_langs
    from ebook_audiobook.voices import BUNDLED

    for b in BUNDLED:
        assert b["language"] in LANGUAGE_NAME_PREFIX, b["id"]
        assert b["name"].startswith(LANGUAGE_NAME_PREFIX[b["language"]]), b["id"]
        # A voice for a language the app does not claim to support properly is
        # a voice nobody can reach: the picker only offers supported languages.
        assert narration_langs.LANGUAGES[b["language"]].tier == "supported", b["id"]


def test_every_supported_narration_language_has_a_voice_and_a_default():
    """A language marked "supported" promises a narrator that speaks it."""
    from ebook_audiobook import narration_langs
    from ebook_audiobook.voices import BUNDLED, DEFAULT_BUNDLED_BY_LANGUAGE

    ids = {b["id"] for b in BUNDLED}
    for code, lg in narration_langs.LANGUAGES.items():
        if lg.tier != "supported":
            continue
        assert any(b["language"] == code for b in BUNDLED), code
        assert DEFAULT_BUNDLED_BY_LANGUAGE.get(code) in ids, code


def test_the_default_narrator_follows_the_interface_language(monkeypatch):
    from ebook_audiobook import i18n
    from ebook_audiobook.voices import default_voice_id

    assert default_voice_id() == "male-north-american"
    assert default_voice_id("fr") == "female-french"
    i18n.set_process_language("fr")
    assert default_voice_id() == "female-french"
    i18n.set_process_language(None)
    # An unknown language gets the English default rather than nothing.
    assert default_voice_id("xx") == "male-north-american"


def test_a_book_imported_from_a_french_interface_starts_with_the_french_narrator(monkeypatch, synthetic_epub):
    from ebook_audiobook.jobs.store import JobStore
    from ebook_audiobook.web import create_app

    monkeypatch.delenv("EBAB_LANG")
    client = create_app().test_client()
    r = client.post("/import", data={"path": str(synthetic_epub), "engine": "fake"},
                    headers={"Accept-Language": "fr"})
    job = r.headers["Location"].rstrip("/").split("/")[-1]
    voice = JobStore(job).load_voice()
    assert voice.extra["voice_id"] == "female-french"
    assert voice.reference_clip.endswith("female-french.flac")

    r = client.post("/import", data={"path": str(synthetic_epub), "engine": "fake"})
    job = r.headers["Location"].rstrip("/").split("/")[-1]
    assert JobStore(job).load_voice().extra["voice_id"] == "male-north-american"


# --- a voice knows what language it speaks -----------------------------------

def test_a_user_added_clip_remembers_the_language_it_was_added_with(clip):
    """Asked for on the form, persisted in the index, read back on the way out.

    Without this a Spanish clip someone uploads is English forever, and never
    appears on the page for the language it actually speaks.
    """
    lib = VoiceLibrary()
    v = lib.add("Abuela", src_path=clip, language="es")
    assert v.language == "es"
    assert lib.get(v.id).language == "es"
    assert any(d["language"] == "es" for d in lib._load_index() if d["id"] == v.id)


def test_a_clip_added_before_voices_had_languages_is_english(clip):
    """An index entry with no language key predates the field; it was English
    at the time and must stay English rather than becoming unreachable."""
    lib = VoiceLibrary()
    v = lib.add("Old one", src_path=clip)
    items = lib._load_index()
    for d in items:
        d.pop("language", None)
    lib._save_index(items)
    assert lib.get(v.id).language == "en"


def test_the_default_narrator_is_per_language(clip):
    """Choosing a Spanish narrator for new books must not hand that voice to
    the next English book — it cannot speak the language."""
    from ebook_audiobook import settings as app_settings
    from ebook_audiobook.voices import default_voice_id

    s = app_settings.load_settings()
    s.set_default_voice("es", "male-spanish-european")
    app_settings.save_settings(s)

    assert default_voice_id("es") == "male-spanish-european"
    assert default_voice_id("en") == "male-north-american"
    assert default_voice_id("fr") == "female-french"


def test_a_default_pointing_at_a_deleted_voice_falls_back(clip):
    from ebook_audiobook import settings as app_settings
    from ebook_audiobook.voices import default_voice_id

    lib = VoiceLibrary()
    v = lib.add("Temporary", src_path=clip, language="en")
    s = app_settings.load_settings()
    s.set_default_voice("en", v.id)
    app_settings.save_settings(s)
    assert default_voice_id("en") == v.id

    lib.delete(v.id)
    assert default_voice_id("en") == "male-north-american"
