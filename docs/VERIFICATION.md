# Verification record

Current in-place safety review; all commands ran in the project directory.

## Automated checks

Exact full-suite command:

```sh
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -m unittest discover -s tests -v
```

**125 tests passed in 2.737 seconds**: all 109 existing tests plus 16 new tests in tests/test_hardening.py. No failing tests remained.

Also passed:

```sh
node --check static/app.js
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -m py_compile server.py manage.py engine/*.py tests/*.py
```

New regression coverage: real-evidence rejection, explicit fixture labels, account-wide positions/pending orders/unknown exposure, concurrent duplicate delivery, journal failure rollback, authentication poisoning, secret redaction, changed-header replay, namedtuple fields, numeric/false adapter results, order-check rejection/success, terminal disconnect and external-host secret configuration.

## Runtime verification

Started an actual server.py subprocess with a fresh temporary SQLite database, SYSTEM_MODE=SIMULATION, MT5_ENABLED=false, LIVE_EXECUTION_ENABLED=false and a test-only webhook secret. Requested port 0; the OS assigned **54191**. The subprocess was stopped after verification.

- GET /: HTTP 200, SentinelFX document served.
- GET /app.js: HTTP 200, new evidence warning present.
- GET /api/health: HTTP 200.
- GET /api/state: HTTP 200, audit chain valid.
- Valid authenticated webhook: HTTP 200, NO_TRADE / MT5_DISABLED.
- Authenticated webhook missing stop: HTTP 200, malformed / stop_loss is required.
- Wrong-secret webhook: HTTP 200, NO_TRADE / WEBHOOK_AUTH_FAILED.
- Header and body secret together: body secret absent from raw_webhooks and audit_logs.
- Fresh database migrations: [1, 2, 3].
- Direct order_send call: LIVE_EXECUTION_NOT_IMPLEMENTED.
- LIVE_EXECUTION_ENABLED and LIVE_GATED configuration checks: refused.

Dashboard HTML/assets and API loading were verified over HTTP. Automated visual rendering was not verified: agent-browser is unavailable. No new screenshot or visual-pass claim is made.

## Preserved boundaries and limitations

Real-terminal candidates are blocked for missing external evidence. Test-only synthetic bridge candidates and dashboard simulations remain available. Schema and prompt assets were not changed; tests cover retained-database upgrades and audit integrity.

Full broker reconciliation, genuine evidence feeds, tick freshness checks, native order-request translation, production authentication/TLS, backups and PostgreSQL remain incomplete. No real terminal or broker was connected during verification.

One SQLite write transaction now spans each bridge request; slow native adapter calls may delay local requests. This favors atomic local correctness and requires a separate worker/timeouts design before deployment.

MetaTrader order-check return handling was checked against the primary reference:
https://www.mql5.com/en/docs/python_metatrader5/mt5ordercheck_py

Live order submission remains disabled.

