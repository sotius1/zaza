"""AutonomousLoop — the high-level ReAct + Reflexion driver.

Pipeline per turn:

    PERCEIVE   user message in, recall_for_turn auto-injected
    PLAN       draft / update Plan if the request is non-trivial
    ACT        emit tool calls (run by the existing executor in cli.py)
    OBSERVE    record tool outcomes + accumulate metrics
    CRITIQUE   self-critique every N steps, replan if off-track
    DONE       reflexion + memory.learn_from_turn

This module owns the *control flow*; the actual LLM calls and tool
execution live in run_agent.py / cli.py.  The loop is invoked through
a single hook there: ``LoopHooks`` carries callbacks for the four
externally-handled steps (call_llm, run_tool, render, etc).

The loop is opt-in: ``config.yaml::autonomy.enabled`` toggles it.  When
disabled the agent behaves exactly as before; turning it on enables
plans, decision policy, and reflexion.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from agent.autonomy.plan import Plan, PlanStep, load_plan, new_plan, save_plan
from agent.autonomy.policy import (
    Decision,
    DecisionPolicy,
    RiskLevel,
    classify,
)
from agent.autonomy.reflexion import Reflexion

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hook surface — the loop is glue; actual LLM / tool calls happen in cli.py
# ---------------------------------------------------------------------------

@dataclass
class LoopHooks:
    """External callbacks the loop needs.

    The loop is intentionally self-contained otherwise.  Implementations
    plug in via these callbacks so the loop can run inside cli.py
    without dragging in 521 KB of CLI code.
    """
    # Build a Plan draft for a non-trivial user request.  Returns the
    # ordered list of step descriptions; success criteria are filled in
    # by the loop.
    draft_plan: Optional[Callable[[str], List[str]]] = None

    # Critique callback — given the current plan + last observation,
    # returns one of "continue" | "replan" | "done" | "ask".
    critique: Optional[Callable[[Plan, str], str]] = None

    # Render an end-of-turn block to the UI.
    on_turn_end: Optional[Callable[[Dict[str, Any]], None]] = None

    # Surface an "ask user" decision.  Should return user's reply.
    ask_user: Optional[Callable[[str], str]] = None


# ---------------------------------------------------------------------------
# Loop state
# ---------------------------------------------------------------------------

@dataclass
class LoopMetrics:
    started_at: float = field(default_factory=time.monotonic)
    tool_calls: int = 0
    plan_steps_completed: int = 0
    critique_runs: int = 0
    replans: int = 0


# ---------------------------------------------------------------------------
# AutonomousLoop
# ---------------------------------------------------------------------------

class AutonomousLoop:
    """Drives the ReAct + Reflexion pipeline for one turn."""

    def __init__(
        self,
        *,
        session_id: str,
        hooks: Optional[LoopHooks] = None,
        policy: Optional[DecisionPolicy] = None,
        critique_every_n_steps: int = 3,
        max_replans: int = 2,
        auxiliary_client: Any = None,
    ):
        self.session_id = session_id
        self.hooks = hooks or LoopHooks()
        self.policy = policy or DecisionPolicy()
        self.critique_every_n_steps = max(1, int(critique_every_n_steps))
        self.max_replans = max(0, int(max_replans))
        self._aux = auxiliary_client
        self.metrics = LoopMetrics()
        self._reflexion = Reflexion(auxiliary_client=auxiliary_client)
        self._plan: Optional[Plan] = load_plan(session_id)

    # ------------------------------------------------------------------
    # Plan management
    # ------------------------------------------------------------------

    @property
    def plan(self) -> Optional[Plan]:
        return self._plan

    def perceive(self, user_message: str) -> Optional[str]:
        """Hook for the PERCEIVE phase.

        Returns the auto-injected recall block (or empty string).  The
        caller is expected to splice this into the system prompt.
        """
        try:
            from agent.memory import recall_for_turn
            return recall_for_turn(user_message, session_id=self.session_id) or ""
        except Exception:
            logger.debug("perceive: recall failed", exc_info=True)
            return ""

    def maybe_plan(self, user_message: str) -> Optional[Plan]:
        """Decide whether to draft / update a plan for this request.

        Heuristic for "non-trivial":
        * user message is longer than 80 chars and contains conjunction
          words (e.g. "and", "potem", "next") or numbered steps;
        * OR user explicitly asks for a plan;
        * OR an existing plan for the session is still in progress.
        """
        if self._plan and not self._plan.is_complete():
            return self._plan

        if not _looks_non_trivial(user_message):
            return None

        if self.hooks.draft_plan is None:
            # No drafter wired in — fall back to a single-step plan with
            # the goal verbatim.  Lets the rest of the loop still work.
            self._plan = new_plan(self.session_id, goal=user_message[:200])
            self._plan.add_step(
                description=user_message[:300],
                success_criteria="user accepts the result",
            )
        else:
            try:
                steps = list(self.hooks.draft_plan(user_message) or [])
            except Exception:
                logger.exception("draft_plan hook raised")
                steps = []
            if not steps:
                return None
            self._plan = new_plan(self.session_id, goal=user_message[:200])
            for description in steps:
                self._plan.add_step(description=description.strip())

        save_plan(self._plan)
        return self._plan

    def begin_step(self) -> Optional[PlanStep]:
        """Mark the next plan step as in-progress.  Returns it."""
        if self._plan is None:
            return None
        step = self._plan.current_step()
        if step is None:
            return None
        if step.status == "draft":
            step.start()
            save_plan(self._plan)
        return step

    def complete_step(self, *, note: str = "") -> None:
        if self._plan is None:
            return
        step = self._plan.current_step()
        if step is None:
            return
        step.complete(note=note)
        self.metrics.plan_steps_completed += 1
        self._plan.mark_complete_if_done()
        save_plan(self._plan)

    # ------------------------------------------------------------------
    # ACT — gate a tool call through the policy
    # ------------------------------------------------------------------

    def gate_tool_call(
        self,
        *,
        tool_name: str,
        tool_args: Dict[str, Any],
        latest_user_message: str = "",
    ) -> Decision:
        """Resolve whether to execute / ask / refuse a tool invocation."""
        decision = self.policy.decide_for_tool(
            tool_name=tool_name,
            tool_args=tool_args,
            latest_user_message=latest_user_message,
            inside_plan=self._plan is not None and not self._plan.is_complete(),
        )
        self.metrics.tool_calls += 1
        return decision

    def classify_action(self, action: str, *, tool_name: Optional[str] = None) -> RiskLevel:
        return classify(action, tool_name=tool_name)

    # ------------------------------------------------------------------
    # CRITIQUE — periodic self-check
    # ------------------------------------------------------------------

    def maybe_critique(self, last_observation: str = "") -> str:
        """Possibly run a self-critique pass.

        Returns ``"continue" | "replan" | "done" | "ask"``.
        Default: ``"continue"``.
        """
        if self._plan is None:
            return "continue"
        if self.metrics.tool_calls == 0:
            return "continue"
        if self.metrics.tool_calls % self.critique_every_n_steps != 0:
            return "continue"
        if self.hooks.critique is None:
            return "continue"

        self.metrics.critique_runs += 1
        try:
            verdict = self.hooks.critique(self._plan, last_observation) or "continue"
        except Exception:
            logger.exception("critique hook raised")
            return "continue"
        if verdict == "replan" and self.metrics.replans < self.max_replans:
            self.metrics.replans += 1
        return verdict

    # ------------------------------------------------------------------
    # END-OF-TURN — reflexion + memory learning
    # ------------------------------------------------------------------

    def end_turn(
        self,
        *,
        user_message: str,
        assistant_response: str,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
        user_feedback_signal: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run reflexion + memory learning; persist plan; report metrics.

        Safe to call multiple times — idempotent for the plan, the
        memory subsystem dedupes via the consolidator (Phase 6).
        """
        # 1. Reflexion (best-effort)
        try:
            lesson = self._reflexion.run(
                user_message=user_message,
                assistant_response=assistant_response,
                tool_calls=tool_calls,
                user_feedback_signal=user_feedback_signal,
                session_id=self.session_id,
            )
        except Exception:
            logger.exception("reflexion failed")
            lesson = None

        # 2. Memory learning (background)
        try:
            from agent.memory import learn_from_turn
            learn_from_turn(
                user_message=user_message,
                assistant_response=assistant_response,
                tool_calls=tool_calls or [],
                session_id=self.session_id,
                auxiliary_client=self._aux,
                run_in_background=True,
            )
        except Exception:
            logger.exception("memory learn dispatch failed")

        # 3. Persist plan one final time
        if self._plan is not None:
            save_plan(self._plan)

        report = {
            "tool_calls": self.metrics.tool_calls,
            "plan_progress": self._plan.progress() if self._plan else None,
            "critique_runs": self.metrics.critique_runs,
            "replans": self.metrics.replans,
            "lesson": (
                {"what_worked": lesson.what_worked,
                 "what_to_change": lesson.what_to_change}
                if lesson else None
            ),
            "elapsed_s": round(time.monotonic() - self.metrics.started_at, 2),
        }

        if self.hooks.on_turn_end is not None:
            try:
                self.hooks.on_turn_end(report)
            except Exception:
                logger.exception("on_turn_end hook raised")

        return report


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------

_PLAN_TRIGGER_WORDS = {
    "potem", "następnie", "and then", "next", "step", "kroki",
    "zrób", "build", "implement", "create", "stwórz", "rozbuduj",
    "1.", "2.", "3.", "4.", "5.",
}


def _looks_non_trivial(user_msg: str) -> bool:
    if not user_msg:
        return False
    if len(user_msg) > 80:
        return True
    lower = user_msg.lower()
    return any(w in lower for w in _PLAN_TRIGGER_WORDS)
