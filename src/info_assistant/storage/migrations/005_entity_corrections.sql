CREATE TABLE entity_corrections (
    id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    value TEXT NOT NULL,
    previous_value TEXT,
    created_at TEXT NOT NULL,
    reverted_at TEXT
);

CREATE INDEX idx_entity_corrections_active
ON entity_corrections(entity_id, field_name, reverted_at, created_at DESC);
