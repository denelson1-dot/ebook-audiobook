"""Silencing the ML stack's import-time noise.

The engine libraries are chatty before they do anything useful: a "loaded
PerthNet" stdout line, tqdm sampling bars, and a handful of deprecation warnings
from perth/diffusers/HF. None of it is actionable, and all of it lands in the
terminal window the app tells the user to leave open — so it reads as a crash.

This lives in its own module, imported for its side effects, because the filters
have to be registered *before* anything imports the engine. Registering them
inside the adapter was not enough: ``checks.check_tts_engine`` imports
``chatterbox`` during startup to report whether the engine is present, and that
happens without the adapter module ever being loaded.

Set ``EBAB_VERBOSE=1`` to restore everything (useful when debugging the engine).
"""

from __future__ import annotations

import os
import warnings

VERBOSE = os.environ.get("EBAB_VERBOSE") == "1"


def apply() -> None:
    """Register the filters. Idempotent, so importers need not coordinate."""
    if VERBOSE:
        return
    os.environ.setdefault("TQDM_DISABLE", "1")  # kills the sampling progress bars
    warnings.filterwarnings("ignore", category=FutureWarning)
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    # perth raises this one as a UserWarning, so it needs matching by message
    # rather than by category.
    warnings.filterwarnings("ignore", message=r".*pkg_resources is deprecated.*")


apply()
