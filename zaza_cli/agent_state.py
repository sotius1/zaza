"""Agent state machine — drives the visual status indicator.

The 5 states correspond to user-visible activity phases:

    IDLE      — waiting for user input.  Calm, no animation.
    THINKING  — LLM streaming reasoning/tokens.  Pulsing cyan.
    WORKING   — a tool is executing.  Pulsing green.
    WAITING   — paused for user approval / rate limit / OAuth.  Amber.
    DONE      — turn just finished, briefly flashes before relaxing to IDLE.

The state is read by:
  * the prompt_toolkit status bar (left-most glyph)
  * the response panel border style
  * the spinner in display.py

A single global instance lives here so any part of the CLI can publish a
state change via ``set_state(...)`` without having to thread the agent
object through every call site.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional


class AgentState(str, Enum):
    IDLE = "idle"
    THINKING = "thinking"
    WORKING = "working"
    WAITING = "waiting"
    DONE = "done"


@dataclass(frozen=True)
class StateInfo:
    """Visual descriptor for an agent state."""
    glyph: str
    color_key: str    # SkinConfig color key (resolved at render time)
    color_fallback: str
    label: str        # human-readable, lowercase (Polish-first)
    animated: bool


_STATE_INFO: dict[AgentState, StateInfo] = {
    AgentState.IDLE: StateInfo(
        glyph="◌", color_key="status_bar_dim", color_fallback="#475569",
        label="idle", animated=False,
    ),
    AgentState.THINKING: StateInfo(
        glyph="◐", color_key="banner_accent", color_fallback="#a855f7",
        label="thinking", animated=True,
    ),
    AgentState.WORKING: StateInfo(
        glyph="●", color_key="ui_ok", color_fallback="#22c55e",
        label="working", animated=True,
    ),
    AgentState.WAITING: StateInfo(
        glyph="◑", color_key="ui_warn", color_fallback="#f59e0b",
        label="waiting", animated=False,
    ),
    AgentState.DONE: StateInfo(
        glyph="✓", color_key="ui_ok", color_fallback="#22c55e",
        label="done", animated=False,
    ),
}


def info_for(state: AgentState) -> StateInfo:
    """Look up the visual descriptor for a state."""
    return _STATE_INFO[state]


# ---------------------------------------------------------------------------
# Global state holder
# ---------------------------------------------------------------------------

class _StateHolder:
    """Thread-safe holder for the agent's current state."""

    def __init__(self) -> None:
        self._state = AgentState.IDLE
        self._since = time.monotonic()
        self._lock = threading.RLock()
        self._listeners: List[Callable[[AgentState, AgentState], None]] = []
        self._tool_name: Optional[str] = None  # active tool while WORKING

    @property
    def state(self) -> AgentState:
        with self._lock:
            return self._state

    @property
    def elapsed(self) -> float:
        with self._lock:
            return time.monotonic() - self._since

    @property
    def tool_name(self) -> Optional[str]:
        with self._lock:
            return self._tool_name

    def set(self, new_state: AgentState, *, tool_name: Optional[str] = None) -> None:
        """Transition to a new state and notify listeners.

        Listeners run with the lock released so they can call back into
        the holder without deadlocking.
        """
        with self._lock:
            old = self._state
            if old == new_state and tool_name == self._tool_name:
                return
            self._state = new_state
            self._since = time.monotonic()
            self._tool_name = tool_name if new_state == AgentState.WORKING else None
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(old, new_state)
            except Exception:
                pass  # never let a listener crash the agent

    def subscribe(self, callback: Callable[[AgentState, AgentState], None]) -> None:
        with self._lock:
            self._listeners.append(callback)


_HOLDER = _StateHolder()


def get_state() -> AgentState:
    return _HOLDER.state


def get_elapsed() -> float:
    """Seconds since the current state began."""
    return _HOLDER.elapsed


def get_tool_name() -> Optional[str]:
    """Name of the active tool while in WORKING state, else None."""
    return _HOLDER.tool_name


def set_state(state: AgentState, *, tool_name: Optional[str] = None) -> None:
    _HOLDER.set(state, tool_name=tool_name)


def subscribe(callback: Callable[[AgentState, AgentState], None]) -> None:
    """Listen for state changes.  Callback receives ``(old, new)``."""
    _HOLDER.subscribe(callback)


# ---------------------------------------------------------------------------
# Convenience render helpers
# ---------------------------------------------------------------------------

def state_glyph(state: Optional[AgentState] = None) -> str:
    """Return the glyph for the given (or current) state."""
    return info_for(state or get_state()).glyph


def state_color(state: Optional[AgentState] = None,
                skin=None, fallback: Optional[str] = None) -> str:
    """Resolve the active color (hex) for the given state.

    If ``skin`` is provided, looks up ``info.color_key`` in the skin;
    otherwise returns ``info.color_fallback`` (or ``fallback`` override).
    """
    info = info_for(state or get_state())
    if skin is not None:
        try:
            return skin.get_color(info.color_key, fallback or info.color_fallback)
        except Exception:
            pass
    return fallback or info.color_fallback


def state_label(state: Optional[AgentState] = None) -> str:
    """Lowercase human-readable label."""
    return info_for(state or get_state()).label
