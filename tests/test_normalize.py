from app.pipeline import normalize as n


def test_curly_quotes_and_dashes():
    out = n.fix_punctuation("“Hi” — she said…")
    assert '"Hi"' in out          # curly quotes -> straight
    assert "..." in out           # ellipsis normalized
    assert "—" not in out and "…" not in out
    assert "," in out             # em dash became a comma pause


def test_strip_footnote_markers():
    assert n.strip_artifacts("word[12] next{3}") == "word next"


def test_apply_pronunciation_whole_word_case_sensitive():
    ov = {"LOG": "log", "JPL": "J P L"}
    out = n.apply_pronunciation("LOG ENTRY. My log at JPL. Catalog.", ov)
    assert out == "log ENTRY. My log at J P L. Catalog."  # 'log'/'Catalog' untouched
    # No overrides -> unchanged; a literal replacement isn't treated as a regex.
    assert n.apply_pronunciation("LOG", {}) == "LOG"
    assert n.apply_pronunciation("PRICE", {"PRICE": r"$5 \1"}) == r"$5 \1"


def test_titles_expanded():
    out = n.expand_abbreviations("Mr. and Mrs. Smith saw Dr. Jones on St. Ives")
    assert "Mister" in out and "Missus" in out and "Doctor" in out and "Saint" in out


def test_year_read_as_pairs():
    assert "nineteen" in n.speak_numbers("in 1999").lower()


def test_year_2000s():
    assert "two thousand" in n.speak_numbers("in 2005").lower()


def test_currency():
    out = n.speak_numbers("$5")
    assert "five" in out and "dollar" in out


def test_percent():
    assert "percent" in n.speak_numbers("50%")


def test_ordinal():
    assert n.speak_numbers("the 2nd day").lower().startswith("the second")


def test_plain_integer():
    assert "three" in n.speak_numbers("she walked 3 miles").lower()


def test_collapse_whitespace():
    assert n.collapse_whitespace("a   b\n\n\n\nc  ,  d") == "a b\n\nc, d"


def test_roman_numeral_in_title():
    assert n.normalize_title("Chapter IV") == "Chapter four" or "four" in n.normalize_title("Chapter IV")


def test_single_letter_I_not_converted():
    # A lone "I" (pronoun) should not become "1".
    assert "1" not in n.normalize_title("Chapter I")


def test_full_pipeline_idempotentish():
    text = "“The year 1999,” Mr. Smith said—twice[1]."
    out = n.normalize_text(text)
    assert "[" not in out and "“" not in out and "Mister" in out
