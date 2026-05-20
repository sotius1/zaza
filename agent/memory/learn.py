"""Public learning entry-point — extract → route in one call.

The agent loop calls ``learn_from_turn(...)`` once per turn (after the
assistant's response is finalised).  Failures are logged and swallowed
— learning is best-effort and must never crash a turn.

Threading model:
* If ``run_in_background`` is True (default), the extraction + routing
  runs in a daemon thread so the user-facing latency is unaffected.
* Otherwise it runs synchronously, useful for tests or when the caller
  wants the RouteResult.

To prevent runaway growth, ``learn_from_turn`` short-circuits when the
turn was empty (no user message, no assistant response).
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from agent.memory.extractor import extract
from agent.memory.router import RouteResult, route

logger = logging.getLogger(__name__)


def learn_from_turn(
    *,
    user_message: str,
    assistant_response: str = "",
    tool_calls: Optional[List[Dict[str, Any]]] = None,
    session_id: Optional[str] = None,
    auxiliary_client: Any = None,
    run_in_background: bool = True,
    on_done: Optional[Callable[[RouteResult], None]] = None,
) -> Optional[RouteResult]:
    """Extract signals from a turn and persist them.

    Returns the RouteResult when run synchronously, or ``None`` when
    backgrounded.  ``on_done`` (if provided) is called with the result
    in either case.
    """
    if not user_message and not assistant_response:
        return None

    def _run() -> RouteResult:
        try:
            signals = extract(
                user_message=user_message,
                assistant_response=assistant_response,
                tool_calls=tool_calls,
                auxiliary_client=auxiliary_client,
            )
            result = route(signals, session_id=session_id)
            logger.info(
                "Memory: learned %d item(s) via %s "
                "(rules=%d facts=%d events=%d profile=%s)",
                result.total(), result.via,
                result.rules_written, result.facts_written, result.events_written,
                "yes" if result.profile_patched else "no",
            )
        except Exception:
            logger.exception("Memory learning failed")
            result = RouteResult(via="error")
        if on_done is not None:
            try:
                on_done(result)
            except Exception:
                logger.exception("on_done callback raised")
        return result

    if not run_in_background:
        return _run()

    t = threading.Thread(
        target=_run,
        name="zaza-memory-learn",
        daemon=True,
    )
    t.start()
    return None
