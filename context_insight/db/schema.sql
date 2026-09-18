-- context_insight SQLite schema. Single source of DDL truth.
-- No backward compatibility is maintained across versions: this file is the only
-- shape a database is expected to have. Breaking changes replace old columns/tables
-- outright instead of growing compatibility shims.

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS repositories (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    root_path     TEXT NOT NULL UNIQUE,
    vcs_url       TEXT,
    default_branch TEXT,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS services (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    root_path     TEXT NOT NULL,
    repository_id INTEGER REFERENCES repositories(id) ON DELETE SET NULL,
    stack         TEXT,
    short_desc    TEXT,
    long_desc     TEXT,
    updated_at    TEXT NOT NULL,
    last_commit   TEXT
);

CREATE TABLE IF NOT EXISTS apis (
    id             INTEGER PRIMARY KEY,
    service_id     INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    method         TEXT NOT NULL,
    path           TEXT NOT NULL,
    summary        TEXT,
    description    TEXT,
    response_shape TEXT,
    request_shape  TEXT,
    evidence_json  TEXT,
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
    confidence      REAL,
    target_kind     TEXT CHECK (target_kind IN ('internal', 'external', 'unknown')) DEFAULT 'unknown',
    evidence_json   TEXT,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS persistence_entities (
    id            INTEGER PRIMARY KEY,
    service_id    INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    kind          TEXT CHECK (kind IN ('sql_table', 'document', 'cache', 'other')),
    schema_json   TEXT,
    evidence_json TEXT,
    updated_at    TEXT NOT NULL,
    UNIQUE(service_id, name)
);

CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY,
    service_id    INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    direction     TEXT NOT NULL CHECK (direction IN ('publishes', 'consumes')),
    channel       TEXT NOT NULL,
    shape_json    TEXT,
    description   TEXT,
    evidence_json TEXT,
    updated_at    TEXT NOT NULL,
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

-- Audit trail of find_change_surface calls, and the outcome feedback agents can
-- report back (record_change_surface_feedback), which recalibrate_confidence()
-- folds into future task inferences for that service.
CREATE TABLE IF NOT EXISTS change_surface_runs (
    id         INTEGER PRIMARY KEY,
    task_text  TEXT NOT NULL,
    backend    TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS change_surface_findings (
    id            INTEGER PRIMARY KEY,
    run_id        INTEGER NOT NULL REFERENCES change_surface_runs(id) ON DELETE CASCADE,
    service       TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('primary', 'secondary', 'no_change', 'external_integration', 'unmapped_internal')),
    reason        TEXT,
    confidence    REAL,
    evidence_json TEXT
);

CREATE TABLE IF NOT EXISTS change_surface_feedback (
    id          INTEGER PRIMARY KEY,
    run_id      INTEGER NOT NULL REFERENCES change_surface_runs(id) ON DELETE CASCADE,
    service     TEXT NOT NULL,
    outcome     TEXT NOT NULL CHECK (outcome IN ('confirmed', 'rejected')),
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_change_surface_findings_run ON change_surface_findings(run_id);
CREATE INDEX IF NOT EXISTS idx_change_surface_findings_service ON change_surface_findings(service);
CREATE INDEX IF NOT EXISTS idx_change_surface_feedback_service ON change_surface_feedback(service);

-- Git ground-truth verification of a past find_change_surface run: what was
-- predicted (from change_surface_findings) vs. what actually changed according to
-- `git diff` against a repository, since a given commit. See generation/verification.py.
CREATE TABLE IF NOT EXISTS change_surface_verifications (
    id                    INTEGER PRIMARY KEY,
    run_id                INTEGER NOT NULL REFERENCES change_surface_runs(id) ON DELETE CASCADE,
    repository            TEXT NOT NULL,
    since_commit          TEXT NOT NULL,
    precision             REAL,
    recall                REAL,
    true_positives_json   TEXT NOT NULL,
    false_positives_json  TEXT NOT NULL,
    false_negatives_json  TEXT NOT NULL,
    verified_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_change_surface_verifications_run ON change_surface_verifications(run_id);

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

-- Full-text search index, populated explicitly by repository.rebuild_search_index*
-- (not kept in sync via triggers — every write path in this project already replaces
-- rows in bulk per service, so an explicit rebuild after each service's writes is
-- simpler and cheap at this project's scale). content_text is the only indexed
-- column; the rest are just retrieved back on a match, not searched.
CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
    kind UNINDEXED,
    service UNINDEXED,
    ref UNINDEXED,
    snippet UNINDEXED,
    content_text,
    service_id UNINDEXED
);

CREATE INDEX IF NOT EXISTS idx_apis_service ON apis(service_id);
CREATE INDEX IF NOT EXISTS idx_service_calls_from ON service_calls(from_service_id);
CREATE INDEX IF NOT EXISTS idx_service_calls_to_name ON service_calls(to_service_name);
CREATE INDEX IF NOT EXISTS idx_service_calls_to_id ON service_calls(to_service_id);
CREATE INDEX IF NOT EXISTS idx_persistence_service ON persistence_entities(service_id);
CREATE INDEX IF NOT EXISTS idx_messages_service ON messages(service_id);
CREATE INDEX IF NOT EXISTS idx_messages_channel ON messages(channel);
CREATE INDEX IF NOT EXISTS idx_indexed_files_service ON indexed_files(service_id);
CREATE INDEX IF NOT EXISTS idx_services_repository ON services(repository_id);
