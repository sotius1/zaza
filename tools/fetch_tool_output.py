"""``fetch_tool_output`` — re-expand a previously offloaded tool result.

When a tool produces a large output, ``agent.tool_output_store`` saves the
full text to disk and replaces it in the conversation with a compact
reference (``tooloff://<sha16>``).  This tool lets the agent fetch the
full content back on demand.

Why a dedicated tool instead of letting the agent read the file directly?
- Cleaner abstraction: agent doesn't need to know the on-disk path
- Safer: the lookup is restricted to the offload store, not arbitrary FS
- Cheaper: the response can be range-limited (head/tail/lines) without
  pulling the whole blob back into the prompt
"""

from __future__ import annotations

import json


_FETCH_SCHEMA = {
    "name": "fetch_tool_output",
    "description": (
        "Expand an offloaded tool output back into the conversation.\n\n"
        "When a previous tool produced a large response, its content was "
        "saved to disk and the conversation only kept a short preview plus a "
        "reference like `tooloff://abc123def456`. Call this tool with that "
        "reference to retrieve the full content.\n\n"
        "Use range parameters to avoid re-inflating the prompt: read only "
        "the section you need (e.g. lines around an error, the last 200 "
        "chars, a specific byte offset)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "ref": {
                "type": "string",
                "description": (
                    "The offload reference. Accepts the full form "
                    "`tooloff://<sha16>` or just the sha prefix."
                ),
            },
            "mode": {
                "type": "string",
                "enum": ["full", "head", "tail", "range"],
                "default": "full",
                "description": (
                    "How to slice the content. 'full' returns everything, "
                    "'head' / 'tail' take the first/last N lines, 'range' "
                    "uses 'start_line' and 'end_line' (1-based, inclusive)."
                ),
            },
            "lines": {
                "type": "integer",
                "minimum": 1,
                "default": 200,
                "description": (
                    "Number of lines for 'head' or 'tail' mode."
                ),
            },
            "start_line": {
                "type": "integer",
                "minimum": 1,
                "description": "1-based start line for 'range' mode.",
            },
            "end_line": {
                "type": "integer",
                "minimum": 1,
                "description": "1-based end line (inclusive) for 'range' mode.",
            },
        },
        "required": ["ref"],
    },
}


def _slice_content(content: str, *, mode: str, lines: int,
                   start_line: int | None, end_line: int | None) -> str:
    if mode == "full":
        return content
    body = content.splitlines()
    if mode == "head":
        return "\n".join(body[:max(lines, 1)])
    if mode == "tail":
        return "\n".join(body[-max(lines, 1):])
    if mode == "range":
        s = max(1, start_line or 1) - 1
        e = max(s + 1, end_line or len(body))
        return "\n".join(body[s:e])
    return content


def fetch_tool_output(
    ref: str,
    mode: str = "full",
    lines: int = 200,
    start_line: int | None = None,
    end_line: int | None = None,
    **_kw,
) -> str:
    """Resolve a ``tooloff://`` reference and return JSON with the requested slice."""
    if not ref:
        return json.dumps({"error": "ref is required"})
    try:
        from agent.tool_output_store import fetch as _fetch
    except Exception as exc:  # pragma: no cover — keep tool resilient
        return json.dumps({"error": f"offload store unavailable: {exc}"})

    full = _fetch(ref)
    if full is None:
        return json.dumps({
            "error": "ref not found",
            "hint": (
                "The output may have been evicted by the cleanup job. "
                "Re-run the original tool to regenerate it."
            ),
            "ref": ref,
        })

    sliced = _slice_content(
        full, mode=mode, lines=lines,
        start_line=start_line, end_line=end_line,
    )
    return json.dumps({
        "ref": ref,
        "mode": mode,
        "total_lines": full.count("\n") + 1,
        "total_bytes": len(full.encode("utf-8", errors="replace")),
        "returned_chars": len(sliced),
        "content": sliced,
    })


def check_fetch_requirements() -> tuple[bool, str]:
    """Always available — no external deps."""
    return True, ""


# --- Registry --------------------------------------------------------------
from tools.registry import registry  # noqa: E402

registry.register(
    name="fetch_tool_output",
    toolset="memory",
    schema=_FETCH_SCHEMA,
    handler=lambda args, **kw: fetch_tool_output(
        ref=args.get("ref", ""),
        mode=args.get("mode", "full"),
        lines=args.get("lines", 200),
        start_line=args.get("start_line"),
        end_line=args.get("end_line"),
    ),
    check_fn=check_fetch_requirements,
    emoji="↩",
    description="Expand an offloaded tool output by reference.",
)
