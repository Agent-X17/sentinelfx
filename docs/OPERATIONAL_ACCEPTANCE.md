# Operational acceptance check

Run from the project root:

```sh
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -B scripts/acceptance_check.py
```

The command creates and retains a new timestamped SQLite demo under the system temporary directory. It starts SentinelFX in `SIMULATION` with synthetic mock diagnostics, verifies startup output, `/status`, `/api/health`, the dashboard data contract and audit chain, and sends valid, bad-secret, stale, duplicate, missing-stop, unmapped-symbol, and invalid-host webhook requests. It then proves live-enabled configuration, `LIVE_GATED`, `order_send()`, and `/api/execute` all refuse. JavaScript syntax and the complete Python test suite run last.

Every check prints `PASS`. The only successful final line is:

```text
ACCEPTANCE RESULT: PASS
```

Any failure prints `ACCEPTANCE RESULT: FAIL`, exits nonzero, terminates its server, and identifies the failed stage. This proves the allowed local scope only. It does not prove real Chromium rendering, real MT5 connectivity, TradingView cloud delivery, broker evidence, profitability, connected-paper eligibility, or live readiness.

## Browser verification truth

On 24 September 2026, `agent-browser` was attempted against the running dashboard with Chromium arguments `--no-sandbox --disable-gpu`. Chrome exited before creating `DevToolsActivePort`; this is a macOS task-host sandbox limitation. No real-browser visual claim is made. The reproducible fallback passed all eight navigation views, simulated open/close, webhook-diagnostics rendering, explicit block-source content, and no DOM script errors:

```sh
NODE_PATH=/tmp/sentinelfx-browser/node_modules node tests/dashboard_dom.cjs http://127.0.0.1:PORT/
```

Because that harness is not a visual browser, complete the manual checklist in [QUICKSTART.md](QUICKSTART.md) from an unrestricted browser before a UI release.
