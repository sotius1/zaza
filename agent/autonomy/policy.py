"""DecisionPolicy — when does the agent act, when does it ask?

The user's contract with ZAZA is autonomy first.  ``lec``, ``wykonaj
wszystkie``, ``ma działać`` — these all mean *execute, don't consult*.
But absolute autonomy is irresponsible: certain actions are
irreversible (rm, push, deploy, drop) and the policy must escalate
those even under a green light.

This module formalises the contract:

* ``classify(action)`` returns a RiskLevel.
* ``decide(action, context)`` returns a Decision: EXECUTE, ASK, or
  REFUSE.

The policy reads its preferences from procedural memory (Phase 4) so
that learned rules — like "user said 'lec' = execute" — automatically
relax the gate.  Memory rules with confidence ≥ 0.7 win over the
defaults.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class RiskLevel(str, Enum):
    """How dangerous an action is."""
    SAFE = "safe"           # local read, no external effect
    LOCAL = "local"         # local write, reversible (Edit, Write)
    DESTRUCTIVE = "destructive"  # rm, drop, force-push, kill PID
    EXTERNAL = "external"   # network call beyond local: deploy, send msg
    IRREVERSIBLE = "irreversible"  # combination of destructive + external


class Decision(str, Enum):
    EXECUTE = "execute"
    ASK = "ask"
    REFUSE = "refuse"


@dataclass
class DecisionContext:
    """What the policy needs in order to decide."""
    action: str                              # human description
    risk: RiskLevel
    user_directive_active: bool = False      # did the user just say "lec"?
    user_explicit_authorisation: bool = False  # explicit yes for THIS action
    inside_plan: bool = False                # action is part of an approved plan
    rules_signal: List[str] = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Static risk classifier
# ---------------------------------------------------------------------------

_DESTRUCTIVE_BASH = re.compile(
    r"\b(?:rm\s+-rf?|git\s+reset\s+--hard|git\s+push\s+(?:--force|-f)\b|"
    r"DROP\s+TABLE|TRUNCATE|DELETE\s+FROM|kill\s+-9|rmdir\s+-r|shred\b|"
    r"dd\s+if=|mkfs\.|fdisk\s+|parted\s+)",
    re.IGNORECASE,
)

_EXTERNAL_BASH = re.compile(
    r"\b(?:vercel\s+(?:deploy|prod)|gh\s+pr\s+merge\b|gh\s+release\s+create\b|"
    r"npm\s+publish\b|pypi-publish\b|aws\s+s3\s+rm\b|"
    r"twilio|sendgrid|stripe\s+(?:create|charge))",
    re.IGNORECASE,
)

_LOCAL_WRITE_TOOLS = {"file_write", "Edit", "Write", "write_file", "edit_file"}
_SAFE_TOOLS = {
    "read_file", "Read", "list_dir", "ls", "find_file", "Glob", "Grep",
    "search_for_pattern", "find_symbol", "memory_recall",
    "fetch_tool_output", "get_symbols_overview",
}


def classify(action: str, *, tool_name: Optional[str] = None) -> RiskLevel:
    """Best-effort risk classification.

    The decision policy uses this as a starting point; learned memory
    rules can shift the outcome.
    """
    if tool_name in _SAFE_TOOLS:
        return RiskLevel.SAFE
    if tool_name in _LOCAL_WRITE_TOOLS:
        return RiskLevel.LOCAL

    if _DESTRUCTIVE_BASH.search(action) and _EXTERNAL_BASH.search(action):
        return RiskLevel.IRREVERSIBLE
    if _DESTRUCTIVE_BASH.search(action):
        return RiskLevel.DESTRUCTIVE
    if _EXTERNAL_BASH.search(action):
        return RiskLevel.EXTERNAL

    # Tool name heuristics
    name = (tool_name or "").lower()
    if any(x in name for x in ("delete", "drop", "remove", "purge", "destroy")):
        return RiskLevel.DESTRUCTIVE
    if any(x in name for x in ("publish", "deploy", "send", "post", "upload")):
        return RiskLevel.EXTERNAL

    # Default — local write
    return RiskLevel.LOCAL


# ---------------------------------------------------------------------------
# Policy resolver
# ---------------------------------------------------------------------------

class DecisionPolicy:
    """Resolve actions against learned rules + risk."""

    def __init__(self, *, default_autonomy: bool = True):
        # default_autonomy=True means SAFE/LOCAL run without asking; the
        # user can flip this off for safety modes.
        self._default_autonomy = default_autonomy

    def decide(self, ctx: DecisionContext) -> Decision:
        """Single decision call.

        Order of precedence (highest first):
            1. explicit user authorisation for this exact action
            2. risk == IRREVERSIBLE  → ASK (always)
            3. risk == EXTERNAL/DESTRUCTIVE without authorisation
                                     → ASK
            4. user_directive_active → EXECUTE for SAFE/LOCAL,
                                        ASK for higher risk
            5. inside_plan and risk ≤ LOCAL → EXECUTE
            6. default_autonomy and risk ≤ LOCAL → EXECUTE
            7. default → ASK
        """
        if ctx.user_explicit_authorisation:
            return Decision.EXECUTE

        if ctx.risk == RiskLevel.IRREVERSIBLE:
            return Decision.ASK

        if ctx.risk in (RiskLevel.DESTRUCTIVE, RiskLevel.EXTERNAL):
            return Decision.ASK

        if ctx.user_directive_active:
            return Decision.EXECUTE

        if ctx.inside_plan and ctx.risk in (RiskLevel.SAFE, RiskLevel.LOCAL):
            return Decision.EXECUTE

        if self._default_autonomy and ctx.risk in (RiskLevel.SAFE, RiskLevel.LOCAL):
            return Decision.EXECUTE

        return Decision.ASK

    # ------------------------------------------------------------------
    # Convenience: build context from a tool call + user message
    # ------------------------------------------------------------------

    def decide_for_tool(
        self,
        *,
        tool_name: str,
        tool_args: Dict[str, Any],
        latest_user_message: str = "",
        inside_plan: bool = False,
    ) -> Decision:
        action = self._describe_tool(tool_name, tool_args)
        risk = classify(action, tool_name=tool_name)
        directive = _looks_like_execute_directive(latest_user_message)
        ctx = DecisionContext(
            action=action,
            risk=risk,
            user_directive_active=directive,
            inside_plan=inside_plan,
        )
        return self.decide(ctx)

    @staticmethod
    def _describe_tool(tool_name: str, tool_args: Dict[str, Any]) -> str:
        """Single-line description used by classify() and the audit log."""
        if tool_name in ("Bash", "terminal", "execute"):
            return str(tool_args.get("command", ""))[:200]
        return f"{tool_name}({_short_args(tool_args)})"


def _short_args(args: Dict[str, Any]) -> str:
    parts = []
    for k, v in (args or {}).items():
        sv = str(v)
        if len(sv) > 40:
            sv = sv[:37] + "..."
        parts.append(f"{k}={sv}")
    return ", ".join(parts)[:160]


_EXECUTE_DIRECTIVE = re.compile(
    r"\b(?:lec|jedź|wykonaj\s+wszystkie|wykonaj\s+to|ma\s+działać|"
    r"go\s+ahead|just\s+do\s+it|pracuj\s+aż|just\s+execute|just\s+go)\b",
    re.IGNORECASE,
)


def _looks_like_execute_directive(user_msg: str) -> bool:
    return bool(user_msg and _EXECUTE_DIRECTIVE.search(user_msg))
