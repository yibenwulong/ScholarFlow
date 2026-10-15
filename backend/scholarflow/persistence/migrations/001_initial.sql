BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    project_id TEXT REFERENCES entities(id),
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision > 0),
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS entity_kind_project ON entities(kind, project_id, created_at);
CREATE TABLE IF NOT EXISTS entity_links (
    owner_id TEXT NOT NULL REFERENCES entities(id),
    relation TEXT NOT NULL,
    target_id TEXT NOT NULL REFERENCES entities(id),
    PRIMARY KEY(owner_id, relation, target_id)
);
CREATE TABLE IF NOT EXISTS idempotency (
    scope TEXT NOT NULL,
    operation_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    entity_id TEXT NOT NULL REFERENCES entities(id),
    PRIMARY KEY(scope, operation_key)
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES entities(id),
    kind TEXT NOT NULL,
    stage TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    result TEXT,
    error TEXT,
    attempt_no INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_expires_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(run_id, kind, stage, input_hash)
);
CREATE INDEX IF NOT EXISTS job_queue ON jobs(kind, status, lease_expires_at);
CREATE TABLE IF NOT EXISTS run_events (
    run_id TEXT NOT NULL REFERENCES entities(id),
    seq INTEGER NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY(run_id, seq)
);
CREATE TABLE IF NOT EXISTS model_calls (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES entities(id),
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    provider_request_id TEXT,
    usage_json TEXT,
    created_at TEXT NOT NULL
);
PRAGMA user_version=1;
COMMIT;
