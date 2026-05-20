"""``memory_recall`` — let the agent search its long-term memory on demand.

The agent loop already auto-injects recall results at the start of each
turn (see ``agent/memory/recall.py::recall_for_turn``).  This tool gives
the agent an *explicit* knob: when it suspects there's more relevant
context it can pull additional memories without waiting for the next
turn.

Why expose it as a tool when there's already an auto-inject?
* Auto-inject runs once per turn with a fixed budget; deep work might
  exhaust it.  An explicit lookup unblocks the agent.
* The agent can scope by layer (e.g. only ``procedural`` rules during
  decision-time).
* Cheap query targeting a remembered fact is faster than re-deriving.
"""

from __future__ import annotations

import json


_SCHEMA = {
    "name": "memory_recall",
    "description": (
        "Search the agent's long-term memory for entries relevant to a query.\n\n"
        "Returns up to N matching memories ranked by similarity + importance. "
        "Use this when the auto-injected recall block doesn't cover what you "
        "need, or when you want to scope the search to a specific layer.\n\n"
        "Layers:\n"
        "  - episodic   past events / decisions / conversation moments\n"
        "  - semantic   entities and typed facts (knowledge graph)\n"
        "  - procedural learned rules ('do this', 'don't do that')\n"
        "  - core       user persona pin (use only when you really need it)\n"
        "Omit `layer` to search every layer."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language query.  E.g. 'how does the user prefer commits?'",
            },
            "layer": {
                "type": "string",
                "enum": ["episodic", "semantic", "procedural", "core"],
                "description": "Optional layer filter.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 20,
                "default": 8,
            },
        },
        "required": ["query"],
    },
}


def memory_recall_tool(query: str, layer: str | None = None, limit: int = 8, **_kw) -> str:
    if not query or not query.strip():
        return json.dumps({"error": "query is required"})

    try:
        from agent.memory import recall, get_user_profile
        from agent.memory.layers import render_core_memory_for_prompt
    except Exception as exc:  # pragma: no cover
        return json.dumps({"error": f"memory subsystem unavailable: {exc}"})

    out: dict = {"query": query, "results": []}

    if layer == "core":
        # Special-case: core memory is the JSON pin, not SQLite rows.
        out["core_memory"] = get_user_profile()
        out["formatted"] = render_core_memory_for_prompt()
        return json.dumps(out, ensure_ascii=False, default=str)

    hits = recall(query, layer=layer, limit=int(limit))
    for h in hits:
        out["results"].append({
            "id": h.row.id,
            "layer": h.row.layer,
            "kind": h.row.kind,
            "content": h.row.content,
            "metadata": h.row.metadata,
            "score": round(h.score, 4),
            "via": h.reason,
            "created_at": h.row.created_at,
            "importance": h.row.importance,
            "confidence": h.row.confidence,
        })
    return json.dumps(out, ensure_ascii=False, default=str)


def check_memory_recall_requirements() -> tuple[bool, str]:
    return True, ""


# --- Registry --------------------------------------------------------------
from tools.registry import registry  # noqa: E402

registry.register(
    name="memory_recall",
    toolset="memory",
    schema=_SCHEMA,
    handler=lambda args, **kw: memory_recall_tool(
        query=args.get("query", ""),
        layer=args.get("layer"),
        limit=int(args.get("limit", 8)),
    ),
    check_fn=check_memory_recall_requirements,
    emoji="🧠",
    description="Search long-term memory for relevant past context.",
)
