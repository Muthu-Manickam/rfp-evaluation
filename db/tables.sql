CREATE TABLE IF NOT EXISTS evaluation_criteria (
    criterion_id INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    description  TEXT NOT NULL DEFAULT '',
    weight       REAL NOT NULL CHECK (weight >= 0 AND weight <= 100),
    max_score    REAL NOT NULL DEFAULT 10 CHECK (max_score > 0),
    is_active    INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rfp_runs (
    rfp_run_id        TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('PENDING', 'RUNNING', 'COMPLETED', 'FAILED')),
    title             TEXT,
    model             TEXT,
    criteria_snapshot TEXT,
    warnings_json     TEXT,
    completed_at      TEXT,
    error             TEXT,
    locked            INTEGER NOT NULL DEFAULT 0,
    locked_at         TEXT
);

CREATE TABLE IF NOT EXISTS supplier_results (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    rfp_run_id        TEXT NOT NULL REFERENCES rfp_runs(rfp_run_id) ON DELETE CASCADE,
    supplier_name     TEXT NOT NULL,
    submission_date   TEXT NOT NULL,
    experience_rating REAL NOT NULL,
    absolute_score    REAL,
    ppi               REAL,
    final_rank        INTEGER,
    file_name         TEXT,
    result_json       TEXT,
    UNIQUE (rfp_run_id, supplier_name)
);

CREATE TABLE IF NOT EXISTS evaluation_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    rfp_run_id    TEXT NOT NULL REFERENCES rfp_runs(rfp_run_id) ON DELETE CASCADE,
    event_type    TEXT NOT NULL,
    supplier_name TEXT,
    criterion_id  INTEGER,
    old_score     REAL,
    new_score     REAL,
    reason        TEXT,
    actor         TEXT,
    created_at    TEXT NOT NULL
);
