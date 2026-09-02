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
    """Captured before the rules moved into pipeline/lang/en.py. Every cached
    segment of every existing render hangs on this staying equal."""
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
