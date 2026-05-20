"""ZAZA memory subsystem — multi-layer continuous-learning memory.

Architecture (5 layers, MemGPT × Mem0 × Letta hybrid):

    Working      (RAM)            current turn, recent tool outputs
    Core         (JSON, pinned)   user persona, golden rules, top prefs
    Episodic     (SQLite + vec)   events, decisions, conversation moments
    Semantic     (SQLite graph)   entities, relations, facts
    Procedural   (SQLite)         learned procedures (commit style, etc.)

The package is intentionally **un-seeded**: no hardcoded user name, no
hardcoded tech stack, no hardcoded persona.  Every preference is learned
from the conversation by ``memory.extractor`` (Phase 4) and consolidated
by ``memory.consolidator`` (Phase 6).

Public API:

    from agent.memory import (
        MemoryStore,            # the SQLite-backed store
        recall,                 # vector + keyword retrieval
        get_user_profile,       # core memory pin (always-in-context)
        update_user_profile,    # extractor write path
        Embedding,              # provider-agnostic embedding interface
    )
"""

from agent.memory.store import MemoryStore, get_default_store
from agent.memory.embeddings import Embedding, get_default_embedder
from agent.memory.layers import (
    MemoryLayer,
    MemoryItem,
    get_user_profile,
    update_user_profile,
    write_memory,
    render_core_memory_for_prompt,
)
from agent.memory.recall import recall, recall_for_turn
from agent.memory.extractor import extract, ExtractedSignals
from agent.memory.router import route, RouteResult
from agent.memory.learn import learn_from_turn

__all__ = [
    "MemoryStore",
    "get_default_store",
    "Embedding",
    "get_default_embedder",
    "MemoryLayer",
    "MemoryItem",
    "get_user_profile",
    "update_user_profile",
    "write_memory",
    "render_core_memory_for_prompt",
    "recall",
    "recall_for_turn",
    "extract",
    "ExtractedSignals",
    "route",
    "RouteResult",
    "learn_from_turn",
]
