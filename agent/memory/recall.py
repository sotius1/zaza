"""Memory recall — vector + keyword retrieval blended into a single API.

Two callable surfaces:

* ``recall(query, …)`` — explicit lookup the agent can request via the
  ``memory_recall`` tool.  Returns a list of ranked items.

* ``recall_for_turn(user_message, session_id, …)`` — automatic preflight
  called once at the start of each turn.  Picks the top-k relevant
  memories and returns them in a compact, prompt-ready block.

Hybrid scoring combines:

    score = 0.7 * cosine_sim(query, item_embedding)
          + 0.3 * importance
          + 0.0 (recency bonus already baked into importance via decay)

When no embedding provider is available we degrade gracefully to a
keyword search and still return the top-k by ``importance``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

from agent.memory.embeddings import Embedding, get_default_embedder
from agent.memory.store import MemoryRow, MemoryStore, get_default_store

logger = logging.getLogger(__name__)


@dataclass
class Recalled:
    row: MemoryRow
    score: float
    reason: str  # "vec" | "keyword"


def recall(
    query: str,
    *,
    layer: Optional[str] = None,
    limit: int = 8,
    store: Optional[MemoryStore] = None,
    embedder: Optional[Embedding] = None,
    min_score: float = 0.05,
) -> List[Recalled]:
    """Retrieve the most relevant memories for ``query``.

    The function is resilient: failures in the embedding provider fall
    through to keyword search, and an empty store returns an empty list.
    """
    if not query or not query.strip():
        return []

    s = store or get_default_store()
    e = embedder or get_default_embedder()

    # 1. Try vector search
    try:
        qvec = e.embed(query)
    except Exception:
        logger.debug("recall: embedding failed, falling back to keyword search", exc_info=True)
        qvec = None

    if qvec:
        scored = s.search_similar(qvec, layer=layer, limit=limit * 2)
        out: List[Recalled] = []
        for row, sim in scored:
            blended = 0.7 * sim + 0.3 * row.importance
            if blended < min_score:
                continue
            out.append(Recalled(row=row, score=blended, reason="vec"))
            s.touch(row.id)
        out.sort(key=lambda r: r.score, reverse=True)
        if out:
            return out[:limit]

    # 2. Keyword fallback
    rows = s.search_text(query, layer=layer, limit=limit)
    return [
        Recalled(row=r, score=float(r.importance), reason="keyword")
        for r in rows
    ]


def recall_for_turn(
    user_message: str,
    *,
    session_id: Optional[str] = None,
    limit_episodic: int = 5,
    limit_semantic: int = 3,
    limit_procedural: int = 5,
) -> str:
    """Preflight recall for a new conversation turn.

    Returns a compact prompt block (or empty string) that callers can
    append to the system prompt.  Layered output keeps the agent aware
    of *what kind* of memory it is reading, which matters for the
    autonomous loop's decision policy.
    """
    if not user_message or not user_message.strip():
        return ""

    blocks: List[str] = []

    procedural = recall(user_message, layer="procedural", limit=limit_procedural)
    if procedural:
        items = []
        for r in procedural:
            polarity = (r.row.metadata or {}).get("polarity", "do").upper()
            items.append(f"  [{polarity}] {r.row.content}")
        blocks.append("Learned procedures:\n" + "\n".join(items))

    semantic = recall(user_message, layer="semantic", limit=limit_semantic)
    if semantic:
        items = []
        for r in semantic:
            items.append(f"  • {r.row.content}")
        blocks.append("Relevant facts:\n" + "\n".join(items))

    episodic = recall(user_message, layer="episodic", limit=limit_episodic)
    if episodic:
        items = []
        for r in episodic:
            ts = (r.row.created_at or "")[:10]
            items.append(f"  • [{ts}] {r.row.content}")
        blocks.append("Past episodes:\n" + "\n".join(items))

    if not blocks:
        return ""

    return "Recall context (auto-injected):\n\n" + "\n\n".join(blocks)
