"""Resolve ZAZA_HOME for standalone skill scripts.

Skill scripts may run outside the ZAZA process (e.g. system Python,
nix env, CI) where ``zaza_constants`` is not importable.  This module
provides the same ``get_zaza_home()`` and ``display_zaza_home()``
contracts as ``zaza_constants`` without requiring it on ``sys.path``.

When ``zaza_constants`` IS available it is used directly so that any
future enhancements (profile resolution, Docker detection, etc.) are
picked up automatically.  The fallback path replicates the core logic
from ``zaza_constants.py`` using only the stdlib.

All scripts under ``google-workspace/scripts/`` should import from here
instead of duplicating the ``ZAZA_HOME = Path(os.getenv(...))`` pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from zaza_constants import display_zaza_home as display_zaza_home
    from zaza_constants import get_zaza_home as get_zaza_home
except (ModuleNotFoundError, ImportError):

    def get_zaza_home() -> Path:
        """Return the ZAZA home directory (default: ~/.agent-zaza/data).

        Mirrors ``zaza_constants.get_zaza_home()``."""
        val = os.environ.get("ZAZA_HOME", "").strip()
        return Path(val) if val else Path.home() / ".agent-zaza/data"

    def display_zaza_home() -> str:
        """Return a user-friendly ``~/``-shortened display string.

        Mirrors ``zaza_constants.display_zaza_home()``."""
        home = get_zaza_home()
        try:
            return "~/" + str(home.relative_to(Path.home()))
        except ValueError:
            return str(home)
