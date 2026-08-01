"""Run the local web UI: ``python -m ebook_audiobook.web``.

Kept as a thin alias for ``ebook-audiobook web`` so the documented command still
works from a source checkout before anything is installed. The real logic lives
in :mod:`ebook_audiobook.web.server`.

    python -m ebook_audiobook.web
    EBAB_PORT=8000 python -m ebook_audiobook.web
    EBAB_NO_BROWSER=1 python -m ebook_audiobook.web
"""

from __future__ import annotations

from ..cli import _use_utf8_console
from .server import serve


def main() -> None:
    _use_utf8_console()
    serve()


if __name__ == "__main__":
    main()
