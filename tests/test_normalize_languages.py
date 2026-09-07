"""Text preparation per narration language: English exactly as it was, French
the way a French narrator needs it."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ebook_audiobook import hashing
from ebook_audiobook.config import SAMPLE_RATE, VoiceSettings
from ebook_audiobook.pipeline import chunk
from ebook_audiobook.pipeline import normalize as n
from ebook_audiobook.pipeline.lang import rules_for

GOLDEN = json.loads((Path(__file__).parent / "data" / "golden_en.json").read_text("utf-8"))


# --- English is unchanged, byte for byte -------------------------------------------------

def test_english_normalisation_matches_the_golden_output():
    """Captured before the rules moved into pipeline/lang/en.py.

    This fixture is the "nothing else moved" pin: every rule but one still
    produces exactly what it produced then. The exception is deliberate and
    covered below — a number ending a sentence used to be left as digits, and
    now is spoken — which this input happens not to contain, so it is still an
    honest pin for everything around it.
    """
    assert n.normalize_text(GOLDEN["input"]) == GOLDEN["normalize_text"]
    for title, expected in GOLDEN["titles"].items():
        assert n.normalize_title(title) == expected, title


def test_english_chunking_matches_the_golden_output():
    text = n.normalize_text(GOLDEN["input"])
    assert chunk.split_sentences(text) == GOLDEN["sentences"]
    got = [list(c) for c in chunk.chunk_structured(text, 350, 500)]
    assert got == GOLDEN["chunks"]


def test_english_hashes_are_pinned():
    vk = hashing.voice_key(VoiceSettings(engine="fake"), SAMPLE_RATE)
    assert vk == GOLDEN["voice_key_fake"]
    assert hashing.segment_id("Hello world.", "fake-1", vk) == GOLDEN["segment_id"]


# --- a number at the end of a sentence ---------------------------------------

def test_a_number_that_ends_a_sentence_is_spoken():
    """It never was, in English or French, until 2026-09-07.

    The trailing guard rejected any following dot, so it could not tell a
    decimal point from a full stop and refused both. "He counted to 300."
    reached the model as digits for it to read however it liked. Years escaped
    only because _YEAR runs first and carries no such guard.

    This changes the text of affected segments, so those segments re-render.
    """
    assert n.normalize_text("He counted to 300.") == "He counted to three hundred."
    assert n.normalize_text("She was 42.") == "She was forty-two."
    assert n.normalize_text("Il est arrivé en 1999.", "fr") == \
        "Il est arrivé en mille neuf cent quatre-vingt-dix-neuf."
    assert n.normalize_text("Elle avait 42.", "fr") == "Elle avait quarante-deux."


def test_a_decimal_point_is_still_not_a_full_stop():
    """The other half of the same guard: relaxing it must not start reading
    version numbers and decimals as sentences full of integers."""
    assert n.normalize_text("It was 3.14 exactly.") == "It was 3.14 exactly."
    assert n.normalize_text("Version 1.2.3 shipped.") == "Version 1.2.3 shipped."


def test_a_comma_after_a_number_survives_it():
    """"\\d[\\d,]*" ate the comma in "to 300, then stopped", taking the pause
    it exists for with it."""
    assert n.normalize_text("He counted to 300, then stopped.") == \
        "He counted to three hundred, then stopped."
    # A grouped thousand is still one number, comma and all.
    assert n.normalize_text("She had 1,200 books.") == \
        "She had one thousand, two hundred books."


def test_unknown_languages_get_the_english_rules():
    assert rules_for("xx") is rules_for("en")
    assert n.normalize_text("Mr. Smith paid $5.", "xx") == n.normalize_text("Mr. Smith paid $5.")


# --- French ---------------------------------------------------------------------------------

def fr(text: str) -> str:
    return n.normalize_text(text, "fr")


def test_french_guillemets_and_spacing():
    out = fr("« Bonjour ! » dit-elle ; puis : rien ?")
    assert out == '"Bonjour!" dit-elle; puis: rien?'


def test_french_titles_and_abbreviations():
    assert fr("M. Dupont et Mme Martin") == "Monsieur Dupont et Madame Martin"
    assert fr("le Dr Roux, Me Petit, Mlle Blanc") == "le Docteur Roux, Maître Petit, Mademoiselle Blanc"
    assert fr("St-Denis et Ste Anne") == "Saint-Denis et Sainte-Anne"
    assert fr("p. ex. ceci, c.-à-d. cela, etc.") == "par exemple ceci, c'est-à-dire cela, et cætera"
    assert fr("le n° 4") == "le numéro quatre"
    assert fr("en 52 av. J.-C.") == "en cinquante-deux avant Jésus-Christ"
    # "M." is Monsieur only before a name, so an initial stays an initial.
    assert fr("M. Dupont") != fr("M. dupont")


def test_french_numbers():
    assert fr("en 1999") == "en mille neuf cent quatre-vingt-dix-neuf"
    assert fr("12 000 habitants") == "douze mille habitants"
    assert fr("1 234 567 francs") == "un million deux cent trente-quatre mille cinq cent soixante-sept francs"
    assert fr("le 1er janvier") == "le premier janvier"
    assert fr("la 1re fois") == "la première fois"
    assert fr("le 2e jour, au 21e siècle") == "le deuxième jour, au vingt et unième siècle"
    assert fr("au XVIIIe siècle") == "au dix-huitième siècle"
    assert fr("3,5 %") == "trois virgule cinq pour cent"
    assert fr("50 %") == "cinquante pour cent"
    assert fr("12,50 €") == "douze euros et cinquante centimes"
    assert fr("5 €") == "cinq euros"
    assert fr("$3") == "trois dollars"
    assert fr("à 14 h 30") == "à quatorze heures trente"
    assert fr("à 8 h") == "à huit heures"
    assert fr("3,5 mètres") == "trois virgule cinq mètres"


def test_french_titles_speak_roman_numerals():
    assert n.normalize_title("Chapitre IV", "fr") == "Chapitre quatre"
    assert n.normalize_title("Livre II", "fr") == "Livre deux"


def test_french_symbols():
    assert fr("30° & rising") == "trente degrés et rising"


def test_french_sentences_split_on_accented_capitals_and_guillemets():
    text = fr("Il partit. Élodie resta. « Non ! » Elle partit.")
    # The closing quote is part of the boundary and drops out, as it always
    # has for English — a narrator does not read quotation marks.
    assert chunk.split_sentences(text) == ["Il partit.", "Élodie resta.", '"Non!', "Elle partit."]


def test_french_front_matter_is_off_by_default():
    from ebook_audiobook.worker import _default_included

    for title in ("Table des matières", "TABLE DES MATIERES", "Remerciements",
                  "À propos de l'auteur", "Du même auteur", "Copyright", "Achevé d'imprimer"):
        assert not _default_included(title, "fr"), title
    for title in ("Préface", "Chapitre premier", "Avant-propos", "Le retour"):
        assert _default_included(title, "fr"), title
    # English hints apply to every book; French ones only to French books.
    assert not _default_included("Contents", "fr")
    assert _default_included("Remerciements", "en")


def test_the_apps_own_words_follow_the_narration_language():
    from ebook_audiobook.jobs.models import Book
    from ebook_audiobook.worker import _intro_chapter, _outro_chapter

    book = Book(job_id="j", source_path="x", source_hash="h", title="Le Livre", author="A. Dupont")
    assert _intro_chapter(book, "fr").text == "De A. Dupont."
    outro = _outro_chapter(book, "fr")
    assert outro.text == "Ici se termine Le Livre, de A. Dupont."
    assert outro.title == "Fin"
    assert _intro_chapter(book).text == "By A. Dupont."
    assert _outro_chapter(book).title == "The End"
    assert rules_for("fr").strings["chapter_n"] % {"n": 3} == "Chapitre 3"


def test_the_fallback_chapter_title_is_in_the_books_language():
    from ebook_audiobook.pipeline.extract import _fallback_title

    assert _fallback_title("en", 2) == "Chapter 2"
    assert _fallback_title("fr", 2) == "Chapitre 2"


# --- Japanese ----------------------------------------------------------------

def test_japanese_digits_become_kanji_so_the_engine_can_read_them():
    """The engine runs Japanese through pykakasi, which turns kanji into the
    hiragana the model was trained on but leaves Arabic digits alone. Left as
    digits they reach the model as bare glyphs."""
    assert n.normalize_text("第3章を読んだ。", "ja") == "第三章を読んだ。"
    assert n.normalize_text("2冊の本と6本の鉛筆。", "ja") == "二冊の本と六本の鉛筆。"


def test_japanese_number_guards_are_not_the_latin_ones():
    """Python's \\w matches kanji, so the Latin word-boundary guard rejected
    every digit touching Japanese text — which, with no spaces, is nearly all
    of them. "第3章" kept its digit."""
    assert "3" not in n.normalize_text("第3章", "ja")
    assert "14" not in n.normalize_text("彼は3.14を計算した。", "ja")


def test_japanese_counters_pykakasi_gets_wrong_are_written_out():
    """Kanji numerals are used almost everywhere because pykakasi reads the
    counter irregularities well. These three it does not: it reads 九十九 as the
    poetic つくも, 一分 as いちぶ, and 十四日 as じゅうよんにち."""
    assert n.normalize_text("1999年", "ja").startswith("せんきゅうひゃくきゅうじゅうきゅうねん")
    assert n.normalize_text("8分", "ja") == "はっぷん"
    assert n.normalize_text("10分", "ja") == "じゅっぷん"
    assert n.normalize_text("1月14日", "ja") == "一月じゅうよっか"
    assert n.normalize_text("4時", "ja") == "よじ"


def test_japanese_sentences_split_without_any_whitespace():
    """The shared splitter needs a space after the full stop. Japanese has
    none, so a whole paragraph arrived as one sentence and was then sliced at
    whatever character sat at the budget."""
    para = "吾輩は猫である。名前はまだ無い。「どこで生れたか」と彼は言った。"
    assert chunk.split_sentences(para, "ja") == [
        "吾輩は猫である。", "名前はまだ無い。", "「どこで生れたか」と彼は言った。"]
    # A quoted sentence ends where the bracket closes, not inside it.
    assert chunk.split_sentences("「行こう。」と言った。", "ja") == ["「行こう。」", "と言った。"]


def test_japanese_wrapping_never_starts_a_chunk_on_a_trailing_mark():
    """With no spaces there are no word boundaries to wrap on, so the slice
    point is walked back off any character that may not open a line."""
    para = "彼は言った、" + "そして彼は静かに歩き続けた、" * 20
    out = chunk.chunk_text(para, lang="ja")
    assert len(out) > 1
    assert all(len(c) <= 200 for c in out)
    assert not any(c[0] in "」』）】〕》〉”’、。，．！？ぁぃぅぇぉっゃゅょ" for c in out)
    # Nothing is lost or gained, and no space is invented between chunks.
    assert "".join(out) == para


def test_japanese_chunks_are_budgeted_for_a_denser_script():
    from ebook_audiobook.pipeline.lang import rules_for

    ja, en = rules_for("ja"), rules_for("en")
    assert ja.chunk_target_chars == 140 and ja.chunk_max_chars == 200
    assert ja.joiner == "" and en.joiner == " "
    # English keeps the global budget rather than naming one of its own.
    assert en.chunk_target_chars is None
