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


def _installed_version() -> str:
    """Version from the installed package metadata.

    Read rather than hard-coded: a literal here has to be bumped in lockstep with
    pyproject.toml, and when that was missed this reported 0.1.0 for a 1.0.x
    release — which is exactly the number a user pastes into a bug report.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("ebook-audiobook")
    except PackageNotFoundError:  # running from a source tree, never installed
        return "0.0.0+unknown"


__version__ = _installed_version()
