# SentinelFX — Small Capital Forex Research & Risk Engine

## Execution and evidence boundary

The default is `SIMULATION`. Live submission is unavailable: live-enabled startup and `LIVE_GATED` refuse, `order_send()` refuses, and no live-order HTTP route exists. A/B/C are $50/$100/$150 simulations; `ACCOUNT_LIVE` is a $300 simulation placeholder, never reconciled real equity.

Real MT5 reads are diagnostic only. External or unknown exposure blocks candidates. Otherwise the real bridge returns `REQUIRED_EXTERNAL_EVIDENCE_UNVERIFIED`, with diagnostic failures for stale/invalid ticks, symbol properties and account identity/currency. Account snapshot freshness and reconciliation remain unverified. Only an explicit in-process mock in SIMULATION can use synthetic bridge evidence; HTTP input cannot select it.

Webhook credential fields are recursively filtered, including nested lists; known authentication-secret strings are filtered too. Arbitrary free text is not guaranteed secret-free. Do not submit credentials in metadata. Failed authentication cannot reserve legitimate alert IDs. Alert IDs take precedence; without an ID, a stable signal-field hash is used. Delivery headers cannot change replay identity.

Real diagnostics are collected outside the SQLite write transaction. The server uses a separate diagnostic process with a five-second deadline and one active worker; timeout/failure/busy states block candidates. Intake is then revalidated and deduplicated inside an atomic transaction. Mock paper reservation, journal and audit still commit or roll back together. This remains a local single-user prototype; real execution and reconciliation are not implemented.

See the [current progress report](docs/CURRENT_PROGRESS_REPORT_2026-09-25.md), [under-five-minute quick start](docs/QUICKSTART.md), [historical full review](docs/FULL_PROJECT_REPORT.md), and [future release checklist](docs/PRELIVE_CHECKLIST.md).

Current release gate: [OPERATIONAL_ACCEPTANCE.md](docs/OPERATIONAL_ACCEPTANCE.md).
The automated suite is the source of truth for the current test count. This is ready for local simulation/diagnostic evaluation, not
connection-only real trading rollout: reconciliation, native requests and evidence
adapters still require a separate implementation/review. See
[INTEGRATION_CONTRACT.md](docs/INTEGRATION_CONTRACT.md) before onboarding.


SentinelFX is a working local decision-control application for small Forex accounts. It receives TradingView-style alerts, normalizes them, checks current MetaTrader 5 account and symbol facts when MT5 is enabled, applies deterministic risk rules, and records a paper/simulation decision with an audit trail.

The default mode is `SIMULATION`. Live order submission is not implemented: `MT5Service.order_send()` always refuses, `LIVE_EXECUTION_ENABLED=true` stops startup, and `LIVE_GATED` is reserved for a separately reviewed release.

## What is implemented

- Account profiles: `ACCOUNT_A=$50`, `ACCOUNT_B=$100`, `ACCOUNT_C=$150`, plus a simulation-only `ACCOUNT_LIVE=$300` placeholder pending verified reconciliation.
- Strict `RiskManager` veto rules and broker-grid `PositionSizer`.
- Authenticated TradingView webhook intake, freshness checks, idempotency, symbol normalization and explicit broker-symbol mapping.
- Optional read/check-only MT5 adapter for account, terminal, symbol, tick, positions, orders, history, rates, margin, profit and `order_check` operations.
- Mock-only exact-size workflow: risk preview calculates a lot size, the explicit test adapter checks that size, and the risk engine re-evaluates before an atomic simulation record. Real MT5 diagnostics always remain blocked.
- SQLite persistence with ordered migrations; the repository boundary is ready for a future PostgreSQL implementation.
- Raw webhooks, normalized signals, broker/account snapshots, decisions, veto reasons, paper positions, provider state, journal entries and hash-chained audit logs.
- Local dashboard for account state, loss budgets, broker research, provider state, signals, veto reasons, bridge activity, symbol mappings and audit history.
- Deterministic tests for risk, persistence, concurrency, HTTP security, webhook validation, MT5 failures and live-execution refusal.

## Start locally on a Mac

Double-click `Start SentinelFX.command`, keep its Terminal window open, and open the dashboard address printed in Terminal. The launcher creates a new timestamped demo database and three synthetic example decisions, so the first dashboard is useful immediately. It normally uses port 8765; if occupied, it chooses an available port.

Or run:

```sh
cd /path/to/forex-engine
python3 -B server.py --demo --port-fallback
```

The default Mac experience needs only Python 3.9+ and a current browser. It runs with MT5 disabled and uses the existing simulator. If the browser says “connection refused,” the local server is not running; launch it again.

### Operational acceptance check

Run this single command before relying on a checkout for local testing:

```sh
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -B scripts/acceptance_check.py
```

It creates a new timestamped demo database, starts an isolated mock-diagnostic server, checks status and health, runs valid and invalid webhook deliveries, verifies persisted dashboard/audit state, proves live startup/order/HTTP paths refuse, and runs the full test suite last. Trust only `ACCEPTANCE RESULT: PASS`; every stage prints an individual `PASS` or the script exits nonzero with `ACCEPTANCE RESULT: FAIL`.

Useful commands:

```sh
# Clean, separate practice database
python3 -B server.py --port 8877 --db data/practice.sqlite3

# Safe end-to-end diagnostic mode (synthetic MT5 reads, always evidence-gated)
MT5_DIAGNOSTIC_MODE=mock python3 -B server.py --demo --port-fallback

# Complete automated suite
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -m unittest discover -s tests -v

# Database and research utilities
python3 -B manage.py init
python3 -B manage.py audit
python3 -B manage.py sample
python3 -B manage.py backtest --input examples/synthetic-candles.json

# Consistent backup and restore, each to a NEW destination path
python3 -B manage.py backup --db data/engine.sqlite3 --output /path/to/new-backup.sqlite3
python3 -B manage.py restore --db /path/to/new-backup.sqlite3 --output /path/to/new-restored.sqlite3
```

## Modes

| Mode | MT5 required | Behavior |
|---|---:|---|
| `DISCONNECTED` | No | Dashboard/research available; webhook candidates stop. |
| `SIMULATION` | No | Default local simulator. Optional mock/real MT5 diagnostics remain evidence-gated. |
| `PAPER_TRADING` | Yes | Requires MT5 enabled; real candidates remain blocked, not paper-approved. |
| `LIVE_DISABLED` | Yes | Connected validation mode with live submission still unavailable. |
| `LIVE_GATED` | N/A | Startup is refused in this release. |

Copy `.env.example` values into your shell environment before launch. The application does not automatically load `.env` files.

## TradingView webhook

Endpoint:

```text
POST http://127.0.0.1:8765/api/webhook/tradingview
```

Example local payload:

```json
{
  "secret": "replace-with-a-long-random-secret",
  "alert_id": "tv-eurusd-20260924-001",
  "symbol": "OANDA:EUR/USD",
  "side": "BUY",
  "timeframe": "H1",
  "strategy": "breakout-v1",
  "timestamp": "2026-09-24T10:00:00+00:00",
  "entry": "1.10010",
  "stop_loss": "1.09800",
  "take_profit": "1.10450",
  "metadata": {"strategy_type": "breakout"}
}
```

The secret may be supplied as `X-Webhook-Secret` or in the JSON payload. Both known secret values and credential-like fields are filtered before persistence. Arbitrary free text cannot be guaranteed secret-free. TradingView needs a public HTTPS receiver; none is deployed here. Explicit `WEBHOOK_ALLOWED_HOSTS` require a secret of at least 32 characters. Host validation is not production authentication.

Send a fresh local test alert without editing timestamps by hand:

```sh
python3 -B scripts/test_webhook.py --url http://127.0.0.1:8765/api/webhook/tradingview
```

Add `--case bad-secret`, `stale`, `duplicate`, `missing-stop`, or `unmapped` to reproduce a specific refusal. Each response includes `http_status`, `test_case`, the exact reason, and `NO_TRADE`.

For an authenticated or tunneled instance, add `--secret "$TRADINGVIEW_WEBHOOK_SECRET"`. See [TRADINGVIEW_TESTING.md](docs/TRADINGVIEW_TESTING.md) for the exact host and HTTPS tunnel setup.

Missing or stale timestamps, missing stops, invalid numbers, unknown or ambiguous symbols, duplicate IDs, unavailable MT5 state and every risk violation return a structured `NO_TRADE` or blocked response.

## MT5 diagnostic setup

Choose `disabled`, `mock`, or `real`. `mock` is visibly synthetic and works locally; it exercises the same intake and diagnostic presentation but can never satisfy the external-evidence gate:

```sh
MT5_DIAGNOSTIC_MODE=mock python3 -B server.py --demo --port-fallback
```

Real diagnostics are unavailable on this Mac because the official `MetaTrader5` Python package and supported terminal host are absent. Use a controlled Windows machine with the official package, a running MT5 terminal, and a **demo-only login**. Do not provide funded-account credentials. On that diagnostic host, set:

```sh
export SYSTEM_MODE=SIMULATION
export LIVE_EXECUTION_ENABLED=false
export MT5_DIAGNOSTIC_MODE=real
export MT5_TERMINAL_PATH='/path/to/terminal'
export TRADINGVIEW_WEBHOOK_SECRET='a-long-random-secret'
python3 -B server.py
```

The real adapter remains diagnostic-only. It fails closed on disconnection, package or terminal unavailability, timeout, terminal-busy state, stale ticks, account drift, invalid symbols, unknown exposure, and existing positions or orders. Those states produce `NO_TRADE`; they never submit an order. The dashboard, `/status`, and `/api/health` show whether the current host and package meet the real-diagnostic prerequisites.

The current MT5 runtime broker profile remains `RUNTIME_UNVERIFIED`. Connecting a terminal does not prove the exact legal entity serving Tanzania, withdrawal rails, commission, swap, news conditions or strategy edge. Those missing facts remain safety blockers for any future live release.

## Risk rules

| Profile | Starting equity | Max risk/trade | Daily loss cap | Weekly loss cap | Open positions |
|---|---:|---:|---:|---:|---:|
| `ACCOUNT_A` | $50 | $0.25 | $1.00 | $2.00 | 1 |
| `ACCOUNT_B` | $100 | $0.50 | $2.00 | $4.00 | 1 |
| `ACCOUNT_C` | $150 | $0.75 | $3.00 | $6.00 | 1 |
| `ACCOUNT_LIVE` | $300 simulation placeholder | 0.5% | 2% | 4% | 1 |

The basis is `min(current equity, starting equity)`, so profits do not automatically raise risk. Loss budgets count losing trades; profits do not erase consumed loss capacity. Open risk is reserved immediately. Day boundaries use `Africa/Dar_es_Salaam`, and weekly accounting begins Monday.

Sizing uses contract size, stop distance, spread, commission, slippage and swap, rounds down to the broker’s volume grid, enforces margin and cost limits, and rejects when even the minimum lot is too large. Scores never override a veto. Provider-supplied lot sizes and client-supplied equity or risk limits are ignored.

## Important boundary

This is a real codebase and a working safety/control prototype. It is not yet a live trading system. Current market news, macro context, legal-entity verification, Tanzania withdrawal verification, real commission/swap schedules, production authentication, HTTPS, secrets management, monitoring and a separately reviewed human-confirmation execution path are still required before live trading could be considered.

The system does not promise daily income. A $50 gain on a $50 account is a 100% return; capital preservation and evidence come first.

## Project map

```text
engine/domain.py       risk rules, sizing and normalization
engine/webhook.py      TradingView validation and symbol mapping
engine/mt5.py          read/check-only MT5 boundary
engine/bridge.py       TradingView → MT5 → risk orchestration
engine/service.py      persistence-aware application workflow
engine/storage.py      SQLite repository and audit chain
migrations/            ordered database schema
static/                local dashboard
tests/                 unit, integration, concurrency and HTTP tests
prompts/               supplied prompts preserved as separate assets
```

Start with [docs/FULL_PROJECT_REPORT.md](docs/FULL_PROJECT_REPORT.md) for the candid implementation history, verification evidence, known weaknesses, and remaining work. See also [docs/API.md](docs/API.md), [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md), [docs/VERIFICATION.md](docs/VERIFICATION.md), and [docs/AGENT_HANDOFF.md](docs/AGENT_HANDOFF.md).
