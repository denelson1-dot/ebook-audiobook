"""Backing up and restoring the data root.

The thing that makes this worth having a module for is that the data root is
wildly lopsided. A finished book on this machine measures:

    jobs/<id>/segments/  1.6 GB   rendered audio, 4,473 files
    jobs/<id>/chapters/  1.7 GB   assembled per-chapter audio
    everything else      2.2 MB   the book, the chaptering, the settings

So a naive "zip the data folder" backup is three gigabytes of *regenerable*
audio wrapped around two megabytes of irreplaceable state — and it is the two
megabytes that actually encodes your work. Rendered audio is content-addressed
and reproducible from the book plus the voice settings; losing it costs GPU
time, not information.

Hence profiles. ``projects`` (the default) keeps everything needed to pick a
book back up and re-render it, and throws away the audio. ``full`` keeps the
audio too, for when re-rendering is the expensive part.

Two directories are *never* included at any setting:

``venv/``  the installed program itself, 7.6 GB, which lives under the data root
           on this platform. It is not data, and restoring it over a different
           machine's Python would be actively harmful.
``tmp/``   scratch space, meaningless once a run ends.
"""

from __future__ import annotations

from .i18n import N_, _
import json
import os
import time
import zipfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from .config import paths

MANIFEST_NAME = "ebook-audiobook-backup.json"
PAYLOAD_PREFIX = "data"
FORMAT_VERSION = 1

# Never backed up, under any profile. See the module docstring.
NEVER = ("venv", ".venv", "tmp")


@dataclass(frozen=True)
class Selection:
    """What goes into the archive. Every field is independently switchable."""

    settings: bool = True        # settings.json and any other small root files
    voices: bool = True          # your reference clips — small and irreplaceable
    imports: bool = True         # the source ebooks
    job_metadata: bool = True    # chaptering, job state, covers: the actual work
    job_audio: bool = False      # rendered segments/chapters — big, regenerable
    outputs: bool = False        # finished .m4b files and previews
    models: bool = False         # re-downloadable TTS model cache
    logs: bool = False           # diagnostic log

    def to_dict(self) -> dict:
        return asdict(self)


PROFILES: dict[str, Selection] = {
    # Just enough to make a new machine feel like this one.
    "settings": Selection(imports=False, job_metadata=False),
    # The default: your books and your work, without the regenerable audio.
    "projects": Selection(),
    # Everything except the model cache, which is a download away.
    "full": Selection(job_audio=True, outputs=True, logs=True),
}

DEFAULT_PROFILE = "projects"

CATEGORY_LABELS = {
    "settings": N_("settings"),
    "voices": N_("voice clips"),
    "imports": N_("imported books"),
    "job_metadata": N_("project data"),
    "job_audio": N_("rendered audio"),
    "outputs": N_("finished audiobooks"),
    "models": N_("model cache"),
    "logs": N_("logs"),
}


def classify(rel: Path) -> str | None:
    """Which category a data-root-relative path belongs to, or None to skip."""
    parts = rel.parts
    if not parts:
        return None
    top = parts[0]
    if top in NEVER:
        return None
    if top == "jobs":
        # jobs/<id>/segments/** and jobs/<id>/chapters/** are the rendered audio;
        # everything else in a job directory is the work itself.
        if len(parts) >= 3 and parts[2] in ("segments", "chapters"):
            return "job_audio"
        return "job_metadata"
    if top == "voices":
        return "voices"
    if top == "imports":
        return "imports"
    if top == "outputs":
        return "outputs"
    if top == "models":
        return "models"
    if top == "logs":
        return "logs"
    # settings.json, and any other small root-level file a future version adds.
    return "settings" if len(parts) == 1 else None


def _iter_files(root: Path):
    """Walk the data root, never *descending* into the excluded directories.

    Pruning matters more than it looks. On a real install the virtualenv sits
    under the data root and holds 48,242 of the 52,915 files there — 91% of the
    tree. Walking it and discarding the results afterwards made simply measuring
    a backup take over three seconds; skipping it outright makes that instant.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        try:
            rel_dir = here.relative_to(root)
        except ValueError:  # pragma: no cover - os.walk cannot produce this
            continue
        if rel_dir == Path("."):
            # NEVER names are top-level only, so prune precisely there rather
            # than blocking a job legitimately named "tmp" deeper in the tree.
            dirnames[:] = [d for d in dirnames if d not in NEVER]
        for name in sorted(filenames):
            p = here / name
            if p.is_symlink() or not p.is_file():
                continue
            yield p, rel_dir / name


@dataclass
class Estimate:
    by_category: dict[str, tuple[int, int]]  # category -> (file count, bytes)
    selected_bytes: int
    selected_files: int
    total_bytes: int

    @property
    def excluded_bytes(self) -> int:
        return self.total_bytes - self.selected_bytes


def estimate(selection: Selection, root: Path | None = None) -> Estimate:
    """Measure what a backup would contain, without writing anything."""
    root = Path(root) if root else paths().root
    by_cat: dict[str, tuple[int, int]] = {}
    total = 0
    sel_bytes = 0
    sel_files = 0
    if not root.is_dir():
        return Estimate({}, 0, 0, 0)
    for p, rel in _iter_files(root):
        cat = classify(rel)
        if cat is None:
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        total += size
        count, nbytes = by_cat.get(cat, (0, 0))
        by_cat[cat] = (count + 1, nbytes + size)
        if getattr(selection, cat, False):
            sel_bytes += size
            sel_files += 1
    return Estimate(by_cat, sel_bytes, sel_files, total)


def resolve_selection(profile: str = DEFAULT_PROFILE, **overrides) -> Selection:
    """A profile, plus any explicit per-category overrides (None means leave)."""
    if profile not in PROFILES:
        raise ValueError(
            f"Unknown profile {profile!r}. Choose from: {', '.join(PROFILES)}")
    sel = PROFILES[profile]
    changes = {k: v for k, v in overrides.items() if v is not None}
    return replace(sel, **changes) if changes else sel


class BackupError(RuntimeError):
    pass


def create(dest: Path, selection: Selection | None = None,
           root: Path | None = None, max_bytes: int | None = None) -> Path:
    """Write a backup archive. Returns the path written.

    ``max_bytes`` is checked against the *uncompressed* selected size before any
    work starts, so an over-budget backup fails immediately rather than after
    writing several gigabytes.
    """
    selection = selection or PROFILES[DEFAULT_PROFILE]
    root = Path(root) if root else paths().root
    dest = Path(dest)
    if not root.is_dir():
        raise BackupError(_("There's no data folder at %(root)s.", root=root))

    est = estimate(selection, root=root)
    if max_bytes is not None and est.selected_bytes > max_bytes:
        raise BackupError(_(
            "That backup would be about %(size)s, over the %(limit)s limit. Use a "
            "smaller profile (--profile settings), or raise --max-size.", size=human_bytes(est.selected_bytes), limit=human_bytes(max_bytes)))

    manifest = {
        "format": FORMAT_VERSION,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "app_version": _app_version(),
        "selection": selection.to_dict(),
        "source_root": str(root),
        "files": est.selected_files,
        "uncompressed_bytes": est.selected_bytes,
    }

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".partial")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2))
            for p, rel in _iter_files(root):
                cat = classify(rel)
                if cat is None or not getattr(selection, cat, False):
                    continue
                z.write(p, f"{PAYLOAD_PREFIX}/{rel.as_posix()}")
        tmp.replace(dest)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise BackupError(_("Couldn't write the backup: %(e)s", e=e)) from e
    return dest


def _app_version() -> str:
    from . import __version__

    return __version__


def read_manifest(archive: Path) -> dict:
    try:
        with zipfile.ZipFile(archive) as z:
            return json.loads(z.read(MANIFEST_NAME).decode("utf-8"))
    except KeyError as e:
        raise BackupError(_("%(name)s isn't an ebook-audiobook backup (no manifest inside).", name=Path(archive).name)) from e
    except (zipfile.BadZipFile, OSError, ValueError) as e:
        raise BackupError(_("Couldn't read %(name)s: %(e)s", name=Path(archive).name, e=e)) from e


def _safe_target(root: Path, name: str) -> Path | None:
    """Resolve an archive entry to a path inside ``root``, or None if it escapes.

    Guards against an archive containing ``../`` or an absolute path — the
    classic zip-slip write-anywhere bug. Cheap to do, catastrophic to skip.
    """
    if not name.startswith(PAYLOAD_PREFIX + "/"):
        return None
    rel = name[len(PAYLOAD_PREFIX) + 1:]
    if not rel or rel.endswith("/"):
        return None
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


@dataclass
class RestoreResult:
    written: int
    skipped: int   # files left alone because they already existed
    root: Path
    ignored: int = 0  # non-payload entries: the manifest, or a path that escaped


def restore(archive: Path, into: Path | None = None, force: bool = False) -> RestoreResult:
    """Unpack a backup into the data root.

    Existing files are left alone unless ``force`` is set: a restore should not
    be able to silently destroy work that is newer than the backup.
    """
    archive = Path(archive)
    root = Path(into) if into else paths().root
    read_manifest(archive)  # validates that this is one of ours before writing
    root.mkdir(parents=True, exist_ok=True)

    written = skipped = ignored = 0
    try:
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue
                target = _safe_target(root, info.filename)
                if target is None:
                    # The manifest, or an entry trying to escape the root. Not a
                    # skipped *file* — saying so would imply data was left behind.
                    ignored += 1
                    continue
                if target.exists() and not force:
                    skipped += 1
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(target, "wb") as out:
                    while chunk := src.read(1 << 20):
                        out.write(chunk)
                written += 1
    except (zipfile.BadZipFile, OSError) as e:
        raise BackupError(_("Couldn't restore %(name)s: %(e)s", name=archive.name, e=e)) from e
    return RestoreResult(written=written, skipped=skipped, root=root, ignored=ignored)


def human_bytes(n: int) -> str:
    step = 1024.0
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < step or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= step
    return f"{value:.1f} TB"  # pragma: no cover - loop always returns
