CREATE TABLE budget_reservations (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    local_day TEXT NOT NULL,
    resource TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount > 0),
    state TEXT NOT NULL CHECK (state IN ('reserved', 'settled', 'released')),
    actual INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_budget_day_resource ON budget_reservations(local_day, resource, state);
CREATE INDEX idx_budget_run_resource ON budget_reservations(run_id, resource, state);
