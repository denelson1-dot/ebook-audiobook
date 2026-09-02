"""Which languages a book can be narrated in, and what that needs on disk.

The engine ships two sets of weights in one Hugging Face repository: the
English model the app has always used, and a multilingual one covering 23
languages. They are separate downloads of about 3 GB each, and a language is
narratable only once its set is in the local cache. This module is the one
place that knows the file lists, tells whether a set is installed **without
touching the network**, and performs the opt-in download when asked.

It imports nothing heavy at module level. A ``--no-tts`` install, the CLI's
``check`` and the test suite all read it; only :func:`install` reaches for
``huggingface_hub``, and only when the user has pressed Install.

Two languages are kept apart everywhere in the app: the language of the
*interface* (``ebook_audiobook.i18n``) and the language a *book* is narrated in
(this module). A French-speaking user narrates English books, and vice versa.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .i18n import N_, _

HF_REPO = "ResembleAI/chatterbox"


@dataclass(frozen=True)
class Pack:
    id: str
    label: str                  # N_() msgid; say it with _() when shown
    files: tuple[str, ...]      # exact filenames in HF_REPO
    size_bytes: int             # measured from the repository listing
    languages: frozenset[str]   # two-letter codes this pack narrates


# Sizes are the repository's own figures for these files (September 2026).
# ``conds.pt`` is listed by both packs: it is one file, shared, and must not be
# removed while either pack remains.
ENGLISH = Pack(
    "english", N_("English"),
    ("ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors", "tokenizer.json", "conds.pt"),
    3_191_971_378,
    frozenset({"en"}),
)
MULTILINGUAL = Pack(
    "multilingual", N_("Other languages"),
    ("ve.pt", "t3_mtl23ls_v2.safetensors", "s3gen.pt", "grapheme_mtl_merged_expanded_v1.json",
     "conds.pt", "Cangjie5_TC.json"),
    3_208_951_748,
    frozenset({"ar", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "it", "ja", "ko",
               "ms", "nl", "no", "pl", "pt", "ru", "sv", "sw", "tr", "zh"}),
)
PACKS = {ENGLISH.id: ENGLISH, MULTILINGUAL.id: MULTILINGUAL}


@dataclass(frozen=True)
class Language:
    code: str
    name: str        # N_() msgid — the language's English name
    pack: str
    # "supported": the text is prepared for narration (numbers, abbreviations,
    # headings). "experimental": the model speaks it, but numbers and
    # abbreviations are read as written.
    tier: str


LANGUAGES: dict[str, Language] = {
    "en": Language("en", N_("English"), "english", "supported"),
    "fr": Language("fr", N_("French"), "multilingual", "supported"),
    "ar": Language("ar", N_("Arabic"), "multilingual", "experimental"),
    "da": Language("da", N_("Danish"), "multilingual", "experimental"),
    "de": Language("de", N_("German"), "multilingual", "experimental"),
    "el": Language("el", N_("Greek"), "multilingual", "experimental"),
    "es": Language("es", N_("Spanish"), "multilingual", "experimental"),
    "fi": Language("fi", N_("Finnish"), "multilingual", "experimental"),
    "he": Language("he", N_("Hebrew"), "multilingual", "experimental"),
    "hi": Language("hi", N_("Hindi"), "multilingual", "experimental"),
    "it": Language("it", N_("Italian"), "multilingual", "experimental"),
    "ja": Language("ja", N_("Japanese"), "multilingual", "experimental"),
    "ko": Language("ko", N_("Korean"), "multilingual", "experimental"),
    "ms": Language("ms", N_("Malay"), "multilingual", "experimental"),
    "nl": Language("nl", N_("Dutch"), "multilingual", "experimental"),
    "no": Language("no", N_("Norwegian"), "multilingual", "experimental"),
    "pl": Language("pl", N_("Polish"), "multilingual", "experimental"),
    "pt": Language("pt", N_("Portuguese"), "multilingual", "experimental"),
    "ru": Language("ru", N_("Russian"), "multilingual", "experimental"),
    "sv": Language("sv", N_("Swedish"), "multilingual", "experimental"),
    "sw": Language("sw", N_("Swahili"), "multilingual", "experimental"),
    "tr": Language("tr", N_("Turkish"), "multilingual", "experimental"),
    "zh": Language("zh", N_("Chinese"), "multilingual", "experimental"),
}

# ISO 639-2/B codes for the m4b's language tag, which is what players read.
ISO639_2 = {
    "en": "eng", "fr": "fra", "ar": "ara", "da": "dan", "de": "deu", "el": "ell", "es": "spa",
    "fi": "fin", "he": "heb", "hi": "hin", "it": "ita", "ja": "jpn", "ko": "kor", "ms": "msa",
    "nl": "nld", "no": "nor", "pl": "pol", "pt": "por", "ru": "rus", "sv": "swe", "sw": "swa",
    "tr": "tur", "zh": "zho",
}
# What an EPUB's dc:language may say instead of the two-letter code. Calibre
# rewrites the tag as ISO 639-2 ("fra") in the file the parser actually reads.
_ALIASES = {
    "eng": "en", "fra": "fr", "fre": "fr", "ara": "ar", "dan": "da", "deu": "de", "ger": "de",
    "ell": "el", "gre": "el", "spa": "es", "fin": "fi", "heb": "he", "hin": "hi", "ita": "it",
    "jpn": "ja", "kor": "ko", "msa": "ms", "may": "ms", "nld": "nl", "dut": "nl", "nor": "no",
    "nob": "no", "nno": "no", "pol": "pl", "por": "pt", "rus": "ru", "swe": "sv", "swa": "sw",
    "tur": "tr", "zho": "zh", "chi": "zh",
}


def normalize_language_tag(raw: str | None) -> str:
    """``"fr-FR"``, ``"fra"``, ``"fre"`` -> ``"fr"``; unknown or absent -> ``"en"``."""
    if not raw:
        return "en"
    primary = str(raw).strip().lower().replace("_", "-").split("-", 1)[0]
    primary = _ALIASES.get(primary, primary)
    return primary if primary in LANGUAGES else "en"


def pack_for(lang: str) -> Pack:
    return PACKS[LANGUAGES.get(lang, LANGUAGES["en"]).pack]


# --- what is on disk, without asking the network ---------------------------------

def cache_root() -> Path:
    """The Hugging Face hub cache, the way huggingface_hub would find it —
    resolved here so this works when that library is not installed."""
    try:
        from huggingface_hub import constants  # type: ignore[import-not-found]

        return Path(constants.HF_HUB_CACHE)
    except Exception:  # noqa: BLE001 - no [tts] extra; mirror its defaults
        pass
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"]) / "hub"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "huggingface" / "hub"


def repo_dir(root: Path | None = None) -> Path:
    return (root or cache_root()) / ("models--" + HF_REPO.replace("/", "--"))


def snapshot_dir(root: Path | None = None) -> Path | None:
    """The snapshot ``refs/main`` points at, or the newest one if that is gone."""
    repo = repo_dir(root)
    ref = repo / "refs" / "main"
    try:
        sha = ref.read_text("utf-8").strip()
        if sha and (repo / "snapshots" / sha).is_dir():
            return repo / "snapshots" / sha
    except OSError:
        pass
    snaps = repo / "snapshots"
    if not snaps.is_dir():
        return None
    candidates = [p for p in snaps.iterdir() if p.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def installed_files(pack: Pack, root: Path | None = None) -> dict[str, int]:
    """``{filename: bytes}`` for the pack's files that are present and complete."""
    snap = snapshot_dir(root)
    if snap is None:
        return {}
    out: dict[str, int] = {}
    for name in pack.files:
        p = snap / name
        try:
            if p.is_file():  # follows the symlink into blobs/, or the copy on Windows
                out[name] = p.stat().st_size
        except OSError:
            continue
    return out


def is_installed(pack_id: str, root: Path | None = None) -> bool:
    pack = PACKS[pack_id]
    return set(installed_files(pack, root)) == set(pack.files)


def bytes_on_disk(pack: Pack, root: Path | None = None) -> int:
    """Complete files plus whatever is still arriving — the download's progress.

    huggingface_hub writes each file as ``blobs/<sha>.incomplete`` and renames
    it when done, so counting those is how far along a download is without a
    progress callback (tqdm is disabled process-wide by ``quiet.py``).
    """
    total = sum(installed_files(pack, root).values())
    blobs = repo_dir(root) / "blobs"
    try:
        for p in blobs.glob("*.incomplete"):
            total += p.stat().st_size
    except OSError:
        pass
    return total


def language_available(lang: str, root: Path | None = None) -> bool:
    """Whether a book can be narrated in ``lang`` on this machine.

    English is always available: its model is the one the engine fetches by
    itself on the first render, as it always has, and the fake engine needs
    no model at all. Only the opt-in pack is ever a precondition.
    """
    if lang == "en":
        return True
    return is_installed(pack_for(lang).id, root)


def available_languages(root: Path | None = None) -> list[Language]:
    return [lg for lg in LANGUAGES.values() if language_available(lg.code, root)]


class LanguagePackMissing(ValueError):
    """Narrating in this language needs a model that is not installed.

    The message is user-facing; ``pack`` says which one to offer."""

    def __init__(self, lang: str):
        self.lang = lang
        self.pack = pack_for(lang)
        super().__init__(_(
            "Narrating in %(language)s needs the additional language model "
            "(%(size)s). Install it in Settings, under Narration languages.",
            language=_(LANGUAGES.get(lang, LANGUAGES["en"]).name),
            size=_human_gb(self.pack.size_bytes)))


def require_installed(lang: str, root: Path | None = None) -> None:
    if not language_available(lang, root):
        raise LanguagePackMissing(lang)


def _human_gb(n: int) -> str:
    from .i18n import fmt_number

    return _("%(gb)s GB", gb=fmt_number(n / 1e9, 1))


# --- the opt-in download ---------------------------------------------------------------

# Room for the pack, a little slack for the cache's own bookkeeping, and half a
# gigabyte so the disk is not left brim-full by the app.
_SLACK = 512 * 1024 * 1024


def free_space_ok(pack: Pack, root: Path | None = None) -> tuple[bool, int]:
    """``(ok, needed_bytes)`` — whether the pack fits where the cache lives."""
    needed = int(pack.size_bytes * 1.05) + _SLACK - bytes_on_disk(pack, root)
    target = root or cache_root()
    probe = target
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        return True, max(needed, 0)  # can't tell; let the download find out
    return free >= needed, max(needed, 0)


class DownloadCancelled(Exception):
    """The user pressed Cancel; nothing is wrong."""


class DownloadError(RuntimeError):
    """A download that failed for a reason worth explaining. User-facing."""


# The last download's failure, for the Settings page to show: the worker that
# runs the download has nowhere else to put it. Cleared by the next attempt.
last_error: dict = {"pack": None, "message": None}


def install(pack_id: str, should_cancel: Callable[[], bool] | None = None,
            root: Path | None = None) -> Path:
    """Download a pack into the cache. The only function here that uses the network.

    Reached from the Install button and from ``ebook-audiobook languages
    install`` — nowhere else — so a download never happens without being asked
    for. Resumable: huggingface_hub keeps partial ``.incomplete`` blobs and
    picks them up on the next attempt, which is what makes Cancel cheap.
    """
    pack = PACKS[pack_id]
    if is_installed(pack_id, root):
        return snapshot_dir(root)  # type: ignore[return-value]
    last_error.update(pack=None, message=None)
    try:
        return _install(pack, should_cancel, root)
    except DownloadCancelled:
        raise
    except DownloadError as e:
        last_error.update(pack=pack_id, message=str(e))
        raise


def _install(pack: Pack, should_cancel, root: Path | None) -> Path:
    try:
        from huggingface_hub import snapshot_download
        from huggingface_hub import errors as hf_errors
    except ImportError as e:
        raise DownloadError(_(
            "The speech engine isn't installed, so there is nothing to add a "
            "language to. Install it first (re-run the installer).")) from e

    kwargs: dict = {}
    if should_cancel is not None:
        kwargs["tqdm_class"] = _cancelling_tqdm(should_cancel)
    if root is not None:
        kwargs["cache_dir"] = str(root)
    try:
        path = snapshot_download(
            repo_id=HF_REPO, repo_type="model", revision="main",
            allow_patterns=list(pack.files), token=os.getenv("HF_TOKEN"), **kwargs)
    except DownloadCancelled:
        raise
    except (hf_errors.LocalEntryNotFoundError, hf_errors.OfflineModeIsEnabled) as e:
        raise DownloadError(_(
            "Couldn't reach huggingface.co. You're offline, a firewall is in the way, "
            "or HF_HUB_OFFLINE is set. Everything downloaded so far is kept; try again "
            "and it resumes.")) from e
    except OSError as e:
        if getattr(e, "errno", None) == 28:  # ENOSPC
            raise DownloadError(_(
                "The disk filled up while downloading. Everything downloaded so far is "
                "kept; free some space and try again — it resumes.")) from e
        raise DownloadError(_("The download failed: %(e)s", e=e)) from e
    return Path(path)


def _cancelling_tqdm(should_cancel: Callable[[], bool]):
    """A tqdm stand-in that raises when the user has asked to stop.

    quiet.py sets TQDM_DISABLE, which would construct this disabled and skip
    ``update`` entirely — so ``disable`` is forced off and ``display`` made a
    no-op. Best effort: if the hub library bypasses it, Cancel takes effect at
    the next file boundary instead.
    """
    from huggingface_hub.utils import tqdm as hf_tqdm

    class Cancelling(hf_tqdm):  # type: ignore[misc,valid-type]
        def __init__(self, *a, **kw):
            kw["disable"] = False
            super().__init__(*a, **kw)

        def display(self, *a, **kw):
            return None

        def update(self, n=1):
            if should_cancel():
                raise DownloadCancelled()
            return super().update(n)

    return Cancelling


def remove(pack_id: str, root: Path | None = None) -> int:
    """Delete a pack's files from the cache; returns the bytes freed.

    Only files no other installed pack also lists: ``conds.pt`` belongs to both.
    """
    pack = PACKS[pack_id]
    snap = snapshot_dir(root)
    if snap is None:
        return 0
    keep = set()
    for other in PACKS.values():
        if other.id != pack_id and is_installed(other.id, root):
            keep |= set(other.files)
    freed = 0
    for name in pack.files:
        if name in keep:
            continue
        link = snap / name
        try:
            if not link.exists() and not link.is_symlink():
                continue
            target = link.resolve() if link.is_symlink() else link
            size = target.stat().st_size if target.exists() else 0
            link.unlink()
            if target != link and target.exists():
                target.unlink()
            freed += size
        except OSError:
            continue
    return freed
