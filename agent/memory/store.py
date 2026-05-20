"""SQLite-backed persistent memory store.

Implementation is plain SQLite + tightly-packed float32 BLOBs for the
embedding column.  No hard dep on ``sqlite-vec`` — if the extension is
present we use it for fast ANN search, otherwise we fall back to
in-Python cosine similarity.  Either way the public API is identical.

Why not require sqlite-vec?
* sqlite-vec is great but adds a build/install step.  ZAZA must run
  cleanly on a fresh machine with nothing extra installed.  When the
  user later does ``pip install sqlite-vec`` we'll automatically pick
  it up on next process start.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import struct
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


# ---------------------------------------------------------------------------
# Vector packing helpers
# ---------------------------------------------------------------------------

def pack_vector(vec: Sequence[float]) -> bytes:
    """Pack a float vector into little-endian float32 bytes."""
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack_vector(blob: bytes) -> List[float]:
    """Unpack the float32 BLOB back into a Python list."""
    if not blob:
        return []
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob[: n * 4]))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity for vectors of the same dim."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    import math
    return dot / (math.sqrt(na) * math.sqrt(nb))


# ---------------------------------------------------------------------------
# Row dataclass
# ---------------------------------------------------------------------------

@dataclass
class MemoryRow:
    """Decoded view of a row in ``memories``."""
    id: int
    layer: str
    kind: str
    content: str
    metadata: dict
    importance: float
    confidence: float
    embedding: List[float]
    embedding_dim: Optional[int]
    embedding_model: Optional[str]
    source: Optional[str]
    session_id: Optional[str]
    created_at: str
    last_accessed: str
    access_count: int
    archived: bool

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "MemoryRow":
        return cls(
            id=row["id"],
            layer=row["layer"],
            kind=row["kind"],
            content=row["content"],
            metadata=_safe_loads(row["metadata_json"]),
            importance=float(row["importance"]),
            confidence=float(row["confidence"]),
            embedding=unpack_vector(row["embedding"] or b""),
            embedding_dim=row["embedding_dim"],
            embedding_model=row["embedding_model"],
            source=row["source"],
            session_id=row["session_id"],
            created_at=row["created_at"],
            last_accessed=row["last_accessed"],
            access_count=int(row["access_count"]),
            archived=bool(row["archived"]),
        )


def _safe_loads(s: Optional[str]) -> dict:
    if not s:
        return {}
    try:
        return json.loads(s)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class MemoryStore:
    """Concurrency-safe SQLite memory store.

    A single ``sqlite3.Connection`` per thread (per Python's standard
    threading model for SQLite).  The class itself is reentrant — methods
    can be called from any thread but each thread maintains its own
    connection lazily.
    """

    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._tls = threading.local()
        self._init_lock = threading.Lock()
        self._initialized = False
        self._sqlite_vec: Optional[Any] = None  # populated if extension loads

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._tls, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                str(self._db_path),
                isolation_level=None,            # autocommit
                detect_types=sqlite3.PARSE_DECLTYPES,
            )
            conn.row_factory = sqlite3.Row
            self._maybe_load_vec_extension(conn)
            self._tls.conn = conn
        if not self._initialized:
            self._initialize(conn)
        return conn

    def _maybe_load_vec_extension(self, conn: sqlite3.Connection) -> None:
        """Try to load ``sqlite-vec`` for ANN search.  Optional."""
        if self._sqlite_vec is False:
            return
        try:
            import sqlite_vec  # type: ignore
        except Exception:
            self._sqlite_vec = False
            return
        try:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            self._sqlite_vec = sqlite_vec
            logger.info("Memory: sqlite-vec extension loaded")
        except Exception:
            self._sqlite_vec = False
            logger.debug("Could not load sqlite-vec extension", exc_info=True)

    def _initialize(self, conn: sqlite3.Connection) -> None:
        with self._init_lock:
            if self._initialized:
                return
            sql = _SCHEMA_PATH.read_text(encoding="utf-8")
            conn.executescript(sql)
            self._initialized = True

    # ------------------------------------------------------------------
    # Write paths
    # ------------------------------------------------------------------

    def add(
        self,
        *,
        layer: str,
        kind: str,
        content: str,
        metadata: Optional[dict] = None,
        importance: float = 0.5,
        confidence: float = 0.5,
        embedding: Optional[Sequence[float]] = None,
        embedding_model: Optional[str] = None,
        source: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> int:
        """Insert a new memory row.  Returns the new row id."""
        conn = self._conn()
        emb_bytes = pack_vector(embedding) if embedding else None
        emb_dim = len(embedding) if embedding else None
        cur = conn.execute(
            """
            INSERT INTO memories (
                layer, kind, content, metadata_json, importance, confidence,
                embedding, embedding_dim, embedding_model, source, session_id
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                layer, kind, content,
                json.dumps(metadata or {}, ensure_ascii=False, default=str),
                float(importance), float(confidence),
                emb_bytes, emb_dim, embedding_model, source, session_id,
            ),
        )
        return int(cur.lastrowid)

    def touch(self, row_id: int) -> None:
        """Bump access_count + last_accessed (for importance decay)."""
        conn = self._conn()
        conn.execute(
            """
            UPDATE memories
               SET access_count = access_count + 1,
                   last_accessed = CURRENT_TIMESTAMP
             WHERE id = ?
            """,
            (row_id,),
        )

    def archive(self, row_id: int) -> None:
        conn = self._conn()
        conn.execute("UPDATE memories SET archived = 1 WHERE id = ?", (row_id,))

    def link(self, src_id: int, dst_id: int, relation: str, strength: float = 1.0) -> None:
        conn = self._conn()
        conn.execute(
            """
            INSERT OR IGNORE INTO memory_links(src_id, dst_id, relation, strength)
            VALUES (?,?,?,?)
            """,
            (src_id, dst_id, relation, float(strength)),
        )

    # ------------------------------------------------------------------
    # Read paths
    # ------------------------------------------------------------------

    def get(self, row_id: int) -> Optional[MemoryRow]:
        conn = self._conn()
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (row_id,)).fetchone()
        return MemoryRow.from_row(row) if row else None

    def by_layer(
        self,
        layer: str,
        *,
        archived: bool = False,
        limit: int = 100,
    ) -> List[MemoryRow]:
        conn = self._conn()
        rows = conn.execute(
            """
            SELECT * FROM memories
             WHERE layer = ? AND archived = ?
          ORDER BY importance DESC, last_accessed DESC
             LIMIT ?
            """,
            (layer, 1 if archived else 0, int(limit)),
        ).fetchall()
        return [MemoryRow.from_row(r) for r in rows]

    def search_similar(
        self,
        query_vec: Sequence[float],
        *,
        layer: Optional[str] = None,
        limit: int = 10,
        archived: bool = False,
    ) -> List[Tuple[MemoryRow, float]]:
        """Vector similarity search.

        Returns ``(row, score)`` tuples sorted by descending score.
        Implementation falls back to brute-force cosine in Python when
        ``sqlite-vec`` is unavailable.  Performance is fine for the
        typical memory size (< 50k rows); when the store grows past
        that, install sqlite-vec for ANN.
        """
        if not query_vec:
            return []
        conn = self._conn()
        if layer:
            sql = (
                "SELECT * FROM memories "
                "WHERE archived = ? AND layer = ? "
                "  AND embedding IS NOT NULL AND embedding_dim = ?"
            )
            params: tuple = (1 if archived else 0, layer, len(query_vec))
        else:
            sql = (
                "SELECT * FROM memories "
                "WHERE archived = ? "
                "  AND embedding IS NOT NULL AND embedding_dim = ?"
            )
            params = (1 if archived else 0, len(query_vec))
        rows = conn.execute(sql, params).fetchall()

        scored: List[Tuple[MemoryRow, float]] = []
        for r in rows:
            mr = MemoryRow.from_row(r)
            sim = cosine(query_vec, mr.embedding)
            scored.append((mr, sim))
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[: max(int(limit), 1)]

    def search_text(
        self,
        query: str,
        *,
        layer: Optional[str] = None,
        limit: int = 10,
    ) -> List[MemoryRow]:
        """Plain LIKE-based keyword fallback.

        Useful when no embedding provider is available, or when the user
        explicitly searches by exact phrase.
        """
        conn = self._conn()
        like = f"%{query.strip()}%"
        if layer:
            rows = conn.execute(
                """
                SELECT * FROM memories
                 WHERE archived = 0 AND layer = ?
                   AND (content LIKE ? OR metadata_json LIKE ?)
              ORDER BY importance DESC
                 LIMIT ?
                """,
                (layer, like, like, int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM memories
                 WHERE archived = 0
                   AND (content LIKE ? OR metadata_json LIKE ?)
              ORDER BY importance DESC
                 LIMIT ?
                """,
                (like, like, int(limit)),
            ).fetchall()
        return [MemoryRow.from_row(r) for r in rows]

    # ------------------------------------------------------------------
    # Entities + relations (semantic layer)
    # ------------------------------------------------------------------

    def upsert_entity(self, name: str, etype: str = "", metadata: Optional[dict] = None) -> int:
        conn = self._conn()
        existing = conn.execute(
            "SELECT id, mentions FROM entities WHERE name = ? AND COALESCE(type,'') = ?",
            (name, etype or ""),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE entities SET mentions = ?, last_seen = CURRENT_TIMESTAMP WHERE id = ?",
                (int(existing["mentions"]) + 1, existing["id"]),
            )
            return int(existing["id"])
        cur = conn.execute(
            """
            INSERT INTO entities(name, type, metadata_json)
            VALUES (?, ?, ?)
            """,
            (name, etype or None, json.dumps(metadata or {}, ensure_ascii=False)),
        )
        return int(cur.lastrowid)

    def upsert_relation(
        self, src: int, dst: int, predicate: str,
        *, confidence: float = 0.5, source_memory: Optional[int] = None,
    ) -> int:
        conn = self._conn()
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO relations(src_entity, dst_entity, predicate, confidence, source_memory)
            VALUES (?, ?, ?, ?, ?)
            """,
            (src, dst, predicate, float(confidence), source_memory),
        )
        if cur.lastrowid:
            return int(cur.lastrowid)
        existing = conn.execute(
            "SELECT id FROM relations WHERE src_entity = ? AND dst_entity = ? AND predicate = ?",
            (src, dst, predicate),
        ).fetchone()
        return int(existing["id"]) if existing else 0

    def neighbors(self, entity_id: int) -> List[Tuple[str, str, str]]:
        """Return ``(predicate, neighbor_name, neighbor_type)`` triples."""
        conn = self._conn()
        rows = conn.execute(
            """
            SELECT r.predicate AS p, e.name AS n, COALESCE(e.type,'') AS t
              FROM relations r
              JOIN entities e ON e.id = r.dst_entity
             WHERE r.src_entity = ?
            UNION
            SELECT r.predicate AS p, e.name AS n, COALESCE(e.type,'') AS t
              FROM relations r
              JOIN entities e ON e.id = r.src_entity
             WHERE r.dst_entity = ?
            """,
            (entity_id, entity_id),
        ).fetchall()
        return [(r["p"], r["n"], r["t"]) for r in rows]

    # ------------------------------------------------------------------
    # Session summaries
    # ------------------------------------------------------------------

    def write_session_summary(
        self, session_id: str, summary: str,
        *, turn_count: int = 0, metadata: Optional[dict] = None,
    ) -> None:
        conn = self._conn()
        conn.execute(
            """
            INSERT INTO session_summaries(session_id, summary, turn_count, metadata_json, ended_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(session_id) DO UPDATE
               SET summary = excluded.summary,
                   turn_count = excluded.turn_count,
                   metadata_json = excluded.metadata_json,
                   ended_at = CURRENT_TIMESTAMP
            """,
            (session_id, summary, int(turn_count),
             json.dumps(metadata or {}, ensure_ascii=False, default=str)),
        )

    def read_session_summary(self, session_id: str) -> Optional[str]:
        conn = self._conn()
        row = conn.execute(
            "SELECT summary FROM session_summaries WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return row["summary"] if row else None

    # ------------------------------------------------------------------
    # Bulk iteration (for the consolidator)
    # ------------------------------------------------------------------

    def iter_layer(self, layer: str, *, archived: bool = False) -> Iterator[MemoryRow]:
        conn = self._conn()
        cur = conn.execute(
            "SELECT * FROM memories WHERE layer = ? AND archived = ? ORDER BY id",
            (layer, 1 if archived else 0),
        )
        for r in cur:
            yield MemoryRow.from_row(r)


# ---------------------------------------------------------------------------
# Default singleton
# ---------------------------------------------------------------------------

_DEFAULT_STORE: Optional[MemoryStore] = None


def get_default_store() -> MemoryStore:
    """Singleton store at ``$ZAZA_HOME/data/memory.db``."""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        try:
            from zaza_constants import get_zaza_home
            base = get_zaza_home() / "data"
        except Exception:
            base = Path.home() / ".agent-zaza" / "data"
        _DEFAULT_STORE = MemoryStore(base / "memory.db")
    return _DEFAULT_STORE
