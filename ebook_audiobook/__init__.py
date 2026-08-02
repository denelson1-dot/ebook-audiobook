"""Offline ebook-to-audiobook converter.

The package is a linear, content-addressed, resumable pipeline:

    ebook -> extract -> normalize -> chunk -> render -> assemble -> package -> .m4b

Everything above the TTS engine is pure Python and testable without a GPU.
The engine sits behind ``ebook_audiobook.tts.adapter.TTSAdapter``.
"""

from .device import enable_mps_fallback

# Apple Silicon: PyTorch reads PYTORCH_ENABLE_MPS_FALLBACK when it is imported,
# so it has to be set before that. Importing this package is the one thing
# guaranteed to happen first, whichever entry point the user came in through.
# See ebook_audiobook.device.enable_mps_fallback for why it matters.
enable_mps_fallback()


def _source_version() -> str | None:
    """The version in pyproject.toml, when running from a source checkout.

    Installed metadata goes stale in a working copy: an editable install records
    the version as it was at install time, so a checkout that has since bumped
    reports the old number — and that is the number that ends up in a bug report
    and in a backup manifest. In a checkout, pyproject.toml is the truth.
    """
    try:
        import tomllib
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        pyproject = root / "pyproject.toml"
        if not (pyproject.is_file() and (root / "ebook_audiobook").is_dir()):
            return None  # installed copy: site-packages has no pyproject.toml
        version = tomllib.loads(pyproject.read_text("utf-8")).get("project", {}).get("version")
        return str(version) if version else None
    except Exception:  # noqa: BLE001 - a version string is never worth crashing over
        return None


def _installed_version() -> str:
    """Version from pyproject.toml in a checkout, else the package metadata.

    Read rather than hard-coded: a literal here has to be bumped in lockstep with
    pyproject.toml, and when that was missed this reported 0.1.0 for a 1.0.x
    release — which is exactly the number a user pastes into a bug report.
    """
    from importlib.metadata import PackageNotFoundError, version

    from_source = _source_version()
    if from_source:
        return from_source
    try:
        return version("ebook-audiobook")
    except PackageNotFoundError:  # running from a source tree, never installed
        return "0.0.0+unknown"


__version__ = _installed_version()
