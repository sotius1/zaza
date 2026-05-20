"""Memory extractor — distil structured signals from a conversation turn.

The extractor runs after each user/assistant exchange.  It looks at the
turn (user message + assistant final response + tool calls) and produces
a structured payload of *signals*:

    {
      "preferences":        [{"rule": "...", "polarity": "do|dont",
                              "confidence": 0..1, "why": "..."}],
      "facts":              [{"subject":"...", "predicate":"...",
                              "object":"...", "confidence": 0..1}],
      "communication_style":{"formality": "...", "response_length": "...",
                              "preferred_language": "...",
                              "examples": [{"user_says":"...",
                                            "interpretation":"..."}]},
      "tech_stack_hints":   ["nextjs", "supabase", ...],
      "project_hints":      ["..."],
      "events":             ["short event description", ...],
      "negative_signals":   ["...", ...],   # things the agent did wrong
      "positive_signals":   ["...", ...]    # things the agent did right
    }

Two extraction strategies, picked at runtime:

1. **LLM-based** (preferred) — call a small model (auxiliary client) with
   a structured-output prompt that returns the JSON above.

2. **Heuristic** (fallback) — simple regex/keyword rules on the user
   message.  Used when the auxiliary client is unavailable, when the
   conversation is offline, or when the LLM extraction fails.  Safer
   than nothing but obviously less precise.

The router then decides which layer each signal lands in.  See
``agent/memory/router.py``.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ExtractedSignals:
    """Structured payload of signals extracted from a single turn."""
    preferences: List[Dict[str, Any]] = field(default_factory=list)
    facts: List[Dict[str, Any]] = field(default_factory=list)
    communication_style: Dict[str, Any] = field(default_factory=dict)
    tech_stack_hints: List[str] = field(default_factory=list)
    project_hints: List[str] = field(default_factory=list)
    events: List[str] = field(default_factory=list)
    negative_signals: List[str] = field(default_factory=list)
    positive_signals: List[str] = field(default_factory=list)
    _via: str = "none"  # "llm" | "heuristic" | "none"

    def is_empty(self) -> bool:
        return not any([
            self.preferences, self.facts, self.communication_style,
            self.tech_stack_hints, self.project_hints, self.events,
            self.negative_signals, self.positive_signals,
        ])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract(
    *,
    user_message: str,
    assistant_response: str = "",
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    auxiliary_client: Any = None,
) -> ExtractedSignals:
    """Pull signals from a single turn.

    ``auxiliary_client`` is the agent's small/cheap LLM client used for
    side-channel jobs (titles, summaries, etc.).  If None or a call
    fails, we fall through to the heuristic extractor.
    """
    if not user_message and not assistant_response:
        return ExtractedSignals()

    if auxiliary_client is not None:
        try:
            payload = _llm_extract(user_message, assistant_response, tool_calls or [], auxiliary_client)
            if payload is not None:
                payload._via = "llm"
                return payload
        except Exception:
            logger.debug("LLM extractor failed, falling through to heuristics", exc_info=True)

    sig = _heuristic_extract(user_message, assistant_response)
    sig._via = "heuristic"
    return sig


# ---------------------------------------------------------------------------
# LLM extractor
# ---------------------------------------------------------------------------

_EXTRACT_SYSTEM = """You are a memory extractor for an autonomous coding assistant.

Read the user's message, the assistant's response, and any tool calls,
then output ONE JSON object describing what should be remembered.

Output schema (all fields optional, omit if nothing applies):

{
  "preferences":        [{"rule":"...","polarity":"do|dont","confidence":0..1,"why":"..."}],
  "facts":              [{"subject":"...","predicate":"...","object":"...","confidence":0..1}],
  "communication_style":{"formality":"casual|formal","response_length":"short|medium|long",
                          "preferred_language":"<bcp47 or natural name>",
                          "examples":[{"user_says":"...","interpretation":"..."}]},
  "tech_stack_hints":   ["..."],
  "project_hints":      ["..."],
  "events":             ["short third-person event description"],
  "negative_signals":   ["..."],
  "positive_signals":   ["..."]
}

CRITICAL RULES:
- Only extract signals the user explicitly stated or strongly implied.
- Do NOT invent facts about the user.  If unsure, skip.
- Confidence reflects how certain you are this is real signal vs. noise.
- preferences.polarity: "do" if it's something to repeat, "dont" if to avoid.
- Output ONLY the JSON object.  No commentary, no markdown fences."""


def _llm_extract(
    user_msg: str,
    assistant_msg: str,
    tool_calls: List[Dict[str, Any]],
    aux_client: Any,
) -> Optional[ExtractedSignals]:
    user_block = f"USER:\n{user_msg.strip()}"
    assistant_block = f"ASSISTANT:\n{assistant_msg.strip()}" if assistant_msg.strip() else ""
    tool_block = ""
    if tool_calls:
        tool_summary = []
        for tc in tool_calls[:8]:
            name = tc.get("name") or tc.get("function", {}).get("name", "?")
            tool_summary.append(f"  - {name}")
        tool_block = "TOOLS USED:\n" + "\n".join(tool_summary)

    prompt = "\n\n".join(b for b in [user_block, assistant_block, tool_block] if b)

    # The auxiliary client API differs slightly across implementations;
    # support both ``.complete(...)`` and ``.chat(...)`` style.
    text: Optional[str] = None
    try:
        if hasattr(aux_client, "chat"):
            text = aux_client.chat(
                system=_EXTRACT_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=800,
                temperature=0.0,
            )
        elif hasattr(aux_client, "complete"):
            text = aux_client.complete(
                system=_EXTRACT_SYSTEM, prompt=prompt,
                max_tokens=800, temperature=0.0,
            )
    except Exception:
        return None

    if not text:
        return None

    raw = _strip_code_fences(text).strip()
    try:
        data = json.loads(raw)
    except Exception:
        # Best-effort: try to find a JSON object inside the response
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except Exception:
            return None

    if not isinstance(data, dict):
        return None

    return ExtractedSignals(
        preferences=list(data.get("preferences") or []),
        facts=list(data.get("facts") or []),
        communication_style=dict(data.get("communication_style") or {}),
        tech_stack_hints=[str(x) for x in (data.get("tech_stack_hints") or []) if x],
        project_hints=[str(x) for x in (data.get("project_hints") or []) if x],
        events=[str(x) for x in (data.get("events") or []) if x],
        negative_signals=[str(x) for x in (data.get("negative_signals") or []) if x],
        positive_signals=[str(x) for x in (data.get("positive_signals") or []) if x],
    )


def _strip_code_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\s*\n?", "", s)
        if s.endswith("```"):
            s = s[: -3]
    return s.strip()


# ---------------------------------------------------------------------------
# Heuristic extractor (no LLM required)
# ---------------------------------------------------------------------------

# Dictionaries are intentionally small.  This is a fallback, not a
# replacement for the LLM extractor.

_NEGATIVE_PATTERNS = [
    (r"\bnie tak\b",           "user explicitly said it's wrong"),
    (r"\bstop\b",              "user told the agent to stop"),
    (r"\bnie pytaj\b",         "user does not want to be asked"),
    (r"\b(?:ale\s+)?to\s+źle\b", "user said it's wrong"),
    (r"\bdon['’]?t\b",         "user used a 'don't'"),
    (r"\bbrzydk[a-zżśćźń]+\b", "user expressed dissatisfaction"),
    (r"\bnie umniejszaj\b",    "user warned against self-deprecation"),
]

_POSITIVE_PATTERNS = [
    (r"\bdokładnie\b",        "user confirmed precisely that"),
    (r"\bperfekcyjnie\b",      "user confirmed perfect"),
    (r"\bidealnie\b",          "user confirmed ideal"),
    (r"\bsuper\b",             "user said super"),
    (r"\bgites\b",             "user said gites"),
    (r"\bperfect\b",           "user said perfect"),
    (r"\bexactly\b",           "user said exactly"),
]

_EXECUTE_PATTERNS = [
    r"\blec\b",
    r"\bjedź\b",
    r"\bwykonaj\s+(?:wszystkie|to)\b",
    r"\bma\s+działać\b",
    r"\bgo\s+ahead\b",
]

_LANGUAGE_HINT = re.compile(r"\b(po\s+polsku|in\s+english|po\s+angielsku)\b", re.IGNORECASE)


def _heuristic_extract(user_msg: str, assistant_msg: str) -> ExtractedSignals:
    sig = ExtractedSignals()
    msg = user_msg or ""

    # Negative / positive
    for pat, desc in _NEGATIVE_PATTERNS:
        if re.search(pat, msg, re.IGNORECASE):
            sig.negative_signals.append(desc)
    for pat, desc in _POSITIVE_PATTERNS:
        if re.search(pat, msg, re.IGNORECASE):
            sig.positive_signals.append(desc)

    # Execute style — implies "user wants execution, not consultation"
    for pat in _EXECUTE_PATTERNS:
        if re.search(pat, msg, re.IGNORECASE):
            sig.preferences.append({
                "rule": "When the user says 'lec'/'wykonaj wszystkie'/'ma działać', execute defaults without asking.",
                "polarity": "do",
                "confidence": 0.6,
                "why": "user-message contained an execution directive",
            })
            break

    # Language hint
    m = _LANGUAGE_HINT.search(msg)
    if m:
        hint = m.group(1).lower()
        if "polsk" in hint:
            sig.communication_style["preferred_language"] = "pl"
        elif "english" in hint or "angielsk" in hint:
            sig.communication_style["preferred_language"] = "en"

    # Length hint — very rough proxy
    if len(msg) < 60 and assistant_msg and len(assistant_msg) > 1500:
        # short user prompt + very long answer might mean we over-explained
        # We DON'T claim a preference yet, just flag for the LLM extractor
        sig.events.append("user prompt was short; assistant response was long")

    return sig
