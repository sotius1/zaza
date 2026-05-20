"""Agent ZAZA — auto-bootstrap of default capabilities.

Runs once on first CLI invocation (idempotent via stamp file).

Adds these MCP servers to config.yaml *only if they are not already
configured by the user*:

  • serena              — LSP-driven code intelligence (symbol search,
                          surgical edits, reference graph). Backed by uvx.
  • chrome-devtools     — sandboxed Chrome browser harness via Chrome
                          DevTools Protocol (live page automation,
                          console & network inspection, screenshots).
  • sequential-thinking — multi-step structured reasoning helper.
  • memory              — persistent knowledge graph that survives across
                          sessions.

The bootstrap is non-destructive: existing entries are never overwritten.
The user can disable any default with `agent-zaza mcp remove <name>`.

A stamp file at ZAZA_HOME/.zaza_defaults_v{N} prevents re-running on
every launch. Bumping the constant below re-runs on next invocation
(handy when adding a new default server in a release).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

# Bump this when default set changes — old stamp becomes stale and bootstrap
# re-runs (still non-destructive, only adds missing servers).
ZAZA_DEFAULTS_VERSION = 1


# Default MCP server set. Each entry is added to config.yaml under the given
# key only if a server with the same key does not already exist.
DEFAULT_MCP_SERVERS = {
    "serena": {
        "command": "uvx",
        "args": [
            "--from",
            "git+https://github.com/oraios/serena",
            "serena",
            "start-mcp-server",
        ],
        "description": (
            "Serena — LSP-backed code intelligence. Use BEFORE grep for "
            "symbol-level navigation and surgical edits."
        ),
        "enabled": True,
    },
    "chrome-devtools": {
        "command": "npx",
        "args": ["-y", "chrome-devtools-mcp@latest"],
        "description": (
            "Chrome DevTools Protocol — live browser automation, console "
            "and network inspection, screenshots, performance traces."
        ),
        "enabled": True,
    },
    "sequential-thinking": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-sequential-thinking"],
        "description": (
            "Sequential Thinking — structured multi-step reasoning for "
            "non-trivial problems."
        ),
        "enabled": True,
    },
    "memory": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-memory"],
        "description": (
            "Persistent knowledge graph that survives across sessions."
        ),
        "enabled": True,
    },
}


def _stamp_path() -> Optional[Path]:
    try:
        from zaza_constants import get_zaza_home
        return get_zaza_home() / f".zaza_defaults_v{ZAZA_DEFAULTS_VERSION}"
    except Exception:
        return None


def _stamp_already_set() -> bool:
    stamp = _stamp_path()
    return bool(stamp and stamp.exists())


def _set_stamp() -> None:
    stamp = _stamp_path()
    if not stamp:
        return
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(
            f"agent-zaza defaults bootstrap v{ZAZA_DEFAULTS_VERSION} ok\n",
            encoding="utf-8",
        )
    except Exception:
        # Best-effort — if we can't write the stamp the bootstrap will
        # re-run next launch, but it's idempotent so that's harmless.
        pass


def _quiet() -> bool:
    """Whether to suppress bootstrap output (set by ZAZA_QUIET=1)."""
    import os
    return os.environ.get("ZAZA_QUIET") == "1" or os.environ.get("ZAZA_QUIET") == "1"


def _say(line: str) -> None:
    if not _quiet():
        sys.stderr.write(line + "\n")


def ensure_zaza_defaults() -> None:
    """Idempotent bootstrap of Agent ZAZA default MCP servers.

    Call once near startup, after auth and before the agent loop begins.
    Safe to call repeatedly — guarded by a stamp file and no-op if all
    defaults are already present.
    """
    if _stamp_already_set():
        return

    try:
        from zaza_cli.config import load_config, save_config
    except Exception as exc:
        _say(f"[zaza] defaults skipped — config module unavailable ({exc}).")
        return

    try:
        config = load_config()
    except Exception as exc:
        _say(f"[zaza] defaults skipped — could not read config.yaml ({exc}).")
        return

    existing = config.get("mcp_servers") or {}
    if not isinstance(existing, dict):
        existing = {}

    added = []
    for name, server_cfg in DEFAULT_MCP_SERVERS.items():
        if name in existing:
            continue
        existing[name] = dict(server_cfg)
        added.append(name)

    if added:
        config["mcp_servers"] = existing
        try:
            save_config(config)
        except Exception as exc:
            _say(f"[zaza] failed to save default MCP config: {exc}")
            return
        _say(
            f"[zaza] bootstrap: added {len(added)} default MCP server(s) — "
            + ", ".join(added)
        )

    _set_stamp()


__all__ = ["ensure_zaza_defaults", "ZAZA_DEFAULTS_VERSION", "DEFAULT_MCP_SERVERS"]
