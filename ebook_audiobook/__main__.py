"""``python -m ebook_audiobook``: the command line, or with ``--gui`` the desktop app.

The Windows installer's shortcuts start the app this way, through the bundled
``pythonw.exe``, rather than through the ``ebook-audiobook-gui.exe`` script that
pip generates. That launcher has the absolute path of the Python it was built
against written into it, which on the build machine is not where the installer
puts it.
"""

import sys

from .cli import main, main_gui

if __name__ == "__main__":
    if sys.argv[1:] == ["--gui"]:
        raise SystemExit(main_gui())
    raise SystemExit(main())
