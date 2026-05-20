"""Plan / PlanStep — persisted multi-step plan structure.

The autonomous loop drafts a Plan when it receives a non-trivial user
request (anything past one-step Q&A).  The plan persists between turns
so the agent (and the user, via the status bar) can see where execution
stands.

Persisted to ``$ZAZA_HOME/data/plans/<session_id>.json`` so a restarted
session can resume.  The format is intentionally JSON-only — no SQL —
because the read path is "load whole plan, mutate, write back" and the
plan is small (<32 KB).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class PlanStatus(str, Enum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    ABANDONED = "abandoned"


@dataclass
class PlanStep:
    id: str
    description: str
    success_criteria: str = ""
    risks: str = ""
    status: str = PlanStatus.DRAFT.value
    notes: List[str] = field(default_factory=list)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    artefacts: List[str] = field(default_factory=list)

    def start(self) -> None:
        self.status = PlanStatus.IN_PROGRESS.value
        self.started_at = time.time()

    def complete(self, *, note: str = "") -> None:
        self.status = PlanStatus.DONE.value
        self.finished_at = time.time()
        if note:
            self.notes.append(note)

    def block(self, reason: str) -> None:
        self.status = PlanStatus.BLOCKED.value
        self.notes.append(f"blocked: {reason}")

    def is_open(self) -> bool:
        return self.status in (
            PlanStatus.DRAFT.value,
            PlanStatus.IN_PROGRESS.value,
            PlanStatus.BLOCKED.value,
        )


@dataclass
class Plan:
    """Plan associated with a single user request inside a session."""
    id: str
    session_id: str
    goal: str
    steps: List[PlanStep] = field(default_factory=list)
    status: str = PlanStatus.DRAFT.value
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Step helpers
    # ------------------------------------------------------------------

    def add_step(self, description: str, *, success_criteria: str = "",
                 risks: str = "") -> PlanStep:
        step = PlanStep(
            id=str(uuid.uuid4()),
            description=description,
            success_criteria=success_criteria,
            risks=risks,
        )
        self.steps.append(step)
        self.updated_at = time.time()
        return step

    def current_step(self) -> Optional[PlanStep]:
        """First step that's not DONE/ABANDONED."""
        for s in self.steps:
            if s.is_open():
                return s
        return None

    def progress(self) -> Dict[str, Any]:
        total = len(self.steps)
        done = sum(1 for s in self.steps if s.status == PlanStatus.DONE.value)
        blocked = sum(1 for s in self.steps if s.status == PlanStatus.BLOCKED.value)
        return {
            "total": total,
            "done": done,
            "blocked": blocked,
            "percent": int(100 * done / total) if total else 0,
        }

    def is_complete(self) -> bool:
        return all(
            s.status in (PlanStatus.DONE.value, PlanStatus.ABANDONED.value)
            for s in self.steps
        )

    def mark_complete_if_done(self) -> None:
        if self.is_complete():
            self.status = PlanStatus.DONE.value
            self.updated_at = time.time()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def to_json(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_json(cls, data: Dict[str, Any]) -> "Plan":
        steps = [PlanStep(**s) for s in (data.get("steps") or [])]
        return cls(
            id=data["id"],
            session_id=data["session_id"],
            goal=data["goal"],
            steps=steps,
            status=data.get("status", PlanStatus.DRAFT.value),
            created_at=data.get("created_at") or time.time(),
            updated_at=data.get("updated_at") or time.time(),
            metadata=data.get("metadata") or {},
        )

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def render_summary(self) -> str:
        """Compact human-readable summary for the system prompt."""
        if not self.steps:
            return f"Plan: {self.goal} (no steps yet)"

        lines = [f"Plan: {self.goal}"]
        for i, step in enumerate(self.steps, 1):
            mark = {
                PlanStatus.DRAFT.value: "·",
                PlanStatus.IN_PROGRESS.value: "▶",
                PlanStatus.BLOCKED.value: "!",
                PlanStatus.DONE.value: "✓",
                PlanStatus.ABANDONED.value: "—",
            }.get(step.status, "·")
            lines.append(f"  {mark} {i}. {step.description}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Persistence layer
# ---------------------------------------------------------------------------

def _plans_dir() -> Path:
    try:
        from zaza_constants import get_zaza_home
        base = get_zaza_home() / "data" / "plans"
    except Exception:
        base = Path.home() / ".agent-zaza" / "data" / "plans"
    base.mkdir(parents=True, exist_ok=True)
    return base


def save_plan(plan: Plan) -> Path:
    plan.updated_at = time.time()
    path = _plans_dir() / f"{plan.session_id}.json"
    path.write_text(
        json.dumps(plan.to_json(), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return path


def load_plan(session_id: str) -> Optional[Plan]:
    path = _plans_dir() / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        return Plan.from_json(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        logger.exception("Failed to load plan for session %s", session_id)
        return None


def new_plan(session_id: str, goal: str) -> Plan:
    plan = Plan(id=str(uuid.uuid4()), session_id=session_id, goal=goal)
    save_plan(plan)
    return plan
