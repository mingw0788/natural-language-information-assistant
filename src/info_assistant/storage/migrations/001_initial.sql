CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    latest_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE task_versions (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    version INTEGER NOT NULL CHECK (version >= 1),
    spec_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(task_id, version)
);

CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    version_id TEXT NOT NULL REFERENCES task_versions(id) ON DELETE RESTRICT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    active_seconds INTEGER NOT NULL DEFAULT 0 CHECK (active_seconds >= 0),
    stop_reason TEXT,
    UNIQUE(id, version_id)
);

CREATE TABLE run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE sources (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    last_checked_at TEXT,
    error_code TEXT,
    error_message TEXT
);

CREATE TABLE source_snapshots (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    fetched_at TEXT NOT NULL,
    content_type TEXT NOT NULL,
    text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    http_status INTEGER,
    page_count INTEGER,
    UNIQUE(source_id, run_id, content_hash)
);

CREATE TABLE entities (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    canonical_name TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE(task_id, identity_key)
);

CREATE TABLE observations (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    raw_value TEXT,
    normalized_value TEXT,
    unit TEXT,
    period TEXT,
    qualifiers_json TEXT NOT NULL DEFAULT '[]',
    origin TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence REAL,
    observed_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT NOT NULL UNIQUE
);

CREATE TABLE observation_evidence (
    observation_id TEXT NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    snapshot_id TEXT NOT NULL REFERENCES source_snapshots(id) ON DELETE RESTRICT,
    start_offset INTEGER NOT NULL CHECK (start_offset >= 0),
    end_offset INTEGER NOT NULL CHECK (end_offset > start_offset),
    quote TEXT NOT NULL,
    page_number INTEGER,
    locator TEXT,
    PRIMARY KEY(observation_id, snapshot_id, start_offset, end_offset)
);

CREATE INDEX idx_versions_task ON task_versions(task_id, version DESC);
CREATE INDEX idx_runs_task_created ON runs(task_id, created_at DESC);
CREATE INDEX idx_events_run_id ON run_events(run_id, id);
CREATE INDEX idx_snapshots_fetched ON source_snapshots(fetched_at);
CREATE INDEX idx_observations_task ON observations(task_id, run_id);
