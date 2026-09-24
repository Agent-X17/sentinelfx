# Verification — 2026-09-24 pre-live review

Base: f7e3985, fetched from GitHub main and confirmed current before changes.

## Automated checks

Executed in the project root:

```sh
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -m unittest discover -s tests -v
node --check static/app.js
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -m py_compile server.py manage.py engine/*.py tests/*.py
```

Final Python result: **142 tests in 3.320 seconds; OK, zero failures/errors**.
All 125 baseline tests remain; 17 new tests are in tests/test_prelive.py.
JavaScript syntax and Python compilation passed. JavaScript syntax was rechecked after final label corrections.

New coverage: nested/list credential filtering, failed-auth redaction, header identity collision/replay, expiry rejection, blocked records, independent live refusals, boolean return-code rejection, port conflicts/fallback, HTTP status semantics, tick timestamps, symbol identity/properties and account uncertainty. Existing concurrency, rollback, sizing, exposure and real-evidence tests remain passing.

## Actual runtime

Started a fresh temporary database:

```sh
SYSTEM_MODE=SIMULATION MT5_ENABLED=false LIVE_EXECUTION_ENABLED=false \
python3 -B server.py --port 8877 --db /tmp/sentinelfx-final-prelive.sqlite3
```

- Root, app.js, style.css, health and state: HTTP 200.
- Initial equity A/B/C/ACCOUNT_LIVE: 50/100/150/300; audit integrity valid.
- Migrations: 1, 2, 3.
- Dashboard request to /api/execute: 404.
- Separate actual startup subprocesses refused LIVE_EXECUTION_ENABLED=true and LIVE_GATED with exit code 1 and explanatory messages.
- Actual HTTP webhook regression server confirmed 401 wrong secret, 200 mock processing, 409 duplicate, 400 missing stop and 422 stale signal.
- Working data/ database was not used, reset or deleted.

## Dashboard verification

An isolated jsdom harness loaded actual HTML, JavaScript and API responses from port 8877. Navigation passed for Overview, Account simulator, Broker comparison, Provider research, Signal decisions, Research library, Withdrawal tests and Audit trail. All pages produced headings and evidence warnings. The harness ran the safe scenario on Overview, observed a simulated position, closed it at its modeled stop, and observed its removal. No DOM script errors occurred.

The harness initially searched for the Overview scenario button on the calculator page and timed out. Correcting that selector/navigation made the harness pass; this was not an application failure. Harness dependencies were installed under /tmp and are not runtime dependencies.

**Actual browser verification is blocked.** Agent-browser could not launch installed Chrome. An independently downloaded headless Chromium also failed before page load with `bootstrap_check_in ... Permission denied (1100)` from the macOS sandbox. No real-browser visual, mobile, accessibility or interaction pass is claimed. DOM integration is not a substitute for those checks.

## Limits

No real MT5 terminal/broker connected. Diagnostic checks are fixture-tested only. Reconciliation, native-call timeouts, production ingress/authentication, real evidence, backup/restore and PostgreSQL remain incomplete. Live submission remains disabled. The requested full success condition is unmet because real-browser verification could not complete; this is not a production-readiness sign-off.
