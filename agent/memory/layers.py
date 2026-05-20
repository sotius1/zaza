"""Layer-aware memory write helpers.

The 5 layers of the ZAZA memory model:

    Working      transient — never persisted (kept by the agent loop)
    Core         user persona, golden rules, communication style.
                 Stored in a small JSON pin (``user_profile.json``) that
                 is *always* injected into the prompt.
    Episodic     events, decisions, conversation moments.  SQLite + vec.
    Semantic     entities + typed relations.  Knowledge graph in SQLite.
    Procedural   learned procedures (commit style, naming, etc).
                 SQLite, kind="rule".

This module gives callers a *single* high-level entry point —
``write_memory(...)`` — that picks the right layer and storage path
without exposing the SQL.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from agent.memory.embeddings import Embedding, get_default_embedder
from agent.memory.store import MemoryStore, get_default_store

logger = logging.getLogger(__name__)


class MemoryLayer(str, Enum):
    CORE = "core"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    WORKING = "working"


@dataclass
class MemoryItem:
    """High-level write request, decoupled from the storage layout."""
    layer: MemoryLayer
    kind: str
    content: str
    metadata: Dict[str, Any]
    importance: float = 0.5
    confidence: float = 0.5
    source: Optional[str] = None
    session_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Core memory: JSON pin (always-in-context)
# ---------------------------------------------------------------------------
#
# The core memory file is intentionally small — it ships with the agent's
# system prompt every turn.  Keep it under ~2 KB to avoid token bloat.
# Anything heavier belongs in episodic or semantic.
#
# Schema (free-form JSON; extractor fills these in over time):
#
#     {
#       "user": {                    // who is the user
#         "name": null,
#         "language": null,
#         "timezone": null
#       },
#       "communication_style": {     // how they like to be talked to
#         "formality": null,         // "casual" | "formal" | null
#         "response_length": null,   // "short" | "medium" | "long" | null
#         "preferred_language": null, // ISO code or natural name
#         "examples": []             // [{user_says, interpretation}]
#       },
#       "tech_stack_focus": [],      // learned from project context
#       "current_projects": [],      // active workstreams (free-form labels)
#       "rules": {                   // hard preferences
#         "do":   [],
#         "dont": []
#       },
#       "facts": [],                 // top-priority facts to remember
#       "_meta": {
#         "created_at": "...",
#         "updated_at": "...",
#         "schema_version": 1
#       }
#     }
#
# Important: this dict starts EMPTY on a fresh install.  We never seed
# user.name = "Jakub" or tech_stack = ["Next.js"].  Every value is
# learned from the conversation.
# ---------------------------------------------------------------------------

EMPTY_PROFILE: Dict[str, Any] = {
    "user": {"name": None, "language": None, "timezone": None},
    "communication_style": {
        "formality": None,
        "response_length": None,
        "preferred_language": None,
        "examples": [],
    },
    "tech_stack_focus": [],
    "current_projects": [],
    "rules": {"do": [], "dont": []},
    "facts": [],
    "_meta": {"schema_version": 1},
}


def _profile_path() -> Path:
    try:
        from zaza_constants import get_zaza_home
        base = get_zaza_home() / "data"
    except Exception:
        base = Path.home() / ".agent-zaza" / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base / "user_profile.json"


def get_user_profile() -> Dict[str, Any]:
    """Return the on-disk core memory pin.  Creates an empty one if absent."""
    path = _profile_path()
    if not path.exists():
        from datetime import datetime, timezone
        profile = json.loads(json.dumps(EMPTY_PROFILE))
        profile["_meta"]["created_at"] = datetime.now(timezone.utc).isoformat()
        profile["_meta"]["updated_at"] = profile["_meta"]["created_at"]
        path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        return profile
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Could not read user profile, returning empty")
        return json.loads(json.dumps(EMPTY_PROFILE))


def update_user_profile(patch: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-merge a patch into the core memory pin and persist.

    Lists are appended (deduplicated for primitives), dicts merged
    recursively, scalars overwritten.  ``_meta.updated_at`` is bumped.
    """
    from datetime import datetime, timezone

    profile = get_user_profile()
    _deep_merge(profile, patch)
    profile.setdefault("_meta", {})["updated_at"] = datetime.now(timezone.utc).isoformat()
    _profile_path().write_text(
        json.dumps(profile, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return profile


def _deep_merge(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    """In-place deep merge; lists deduplicate primitives, dicts recurse."""
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        elif isinstance(v, list) and isinstance(dst.get(k), list):
            existing = dst[k]
            for item in v:
                if isinstance(item, (str, int, float, bool)) and item in existing:
                    continue
                existing.append(item)
        else:
            dst[k] = v


# ---------------------------------------------------------------------------
# Unified write entry point
# ---------------------------------------------------------------------------

def write_memory(
    item: MemoryItem,
    *,
    store: Optional[MemoryStore] = None,
    embedder: Optional[Embedding] = None,
) -> Optional[int]:
    """Persist ``item`` to the right layer.

    Returns the SQLite row id (or None for working/core layers, which
    don't go through SQLite).
    """
    if item.layer == MemoryLayer.WORKING:
        # Working memory is in-RAM only — caller is expected to hold it
        # in their conversation state.  This function is a no-op for it
        # so callers can use a single API regardless of layer.
        return None

    if item.layer == MemoryLayer.CORE:
        # Core writes go to the JSON pin.  The patch must be a structured
        # dict embedded in metadata; content is treated as a freeform
        # description for traceability.
        patch = item.metadata.get("profile_patch")
        if isinstance(patch, dict):
            update_user_profile(patch)
        else:
            update_user_profile({"facts": [item.content]})
        return None

    s = store or get_default_store()
    e = embedder or get_default_embedder()

    try:
        vec = e.embed(item.content)
        emb_model = e.name
    except Exception:
        logger.warning("Embedding failed for memory write; storing without vector")
        vec = None
        emb_model = None

    return s.add(
        layer=item.layer.value,
        kind=item.kind,
        content=item.content,
        metadata=item.metadata,
        importance=item.importance,
        confidence=item.confidence,
        embedding=vec,
        embedding_model=emb_model,
        source=item.source,
        session_id=item.session_id,
    )


# ---------------------------------------------------------------------------
# Convenience wrappers per layer
# ---------------------------------------------------------------------------

def remember_event(
    content: str, *, importance: float = 0.5, confidence: float = 0.7,
    metadata: Optional[Dict[str, Any]] = None, session_id: Optional[str] = None,
    source: str = "agent",
) -> Optional[int]:
    return write_memory(MemoryItem(
        layer=MemoryLayer.EPISODIC, kind="event", content=content,
        metadata=metadata or {}, importance=importance, confidence=confidence,
        source=source, session_id=session_id,
    ))


def remember_fact(
    subject: str, predicate: str, obj: str,
    *, confidence: float = 0.5, source_memory_id: Optional[int] = None,
    source: str = "extractor",
) -> Optional[int]:
    """Insert into both the semantic graph and the memory layer.

    Graph row is the source of truth for queries; the memory row is what
    gets retrieved by content-similarity recall.
    """
    store = get_default_store()
    src_id = store.upsert_entity(subject)
    dst_id = store.upsert_entity(obj)
    rel_id = store.upsert_relation(
        src_id, dst_id, predicate,
        confidence=confidence, source_memory=source_memory_id,
    )
    text = f"{subject} {predicate} {obj}"
    mem_id = write_memory(MemoryItem(
        layer=MemoryLayer.SEMANTIC, kind="fact", content=text,
        metadata={"subject": subject, "predicate": predicate, "object": obj,
                  "relation_id": rel_id},
        importance=0.6, confidence=confidence,
        source=source,
    ))
    return mem_id


def remember_rule(
    rule: str, *, polarity: str = "do", confidence: float = 0.7,
    why: Optional[str] = None, source: str = "extractor",
) -> Optional[int]:
    """Procedural memory: a rule the agent should follow.

    Polarity is ``do`` (positive — repeat this) or ``dont`` (negative —
    avoid this).  ``why`` records the originating motivation so the
    consolidator can resolve conflicts later.
    """
    metadata: Dict[str, Any] = {"polarity": polarity}
    if why:
        metadata["why"] = why
    return write_memory(MemoryItem(
        layer=MemoryLayer.PROCEDURAL, kind="rule", content=rule,
        metadata=metadata, importance=0.7, confidence=confidence,
        source=source,
    ))


# ---------------------------------------------------------------------------
# Render core memory for the system prompt
# ---------------------------------------------------------------------------

def render_core_memory_for_prompt(profile: Optional[Dict[str, Any]] = None) -> str:
    """Render the core memory pin as a compact prompt block.

    Returns an empty string when the profile has no learned content yet,
    so the agent's system prompt stays clean on a fresh install.
    """
    p = profile or get_user_profile()
    lines: List[str] = []

    user = p.get("user") or {}
    if user.get("name"):
        lines.append(f"- user: {user['name']}")
    if user.get("language"):
        lines.append(f"- language: {user['language']}")
    if user.get("timezone"):
        lines.append(f"- timezone: {user['timezone']}")

    style = p.get("communication_style") or {}
    style_parts: List[str] = []
    if style.get("formality"):
        style_parts.append(f"formality={style['formality']}")
    if style.get("response_length"):
        style_parts.append(f"length={style['response_length']}")
    if style.get("preferred_language"):
        style_parts.append(f"reply-in={style['preferred_language']}")
    if style_parts:
        lines.append(f"- style: {', '.join(style_parts)}")

    tech: Sequence[Any] = p.get("tech_stack_focus") or []
    if tech:
        lines.append(f"- tech focus: {', '.join(str(x) for x in tech[:8])}")

    projects: Sequence[Any] = p.get("current_projects") or []
    if projects:
        lines.append(f"- projects: {', '.join(str(x) for x in projects[:5])}")

    rules = p.get("rules") or {}
    do_rules: Sequence[Any] = rules.get("do") or []
    dont_rules: Sequence[Any] = rules.get("dont") or []
    for rule in list(do_rules)[:5]:
        lines.append(f"- DO: {rule}")
    for rule in list(dont_rules)[:5]:
        lines.append(f"- DON'T: {rule}")

    facts: Sequence[Any] = p.get("facts") or []
    for fact in list(facts)[:5]:
        lines.append(f"- {fact}")

    if not lines:
        return ""

    return "Core memory (learned about this user):\n" + "\n".join(lines)
