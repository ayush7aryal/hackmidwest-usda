PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS locations (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    latitude REAL NOT NULL CHECK(latitude BETWEEN -90 AND 90),
    longitude REAL NOT NULL CHECK(longitude BETWEEN -180 AND 180),
    timezone TEXT NOT NULL,
    open_time TEXT NOT NULL,
    close_time TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'NORMAL',
    status_reason TEXT,
    status_confidence REAL,
    affected_start TEXT,
    affected_end TEXT,
    last_checked_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shifts (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    weekly_hours_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    emp_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    location_id INTEGER NOT NULL REFERENCES locations(id),
    is_manager INTEGER NOT NULL DEFAULT 0 CHECK(is_manager IN (0,1)),
    is_essential INTEGER NOT NULL DEFAULT 0 CHECK(is_essential IN (0,1)),
    is_teleworking INTEGER NOT NULL DEFAULT 0 CHECK(is_teleworking IN (0,1)),
    shift_id INTEGER REFERENCES shifts(id)
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    location_id INTEGER NOT NULL REFERENCES locations(id),
    evaluated_at TEXT NOT NULL,
    posture TEXT NOT NULL,
    reason TEXT NOT NULL,
    confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    affected_start TEXT,
    affected_end TEXT,
    evidence_json TEXT NOT NULL,
    threshold_json TEXT NOT NULL,
    alternatives_json TEXT NOT NULL,
    recipients_json TEXT NOT NULL,
    draft_subject TEXT NOT NULL,
    draft_body TEXT NOT NULL,
    manager_status TEXT NOT NULL DEFAULT 'PENDING',
    reviewed_by INTEGER REFERENCES users(emp_id),
    reviewed_at TEXT,
    sent_at TEXT,
    delivery_json TEXT,
    manager_alert_sent_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_users_location ON users(location_id);
CREATE INDEX IF NOT EXISTS idx_decisions_location_evaluated
    ON decisions(location_id, evaluated_at);
