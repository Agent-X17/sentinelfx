CREATE TABLE demo_execution_attempts(
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL UNIQUE REFERENCES demo_trade_proposals(id),
    status TEXT NOT NULL CHECK(status IN ('PREPARED','BLOCKED_NO_TRADE','CONFIRMED_DEMO_EXECUTED','UNRESOLVED_RECONCILIATION_REQUIRED')),
    request_digest TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE demo_execution_history(
    id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL REFERENCES demo_execution_attempts(id),
    event TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER demo_execution_history_no_update BEFORE UPDATE ON demo_execution_history BEGIN SELECT RAISE(ABORT,'Demo execution history is append-only'); END;
CREATE TRIGGER demo_execution_history_no_delete BEFORE DELETE ON demo_execution_history BEGIN SELECT RAISE(ABORT,'Demo execution history is append-only'); END;
INSERT INTO schema_migrations VALUES(5,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
