"""Offline ebook-to-audiobook converter.

The package is a linear, content-addressed, resumable pipeline:

    ebook -> extract -> normalize -> chunk -> render -> assemble -> package -> .m4b

Everything above the TTS engine is pure Python and testable without a GPU.
The engine sits behind ``app.tts.adapter.TTSAdapter``.
"""

__version__ = "0.1.0"
