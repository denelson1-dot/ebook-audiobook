"""The interface language: how it is chosen, and that the French is sound.

Two kinds of test. The catalog ones (Babel-dependent, skipped without it) read
``locale/fr/LC_MESSAGES/messages.po`` and hold it to the same standard
``tools/i18n.py check`` and CI do: compiled, complete, placeholders intact. The
rest drive the Flask app and the resolution rules with no Babel at all, the
way the installed app runs.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from ebook_audiobook import i18n
from ebook_audiobook.web import create_app

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "ebook_audiobook" / "web" / "static" / "app.js").read_text("utf-8")

FRENCH = "fr-CA,fr;q=0.9,en;q=0.5"


def _tool():
    """tools/i18n.py, loaded by path so the check logic is shared, not copied."""
    pytest.importorskip("babel")
    spec = importlib.util.spec_from_file_location("i18n_tool", ROOT / "tools" / "i18n.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the catalog itself ---------------------------------------------------------

def test_french_catalog_is_compiled_complete_and_well_formed():
    """Exactly what CI's `tools/i18n.py check` asks, so pytest cannot disagree."""
    assert _tool().problems("fr") == []


def test_every_string_in_the_source_is_in_the_french_po():
    tool = _tool()
    catalog = tool.read_catalog("fr")
    missing = sorted(m.id for m in tool.extract_catalog() if m.id and m.id not in catalog)
    assert not missing, f"run: python tools/i18n.py update — missing {missing}"


def test_plural_rule_in_python_matches_the_po_header():
    tool = _tool()
    header = tool.read_catalog("fr").plural_expr  # e.g. "(n > 1)"
    for n in (0, 1, 2, 5, 21, 1_000_000):
        assert i18n.SUPPORTED["fr"].plural(n) == int(eval(header, {}, {"n": n}))  # noqa: S307


def test_every_supported_language_has_a_js_plural_rule():
    block = re.search(r"const PLURAL_RULES = \{(.*?)\n\};", APP_JS, re.S).group(1)
    keys = set(re.findall(r"^\s*(\w+):", block, re.M))
    assert set(i18n.SUPPORTED) <= keys


def test_catalog_round_trips_to_the_browser():
    """What the page inlines for JS is the same catalog Python reads."""
    assert i18n.js_catalog("en") == {}
    fr = i18n.js_catalog("fr")
    assert fr, "no French strings loaded — is messages.mo compiled?"
    assert fr["Library"] == i18n.translations("fr").gettext("Library") != "Library"
    for msgid, value in fr.items():
        assert isinstance(msgid, str) and msgid
        if isinstance(value, list):
            assert value and all(isinstance(v, str) for v in value)
        else:
            assert isinstance(value, str)


# --- choosing the language -------------------------------------------------------

def test_normalize_folds_regions_and_rejects_unknowns():
    assert i18n.normalize("fr-CA") == "fr"
    assert i18n.normalize("fr_FR") == "fr"
    assert i18n.normalize("EN") == "en"
    assert i18n.normalize("de") == ""
    assert i18n.normalize(None) == ""


def test_resolution_order(monkeypatch):
    monkeypatch.delenv("EBAB_LANG", raising=False)
    assert i18n.resolve("", None) == "en"
    assert i18n.resolve("", "fr") == "fr"
    assert i18n.resolve("fr", "en") == "fr"
    assert i18n.resolve("xx", "fr") == "fr"
    monkeypatch.setenv("EBAB_LANG", "en")
    assert i18n.resolve("fr", "fr") == "en"


def test_browser_language_is_honoured(monkeypatch):
    monkeypatch.delenv("EBAB_LANG")
    client = create_app().test_client()
    r = client.get("/", headers={"Accept-Language": FRENCH})
    body = r.data.decode()
    assert '<html lang="fr">' in body
    assert r.headers["Content-Language"] == "fr"
    assert "Bibliothèque" in body
    assert "Ajouter un livre" in body

    r = client.get("/", headers={"Accept-Language": "de"})
    assert '<html lang="en">' in r.data.decode()
    r = client.get("/")
    assert '<html lang="en">' in r.data.decode()
    assert r.headers["Content-Language"] == "en"


def test_setting_beats_the_browser(monkeypatch):
    monkeypatch.delenv("EBAB_LANG")
    client = create_app().test_client()
    r = client.post("/settings", data={"language": "fr"})
    assert r.get_json()["language"] == "fr"
    assert '<html lang="fr">' in client.get("/", headers={"Accept-Language": "en"}).data.decode()

    # Back to automatic, and an unknown code is the same as automatic.
    assert client.post("/settings", data={"language": ""}).get_json()["language"] == ""
    assert '<html lang="en">' in client.get("/", headers={"Accept-Language": "en"}).data.decode()
    assert client.post("/settings", data={"language": "xx"}).get_json()["language"] == ""


def test_environment_beats_everything(monkeypatch):
    monkeypatch.setenv("EBAB_LANG", "fr")
    client = create_app().test_client()
    client.post("/settings", data={"language": "en"})
    assert '<html lang="fr">' in client.get("/", headers={"Accept-Language": "en"}).data.decode()


def test_a_request_language_does_not_leak_out_of_the_request(monkeypatch):
    monkeypatch.delenv("EBAB_LANG")
    client = create_app().test_client()
    assert "Bibliothèque" in client.get("/", headers={"Accept-Language": FRENCH}).data.decode()
    assert i18n.current_language() == "en"
    assert i18n.gettext("Library") == "Library"


def test_saving_the_setting_sets_the_process_language_for_the_tray(monkeypatch):
    monkeypatch.delenv("EBAB_LANG")
    create_app().test_client().post("/settings", data={"language": "fr"})
    assert i18n.current_language() == "fr"
    assert i18n.gettext("Quit") != "Quit"


def test_the_page_carries_the_catalog_for_its_javascript(monkeypatch):
    monkeypatch.delenv("EBAB_LANG")
    client = create_app().test_client()
    fr = client.get("/new", headers={"Accept-Language": FRENCH}).data.decode()
    assert 'window.EBAB_LANG = "fr"' in fr
    assert "window.EBAB_I18N = {" in fr and '"Library":' in fr
    en = client.get("/new").data.decode()
    assert 'window.EBAB_LANG = "en"; window.EBAB_I18N = {};' in en


def test_the_proof_page_is_entirely_french(monkeypatch):
    monkeypatch.delenv("EBAB_LANG")
    body = create_app().test_client().get("/new", headers={"Accept-Language": FRENCH}).data.decode()
    # The inlined catalog carries the English msgids by design; look at the markup.
    body = re.sub(r"<script\b.*?</script>", "", body, flags=re.S)
    for english in ("Add a book", "Ebook file", "Nothing chosen yet", "Read this book",
                    "Narration engine", "Browse…"):
        assert english not in body, english
    assert "Ajouter un livre" in body
    # A msgid with inline markup renders as markup, not escaped text.
    assert "<code>.m4b</code>" in body


def test_language_setting_is_backward_compatible():
    from ebook_audiobook.settings import Settings

    assert Settings.from_dict({}).language == ""
    assert Settings.from_dict({"power_mode": "quiet"}).language == ""


# --- numbers in French ---------------------------------------------------------------

def test_french_number_formatting():
    assert i18n.fmt_int(1234, "fr") == "1 234"
    assert i18n.fmt_int(1234, "en") == "1,234"
    assert i18n.fmt_number(1.5, 1, "fr") == "1,5"
    assert i18n.human_bytes(1.5 * 2**20, "fr") == "1,5 Mo"
    assert i18n.human_bytes(1.5 * 2**20, "en") == "1.5 MB"
    assert i18n.human_bytes(512, "fr") == "512 o"
    assert i18n.human_bytes(None) == "—"


def test_listening_time_in_french_and_english():
    # 8h04m at 64 kbps: seconds * kbps * 125 bytes
    eight_oh_four = int((8 * 3600 + 4 * 60) * 64 * 125)
    assert i18n.fmt_listening(eight_oh_four, 64) == "8h 04m"
    i18n.set_process_language("fr")
    assert i18n.fmt_listening(eight_oh_four, 64) == "8 h 04 min"
    assert i18n.fmt_listening(12 * 60 * 64 * 125, 64) == "12 min"


def test_python_gettext_formats_like_the_templates():
    """One rule for all three layers: keyword placeholders, %% for a percent."""
    assert i18n.gettext("No folder at %(root)s.", root="/x") == "No folder at /x."
    assert i18n.gettext("Roughly 10–25%% slower.") == "Roughly 10–25% slower."
    assert i18n.ngettext("%(n)s book", "%(n)s books", 1) == "1 book"
    assert i18n.ngettext("%(num)d book", "%(num)d books", 3) == "3 books"
    i18n.set_process_language("fr")
    assert i18n.gettext("Leave room to keep working. Roughly 10–25%% slower.").endswith("plus lent.")
    assert "%%" not in i18n.gettext("Leave room to keep working. Roughly 10–25%% slower.")


def test_stage_labels_and_power_modes_follow_the_request(monkeypatch):
    from ebook_audiobook import power
    from ebook_audiobook.jobs.models import stage_label

    monkeypatch.delenv("EBAB_LANG")
    assert stage_label("rendering") == "Narrating"
    assert power.describe("full").startswith("Full speed")
    client = create_app().test_client()
    body = _visible(client.get("/settings", headers={"Accept-Language": FRENCH}).data.decode())
    assert "Pleine vitesse" in body and "Full speed" not in body
    # The CLI's view of the same tables, outside any request, is still English.
    assert stage_label("rendering") == "Narrating"


def test_prerequisite_banner_is_cached_per_language(monkeypatch):
    monkeypatch.delenv("EBAB_LANG")
    client = create_app().test_client()
    fr = client.get("/api/prereqs", headers={"Accept-Language": FRENCH}).get_json()
    en = client.get("/api/prereqs").get_json()
    names_fr = {c["name"] for c in fr["checks"]}
    names_en = {c["name"] for c in en["checks"]}
    assert "dossier de données (accessible en écriture)" in names_fr
    assert "data folder (writable)" in names_en


def test_english_formatting_is_unchanged_by_the_refactor():
    from ebook_audiobook.web import app as web_app

    assert web_app.human_bytes(1536) == "1.5 KB"
    assert web_app.human_bytes(512) == "512 B"
    assert web_app.human_bytes(3 * 1024**3) == "3.0 GB"
    assert web_app.fmt_listening(None, 64) is None
    assert web_app.fmt_listening(100, 0) is None


# --- every page, in French ------------------------------------------------------------

def _visible(html: str) -> str:
    """The markup without its scripts — the inlined catalog carries English keys."""
    return re.sub(r"<script\b.*?</script>", "", html, flags=re.S)


@pytest.mark.parametrize("path, english, french", [
    ("/", "Add a book", "Ajouter un livre"),
    ("/new", "Read this book", "Lire ce livre"),
    ("/voices", "Add a voice", "Ajouter une voix"),
    ("/settings", "Check for updates", "Rechercher des mises à jour"),
    ("/storage", "Show me the folder", "Afficher le dossier"),
])
def test_every_page_renders_in_french(monkeypatch, path, english, french):
    monkeypatch.delenv("EBAB_LANG")
    r = create_app().test_client().get(path, headers={"Accept-Language": FRENCH})
    assert r.status_code == 200
    body = _visible(r.data.decode())
    assert french in body
    assert english not in body


def test_the_job_page_renders_in_french(monkeypatch, synthetic_epub):
    monkeypatch.delenv("EBAB_LANG")
    client = create_app().test_client()
    r = client.post("/import", data={"path": str(synthetic_epub), "engine": "fake"})
    assert r.status_code in (302, 303)
    job = r.headers["Location"].rstrip("/").split("/")[-1]
    body = _visible(client.get(f"/job/{job}", headers={"Accept-Language": FRENCH}).data.decode())
    for french in ("Narrer le livre", "Ce qui sera narré", "Réglages du moteur", "Écouter d'abord"):
        assert french in body, french
    for english in ("Narrate the book", "What gets narrated", "Engine settings", "Hear it first"):
        assert english not in body, english
