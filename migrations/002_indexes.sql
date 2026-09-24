CREATE INDEX decisions_by_account ON decisions(account_profile,timestamp);
CREATE INDEX signals_by_provider ON signals(provider_id,timestamp);
CREATE INDEX outcomes_by_account ON signal_outcomes(account_profile,status);
CREATE INDEX audit_by_event ON audit_logs(event,timestamp);
INSERT INTO schema_migrations VALUES(2,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
