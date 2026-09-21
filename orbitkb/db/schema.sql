-- OrbitKB SQLite schema. Single source of DDL truth.
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
    -- Only meaningful when target_kind = 'external': what kind of resource it is.
    -- Same precedence as target_kind — LLM judgment on real code first, the
    -- deterministic vendor-keyword list (integration_heuristics.classify_resource_type)
    -- only fills gaps left 'unknown'.
    resource_type   TEXT CHECK (resource_type IN ('queue', 'storage', 'compute', 'saas', 'db_managed', 'other', 'not_applicable')) DEFAULT 'not_applicable',
    evidence_json   TEXT,
    updated_at      TEXT NOT NULL
);

-- `engine` is the concrete database (postgres, mysql, mongodb, cassandra, dynamodb,
-- redis, elasticsearch, sqlite), inferred by the LLM the same way messages.provider is —
-- an ORM model/entity definition (SQLAlchemy, JPA, GORM) rarely names its own engine,
-- so this leans on the service's dependency manifest and config files as evidence.
-- 'unknown' means neither resolved it; never a guess.
CREATE TABLE IF NOT EXISTS persistence_entities (
    id            INTEGER PRIMARY KEY,
    service_id    INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    kind          TEXT CHECK (kind IN ('sql_table', 'document', 'cache', 'other')),
    engine        TEXT NOT NULL DEFAULT 'unknown',
    schema_json   TEXT,
    evidence_json TEXT,
    updated_at    TEXT NOT NULL,
    UNIQUE(service_id, name)
);

-- `provider` is the concrete message broker/vendor (kafka, rabbitmq, sqs, sns,
-- service_bus, activemq, nats), inferred by the LLM from real code the same way
-- service_calls.target_kind is — 'unknown' means the code only showed a
-- transport-agnostic abstraction (JMS, Celery, NestJS microservices) and no
-- configuration evidence resolved it; never a guess.
CREATE TABLE IF NOT EXISTS messages (
    id            INTEGER PRIMARY KEY,
    service_id    INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    direction     TEXT NOT NULL CHECK (direction IN ('publishes', 'consumes')),
    channel       TEXT NOT NULL,
    shape_json    TEXT,
    description   TEXT,
    provider      TEXT NOT NULL DEFAULT 'unknown',
    evidence_json TEXT,
    updated_at    TEXT NOT NULL,
    UNIQUE(service_id, direction, channel)
);

-- One row per class/controller/module cluster of endpoints within a service, synthesized
-- from the already-generated `apis` summaries of the endpoints it groups (never raw code
-- read again) — the layer between a single endpoint and the whole service. `file_path` is
-- the file the class/module lives in; `name` falls back to the file's stem when no class
-- wraps the endpoints (the common case for function-based routing, e.g. FastAPI/Flask).
CREATE TABLE IF NOT EXISTS components (
    id            INTEGER PRIMARY KEY,
    service_id    INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    summary       TEXT,
    evidence_json TEXT,
    updated_at    TEXT NOT NULL,
    UNIQUE(service_id, name, file_path)
);

CREATE INDEX IF NOT EXISTS idx_components_service ON components(service_id);

-- Deterministic execution context. These tables intentionally store a bounded
-- entrypoint-to-boundary flow, not an all-purpose code graph. They are populated
-- by local AST analyzers and optionally enriched by a depth provider.
CREATE TABLE IF NOT EXISTS entrypoints (
    id          INTEGER PRIMARY KEY,
    service_id  INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('http', 'graphql', 'message', 'cli', 'job', 'rpc')),
    method      TEXT NOT NULL,
    name        TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    start_line  INTEGER NOT NULL,
    end_line    INTEGER NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(service_id, kind, method, name, symbol)
);

CREATE TABLE IF NOT EXISTS flow_edges (
    id            INTEGER PRIMARY KEY,
    service_id    INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    entrypoint_id INTEGER REFERENCES entrypoints(id) ON DELETE CASCADE,
    from_symbol   TEXT NOT NULL,
    to_symbol     TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('invokes', 'injects', 'validates', 'reads', 'writes', 'publishes', 'consumes')),
    confidence    TEXT NOT NULL CHECK (confidence IN ('high', 'medium', 'low')),
    origin        TEXT NOT NULL CHECK (origin IN ('static', 'codegraph', 'runtime')),
    file_path     TEXT NOT NULL,
    start_line    INTEGER NOT NULL,
    end_line      INTEGER NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_entrypoints_service ON entrypoints(service_id);
CREATE INDEX IF NOT EXISTS idx_flow_edges_service ON flow_edges(service_id);
CREATE INDEX IF NOT EXISTS idx_flow_edges_entrypoint ON flow_edges(entrypoint_id);

CREATE TABLE IF NOT EXISTS entrypoint_contracts (
    entrypoint_id  INTEGER PRIMARY KEY REFERENCES entrypoints(id) ON DELETE CASCADE,
    contract_json  TEXT NOT NULL
);

-- Findings contain only a category, location and remediation guidance. Secret values
-- are deliberately never persisted in the knowledge base.
CREATE TABLE IF NOT EXISTS security_findings (
    id          INTEGER PRIMARY KEY,
    service_id  INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('tracked_dotenv', 'hardcoded_secret')),
    severity    TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error')),
    file_path   TEXT NOT NULL,
    line        INTEGER NOT NULL,
    reason      TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(service_id, kind, file_path, line)
);

CREATE INDEX IF NOT EXISTS idx_security_findings_service ON security_findings(service_id);

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
    id            INTEGER PRIMARY KEY,
    task_text     TEXT NOT NULL,
    backend       TEXT,
    created_at    TEXT NOT NULL,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd      REAL
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
    notes         TEXT,
    -- Best-effort token/cost accounting summed across every unit generated in this
    -- run (see generation.backend_base.LLMUsage) — NULL, never a guess, whenever the
    -- backend's CLI output didn't carry it. See generation/llm_harness.py.
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd      REAL
);

-- Deterministic, whole-graph structural findings (cycles, fan-in/out imbalance, shared
-- database, duplicate external integration) recomputed after every index/update from
-- already-indexed facts alone — no LLM call. See generation/architecture.py. Versioned
-- per run, the same way change_surface_runs is, so findings are comparable over time
-- (e.g. is a monolith's fan-in shrinking as a strangler-fig migration progresses).
CREATE TABLE IF NOT EXISTS architecture_runs (
    id               INTEGER PRIMARY KEY,
    created_at       TEXT NOT NULL,
    services_indexed INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS architecture_findings (
    id            INTEGER PRIMARY KEY,
    run_id        INTEGER NOT NULL REFERENCES architecture_runs(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN ('cycle', 'fan_in', 'fan_out', 'shared_database', 'duplicate_external_integration')),
    severity      TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'critical')) DEFAULT 'info',
    services_json TEXT NOT NULL,
    detail_json   TEXT,
    reason        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_architecture_findings_run ON architecture_findings(run_id);
CREATE INDEX IF NOT EXISTS idx_architecture_findings_kind ON architecture_findings(kind);

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

-- Local, zero-marginal-cost semantic vectors (see generation/embeddings.py) used as
-- a fallback when FTS5 keyword retrieval (search_fts) finds nothing for a task's
-- vocabulary, and to rank similar past find_change_surface tasks. vector_json is a
-- JSON array of floats — brute-force cosine similarity in Python is plenty fast at
-- this project's catalog scale, so no vector-DB dependency or BLOB packing is
-- introduced for it. Both tables are the same "vector storage for X" concern for two
-- different aggregates, read/written by the single db/repositories/embeddings.py.
CREATE TABLE IF NOT EXISTS service_embeddings (
    service_id  INTEGER PRIMARY KEY REFERENCES services(id) ON DELETE CASCADE,
    model_name  TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS change_surface_run_embeddings (
    run_id      INTEGER PRIMARY KEY REFERENCES change_surface_runs(id) ON DELETE CASCADE,
    model_name  TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    updated_at  TEXT NOT NULL
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
