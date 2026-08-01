from ebook_audiobook.config import VoiceSettings
from ebook_audiobook.hashing import segment_id, voice_key
from ebook_audiobook.jobs.models import Book, Chapter, JobState, Segment, Stage
from ebook_audiobook.jobs.store import JobStore


def test_roundtrip_book_chapters_segments_state():
    store = JobStore("job1").ensure()
    store.save_book(Book(job_id="job1", source_path="/x.epub", source_hash="abc", title="T", author="A"))
    assert store.load_book().title == "T"

    chapters = [Chapter(chapter_id="ch0000", sequence=0, title="One", text="hello", char_count=5)]
    store.save_chapters(chapters)
    assert store.load_chapters()[0].title == "One"

    segs = [Segment(segment_id="s1", chapter_id="ch0000", sequence=0, chapter_sequence=0, text="hi", text_hash="h")]
    store.save_segments(segs)
    assert store.load_segments()[0].text == "hi"

    st = JobState(job_id="job1", total_segments=1)
    store.save_state(st)
    assert store.load_state().total_segments == 1


def test_set_stage_and_messages():
    store = JobStore("job2").ensure()
    store.save_state(JobState(job_id="job2"))
    store.set_stage(Stage.RENDERING, "go")
    st = store.load_state()
    assert st.stage == "rendering"
    assert "go" in st.messages


def test_list_ids():
    JobStore("a").ensure().save_book(Book(job_id="a", source_path="", source_hash=""))
    JobStore("b").ensure().save_book(Book(job_id="b", source_path="", source_hash=""))
    assert set(JobStore.list_ids()) >= {"a", "b"}


def test_segment_id_depends_on_text_engine_and_voice():
    vk = voice_key(VoiceSettings(engine="fake"), 24000)
    a = segment_id("hello", "fake-1", vk)
    b = segment_id("hello", "fake-1", vk)
    c = segment_id("world", "fake-1", vk)
    d = segment_id("hello", "fake-2", vk)
    assert a == b            # deterministic
    assert a != c            # text changes id
    assert a != d            # engine version changes id


def test_voice_key_changes_with_settings():
    k1 = voice_key(VoiceSettings(exaggeration=0.5), 24000)
    k2 = voice_key(VoiceSettings(exaggeration=0.9), 24000)
    assert k1 != k2


def test_voice_key_changes_with_new_params():
    base = VoiceSettings()
    for field, val in [("temperature", 1.1), ("repetition_penalty", 1.5),
                       ("min_p", 0.1), ("top_p", 0.8), ("cfg_weight", 0.7)]:
        other = VoiceSettings(**{field: val})
        assert voice_key(base, 24000) != voice_key(other, 24000), field


def test_voice_key_ignores_encode_only_bitrate():
    # bitrate lives in extra and must NOT invalidate cached segment audio.
    a = VoiceSettings(extra={"bitrate_kbps": 64})
    b = VoiceSettings(extra={"bitrate_kbps": 96})
    assert voice_key(a, 24000) == voice_key(b, 24000)


def test_cleanup_intermediates_keeps_metadata_and_output(tmp_path):
    from ebook_audiobook.config import paths
    store = JobStore("cln").ensure()
    store.save_book(Book(job_id="cln", source_path="", source_hash="cln", title="T", author="A"))
    # fake intermediate artifacts
    (store.segments_dir / "s1.wav").write_bytes(b"x" * 1000)
    (store.chapters_audio_dir / "c1.wav").write_bytes(b"y" * 2000)
    (store.dir / "normalized.epub").write_bytes(b"z" * 500)
    store.preview_path().parent.mkdir(parents=True, exist_ok=True)
    store.preview_path().write_bytes(b"p" * 300)
    st = store.load_state()
    st.preview_output = str(store.preview_path())
    st.preview_at = "2026-07-02T00:00:00+00:00"
    store.save_state(st)

    inter = store.intermediate_bytes()
    assert inter == 1000 + 2000 + 500 + 300
    freed = store.cleanup_intermediates()
    assert freed == inter
    assert not (store.segments_dir / "s1.wav").exists()
    assert not store.preview_path().exists()
    assert store.exists()  # book.json (metadata) survives
    # Preview state is cleared so the UI stops pointing at the deleted file.
    cleaned = store.load_state()
    assert cleaned.preview_output is None and cleaned.preview_at is None


def test_delete_removes_everything(tmp_path):
    store = JobStore("del").ensure()
    store.save_book(Book(job_id="del", source_path="", source_hash="del"))
    (store.segments_dir / "s1.wav").write_bytes(b"x" * 1234)
    store.preview_path().parent.mkdir(parents=True, exist_ok=True)
    store.preview_path().write_bytes(b"p" * 300)
    freed = store.delete()
    assert freed >= 1234
    assert not store.dir.exists()
    assert not store.preview_path().exists()
