PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_path TEXT NOT NULL,
    document_hash TEXT NOT NULL UNIQUE,
    sector TEXT NOT NULL DEFAULT 'inconnu',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_objects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    object_id INTEGER,
    element_type TEXT NOT NULL,
    tag TEXT NOT NULL,
    type_code TEXT NOT NULL DEFAULT '',
    internal_id INTEGER,
    value TEXT,
    extra_text TEXT,
    position_x INTEGER,
    position_y INTEGER,
    child_count INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    chunk_hash TEXT NOT NULL UNIQUE,
    content TEXT NOT NULL,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_plan_objects_document_id
    ON plan_objects(document_id);

CREATE INDEX IF NOT EXISTS idx_plan_objects_type_code
    ON plan_objects(type_code);

CREATE INDEX IF NOT EXISTS idx_chunks_document_id
    ON chunks(document_id);
