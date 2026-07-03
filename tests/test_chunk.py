from app.pipeline.chunk import chunk_structured, chunk_text, is_scene_break, split_sentences


def test_split_basic_sentences():
    s = split_sentences("Hello world. How are you? I am fine!")
    assert s == ["Hello world.", "How are you?", "I am fine!"]


def test_paragraphs_never_merge():
    s = split_sentences("First para.\n\nSecond para.")
    assert s == ["First para.", "Second para."]


def test_chunks_respect_max():
    text = " ".join(f"Sentence number {i} here." for i in range(200))
    chunks = chunk_text(text, target_chars=350, max_chars=500)
    assert all(len(c) <= 500 for c in chunks)
    assert len(chunks) > 1


def test_order_preserved_and_lossless_words():
    text = "Alpha one. Beta two. Gamma three. Delta four."
    chunks = chunk_text(text, target_chars=20, max_chars=30)
    joined = " ".join(chunks)
    for word in ["Alpha", "Beta", "Gamma", "Delta"]:
        assert word in joined
    # order preserved
    assert joined.index("Alpha") < joined.index("Beta") < joined.index("Gamma")


def test_long_sentence_is_split_on_clauses():
    long = "This clause is here, and this clause is here, and yet another clause is here, plus more."
    chunks = chunk_text(long, target_chars=30, max_chars=40)
    assert all(len(c) <= 40 for c in chunks)


def test_overlong_single_word_hard_wraps():
    text = "x" * 1200
    chunks = chunk_text(text, target_chars=350, max_chars=500)
    assert all(len(c) <= 500 for c in chunks)


def test_empty_text():
    assert chunk_text("") == []
    assert chunk_text("   \n\n  ") == []


# --- structure-aware chunking ------------------------------------------------

def test_is_scene_break():
    for m in ["* * *", "***", "⁂", "###", "· · ·", "~~~"]:
        assert is_scene_break(m), m
    for not_m in ["A normal sentence.", "* item in a list", "", "1999"]:
        assert not is_scene_break(not_m), not_m


def test_structured_marks_paragraph_boundaries():
    text = "First para sentence one. Sentence two.\n\nSecond paragraph here."
    out = chunk_structured(text, target_chars=1000, max_chars=1000)
    assert len(out) == 2                    # one chunk per (short) paragraph
    assert out[0][1] == "paragraph"
    assert out[1][1] == "paragraph"


def test_structured_sentence_boundary_within_paragraph():
    out = chunk_structured("Alpha one. Beta two. Gamma three.", target_chars=12, max_chars=15)
    assert len(out) >= 2
    assert out[-1][1] == "paragraph"        # end of the paragraph
    assert all(b == "sentence" for _, b in out[:-1])  # mid-paragraph breaks


def test_structured_never_merges_across_paragraphs():
    out = chunk_structured("End here.\n\nNew paragraph starts.", target_chars=1000, max_chars=1000)
    assert [t for t, _ in out] == ["End here.", "New paragraph starts."]


def test_scene_break_upgrades_previous_boundary_and_is_not_spoken():
    out = chunk_structured("End of a scene.\n\n* * *\n\nStart of the next scene.",
                           target_chars=1000, max_chars=1000)
    assert len(out) == 2
    assert out[0][1] == "scene"
    assert all("*" not in t for t, _ in out)
