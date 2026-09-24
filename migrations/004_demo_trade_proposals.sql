CREATE TABLE demo_trade_proposals(
    id TEXT PRIMARY KEY,
    raw_webhook_id TEXT NOT NULL UNIQUE REFERENCES raw_webhooks(id),
    signal_id TEXT NOT NULL UNIQUE,
    alert_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('PENDING_LOCAL_REVIEW','APPROVED_FOR_FUTURE_DEMO_EXECUTION','REJECTED','CANCELLED','EXPIRED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE UNIQUE INDEX one_active_demo_trade_proposal ON demo_trade_proposals((1))
    WHERE status IN ('PENDING_LOCAL_REVIEW','APPROVED_FOR_FUTURE_DEMO_EXECUTION');
CREATE INDEX demo_trade_proposals_status ON demo_trade_proposals(status,created_at);
CREATE TABLE demo_trade_proposal_history(
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES demo_trade_proposals(id),
    action TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX demo_trade_proposal_history_proposal ON demo_trade_proposal_history(proposal_id,created_at);
CREATE TRIGGER proposal_history_no_update BEFORE UPDATE ON demo_trade_proposal_history BEGIN SELECT RAISE(ABORT,'Proposal history is append-only'); END;
CREATE TRIGGER proposal_history_no_delete BEFORE DELETE ON demo_trade_proposal_history BEGIN SELECT RAISE(ABORT,'Proposal history is append-only'); END;
INSERT INTO schema_migrations VALUES(4,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
