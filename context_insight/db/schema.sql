-- context_insight SQLite schema. Single source of DDL truth.

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS services (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    root_path   TEXT NOT NULL,
    stack       TEXT,
    short_desc  TEXT,
    long_desc   TEXT,
    updated_at  TEXT NOT NULL,
    last_commit TEXT
);

CREATE TABLE IF NOT EXISTS apis (
    id             INTEGER PRIMARY KEY,
    service_id     INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    method         TEXT NOT NULL,
    path           TEXT NOT NULL,
    summary        TEXT,
    description    TEXT,
    response_shape TEXT,
    source_files   TEXT,
    updated_at     TEXT NOT NULL,
    UNIQUE(service_id, method, path)
);

CREATE TABLE IF NOT EXISTS api_validations (
    id          INTEGER PRIMARY KEY,
    api_id      INTEGER NOT NULL REFERENCES apis(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('input_validation', 'authorization')),
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS service_calls (
    id              INTEGER PRIMARY KEY,
    from_service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    from_api_id     INTEGER REFERENCES apis(id) ON DELETE CASCADE,
    to_service_name TEXT NOT NULL,
    to_service_id   INTEGER REFERENCES services(id) ON DELETE SET NULL,
    call_kind       TEXT NOT NULL CHECK (call_kind IN ('http', 'grpc', 'queue_publish', 'queue_consume')),
    reason          TEXT,
    data_needed     TEXT,
    purpose_kind    TEXT CHECK (purpose_kind IN ('validation', 'data_fetch', 'enrichment', 'notification', 'other')),
    source_files    TEXT,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS persistence_entities (
    id           INTEGER PRIMARY KEY,
    service_id   INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    kind         TEXT CHECK (kind IN ('sql_table', 'document', 'cache', 'other')),
    schema_json  TEXT,
    source_files TEXT,
    updated_at   TEXT NOT NULL,
    UNIQUE(service_id, name)
);

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY,
    service_id   INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    direction    TEXT NOT NULL CHECK (direction IN ('publishes', 'consumes')),
    channel      TEXT NOT NULL,
    shape_json   TEXT,
    description  TEXT,
    source_files TEXT,
    updated_at   TEXT NOT NULL,
    UNIQUE(service_id, direction, channel)
);

CREATE TABLE IF NOT EXISTS indexed_files (
    id              INTEGER PRIMARY KEY,
    service_id      INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    file_path       TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    category        TEXT,
    last_indexed_at TEXT NOT NULL,
    UNIQUE(service_id, file_path)
);

CREATE TABLE IF NOT EXISTS index_runs (
    id            INTEGER PRIMARY KEY,
    service_id    INTEGER REFERENCES services(id) ON DELETE SET NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT CHECK (status IN ('ok', 'partial', 'failed')),
    backend       TEXT,
    files_changed INTEGER DEFAULT 0,
    llm_calls     INTEGER DEFAULT 0,
    notes         TEXT
);

CREATE INDEX IF NOT EXISTS idx_apis_service ON apis(service_id);
CREATE INDEX IF NOT EXISTS idx_service_calls_from ON service_calls(from_service_id);
CREATE INDEX IF NOT EXISTS idx_service_calls_to_name ON service_calls(to_service_name);
CREATE INDEX IF NOT EXISTS idx_persistence_service ON persistence_entities(service_id);
CREATE INDEX IF NOT EXISTS idx_messages_service ON messages(service_id);
CREATE INDEX IF NOT EXISTS idx_indexed_files_service ON indexed_files(service_id);
