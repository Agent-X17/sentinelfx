# Verification

## Current operational acceptance — 2026-09-24

The current authoritative gate is:

```sh
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -B scripts/acceptance_check.py
```

Verified again on 2026-09-25: `ACCEPTANCE RESULT: PASS`; **181 tests passed**. The gate also passed startup truth, status, health, valid/bad-secret/stale/duplicate/missing-stop/unmapped/invalid-host webhook cases, persisted dashboard and audit state, live configuration refusal, `order_send()` refusal, missing live HTTP route, and frontend syntax. The added tests cover the separate read-only Mac MT5 demo monitor. See [CURRENT_PROGRESS_REPORT_2026-09-25.md](CURRENT_PROGRESS_REPORT_2026-09-25.md) and [OPERATIONAL_ACCEPTANCE.md](OPERATIONAL_ACCEPTANCE.md).

Real Chromium automation is still blocked on this task host: Chrome exits before creating `DevToolsActivePort`, including with `--no-sandbox --disable-gpu`. The updated DOM fallback passed all eight pages, simulated open/close, webhook diagnostics, explicit block-source rendering, and no script errors. No visual-browser success is claimed.

## Historical system-readiness review — 2026-09-24

Base: 09adec9, fetched from GitHub prelive-hardening and confirmed current before changes.

## Automated checks

Executed in the project root:

```sh
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -m unittest discover -s tests -v
node --check static/app.js
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -m py_compile server.py manage.py engine/*.py tests/*.py
```

Final Python result: **170 tests in 4.486 seconds; OK, zero failures/errors**.
All 142 baseline tests remain; 28 new tests are in tests/test_readiness.py.
JavaScript syntax and Python compilation passed. A concurrency test exposed initial WAL contention during the pass; a bounded lock retry fixed it. The focused test and full suite passed afterward.

New coverage includes invalid-ID poisoning, corrected deliveries, canonical/legacy replay identity, secret-independent identities, both header/body secrets, empty expiry, malformed mappings, mock balance isolation, real reads without a DB lock, within-read identity drift, secret keys, duplicate JSON keys, bounded redaction depth, invalid diagnostics, worker timeout/failure/busy/drift states, concurrent initialization, newer schema refusal, damaged audit refusal, and verified backup/restore without overwrite. Earlier sizing, rollback and live boundary tests remain passing.

## Actual runtime

Started a fresh temporary database:

```sh
SYSTEM_MODE=SIMULATION MT5_ENABLED=false LIVE_EXECUTION_ENABLED=false \
python3 -B server.py --port 8877 --db /tmp/sentinelfx-readiness.QO3Alp/review.sqlite3
```

- Root, app.js, style.css, health and state: HTTP 200.
- Initial equity A/B/C/ACCOUNT_LIVE: 50/100/150/300; audit integrity valid.
- Migrations: 1, 2, 3.
- Dashboard requests to /api/execute, /api/live and /api/order_send: 404.
- Separate actual startup subprocesses refused LIVE_EXECUTION_ENABLED=true and LIVE_GATED with exit code 1 and explanatory messages.
- Actual running server confirmed 401 wrong secret, 200 NO_TRADE/MT5_DISABLED, 409 duplicate and 400 missing stop. Existing HTTP regressions additionally check mock success and 422 stale rejection.
- Actual isolated native worker returned MT5_PACKAGE_UNAVAILABLE on this Mac. Timeout/busy/drift branches are fixture-tested, not real-terminal verified.
- Backup and restore CLI round-trip passed; restored audit chain was VALID.
- Working data/ database was not used, reset or deleted.

## Dashboard verification

An isolated jsdom harness loaded actual HTML, JavaScript and API responses from port 8877. Navigation passed for Overview, Account simulator, Broker comparison, Provider research, Signal decisions, Research library, Withdrawal tests and Audit trail. All pages produced headings and evidence warnings. The harness ran the safe scenario on Overview, observed a simulated position, closed it at its modeled stop, and observed its removal. No DOM script errors occurred.

The reproducible harness is tests/dashboard_dom.cjs. It was run with NODE_PATH pointing to an isolated jsdom installation under /tmp; jsdom is not a Python application runtime dependency. Use a fresh practice database so existing risk locks do not intentionally reject the safe scenario.

**Actual browser verification is blocked.** Agent-browser could not launch installed Chrome. An independently downloaded headless Chromium also failed before page load with `bootstrap_check_in ... Permission denied (1100)` from the macOS sandbox. No real-browser visual, mobile, accessibility or interaction pass is claimed. DOM integration is not a substitute for those checks.

## Limits

No real MT5 terminal/broker connected. Native diagnostic timeout isolation and verified backup/restore are implemented. Full account/order reconciliation, native broker request translation, actual evidence adapters and production deployment controls still require engineering and verification. PostgreSQL is not implemented or required for local operation. Live submission remains disabled. This is not a connection-only or production-readiness sign-off; see SYSTEM_READINESS_REVIEW.md.
