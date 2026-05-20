"""Reflexion — record one lesson per turn so the agent improves over time.

After each turn we ask the agent (or the heuristic fallback) to answer
two questions:

    1. What worked?
    2. What would I do differently next time?

The answers become a ``Lesson`` and are persisted to:

* the ``procedural`` memory layer (so future turns recall them);
* the active plan's step notes (so the next critique pass sees them).

Reflexion is intentionally short — one or two sentences — to avoid
flooding memory with verbose self-talk.  Quality over quantity.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Lesson:
    """A single distilled lesson from a turn."""
    what_worked: str
    what_to_change: str
    confidence: float = 0.6


def reflect(
    *,
    user_message: str,
    assistant_response: str,
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    user_feedback_signal: Optional[str] = None,  # "positive" | "negative" | None
    auxiliary_client: Any = None,
) -> Optional[Lesson]:
    """Run a self-critique pass.

    Returns a Lesson or None when nothing new was learned.
    """
    if auxiliary_client is not None:
        try:
            lesson = _llm_reflect(
                user_message, assistant_response, tool_calls or [],
                user_feedback_signal, auxiliary_client,
            )
            if lesson:
                return lesson
        except Exception:
            logger.debug("LLM reflexion failed", exc_info=True)

    return _heuristic_reflect(
        user_message, assistant_response, tool_calls or [],
        user_feedback_signal,
    )


# ---------------------------------------------------------------------------
# LLM reflexion
# ---------------------------------------------------------------------------

_REFLECT_SYSTEM = """You are the agent reflecting on its own turn.

Read the user's message, your response, and the tools you used.
Output ONE JSON object with two fields:

  {"what_worked": "...", "what_to_change": "..."}

Both fields are at most ONE short sentence.  If nothing notable
worked or would be changed, return null instead of the object.

Rules:
- Be specific — name the action, not vague feelings.
- "what_to_change" must be actionable next time.
- Output ONLY the JSON or the literal word `null`.  No markdown."""


def _llm_reflect(
    user_msg: str,
    assistant_msg: str,
    tool_calls: List[Dict[str, Any]],
    feedback: Optional[str],
    aux_client: Any,
) -> Optional[Lesson]:
    fb = ""
    if feedback == "positive":
        fb = "USER FEEDBACK: positive (the user signalled the agent did well)\n"
    elif feedback == "negative":
        fb = "USER FEEDBACK: negative (the user pushed back or corrected)\n"

    tool_block = ""
    if tool_calls:
        tool_block = "TOOLS USED:\n" + "\n".join(
            f"  - {tc.get('name') or tc.get('function', {}).get('name', '?')}"
            for tc in tool_calls[:8]
        )

    prompt = f"{fb}USER:\n{user_msg.strip()}\n\nASSISTANT:\n{assistant_msg.strip()}"
    if tool_block:
        prompt += "\n\n" + tool_block

    text: Optional[str] = None
    try:
        if hasattr(aux_client, "chat"):
            text = aux_client.chat(
                system=_REFLECT_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=200, temperature=0.2,
            )
        elif hasattr(aux_client, "complete"):
            text = aux_client.complete(
                system=_REFLECT_SYSTEM, prompt=prompt,
                max_tokens=200, temperature=0.2,
            )
    except Exception:
        return None

    if not text:
        return None

    raw = text.strip()
    if raw.lower() in ("null", "none", "{}"):
        return None

    raw = re.sub(r"^```[a-zA-Z0-9_-]*\s*\n?", "", raw)
    if raw.endswith("```"):
        raw = raw[:-3]
    raw = raw.strip()

    try:
        data = json.loads(raw)
    except Exception:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except Exception:
            return None

    if not isinstance(data, dict):
        return None

    worked = str(data.get("what_worked") or "").strip()
    change = str(data.get("what_to_change") or "").strip()
    if not worked and not change:
        return None
    confidence = 0.75 if (worked and change) else 0.55
    return Lesson(what_worked=worked, what_to_change=change, confidence=confidence)


# ---------------------------------------------------------------------------
# Heuristic reflexion (no LLM)
# ---------------------------------------------------------------------------

def _heuristic_reflect(
    user_msg: str,
    assistant_msg: str,
    tool_calls: List[Dict[str, Any]],
    feedback: Optional[str],
) -> Optional[Lesson]:
    if feedback == "negative":
        return Lesson(
            what_worked="execution completed",
            what_to_change=(
                "user pushed back — recall the rule that triggered, surface "
                "uncertainty earlier next time"
            ),
            confidence=0.6,
        )
    if feedback == "positive":
        return Lesson(
            what_worked="approach matched user expectations",
            what_to_change="",
            confidence=0.6,
        )

    # No explicit signal — only emit a lesson when the turn was tool-heavy
    # without resolving (proxy for "thrashing").
    if len(tool_calls) >= 8 and len(assistant_msg) < 200:
        return Lesson(
            what_worked="",
            what_to_change=(
                "many tool calls produced a short answer — consider planning "
                "before execution next time"
            ),
            confidence=0.4,
        )

    return None


# ---------------------------------------------------------------------------
# Persist a lesson into procedural memory
# ---------------------------------------------------------------------------

def persist_lesson(lesson: Lesson, *, session_id: Optional[str] = None) -> List[int]:
    """Write the lesson into procedural memory.

    Returns the list of memory row ids created (0–2 entries depending on
    whether ``what_worked`` and ``what_to_change`` are both non-empty).
    """
    from agent.memory.layers import remember_rule

    written: List[int] = []
    if lesson.what_worked:
        rid = remember_rule(
            f"Repeat: {lesson.what_worked}",
            polarity="do", confidence=lesson.confidence,
            why="reflexion: this approach worked",
            source="reflexion",
        )
        if rid is not None:
            written.append(rid)

    if lesson.what_to_change:
        rid = remember_rule(
            f"Change: {lesson.what_to_change}",
            polarity="dont", confidence=lesson.confidence,
            why="reflexion: this didn't go ideally",
            source="reflexion",
        )
        if rid is not None:
            written.append(rid)

    return written


# ---------------------------------------------------------------------------
# Reflexion convenience class
# ---------------------------------------------------------------------------

class Reflexion:
    """Stateful holder around the reflect/persist pair, useful for tests."""

    def __init__(self, *, auxiliary_client: Any = None):
        self._aux = auxiliary_client
        self.last: Optional[Lesson] = None

    def run(
        self,
        *,
        user_message: str,
        assistant_response: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        user_feedback_signal: Optional[str] = None,
        persist: bool = True,
        session_id: Optional[str] = None,
    ) -> Optional[Lesson]:
        lesson = reflect(
            user_message=user_message,
            assistant_response=assistant_response,
            tool_calls=tool_calls,
            user_feedback_signal=user_feedback_signal,
            auxiliary_client=self._aux,
        )
        self.last = lesson
        if lesson and persist:
            persist_lesson(lesson, session_id=session_id)
        return lesson
