"""Backup and restore, and above all what they leave out.

The data root is lopsided: on a real machine one finished book is 3.3 GB of
rendered audio wrapped around 2.2 MB of irreplaceable state. The audio is
content-addressed and reproducible; the 2.2 MB is not. So the defaults matter
more than the mechanism, and most of these tests are about what does *not* end
up in the archive — including the 7.6 GB virtualenv that lives under the data
root and is not data at all.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from ebook_audiobook import backup


@pytest.fixture
def data_root(tmp_path) -> Path:
    """A miniature of the real layout, including the traps."""
    root = tmp_path / "data"
    files = {
        "settings.json": '{"audiobooks_root": "/media/books"}',
        "voices/narrator.wav": "voice clip",
        "imports/abc123.epub": "the source book",
        "jobs/abc123/book.json": '{"title": "A Book"}',
        "jobs/abc123/chapters.json": "[]",
        "jobs/abc123/job_state.json": '{"stage": "done"}',
        "jobs/abc123/segments.jsonl": '{"id": 1}',
        "jobs/abc123/cover.jpg": "cover bytes",
        "jobs/abc123/segments/aaaa.wav": "RENDERED AUDIO" * 100,
        "jobs/abc123/segments/bbbb.wav": "RENDERED AUDIO" * 100,
        "jobs/abc123/chapters/ch1.wav": "ASSEMBLED AUDIO" * 100,
        "outputs/abc123.m4b": "finished audiobook",
        "models/blob.bin": "downloadable model",
        "logs/errors.log": "{}",
        "tmp/scratch.wav": "scratch",
        # The installed program, which lives under the data root on Linux/macOS.
        "venv/bin/python": "#!/binary",
        "venv/lib/python3.12/site-packages/torch/big.so": "T" * 5000,
    }
    for rel, content in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return root


def _names(archive: Path) -> set[str]:
    with zipfile.ZipFile(archive) as z:
        return {n[len(backup.PAYLOAD_PREFIX) + 1:] for n in z.namelist()
                if n.startswith(backup.PAYLOAD_PREFIX + "/")}


# --- what is never included ---------------------------------------------------

@pytest.mark.parametrize("profile", list(backup.PROFILES))
def test_the_virtualenv_is_never_backed_up(data_root, tmp_path, profile):
    """7.6 GB of installed program, and restoring it elsewhere would be harmful."""
    dest = backup.create(tmp_path / f"{profile}.zip",
                         backup.PROFILES[profile], root=data_root)
    assert not any(n.startswith("venv/") for n in _names(dest))


@pytest.mark.parametrize("profile", list(backup.PROFILES))
def test_scratch_space_is_never_backed_up(data_root, tmp_path, profile):
    dest = backup.create(tmp_path / f"{profile}.zip",
                         backup.PROFILES[profile], root=data_root)
    assert not any(n.startswith("tmp/") for n in _names(dest))


def test_the_venv_is_not_even_counted_in_the_estimate(data_root):
    """It must not inflate the size we quote, either."""
    est = backup.estimate(backup.PROFILES["full"], root=data_root)
    assert "venv" not in est.by_category
    assert est.total_bytes < 5000, "the venv leaked into the totals"


# --- the default profile ------------------------------------------------------

def test_projects_profile_keeps_the_work(data_root, tmp_path):
    dest = backup.create(tmp_path / "b.zip", backup.PROFILES["projects"], root=data_root)
    names = _names(dest)
    assert "settings.json" in names
    assert "voices/narrator.wav" in names
    assert "imports/abc123.epub" in names
    assert "jobs/abc123/book.json" in names
    assert "jobs/abc123/chapters.json" in names
    assert "jobs/abc123/segments.jsonl" in names
    assert "jobs/abc123/cover.jpg" in names


def test_projects_profile_drops_the_rendered_audio(data_root, tmp_path):
    """The whole point: regenerable audio is not worth backing up."""
    dest = backup.create(tmp_path / "b.zip", backup.PROFILES["projects"], root=data_root)
    names = _names(dest)
    assert not any("/segments/" in n for n in names)
    assert not any("/chapters/" in n for n in names)
    # ...but the segment *index* is metadata and must survive.
    assert "jobs/abc123/segments.jsonl" in names


def test_projects_is_dramatically_smaller_than_full(data_root):
    projects = backup.estimate(backup.PROFILES["projects"], root=data_root)
    full = backup.estimate(backup.PROFILES["full"], root=data_root)
    assert projects.selected_bytes < full.selected_bytes / 2


def test_settings_profile_is_the_smallest(data_root):
    sizes = {name: backup.estimate(sel, root=data_root).selected_bytes
             for name, sel in backup.PROFILES.items()}
    assert sizes["settings"] < sizes["projects"] < sizes["full"]


def test_full_still_excludes_the_model_cache(data_root, tmp_path):
    """A gigabyte that a download replaces is not worth carrying around."""
    dest = backup.create(tmp_path / "b.zip", backup.PROFILES["full"], root=data_root)
    assert not any(n.startswith("models/") for n in _names(dest))


def test_models_can_be_opted_into(data_root, tmp_path):
    sel = backup.resolve_selection("full", models=True)
    dest = backup.create(tmp_path / "b.zip", sel, root=data_root)
    assert "models/blob.bin" in _names(dest)


# --- classification -----------------------------------------------------------

@pytest.mark.parametrize("rel,expected", [
    ("settings.json", "settings"),
    ("voices/a.wav", "voices"),
    ("imports/a.epub", "imports"),
    ("jobs/x/book.json", "job_metadata"),
    ("jobs/x/segments/a.wav", "job_audio"),
    ("jobs/x/chapters/a.wav", "job_audio"),
    ("outputs/a.m4b", "outputs"),
    ("models/a.bin", "models"),
    ("logs/errors.log", "logs"),
    ("venv/bin/python", None),
    ("tmp/a.wav", None),
])
def test_classify(rel, expected):
    assert backup.classify(Path(rel)) == expected


# --- overrides and limits -----------------------------------------------------

def test_overrides_beat_the_profile(data_root, tmp_path):
    sel = backup.resolve_selection("projects", job_audio=True, imports=False)
    dest = backup.create(tmp_path / "b.zip", sel, root=data_root)
    names = _names(dest)
    assert any("/segments/" in n for n in names)
    assert not any(n.startswith("imports/") for n in names)


def test_an_unset_override_leaves_the_profile_alone():
    assert backup.resolve_selection("projects") == backup.PROFILES["projects"]


def test_unknown_profile_is_rejected():
    with pytest.raises(ValueError, match="Unknown profile"):
        backup.resolve_selection("enormous")


def test_max_size_refuses_before_writing_anything(data_root, tmp_path):
    dest = tmp_path / "b.zip"
    with pytest.raises(backup.BackupError, match="over the"):
        backup.create(dest, backup.PROFILES["full"], root=data_root, max_bytes=10)
    assert not dest.exists(), "a rejected backup must not leave a file behind"


def test_a_partial_file_is_not_left_behind(data_root, tmp_path):
    dest = tmp_path / "b.zip"
    backup.create(dest, backup.PROFILES["projects"], root=data_root)
    assert not dest.with_name(dest.name + ".partial").exists()


# --- restore ------------------------------------------------------------------

def test_round_trip_restores_every_file(data_root, tmp_path):
    dest = backup.create(tmp_path / "b.zip", backup.PROFILES["projects"], root=data_root)
    target = tmp_path / "restored"
    result = backup.restore(dest, into=target)
    assert result.written == len(_names(dest))
    assert (target / "settings.json").read_text() == '{"audiobooks_root": "/media/books"}'
    assert (target / "jobs/abc123/book.json").read_text() == '{"title": "A Book"}'


def test_restore_does_not_clobber_newer_work(data_root, tmp_path):
    dest = backup.create(tmp_path / "b.zip", backup.PROFILES["projects"], root=data_root)
    target = tmp_path / "restored"
    (target / "jobs/abc123").mkdir(parents=True)
    (target / "jobs/abc123/book.json").write_text("NEWER")
    result = backup.restore(dest, into=target)
    assert (target / "jobs/abc123/book.json").read_text() == "NEWER"
    assert result.skipped == 1


def test_force_overwrites(data_root, tmp_path):
    dest = backup.create(tmp_path / "b.zip", backup.PROFILES["projects"], root=data_root)
    target = tmp_path / "restored"
    (target / "jobs/abc123").mkdir(parents=True)
    (target / "jobs/abc123/book.json").write_text("NEWER")
    backup.restore(dest, into=target, force=True)
    assert (target / "jobs/abc123/book.json").read_text() == '{"title": "A Book"}'


def test_the_manifest_is_not_reported_as_a_skipped_file(data_root, tmp_path):
    """It isn't payload, and calling it "skipped" implies data was left behind."""
    dest = backup.create(tmp_path / "b.zip", backup.PROFILES["projects"], root=data_root)
    result = backup.restore(dest, into=tmp_path / "restored")
    assert result.skipped == 0
    assert result.ignored == 1


def test_restore_rejects_a_foreign_zip(tmp_path):
    bogus = tmp_path / "holiday-photos.zip"
    with zipfile.ZipFile(bogus, "w") as z:
        z.writestr("photo.jpg", "not a backup")
    with pytest.raises(backup.BackupError, match="isn't an ebook-audiobook backup"):
        backup.restore(bogus, into=tmp_path / "restored")


def test_restore_refuses_to_write_outside_the_root(tmp_path):
    """Zip-slip: an archive must not be able to write anywhere it likes."""
    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr(backup.MANIFEST_NAME, '{"format": 1}')
        z.writestr(f"{backup.PAYLOAD_PREFIX}/../../escaped.txt", "pwned")
    target = tmp_path / "restored"
    result = backup.restore(evil, into=target)
    assert result.written == 0
    assert not (tmp_path.parent / "escaped.txt").exists()
    assert not (tmp_path / "escaped.txt").exists()


def test_the_manifest_records_what_was_chosen(data_root, tmp_path):
    dest = backup.create(tmp_path / "b.zip", backup.PROFILES["projects"], root=data_root)
    manifest = backup.read_manifest(dest)
    assert manifest["format"] == backup.FORMAT_VERSION
    assert manifest["selection"]["job_audio"] is False
    assert manifest["files"] > 0
    assert manifest["app_version"]


def test_backing_up_a_missing_root_is_an_error(tmp_path):
    with pytest.raises(backup.BackupError, match="no data folder"):
        backup.create(tmp_path / "b.zip", root=tmp_path / "nope")


def test_human_bytes():
    assert backup.human_bytes(512) == "512 B"
    assert backup.human_bytes(1536) == "1.5 KB"
    assert backup.human_bytes(3 * 1024**3) == "3.0 GB"
