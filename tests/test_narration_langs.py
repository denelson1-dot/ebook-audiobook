"""The language-model registry: what is installed, judged from disk alone."""

from __future__ import annotations

import pytest

from ebook_audiobook import narration_langs as nl


def fake_cache(tmp_path, files: dict[str, int], incomplete: int = 0):
    """A Hugging Face cache with one snapshot holding ``files`` (name -> bytes)."""
    repo = nl.repo_dir(tmp_path)
    (repo / "refs").mkdir(parents=True)
    (repo / "refs" / "main").write_text("abc123", "utf-8")
    snap = repo / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    blobs = repo / "blobs"
    blobs.mkdir()
    for name, size in files.items():
        blob = blobs / f"sha-{name}"
        blob.write_bytes(b"x" * size)
        (snap / name).symlink_to(blob)
    if incomplete:
        (blobs / "partial.incomplete").write_bytes(b"y" * incomplete)
    return tmp_path


def test_language_tags_are_normalised():
    assert nl.normalize_language_tag("fr-FR") == "fr"
    assert nl.normalize_language_tag("fra") == "fr"
    assert nl.normalize_language_tag("fre") == "fr"
    assert nl.normalize_language_tag("FR") == "fr"
    assert nl.normalize_language_tag("und") == "en"
    assert nl.normalize_language_tag(None) == "en"
    assert nl.normalize_language_tag("xx") == "en"
    assert nl.normalize_language_tag("deu") == "de"


def test_every_language_has_a_pack_a_tier_and_an_iso_code():
    for code, lang in nl.LANGUAGES.items():
        assert lang.pack in nl.PACKS
        assert code in nl.PACKS[lang.pack].languages
        assert lang.tier in ("supported", "experimental")
        assert code in nl.ISO639_2


def test_the_registry_matches_the_engines_language_list():
    chatterbox = pytest.importorskip("chatterbox.mtl_tts")
    assert set(chatterbox.SUPPORTED_LANGUAGES) == nl.MULTILINGUAL.languages


def test_nothing_installed_in_an_empty_cache(tmp_path):
    assert nl.snapshot_dir(tmp_path) is None
    assert not nl.is_installed("english", tmp_path)
    assert not nl.language_available("fr", tmp_path)
    assert nl.bytes_on_disk(nl.MULTILINGUAL, tmp_path) == 0


def test_english_is_always_available_even_before_its_first_download(tmp_path):
    """The English model is fetched by the engine itself on the first render,
    as it always was — and the fake engine needs none. A fresh install must
    not be told to install anything before narrating in English."""
    assert nl.language_available("en", tmp_path)
    assert [lg.code for lg in nl.available_languages(tmp_path)] == ["en"]
    nl.require_installed("en", tmp_path)  # does not raise


def test_installed_means_every_file_present(tmp_path):
    files = {name: 10 for name in nl.ENGLISH.files}
    root = fake_cache(tmp_path, files)
    assert nl.is_installed("english", root)
    assert not nl.is_installed("multilingual", root)
    assert nl.language_available("en", root)
    assert not nl.language_available("fr", root)
    assert [lg.code for lg in nl.available_languages(root)] == ["en"]

    # One file short is not installed — a half-finished download must not count.
    (nl.snapshot_dir(root) / "t3_cfg.safetensors").unlink()
    assert not nl.is_installed("english", root)

    # The multilingual pack, complete, makes French available.
    root2 = fake_cache(tmp_path / "two", {name: 1 for name in nl.MULTILINGUAL.files})
    assert nl.language_available("fr", root2)


def test_download_progress_counts_partial_blobs(tmp_path):
    root = fake_cache(tmp_path, {"conds.pt": 100}, incomplete=250)
    assert nl.bytes_on_disk(nl.MULTILINGUAL, root) == 350


def test_require_installed_explains_what_to_do(tmp_path):
    with pytest.raises(nl.LanguagePackMissing) as info:
        nl.require_installed("fr", tmp_path)
    assert info.value.pack is nl.MULTILINGUAL
    assert "French" in str(info.value) and "Settings" in str(info.value)
    nl.require_installed("en", fake_cache(tmp_path / "ok", {n: 1 for n in nl.ENGLISH.files}))


def test_remove_keeps_the_file_the_other_pack_shares(tmp_path):
    files = {name: 10 for name in set(nl.ENGLISH.files) | set(nl.MULTILINGUAL.files)}
    root = fake_cache(tmp_path, files)
    assert nl.is_installed("english", root) and nl.is_installed("multilingual", root)
    freed = nl.remove("multilingual", root)
    assert freed == 10 * (len(nl.MULTILINGUAL.files) - 1)
    assert not nl.is_installed("multilingual", root)
    assert nl.is_installed("english", root), "conds.pt is shared and must survive"


def test_free_space_check_accounts_for_what_is_already_there(tmp_path):
    root = fake_cache(tmp_path, {"conds.pt": 5})
    ok, needed = nl.free_space_ok(nl.MULTILINGUAL, root)
    assert isinstance(ok, bool)
    assert needed <= int(nl.MULTILINGUAL.size_bytes * 1.05) + nl._SLACK


def test_install_without_the_engine_says_so(monkeypatch, tmp_path):
    import builtins

    real_import = builtins.__import__

    def no_hub(name, *a, **kw):
        if name.startswith("huggingface_hub"):
            raise ImportError(name)
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", no_hub)
    with pytest.raises(nl.DownloadError) as info:
        nl.install("multilingual", root=tmp_path)
    assert "engine" in str(info.value)
