CREATE TABLE run_source_states (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    canonical_url TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'fetching', 'paused', 'fetched', 'failed', 'unknown')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_url TEXT,
    error_code TEXT,
    error_message TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, canonical_url)
);

CREATE INDEX idx_run_source_states_status ON run_source_states(run_id, status);
