"""ZAZA-native rendering helpers — markers, end-of-turn block, separators.

Centralizes the visual vocabulary so the entire CLI shares one design
language without touching skin colors directly at every call site.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from typing import Optional


def _skin():
    try:
        from zaza_cli.skin_engine import get_active_skin
        return get_active_skin()
    except Exception:
        return None


def _color(key: str, fallback: str) -> str:
    s = _skin()
    if s is None:
        return fallback
    try:
        return s.get_color(key, fallback)
    except Exception:
        return fallback


# ---------------------------------------------------------------------------
# Turn lifecycle metrics
# ---------------------------------------------------------------------------

@dataclass
class TurnMetrics:
    """Metrics accumulated during a single agent turn.

    A turn starts when the user submits a message and ends after the
    agent finishes streaming its response (and any tool calls).  Used
    by ``print_end_of_turn`` to render the closing summary block.
    """
    started_at: float = field(default_factory=time.monotonic)
    tools_called: int = 0
    tool_durations_ms: list[float] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    errors: int = 0

    @property
    def duration_s(self) -> float:
        return time.monotonic() - self.started_at

    def record_tool(self, name: str, duration_ms: float) -> None:
        self.tools_called += 1
        self.tool_durations_ms.append(duration_ms)


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------

def _format_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{int(seconds * 1000)}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    s = int(seconds % 60)
    return f"{minutes}m{s:02d}s"


def _format_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 10_000:
        return f"{n / 1000:.1f}k"
    return f"{n // 1000}k"


# ---------------------------------------------------------------------------
# Public render functions
# ---------------------------------------------------------------------------

def print_end_of_turn(metrics: TurnMetrics, *, console=None) -> None:
    """Print the end-of-turn summary line.

    Format::

        ─── ✓ done · 18.3s · 7 tools · 4.2k↑ 12.1k↓ ──────────────

    Quietly skipped if ``metrics`` looks empty (no time, no tools, no
    tokens) — avoids noise on trivial conversational turns.
    """
    duration = metrics.duration_s
    if duration < 0.05 and metrics.tools_called == 0 and metrics.tokens_in == 0 and metrics.tokens_out == 0:
        return

    ok_color = _color("ui_ok", "#22c55e")
    dim_color = _color("status_bar_dim", "#475569")
    accent_color = _color("banner_accent", "#a855f7")

    parts = [f"[{ok_color}]✓ done[/]", f"[{dim_color}]{_format_duration(duration)}[/]"]
    if metrics.tools_called:
        parts.append(f"[{dim_color}]{metrics.tools_called} tool(s)[/]")
    if metrics.tokens_in or metrics.tokens_out:
        token_parts = []
        if metrics.tokens_in:
            token_parts.append(f"[{accent_color}]{_format_tokens(metrics.tokens_in)}↑[/]")
        if metrics.tokens_out:
            token_parts.append(f"[{accent_color}]{_format_tokens(metrics.tokens_out)}↓[/]")
        parts.append(" ".join(token_parts))
    if metrics.errors:
        err_color = _color("ui_error", "#ef4444")
        parts.append(f"[{err_color}]{metrics.errors} err[/]")

    body = f" [{dim_color}]·[/] ".join(parts)

    width = shutil.get_terminal_size().columns
    rule_left = "─" * 3
    used = 4 + sum(_visible_len(p) for p in parts) + 3 * (len(parts) - 1)
    rule_right_len = max(width - used - 6, 3)
    rule_right = "─" * rule_right_len

    line = f"\n[{dim_color}]{rule_left}[/] {body} [{dim_color}]{rule_right}[/]"

    if console is not None:
        console.print(line)
    else:
        # Lazy import — avoid Rich dependency for callers that pass a console.
        from rich.console import Console
        Console().print(line)


def _visible_len(rich_markup: str) -> int:
    """Approximate the visible length of a Rich-markup string.

    Rich tag stripping is heuristic — good enough for sizing the
    end-of-turn rule without pulling in the full Rich measurer.
    """
    out = []
    in_tag = False
    for ch in rich_markup:
        if ch == "[":
            in_tag = True
            continue
        if ch == "]":
            in_tag = False
            continue
        if not in_tag:
            out.append(ch)
    return len(out)


def print_user_marker(text: str, *, console=None) -> None:
    """Render the user-input marker (used when echoing user messages)."""
    accent = _color("banner_accent", "#a855f7")
    dim = _color("status_bar_dim", "#475569")
    line = f"[{accent}]▎[/] [{dim}]›[/] {text}"
    if console is not None:
        console.print(line)
    else:
        from rich.console import Console
        Console().print(line)


def print_state_separator(state_label: str, *, console=None) -> None:
    """Single thin separator line tagged with the current state.

    Useful when transitioning between sub-phases of a turn (e.g. plan →
    execute → critique) so users see clear boundaries.
    """
    dim = _color("status_bar_dim", "#475569")
    accent = _color("banner_accent", "#a855f7")
    width = shutil.get_terminal_size().columns
    pad = max(width - len(state_label) - 8, 4)
    rule = "─" * pad
    line = f"[{dim}]── [/][{accent}]{state_label}[/] [{dim}]{rule}[/]"
    if console is not None:
        console.print(line)
    else:
        from rich.console import Console
        Console().print(line)
