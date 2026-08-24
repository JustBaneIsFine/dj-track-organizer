-- Saved result of one scrape/update run: which tracks it found new, so the run's
-- "what's new" can be reopened later instead of being lost when the panel closes.
-- One row per run; runs are never merged. Pruned to the user's keep-count setting.
CREATE TABLE IF NOT EXISTS run_results (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    created_at TEXT    NOT NULL,
    label      TEXT,
    track_count INTEGER NOT NULL DEFAULT 0,
    track_ids  TEXT    NOT NULL  -- JSON array of track ids
);

CREATE INDEX IF NOT EXISTS idx_run_results_created ON run_results(created_at DESC);
