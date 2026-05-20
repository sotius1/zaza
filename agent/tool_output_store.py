"""Tool output offloader — keeps the in-context conversation slim.

Large tool outputs (file dumps, command logs, search hits) blow up the
prompt budget without contributing proportional value once the agent has
extracted the relevant signal.  This module stores oversize outputs on
disk and replaces them in the conversation with a compact reference
preview that the agent can re-expand on demand via the
``fetch_tool_output`` tool.

Storage layout::

    ~/.agent-zaza/data/tool_results/
      <sha256>.txt        full original output
      <sha256>.meta.json  {tool_name, args, sha, size, timestamp, preview}

Reference format placed in the message history::

    [output offloaded · 12.4 KB · ref=tooloff://abc123def456 · use fetch_tool_output to expand]
    <first ~400 visible chars of the output, line-trimmed>
    ...

Why the inline preview matters: most agent decisions only need the *gist*
of a tool output (matched lines, exit code, first error).  Inline preview
keeps the agent productive without forcing it to re-fetch on every turn.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# 8 KB — outputs larger than this get offloaded.  Below this threshold the
# overhead of a reference is worse than just keeping the bytes inline.
OFFLOAD_THRESHOLD_BYTES = 8 * 1024

# Length of the inline preview that stays in the conversation.
PREVIEW_CHARS = 400


def _store_dir() -> Path:
    """Resolve the on-disk store directory, creating it on demand."""
    try:
        from zaza_constants import get_zaza_home
        base = get_zaza_home() / "data" / "tool_results"
    except Exception:
        base = Path.home() / ".agent-zaza" / "data" / "tool_results"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _content_hash(content: str) -> str:
    """Stable SHA-256 over the raw output — same content = same ref."""
    return hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()


def _build_preview(content: str, *, chars: int = PREVIEW_CHARS) -> str:
    """First N chars trimmed at the closest whole line boundary.

    Avoids leaving the agent staring at half a JSON object — if the
    truncation point falls in the middle of a line, we back up to the
    previous newline so the preview always ends cleanly.
    """
    if len(content) <= chars:
        return content
    cut = content[:chars]
    last_newline = cut.rfind("\n")
    if last_newline > chars * 0.5:  # only back up if we have at least half
        cut = cut[:last_newline]
    return cut.rstrip()


@dataclass(frozen=True)
class OffloadedOutput:
    """Result of an offload — what to put back into the conversation."""
    sha: str
    size_bytes: int
    preview: str
    inline_text: str  # what the agent sees in the message history

    @property
    def reference(self) -> str:
        return f"tooloff://{self.sha[:16]}"


def maybe_offload(
    content: str,
    *,
    tool_name: str,
    tool_args: Optional[Dict[str, Any]] = None,
    threshold_bytes: int = OFFLOAD_THRESHOLD_BYTES,
) -> Optional[OffloadedOutput]:
    """If ``content`` is larger than the threshold, offload it.

    Returns the OffloadedOutput on success, or None if the content was
    small enough to leave inline.  Errors during write are logged and
    swallowed — we'd rather keep the original content than crash a turn.
    """
    if not content or len(content.encode("utf-8", errors="replace")) < threshold_bytes:
        return None

    try:
        sha = _content_hash(content)
        store = _store_dir()
        body_path = store / f"{sha}.txt"
        meta_path = store / f"{sha}.meta.json"

        if not body_path.exists():
            body_path.write_text(content, encoding="utf-8")
            meta = {
                "tool_name": tool_name,
                "tool_args": tool_args or {},
                "sha": sha,
                "size_bytes": len(content.encode("utf-8", errors="replace")),
                "timestamp": time.time(),
                "preview_chars": PREVIEW_CHARS,
            }
            meta_path.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")

        size = len(content.encode("utf-8", errors="replace"))
        preview = _build_preview(content)

        size_label = _format_size(size)
        ref = f"tooloff://{sha[:16]}"
        inline = (
            f"[output offloaded · {size_label} · ref={ref} · "
            f"use fetch_tool_output to expand]\n{preview}\n…"
        )
        return OffloadedOutput(sha=sha, size_bytes=size, preview=preview, inline_text=inline)
    except Exception:
        logger.exception("Tool output offload failed for tool=%s", tool_name)
        return None


def fetch(reference_or_sha: str) -> Optional[str]:
    """Resolve a reference back to the original full content.

    Accepts either the short ``tooloff://<sha16>`` form or a full sha.
    Returns None if the reference is unknown (e.g. the store was cleaned).
    """
    sha_part = reference_or_sha.removeprefix("tooloff://").strip()
    if not sha_part:
        return None
    store = _store_dir()
    # Match by prefix — short refs use first 16 chars of the sha
    matches = list(store.glob(f"{sha_part}*.txt"))
    if not matches:
        return None
    try:
        return matches[0].read_text(encoding="utf-8")
    except Exception:
        logger.exception("Tool output fetch failed for ref=%s", reference_or_sha)
        return None


def _format_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


# ---------------------------------------------------------------------------
# Maintenance — bounded storage
# ---------------------------------------------------------------------------

# Default retention: 200 MB, age-based eviction (oldest first).  Set
# ZAZA_TOOL_STORE_MAX_BYTES to override.
DEFAULT_MAX_TOTAL_BYTES = 200 * 1024 * 1024


def cleanup(max_total_bytes: Optional[int] = None) -> Dict[str, int]:
    """Evict oldest stored outputs until total size fits the budget.

    Idempotent: safe to call from a periodic job or at session start.
    Returns ``{"evicted": N, "freed_bytes": B}``.
    """
    if max_total_bytes is None:
        try:
            max_total_bytes = int(os.environ.get("ZAZA_TOOL_STORE_MAX_BYTES", DEFAULT_MAX_TOTAL_BYTES))
        except (TypeError, ValueError):
            max_total_bytes = DEFAULT_MAX_TOTAL_BYTES

    store = _store_dir()
    entries = []
    total = 0
    for path in store.glob("*.txt"):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        entries.append((stat.st_mtime, stat.st_size, path))
        total += stat.st_size

    entries.sort()  # oldest first
    evicted = 0
    freed = 0
    while total > max_total_bytes and entries:
        _mtime, size, path = entries.pop(0)
        meta_path = path.with_suffix(".meta.json")
        try:
            path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            total -= size
            freed += size
            evicted += 1
        except Exception:
            logger.exception("Tool output eviction failed for %s", path)
            break
    return {"evicted": evicted, "freed_bytes": freed}
