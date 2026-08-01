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
# Accepted upload/import formats. Anything that isn't already a WAV is transcoded
# to WAV via ffmpeg on import (see VoiceLibrary.add), so container/AAC formats
# like .mp4/.m4a — which librosa can't reliably decode — work regardless.
AUDIO_EXTS = {".wav", ".mp3", ".flac", ".m4a", ".mp4", ".ogg", ".opus", ".aac", ".webm"}


@dataclass
class Voice:
    id: str
    name: str
    clip_filename: str | None  # None => built-in default voice

    @property
    def is_default(self) -> bool:
        return self.clip_filename is None

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "clip_filename": self.clip_filename,
                "is_default": self.is_default}


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
        return Voice(DEFAULT_VOICE_ID, "Default narrator", None)

    def list(self) -> list[Voice]:
        voices = [self._default()]
        for d in self._load_index():
            if d.get("id") and d["id"] != DEFAULT_VOICE_ID:
                voices.append(Voice(d["id"], d.get("name", d["id"]), d.get("clip_filename")))
        return voices

    def get(self, voice_id: str) -> Voice | None:
        return next((v for v in self.list() if v.id == voice_id), None)

    def clip_path(self, voice_id: str) -> Path | None:
        v = self.get(voice_id)
        if not v or not v.clip_filename:
            return None
        return self.dir / v.clip_filename

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
        taken = {d["id"] for d in items} | {DEFAULT_VOICE_ID}
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
        if voice_id == DEFAULT_VOICE_ID:
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
