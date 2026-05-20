-- ZAZA memory schema (Phase 3)
--
-- Single SQLite database stores all four persistent memory layers
-- (core / episodic / semantic / procedural). The core layer also has a
-- JSON pin file outside the DB for fast read-on-startup; the rest live
-- here.

PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- memories: every individual remembered item
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    layer           TEXT NOT NULL,        -- core | episodic | semantic | procedural
    kind            TEXT NOT NULL,        -- preference | fact | event | rule | persona | ...
    content         TEXT NOT NULL,        -- canonical natural-language form
    metadata_json   TEXT NOT NULL DEFAULT '{}',  -- structured payload (JSON)
    importance      REAL NOT NULL DEFAULT 0.5,   -- 0..1, decay-adjusted
    confidence      REAL NOT NULL DEFAULT 0.5,   -- 0..1, source-quality
    embedding       BLOB,                  -- packed float32 vector (LE)
    embedding_dim   INTEGER,               -- dim of the stored embedding
    embedding_model TEXT,                  -- provider name (for re-embed audit)
    source          TEXT,                  -- "user" | "agent" | "extractor" | "consolidator" | ...
    session_id      TEXT,                  -- the session that produced it (nullable)
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_accessed   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    access_count    INTEGER NOT NULL DEFAULT 0,
    archived        INTEGER NOT NULL DEFAULT 0   -- 0=live, 1=consolidated/superseded
);

CREATE INDEX IF NOT EXISTS idx_memories_layer       ON memories(layer);
CREATE INDEX IF NOT EXISTS idx_memories_kind        ON memories(kind);
CREATE INDEX IF NOT EXISTS idx_memories_importance  ON memories(importance DESC);
CREATE INDEX IF NOT EXISTS idx_memories_created     ON memories(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_archived    ON memories(archived);

-- ---------------------------------------------------------------------------
-- memory_links: graph edges between memories (semantic layer + cross-links)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_links (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    src_id          INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    dst_id          INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
    relation        TEXT NOT NULL,        -- "supersedes" | "contradicts" | "supports" | <free>
    strength        REAL NOT NULL DEFAULT 1.0,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(src_id, dst_id, relation)
);

CREATE INDEX IF NOT EXISTS idx_links_src ON memory_links(src_id);
CREATE INDEX IF NOT EXISTS idx_links_dst ON memory_links(dst_id);

-- ---------------------------------------------------------------------------
-- entities: semantic-layer nodes
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    type            TEXT,                 -- "person" | "project" | "tool" | "language" | <free>
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    first_seen      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    mentions        INTEGER NOT NULL DEFAULT 1,
    UNIQUE(name, type)
);

CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);

-- ---------------------------------------------------------------------------
-- relations: typed edges between entities
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS relations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    src_entity      INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    dst_entity      INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    predicate       TEXT NOT NULL,        -- "uses" | "owns" | "knows" | <free>
    confidence      REAL NOT NULL DEFAULT 0.5,
    source_memory   INTEGER REFERENCES memories(id) ON DELETE SET NULL,
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(src_entity, dst_entity, predicate)
);

CREATE INDEX IF NOT EXISTS idx_relations_src ON relations(src_entity);
CREATE INDEX IF NOT EXISTS idx_relations_dst ON relations(dst_entity);

-- ---------------------------------------------------------------------------
-- session_summaries: rolling summaries written by the consolidator
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS session_summaries (
    session_id      TEXT PRIMARY KEY,
    summary         TEXT NOT NULL,
    turn_count      INTEGER NOT NULL DEFAULT 0,
    started_at      TIMESTAMP,
    ended_at        TIMESTAMP,
    metadata_json   TEXT NOT NULL DEFAULT '{}'
);

-- ---------------------------------------------------------------------------
-- Schema version & migration metadata
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memory_meta (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO memory_meta(key, value) VALUES ('schema_version', '1');
