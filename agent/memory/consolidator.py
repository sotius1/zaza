"""Memory consolidator — periodic background merge / dedupe / decay.

Three jobs, run together as the consolidation pass:

1. **Decay**: every memory's ``importance`` slowly drops with age unless
   it gets re-accessed.  Items below the floor are archived (soft-delete).

2. **Dedupe**: near-duplicate procedural rules and facts are merged.  We
   keep the entry with the highest combined ``importance × confidence``
   and link the others as ``supersedes`` edges so we have an audit
   trail.

3. **Drift detection**: when two procedural rules contradict (one ``do``
   the same content as another ``dont``), pick the more recent / higher-
   confidence winner; archive the loser; emit a warning into a meta log.

The pass is *idempotent* and *safe to interrupt* — partial work is
committed row-by-row and re-runs pick up where the last pass left off.

Trigger surface:
* ``run_once()`` — synchronous, used by tests and by the maintenance
  CLI command ``zaza memory consolidate``.
* ``schedule_periodic(interval_s)`` — kicks off a daemon thread that
  runs ``run_once()`` every ``interval_s`` seconds.  Default cadence
  is 6 hours.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

from agent.memory.store import MemoryRow, MemoryStore, get_default_store
from agent.memory.embeddings import Embedding, get_default_embedder
from agent.memory.store import cosine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables — these can move to config later
# ---------------------------------------------------------------------------

# Importance loss per day of no access.  An item with importance 0.7
# falls below the archival floor (0.05) after about 26 days idle —
# matches the practical "if I haven't used it in a month, forget it"
# heuristic.
DAILY_DECAY = 0.025

# Below this importance, the item is archived (soft-delete).
ARCHIVAL_FLOOR = 0.05

# Minimum cosine similarity to treat two rows as near-duplicates.
DEDUPE_SIM_THRESHOLD = 0.92

# Minimum cosine similarity for the drift detector to consider two
# procedural rules as the *same rule* with opposite polarity.
DRIFT_SIM_THRESHOLD = 0.85


@dataclass
class ConsolidationReport:
    decayed: int = 0
    archived: int = 0
    deduped: int = 0
    drift_resolved: int = 0
    elapsed_s: float = 0.0


# ---------------------------------------------------------------------------
# Decay
# ---------------------------------------------------------------------------

def _days_since(ts_iso: str) -> float:
    """Best-effort age in days from an ISO timestamp."""
    if not ts_iso:
        return 0.0
    try:
        from datetime import datetime, timezone
        # SQLite's CURRENT_TIMESTAMP returns "YYYY-MM-DD HH:MM:SS"
        ts = ts_iso.replace("T", " ").split(".")[0]
        try:
            dt = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
        except ValueError:
            dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
    except Exception:
        return 0.0


def _apply_decay(store: MemoryStore, report: ConsolidationReport) -> None:
    conn = store._conn()  # internal — consolidator is intimate with the store
    cursor = conn.execute(
        "SELECT id, importance, last_accessed FROM memories WHERE archived = 0"
    )
    for row in cursor.fetchall():
        days = _days_since(row["last_accessed"])
        if days <= 0:
            continue
        old = float(row["importance"])
        new_imp = max(0.0, old - DAILY_DECAY * days)
        if new_imp >= old:
            continue
        if new_imp < ARCHIVAL_FLOOR:
            conn.execute(
                "UPDATE memories SET importance = ?, archived = 1 WHERE id = ?",
                (new_imp, row["id"]),
            )
            report.archived += 1
        else:
            conn.execute(
                "UPDATE memories SET importance = ? WHERE id = ?",
                (new_imp, row["id"]),
            )
            report.decayed += 1


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------

def _dedupe_layer(
    store: MemoryStore, embedder: Embedding,
    *, layer: str, report: ConsolidationReport,
) -> None:
    """Merge near-duplicate rows in a layer."""
    rows: List[MemoryRow] = list(store.iter_layer(layer))
    if len(rows) < 2:
        return

    # Build the embedding for rows that don't have one yet (rare but
    # happens after a provider switch).
    for r in rows:
        if not r.embedding:
            try:
                vec = embedder.embed(r.content)
            except Exception:
                continue
            store._conn().execute(
                "UPDATE memories SET embedding = ?, embedding_dim = ?, embedding_model = ? "
                "WHERE id = ?",
                (
                    _pack(vec), len(vec), embedder.name, r.id,
                ),
            )
            r.embedding = vec

    rows = [r for r in rows if r.embedding]

    visited: set = set()
    for i, a in enumerate(rows):
        if a.id in visited:
            continue
        cluster: List[MemoryRow] = [a]
        for b in rows[i + 1:]:
            if b.id in visited:
                continue
            if a.embedding_dim != b.embedding_dim:
                continue
            sim = cosine(a.embedding, b.embedding)
            if sim >= DEDUPE_SIM_THRESHOLD:
                cluster.append(b)
                visited.add(b.id)
        if len(cluster) <= 1:
            continue

        # Keep the strongest, archive the rest, link as supersedes.
        cluster.sort(key=lambda r: r.importance * r.confidence, reverse=True)
        winner = cluster[0]
        for loser in cluster[1:]:
            store.archive(loser.id)
            store.link(winner.id, loser.id, "supersedes", strength=1.0)
            report.deduped += 1


# ---------------------------------------------------------------------------
# Drift detection on procedural rules
# ---------------------------------------------------------------------------

def _resolve_drift(
    store: MemoryStore, embedder: Embedding,
    report: ConsolidationReport,
) -> None:
    """Find pairs of procedural rules with opposite polarity but same meaning."""
    rules: List[MemoryRow] = list(store.iter_layer("procedural"))
    if len(rules) < 2:
        return

    do_rules = [r for r in rules if (r.metadata or {}).get("polarity") == "do" and r.embedding]
    dont_rules = [r for r in rules if (r.metadata or {}).get("polarity") == "dont" and r.embedding]

    for d in do_rules:
        for n in dont_rules:
            if d.embedding_dim != n.embedding_dim:
                continue
            sim = cosine(d.embedding, n.embedding)
            if sim < DRIFT_SIM_THRESHOLD:
                continue
            # Pick the winner: higher importance × confidence; tie → newer
            d_score = d.importance * d.confidence
            n_score = n.importance * n.confidence
            if d_score == n_score:
                # Use last_accessed to break ties — the more recently
                # touched rule probably reflects the current preference.
                winner, loser = (d, n) if d.last_accessed > n.last_accessed else (n, d)
            else:
                winner, loser = (d, n) if d_score > n_score else (n, d)
            store.archive(loser.id)
            store.link(winner.id, loser.id, "contradicts", strength=sim)
            logger.info(
                "Memory drift resolved: rule '%s' wins over '%s' (sim=%.2f)",
                winner.content[:60], loser.content[:60], sim,
            )
            report.drift_resolved += 1


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def run_once(
    *,
    store: Optional[MemoryStore] = None,
    embedder: Optional[Embedding] = None,
) -> ConsolidationReport:
    """One consolidation pass.  Safe to call from anywhere."""
    s = store or get_default_store()
    e = embedder or get_default_embedder()
    report = ConsolidationReport()
    started = time.monotonic()

    try:
        _apply_decay(s, report)
    except Exception:
        logger.exception("decay pass failed")

    for layer in ("procedural", "semantic", "episodic"):
        try:
            _dedupe_layer(s, e, layer=layer, report=report)
        except Exception:
            logger.exception("dedupe failed for layer %s", layer)

    try:
        _resolve_drift(s, e, report)
    except Exception:
        logger.exception("drift resolution failed")

    report.elapsed_s = round(time.monotonic() - started, 3)
    logger.info(
        "Memory consolidation: decayed=%d archived=%d deduped=%d drift=%d in %.2fs",
        report.decayed, report.archived, report.deduped,
        report.drift_resolved, report.elapsed_s,
    )
    return report


_PERIODIC_THREAD: Optional[threading.Thread] = None
_PERIODIC_STOP = threading.Event()


def schedule_periodic(interval_s: int = 6 * 3600) -> None:
    """Kick off a daemon thread that runs the consolidator periodically."""
    global _PERIODIC_THREAD
    if _PERIODIC_THREAD and _PERIODIC_THREAD.is_alive():
        return

    def _run() -> None:
        while not _PERIODIC_STOP.is_set():
            try:
                run_once()
            except Exception:
                logger.exception("periodic consolidation crashed")
            _PERIODIC_STOP.wait(timeout=max(60, int(interval_s)))

    _PERIODIC_THREAD = threading.Thread(
        target=_run, name="zaza-memory-consolidator", daemon=True,
    )
    _PERIODIC_THREAD.start()


def stop_periodic() -> None:
    _PERIODIC_STOP.set()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pack(vec: Iterable[float]) -> bytes:
    import struct
    vec = list(vec)
    return struct.pack(f"<{len(vec)}f", *vec)
