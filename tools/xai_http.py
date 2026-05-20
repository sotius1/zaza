"""Shared helpers for direct xAI HTTP integrations."""

from __future__ import annotations


def zaza_xai_user_agent() -> str:
    """Return a stable ZAZA-specific User-Agent for xAI HTTP calls."""
    try:
        from zaza_cli import __version__
    except Exception:
        __version__ = "unknown"
    return f"ZAZA-Agent/{__version__}"
