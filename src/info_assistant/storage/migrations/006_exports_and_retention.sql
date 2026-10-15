CREATE TABLE observation_evidence_new (
    observation_id TEXT NOT NULL REFERENCES observations(id) ON DELETE CASCADE,
    snapshot_id TEXT REFERENCES source_snapshots(id) ON DELETE SET NULL,
    start_offset INTEGER NOT NULL CHECK (start_offset >= 0),
    end_offset INTEGER NOT NULL CHECK (end_offset > start_offset),
    quote TEXT NOT NULL,
    page_number INTEGER,
    locator TEXT,
    source_url TEXT NOT NULL DEFAULT '',
    source_title TEXT NOT NULL DEFAULT '',
    source_fetched_at TEXT,
    PRIMARY KEY (observation_id, start_offset, end_offset)
);

INSERT INTO observation_evidence_new (
    observation_id, snapshot_id, start_offset, end_offset, quote, page_number, locator,
    source_url, source_title, source_fetched_at
)
SELECT oe.observation_id, oe.snapshot_id, oe.start_offset, oe.end_offset, oe.quote,
       oe.page_number, oe.locator, COALESCE(s.url, ''), COALESCE(s.title, ''), ss.fetched_at
FROM observation_evidence oe
LEFT JOIN source_snapshots ss ON ss.id=oe.snapshot_id
LEFT JOIN sources s ON s.id=ss.source_id;

DROP TABLE observation_evidence;
ALTER TABLE observation_evidence_new RENAME TO observation_evidence;
CREATE INDEX idx_evidence_snapshot ON observation_evidence(snapshot_id);

CREATE TABLE exports (
    id TEXT PRIMARY KEY,
    task_version_id TEXT NOT NULL REFERENCES task_versions(id) ON DELETE CASCADE,
    selection_json TEXT NOT NULL,
    format TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    record_count INTEGER NOT NULL CHECK (record_count >= 0)
);

CREATE INDEX idx_exports_version_generated ON exports(task_version_id, generated_at DESC);
