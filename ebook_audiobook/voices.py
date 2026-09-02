"""Voice library.

A named collection of narrator voices stored globally under ``local-data/voices/``
and reusable across books. A voice = ONE reference clip (Chatterbox zero-shot
cloning uses a single ~10s clip; it cannot be "trained" on many samples). The
built-in "Default narrator" (no clip → the model's shipped default voice) is
always present and cannot be deleted.

Index: ``local-data/voices/voices.json`` = ``[{id, name, clip_filename}]``.
Clip files live alongside it in the same directory.
"""

from __future__ import annotations

from .i18n import N_, _
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import tools
from .config import paths

DEFAULT_VOICE_ID = "default"
_INDEX = "voices.json"

# Reference clips shipped with the application, so a new install has usable
# voices without anyone having to find or record one. Read from the package
# rather than copied into the user's library: they cannot then be deleted by
# accident, and an upgrade that improves a clip improves it for everyone.
#
# ``pacing`` and ``expressiveness`` are this clip's suggested settings, applied
# when the voice is picked. They are starting points tuned by ear, not
# constraints — both sliders still win, and moving one is visible.
BUNDLED_DIR = Path(__file__).resolve().parent / "assets" / "voices"

# ``language`` is the language the clip was recorded in — the one this voice
# narrates naturally. Each language ships its own voices and its own default;
# a voice is never offered as a narrator for a language it does not speak.
BUNDLED = (
    {"id": "male-north-american", "name": N_("English — Male, North American"), "language": "en",
     "file": "male-north-american.flac", "pacing": 0.50, "expressiveness": 0.60},
    {"id": "male-north-american-alt", "name": N_("English — Male, North American (alt)"), "language": "en",
     "file": "male-north-american-alt.flac", "pacing": 0.42},
    {"id": "female-north-american", "name": N_("English — Female, North American"), "language": "en",
     "file": "female-north-american.flac", "pacing": 0.42},
    {"id": "male-british", "name": N_("English — Male, British"), "language": "en",
     "file": "male-british.flac", "pacing": 0.42},
    {"id": "female-british", "name": N_("English — Female, British"), "language": "en",
     "file": "female-british.flac", "pacing": 0.42},
    {"id": "female-french", "name": N_("French — Female"), "language": "fr",
     "file": "female-french.flac", "pacing": 0.42},
    {"id": "male-french", "name": N_("French — Male"), "language": "fr",
     "file": "male-french.flac", "pacing": 0.42},
)

# Which voice a newly imported book starts with, per language. Until a book
# carries its own language, the interface language decides: someone using the
# app in French is, for now, taken to be narrating French books.
DEFAULT_BUNDLED_BY_LANGUAGE = {"en": "male-north-american", "fr": "female-french"}
DEFAULT_BUNDLED_ID = DEFAULT_BUNDLED_BY_LANGUAGE["en"]
# Accepted upload/import formats. Anything that isn't already a WAV is transcoded
# to WAV via ffmpeg on import (see VoiceLibrary.add), so container/AAC formats
# like .mp4/.m4a — which librosa can't reliably decode — work regardless.
AUDIO_EXTS = {".wav", ".mp3", ".flac", ".m4a", ".mp4", ".ogg", ".opus", ".aac", ".webm"}


@dataclass
class Voice:
    id: str
    name: str
    clip_filename: str | None  # None => the engine's own default voice
    # Shipped with the app rather than added by the user. Its clip lives in the
    # package, so it is neither stored in nor deletable from the user's library.
    bundled: bool = False
    # Suggested settings for this clip, or None to leave that slider alone.
    pacing: float | None = None
    expressiveness: float | None = None
    # The language the clip is spoken in. User-added voices are English until
    # the picker asks; the bundled ones say so themselves.
    language: str = "en"

    @property
    def is_default(self) -> bool:
        """The engine's own voice — no reference clip at all."""
        return self.clip_filename is None and not self.bundled

    @property
    def removable(self) -> bool:
        """Only voices the user added can be deleted."""
        return not (self.is_default or self.bundled)

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "clip_filename": self.clip_filename,
                "is_default": self.is_default, "bundled": self.bundled,
                "removable": self.removable, "pacing": self.pacing,
                "expressiveness": self.expressiveness, "language": self.language}


def _slug(name: str, default: str = "voice") -> str:
    s = re.sub(r"[^\w\- ]+", "", name).strip().lower().replace(" ", "-")
    s = re.sub(r"-+", "-", s)
    return s[:40] or default


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(text)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class VoiceLibrary:
    def __init__(self):
        self.dir = paths().voices

    @property
    def index_path(self) -> Path:
        return self.dir / _INDEX

    def _load_index(self) -> list[dict]:
        if not self.index_path.exists():
            return []
        try:
            data = json.loads(self.index_path.read_text("utf-8"))
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _save_index(self, items: list[dict]) -> None:
        _atomic_write(self.index_path, json.dumps(items, indent=2))

    @staticmethod
    def _default() -> Voice:
        return Voice(DEFAULT_VOICE_ID, _("Default narrator"), None)

    @staticmethod
    def _bundled() -> list[Voice]:
        """The shipped voices, in the order they appear in the picker."""
        voices = []
        for b in BUNDLED:
            if not (BUNDLED_DIR / b["file"]).is_file():
                continue
            # The name is marked N_() in BUNDLED and said here in the request's
            # language. (Kept out of the _() call as a literal: the extractor
            # would otherwise read the subscript's "name" as a message.)
            label = b["name"]
            voices.append(Voice(b["id"], _(label), b["file"], bundled=True,
                                pacing=b.get("pacing"), expressiveness=b.get("expressiveness"),
                                language=b.get("language", "en")))
        return voices

    def list(self) -> list[Voice]:
        # Shipped voices first: a new install should meet a usable narrator
        # before it meets the engine's raw default.
        voices = self._bundled()
        voices.append(self._default())
        bundled_ids = {v.id for v in voices}
        for d in self._load_index():
            if d.get("id") and d["id"] not in bundled_ids:
                voices.append(Voice(d["id"], d.get("name", d["id"]), d.get("clip_filename")))
        return voices

    def get(self, voice_id: str) -> Voice | None:
        return next((v for v in self.list() if v.id == voice_id), None)

    def clip_path(self, voice_id: str) -> Path | None:
        """Where this voice's reference clip actually lives, or None for the
        engine's own voice. Bundled clips resolve into the package."""
        v = self.get(voice_id)
        if not v or not v.clip_filename:
            return None
        path = (BUNDLED_DIR if v.bundled else self.dir) / v.clip_filename
        return path if path.is_file() else None

    def add(self, name: str, src_path: str | None = None, file_storage=None,
            orig_filename: str | None = None) -> Voice:
        """Add a voice from a local file path or an uploaded file. The clip is
        copied into the library. Returns the created Voice."""
        self.dir.mkdir(parents=True, exist_ok=True)
        name = (name or "").strip() or "Voice"

        ext = None
        if src_path:
            ext = Path(src_path).suffix.lower()
        elif orig_filename:
            ext = Path(orig_filename).suffix.lower()
        if ext not in AUDIO_EXTS:
            raise ValueError(f"unsupported audio format: {ext or '(none)'}")

        items = self._load_index()
        taken = ({d["id"] for d in items} | {DEFAULT_VOICE_ID}
                 | {b["id"] for b in BUNDLED})
        vid = base = _slug(name)
        n = 2
        while vid in taken:
            vid = f"{base}-{n}"
            n += 1

        # Always store a WAV so Chatterbox can definitely read it. WAV inputs are
        # copied as-is (no ffmpeg needed); everything else is transcoded.
        clip_filename = f"{vid}.wav"
        dest = self.dir / clip_filename

        if ext == ".wav":
            if src_path:
                shutil.copy2(src_path, dest)
            elif file_storage is not None:
                file_storage.save(str(dest))
            else:
                raise ValueError("provide src_path or file_storage")
        else:
            if src_path:
                self._ffmpeg_to_wav(src_path, dest)
            elif file_storage is not None:
                tmp = self.dir / f".incoming-{vid}{ext}"
                try:
                    file_storage.save(str(tmp))
                    self._ffmpeg_to_wav(str(tmp), dest)
                finally:
                    tmp.unlink(missing_ok=True)
            else:
                raise ValueError("provide src_path or file_storage")

        items.append({"id": vid, "name": name, "clip_filename": clip_filename})
        self._save_index(items)
        return Voice(vid, name, clip_filename)

    @staticmethod
    def _ffmpeg_to_wav(src: str, dest: Path) -> None:
        """Decode any supported audio (incl. .mp4/.m4a/.aac) to mono 24 kHz WAV."""
        try:
            ffmpeg = tools.require_ffmpeg()
        except tools.MissingToolError as e:
            raise ValueError(str(e)) from e
        proc = tools.run(
            [ffmpeg, "-y", "-i", src, "-vn", "-ac", "1", "-ar", "24000",
             "-c:a", "pcm_s16le", dest],
            timeout=300,
        )
        if proc.returncode != 0 or not dest.exists():
            dest.unlink(missing_ok=True)
            raise ValueError(f"could not decode audio: {(proc.stderr or '')[-300:]}")

    def delete(self, voice_id: str) -> bool:
        # The engine's own voice and the shipped ones have nothing in the user's
        # library to remove; refusing here means a hand-made request cannot
        # corrupt the index either.
        if voice_id == DEFAULT_VOICE_ID or any(b["id"] == voice_id for b in BUNDLED):
            return False
        items = self._load_index()
        kept, removed = [], None
        for d in items:
            if d.get("id") == voice_id:
                removed = d
            else:
                kept.append(d)
        if not removed:
            return False
        if removed.get("clip_filename"):
            (self.dir / removed["clip_filename"]).unlink(missing_ok=True)
        (self.dir / f"_sample_{voice_id}.wav").unlink(missing_ok=True)  # audition clip
        self._save_index(kept)
        return True


def default_voice_id(language: str | None = None) -> str:
    """The voice a newly imported book starts with.

    The user's choice if they have made one and it still exists, otherwise the
    shipped default for ``language`` — the interface language when none is
    given, so a French interface starts a book with the French narrator. Falls
    back to the engine's own voice if the bundled clips are missing — a source
    checkout without them should still work.
    """
    from . import settings as app_settings
    from .i18n import current_language

    lib = VoiceLibrary()
    available = {v.id for v in lib.list()}
    chosen = app_settings.load_settings().default_voice_id
    if chosen and chosen in available:
        return chosen
    wanted = DEFAULT_BUNDLED_BY_LANGUAGE.get(language or current_language(), DEFAULT_BUNDLED_ID)
    for candidate in (wanted, DEFAULT_BUNDLED_ID):
        if candidate in available:
            return candidate
    return DEFAULT_VOICE_ID