# Integration contract — diagnostics only

This release cannot be turned into live execution by supplying credentials.
Real order translation, account/order reconciliation and evidence adapters still
require implementation and separate review. No HTTP input can select a mock or
invoke order_send. The existing approved path uses explicit synthetic test fixtures.

## TradingView boundary

POST JSON to `/api/webhook/tradingview`. Required strings: symbol, side (BUY/SELL),
timeframe, strategy and timezone-aware timestamp. Stop loss is required. A supplied
expiry must be valid and in the future; missing expiry defaults to the configured
window. Configure broker symbols locally; missing or ambiguous mappings block.

Prefer a stable unique alert_id, 1–194 characters after trimming. Its SHA-256-based
identity is independent of secret redaction. Without an ID, a canonical JSON tuple
of symbol, side, strategy, timeframe and timestamp determines replay identity.
Delivery headers cannot override it. Rejected malformed/authenticated requests get
separate rejection identities so they cannot poison a corrected alert. Legacy alert
IDs and exact legacy fallback hashes are recognized; no old audit rows are rewritten.

Use X-Webhook-Secret or a body secret. Header authentication takes precedence;
both values are filtered if supplied. Credential-like nested keys and known secret
strings, including keys, are filtered. Arbitrary free text remains best effort.
Duplicate JSON keys are rejected. No credentials belong in arbitrary metadata.

HTTP 200 acknowledges processed delivery, including NO_TRADE; 401 rejects auth,
400 rejects malformed input, 422 rejects stale/unmapped signals, 409 duplicates,
503 infrastructure failures. Do not interpret HTTP 200 as approval.

External allowed hosts require at least 32 secret characters. Public HTTPS ingress,
rate limiting, access control and deployment logging are not implemented here; a
tunnel alone does not make the dashboard safe for public access.

## MT5 diagnostic boundary

The server constructs IsolatedMT5Service, never MockMT5Service. With MT5 enabled,
each diagnostic collection runs the fixed `engine.mt5_worker` module in a subprocess.
Its deadline is five seconds, including initialize/read/shutdown. Timeout terminates
the worker; one worker runs at a time and concurrent requests fail closed as busy.
No worker operation sends orders or imports terminal balances into the ledger.

An account identity change within a collection blocks it. Changes in known
login/server/currency across collections latch an account-drift block until operator
review and process restart. This is diagnostic detection, not full reconciliation.
The account is still unreconciled after restart. Disconnection is reported separately.
Worker process failures have generic messages to avoid leaking native exception data.

Reads occur outside a SQLite write transaction. The bridge then revalidates the
alert and checks duplicate identity inside the atomic persistence transaction.
It records diagnostic blocks and no positions. This avoids needing to reserve risk
against a stale real snapshot: real approval is unconditionally unavailable.

For a future real approval path, implement versioned account/exposure reconciliation,
freshness and instrument/account currency rules, evidence provenance, exact native
request translation and confirmation. Do not reuse snapshot success as approval.

## Database operations

Startup locks before migration-version inspection and applies complete SQL statements
atomically. Initial WAL lock contention has bounded retry. Newer unknown schemas,
integrity failures and damaged audit chains refuse startup. Existing migrations and
audit rows are preserved.

`manage.py backup --db SOURCE --output NEW_PATH` and `restore` both use SQLite's
consistent backup API, then verify database integrity, foreign keys, audit chain and
schema versions. Existing targets or companion WAL/SHM files are never overwritten.
Copies are private to the owner on POSIX. A failed verification leaves an explicitly
unapproved artifact for diagnosis. Do not run it as the working database.

To recover, stop the application, restore to a new path, and start with `--db` pointing
to that verified copy. Keep the original for investigation. Scheduled backups,
retention, off-host copies and operational recovery drills remain deployment work.
