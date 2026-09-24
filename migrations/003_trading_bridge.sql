CREATE TABLE raw_webhooks(
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    received_at TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT,
    payload TEXT NOT NULL
);
CREATE TABLE normalized_signals(
    id TEXT PRIMARY KEY,
    raw_webhook_id TEXT NOT NULL REFERENCES raw_webhooks(id),
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE symbol_mappings(
    canonical_symbol TEXT NOT NULL,
    mt5_symbol TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    notes TEXT,
    PRIMARY KEY(canonical_symbol, mt5_symbol)
);
CREATE TABLE broker_snapshots(
    id TEXT PRIMARY KEY,
    raw_webhook_id TEXT REFERENCES raw_webhooks(id),
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE account_snapshots(
    id TEXT PRIMARY KEY,
    raw_webhook_id TEXT REFERENCES raw_webhooks(id),
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE risk_checks(
    id TEXT PRIMARY KEY,
    signal_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE journal_entries(
    id TEXT PRIMARY KEY,
    decision_id TEXT,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE paper_trades(
    id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE TABLE trades(
    id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE veto_reasons(
    id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL,
    code TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE replay_sessions(
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE settings(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE loss_locks(
    id TEXT PRIMARY KEY,
    account_profile TEXT NOT NULL,
    lock_type TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    started_at TEXT NOT NULL,
    reviewed_at TEXT
);
CREATE INDEX raw_webhooks_received ON raw_webhooks(received_at);
CREATE INDEX normalized_signals_status ON normalized_signals(status, created_at);
CREATE INDEX risk_checks_signal ON risk_checks(signal_id, created_at);
CREATE INDEX veto_reasons_decision ON veto_reasons(decision_id, created_at);
INSERT OR IGNORE INTO symbol_mappings(canonical_symbol, mt5_symbol, status, notes) VALUES
    ('EURUSD', 'EURUSD', 'ACTIVE', 'Default mapping; verify broker suffix before paper use'),
    ('GBPUSD', 'GBPUSD', 'ACTIVE', 'Default mapping; verify broker suffix before paper use'),
    ('USDJPY', 'USDJPY', 'ACTIVE', 'Default mapping; verify broker suffix before paper use');
INSERT INTO schema_migrations VALUES(3,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
