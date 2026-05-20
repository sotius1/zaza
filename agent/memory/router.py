"""Memory router — turn ExtractedSignals into layered writes.

Lives between ``extractor.py`` and the per-layer write helpers in
``layers.py``.  Single entry point: ``route(signals, …)``.

Routing table
=============

================  ===========================  ====================
signal field      destination layer            kind
================  ===========================  ====================
preferences       procedural (rule)            "rule"
facts             semantic (entity + relation) "fact"
communication_style core (profile patch)        n/a (JSON pin)
tech_stack_hints  core (profile patch)         n/a
project_hints     core (profile patch)         n/a
events            episodic                     "event"
negative_signals  procedural ("dont" rule)     "rule"
positive_signals  procedural ("do"   rule)     "rule"
================  ===========================  ====================

Importance / confidence policy
==============================

* Preferences default ``importance=0.7`` because they steer future
  behaviour directly.
* Facts default ``importance=0.6``.
* Events default ``importance=0.5``; bumped when the extractor flagged
  them as "decision" or "incident".
* Procedural rules default ``importance=0.75`` (they're the closest
  thing the agent has to operating policy).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from agent.memory.extractor import ExtractedSignals
from agent.memory.layers import (
    MemoryItem,
    MemoryLayer,
    remember_event,
    remember_fact,
    remember_rule,
    update_user_profile,
    write_memory,
)

logger = logging.getLogger(__name__)


@dataclass
class RouteResult:
    """Summary of what the router persisted."""
    rules_written: int = 0
    facts_written: int = 0
    events_written: int = 0
    profile_patched: bool = False
    via: str = "none"

    def total(self) -> int:
        return (
            self.rules_written
            + self.facts_written
            + self.events_written
            + (1 if self.profile_patched else 0)
        )


def route(
    signals: ExtractedSignals,
    *,
    session_id: Optional[str] = None,
    source_hint: str = "extractor",
) -> RouteResult:
    """Persist all signals to their appropriate layers.

    Idempotent in spirit: re-running the router with the same signals
    will create new rows but the consolidator (Phase 6) will dedupe
    them.  We don't dedupe here because that requires a similarity
    pass, and the extractor already runs once per turn.
    """
    result = RouteResult(via=signals._via)
    if signals.is_empty():
        return result

    # ---- procedural rules ------------------------------------------------
    for pref in signals.preferences:
        rule = (pref.get("rule") or "").strip()
        if not rule:
            continue
        polarity = pref.get("polarity") or "do"
        if polarity not in ("do", "dont"):
            polarity = "do"
        confidence = _clamp01(float(pref.get("confidence") or 0.5))
        if remember_rule(
            rule, polarity=polarity, confidence=confidence,
            why=pref.get("why"), source=source_hint,
        ) is not None:
            result.rules_written += 1

    for neg in signals.negative_signals:
        if not neg.strip():
            continue
        if remember_rule(
            f"Avoid: {neg.strip()}", polarity="dont", confidence=0.7,
            why="implicit negative signal from user", source=source_hint,
        ) is not None:
            result.rules_written += 1

    for pos in signals.positive_signals:
        if not pos.strip():
            continue
        if remember_rule(
            f"Repeat: {pos.strip()}", polarity="do", confidence=0.6,
            why="implicit positive signal from user", source=source_hint,
        ) is not None:
            result.rules_written += 1

    # ---- semantic facts --------------------------------------------------
    for fact in signals.facts:
        s = (fact.get("subject") or "").strip()
        p = (fact.get("predicate") or "").strip()
        o = (fact.get("object") or "").strip()
        if not (s and p and o):
            continue
        confidence = _clamp01(float(fact.get("confidence") or 0.5))
        if remember_fact(s, p, o, confidence=confidence, source=source_hint) is not None:
            result.facts_written += 1

    # ---- episodic events ------------------------------------------------
    for event in signals.events:
        text = (event or "").strip()
        if not text:
            continue
        if remember_event(
            text, importance=0.5, confidence=0.7,
            session_id=session_id, source=source_hint,
        ) is not None:
            result.events_written += 1

    # ---- core profile patch ---------------------------------------------
    patch: dict = {}
    if signals.communication_style:
        patch["communication_style"] = dict(signals.communication_style)

    if signals.tech_stack_hints:
        # The profile's ``tech_stack_focus`` is the canonical learned
        # tech list.  Lowercase & dedupe at write time so the JSON pin
        # stays clean.
        normalized = sorted({h.strip().lower() for h in signals.tech_stack_hints if h and h.strip()})
        if normalized:
            patch["tech_stack_focus"] = normalized

    if signals.project_hints:
        patch["current_projects"] = [
            h.strip() for h in signals.project_hints if h and h.strip()
        ]

    if patch:
        update_user_profile(patch)
        result.profile_patched = True

    return result


def _clamp01(x: float) -> float:
    if x != x:  # NaN
        return 0.5
    if x < 0:
        return 0.0
    if x > 1:
        return 1.0
    return x
