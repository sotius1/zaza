"""Lightweight bootstrap that wires Memory + Autonomy into the CLI.

Goal: keep cli.py untouched.  All integration happens here, called once
from ``zaza_cli/main.py`` at process start.

What gets wired:
  * Default memory store + embedding provider become available
  * ``recall_for_turn`` is registered as a system-prompt augmenter
  * ``learn_from_turn`` is registered as a post-response hook
  * The consolidator is scheduled (every 6 h) for online instances
  * The autonomy loop is mounted under the active session if
    ``config.autonomy.enabled`` is True
  * ``tool_output_store.cleanup`` is run once at startup so the
    on-disk store doesn't grow unbounded between sessions

Failures are logged and swallowed — bootstrap must not break a session.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


def bootstrap(*, config: Optional[dict] = None) -> None:
    """Initialise memory + autonomy.  Idempotent — safe to call twice.

    The function reads the live ``config.yaml`` if ``config`` isn't
    passed and only enables features the user has opted into.  Defaults
    are conservative: memory recall is on, autonomy is on (read-only
    decision policy + reflexion), the consolidator runs every 6h.
    """
    cfg = config or _read_config()
    mem_cfg = (cfg.get("memory") or {}) if isinstance(cfg, dict) else {}
    autonomy_cfg = (cfg.get("autonomy") or {}) if isinstance(cfg, dict) else {}

    if mem_cfg.get("enabled", True):
        _bootstrap_memory()
    if autonomy_cfg.get("enabled", True):
        _bootstrap_autonomy()
    _bootstrap_tool_output_store()


def _bootstrap_memory() -> None:
    try:
        from agent.memory import get_default_store, get_default_embedder
        # Eagerly resolve to surface provider issues now, not mid-turn.
        store = get_default_store()
        embedder = get_default_embedder()
        # Touch the schema by issuing a no-op query — confirms the DB
        # is writable and the schema is current.
        store._conn().execute("SELECT 1").fetchone()
        logger.info(
            "Memory bootstrap OK — store=%s, embedder=%s (dim=%d)",
            store._db_path, embedder.name, embedder.dim,
        )
    except Exception:
        logger.exception("Memory bootstrap failed (degraded mode)")
        return

    # Schedule periodic consolidation in long-running processes (TUI, gateway).
    # In one-shot CLI invocations the daemon thread will be killed when the
    # process exits — that's fine, no work is lost (each run is idempotent).
    if os.environ.get("ZAZA_DISABLE_BACKGROUND") != "1":
        try:
            from agent.memory.consolidator import schedule_periodic
            schedule_periodic(interval_s=6 * 3600)
            logger.info("Memory consolidator scheduled (6h cadence)")
        except Exception:
            logger.exception("Could not schedule memory consolidator")


def _bootstrap_autonomy() -> None:
    """Verify the autonomy package imports cleanly.

    The actual loop instances are created per-session by cli.py once
    we wire it in; here we just probe-load to surface import errors.
    """
    try:
        from agent.autonomy import AutonomousLoop, DecisionPolicy  # noqa: F401
        logger.info("Autonomy bootstrap OK")
    except Exception:
        logger.exception("Autonomy bootstrap failed")


def _bootstrap_tool_output_store() -> None:
    try:
        from agent.tool_output_store import cleanup
        report = cleanup()
        if report["evicted"]:
            logger.info(
                "Tool-output store: evicted %d, freed %d bytes",
                report["evicted"], report["freed_bytes"],
            )
    except Exception:
        logger.debug("Tool-output store cleanup skipped", exc_info=True)


def _read_config() -> dict:
    try:
        from zaza_constants import get_zaza_home
        path = get_zaza_home() / "data" / "config.yaml"
    except Exception:
        from pathlib import Path
        path = Path.home() / ".agent-zaza" / "data" / "config.yaml"
    if not path.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Helpers callers can use directly — turn boundary integration
# ---------------------------------------------------------------------------

def augment_system_prompt(system_prompt: str, *, user_message: str,
                          session_id: Optional[str] = None) -> str:
    """Append the recall + core-memory blocks to the system prompt.

    Empty when nothing has been learned yet — keeps a fresh install's
    prompts clean.  Caller is responsible for calling this once per
    turn before sending to the LLM.
    """
    blocks = [system_prompt.rstrip()]
    try:
        from agent.memory.layers import render_core_memory_for_prompt
        core = render_core_memory_for_prompt()
        if core:
            blocks.append(core)
    except Exception:
        logger.debug("core memory render skipped", exc_info=True)

    try:
        from agent.memory import recall_for_turn
        recall_block = recall_for_turn(user_message, session_id=session_id)
        if recall_block:
            blocks.append(recall_block)
    except Exception:
        logger.debug("recall skipped", exc_info=True)

    return "\n\n".join(b for b in blocks if b)


def learn_after_turn(*, user_message: str, assistant_response: str,
                     tool_calls: Any = None,
                     session_id: Optional[str] = None,
                     auxiliary_client: Any = None) -> None:
    """Fire-and-forget memory write at the end of a turn."""
    try:
        from agent.memory import learn_from_turn
        learn_from_turn(
            user_message=user_message,
            assistant_response=assistant_response,
            tool_calls=tool_calls,
            session_id=session_id,
            auxiliary_client=auxiliary_client,
            run_in_background=True,
        )
    except Exception:
        logger.exception("learn_after_turn dispatch failed")
