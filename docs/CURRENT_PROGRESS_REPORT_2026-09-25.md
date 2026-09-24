# SentinelFX current progress and independent-agent handoff

Report date: 2026-09-25  
Repository: `Agent-X17/sentinelfx`  
Branch reviewed: `prelive-hardening`  
Base before this report: `4a63c8dc9c271c064d023686b54d27d3fd29249b`

## Executive status

SentinelFX is a local safety, research, simulation and diagnostic-control project. The deterministic risk engine, dashboard, authenticated TradingView intake, persistence, audit trail and MT5 diagnostic boundaries are implemented and tested.

The project is **not a live trading system**. Live-enabled startup refuses, `LIVE_GATED` refuses, `MT5Service.order_send()` refuses and there is no live execution HTTP route. No funded-account credentials are stored and no broker order has been sent.

Current operator progress:

- an HFM MT5 demo account is configured with a $500 virtual balance;
- the repository's demo-only MQL5 monitor has been compiled in MetaEditor with 0 errors and 0 warnings;
- the monitor is attached to an EURUSD H1 demo chart with Algo Trading left disabled;
- a fresh local snapshot reported demo/connected, $500 balance, $500 equity, $500 free margin, zero positions, zero pending orders and updating EURUSD bid/ask values;
- a TradingView one-time alert reached the temporary HTTPS receiver and was rejected as designed because its connection-test payload omitted `stop_loss`;
- a complete hypothetical signal passed local webhook structure validation but returned `NO_TRADE / MT5_DISABLED`; it was not submitted as a public TradingView alert;
- the latest repository acceptance run passed and ran 181 tests after adding the three demo-monitor tests in this change.

These facts prove local delivery and read-only diagnostics. They do not prove a profitable strategy, complete broker reconciliation, real order checking or live execution readiness.

## What the system currently does

The main flow is:

```text
TradingView-style JSON alert
  -> authentication, freshness, replay and symbol checks
  -> diagnostic MT5/account reads when explicitly enabled
  -> deterministic risk and evidence gates
  -> NO_TRADE, WATCHLIST or a simulation-only decision
  -> SQLite journal and hash-chained audit record
```

The dashboard simulator uses synthetic broker, provider and market fixtures. A simulation BUY or SELL is a modeled decision, not a broker order.

The Mac demo monitor is a separate read-only path:

```text
MT5 demo terminal
  -> SentinelFX_DemoMonitor.mq5
  -> Common/Files/SentinelFX/demo-status.json
  -> engine/mt5_monitor.py allowlisted reader
  -> local diagnostics only
```

The monitor has no order functions, DLL access, network calls or password fields. It rejects live accounts, disconnected terminals, malformed snapshots and snapshots older than ten seconds. Its data cannot authorize a signal or update the simulated ledger.

## Verified operator workflow

### HFM and MT5

The operator created an HFM Demo Premium MT5 account using USD, $500 virtual balance and 1:100 leverage. MT5 showed the $500 balance and the terminal logged into the demo account.

`mt5/SentinelFX_DemoMonitor.mq5` was copied into the MT5 Experts area, opened in MetaEditor and compiled successfully. The compile pane showed `0 errors, 0 warnings`. It was attached to EURUSD H1. The `Allow Algo Trading` box remained unchecked because the monitor only publishes diagnostics.

On 2026-09-25 a direct read of the terminal-generated snapshot showed:

| Field | Verified value |
|---|---:|
| Account mode | Demo |
| Terminal connection | Connected |
| Currency | USD |
| Balance | 500.00 |
| Equity | 500.00 |
| Free margin | 500.00 |
| Open positions | 0 |
| Pending orders | 0 |
| Monitored symbol | EURUSD |
| Snapshot age at inspection | about 2 seconds |

Bid and ask were updating. Exact prices are intentionally omitted because they become stale and are not trade recommendations.

### TradingView delivery

TradingView Essential was activated by the operator. A one-time `Price > 0` alert was created solely to test connectivity. The alert used a temporary Cloudflare Quick Tunnel and a dedicated webhook secret. It arrived at SentinelFX and returned `NO_TRADE` because `stop_loss` was intentionally missing.

That test proves that one alert traveled from TradingView through the temporary HTTPS address to the local receiver. It does not create a durable connection: Quick Tunnel URLs change after restart, and the one-time alert has already fired.

The tunnel URL and webhook secret are not included in this repository. Runtime files, logs and databases are ignored. Rotate the webhook secret after external testing.

### Complete hypothetical signal exercise

A fixed, invented EURUSD BUY example was used for local validation:

- entry: 1.10000;
- stop: 1.09800;
- target: 1.10400;
- stop distance: 20 pips;
- reward/risk ratio: 2.0.

An isolated arithmetic illustration used $500 equity, the default 0.5% risk fraction, a 100,000-unit contract, 0.01 minimum/step, 1:100 leverage, 1 pip spread, 0.5 pip slippage and zero commission/swap. It calculated:

| Result | Value |
|---|---:|
| Illustrative risk budget | $2.50 |
| Position size | 0.01 lot |
| Estimated modeled loss | $2.15 |
| Estimated margin | $11.00 |

The entry, stop, target and cost assumptions are synthetic. The calculation is not a market signal, trade approval or guaranteed maximum loss. The real $500 demo balance is not imported into the repository's risk profiles; `ACCOUNT_LIVE` remains a capped $300 simulation placeholder.

The complete payload was submitted only to the localhost receiver and returned `NO_TRADE / MT5_DISABLED / order_sent=false`. A second public test was not performed because it would have transmitted the credential-bearing payload through a new public endpoint without a separate operator action.

## Repository implementation

Key files:

- `engine/domain.py`: decimal risk limits, signal validation, cost-aware sizing, margin checks and veto logic;
- `engine/service.py`: account/provider state, decision persistence, reservations and simulation lifecycle;
- `engine/webhook.py`: authentication, freshness, idempotency, symbol mapping and secret redaction;
- `engine/bridge.py`: diagnostic collection, evidence gate, risk orchestration and atomic persistence;
- `engine/mt5.py`: native read/check boundary whose `order_send()` always refuses;
- `engine/isolated_mt5.py` and `engine/mt5_worker.py`: time-bounded isolated diagnostics and account-drift controls;
- `engine/mt5_monitor.py`: allowlisted reader for the separate demo-only MQL5 monitor;
- `mt5/SentinelFX_DemoMonitor.mq5`: two-second local demo snapshot publisher;
- `engine/storage.py` and `migrations/`: SQLite migrations, transactions and audit chain;
- `server.py`: localhost UI/API server with strict webhook response semantics;
- `scripts/acceptance_check.py`: current release gate;
- `static/`: eight-view local dashboard;
- `tests/`: domain, bridge, security, HTTP, operations, readiness and monitor coverage.

The current public-interface behavior includes:

- 401 for failed webhook authentication;
- 400 for malformed or missing-stop input;
- 409 for duplicate deliveries;
- 422 for stale or unmapped input;
- 403 for invalid webhook hosts;
- 200 with explicit `NO_TRADE` for an authenticated, structurally valid candidate stopped by diagnostics/evidence/risk;
- 404 for `/api/execute`.

## Latest verification

Run from the project root:

```sh
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -B scripts/acceptance_check.py
```

Latest result on 2026-09-25: `ACCEPTANCE RESULT: PASS`.

The gate verified:

- a fresh timestamped demo database;
- truthful startup output;
- `/status` and `/api/health`;
- valid, bad-secret, stale, duplicate, missing-stop and unmapped webhooks;
- invalid-host rejection;
- persisted dashboard state and valid audit chain;
- refusal of live-enabled and `LIVE_GATED` startup;
- refusal by `order_send()` and absence of a live HTTP route;
- frontend JavaScript syntax;
- 181 Python tests with zero failures or errors.

The acceptance server used mock diagnostics and returned `REQUIRED_EXTERNAL_EVIDENCE_UNVERIFIED / NO_TRADE` for the otherwise valid webhook. This is the required fail-closed behavior.

The three added monitor tests verify valid telemetry, explicit `order_submission_available=false`, field allowlisting, stale/future/live/disconnected rejection and malformed/oversized input rejection.

## Safety boundary

The following are deliberate and must remain true:

1. `SIMULATION` is the default.
2. Live-enabled startup and `LIVE_GATED` refuse.
3. `order_send()` refuses under every input.
4. There is no live execution route.
5. Real MT5 diagnostics cannot satisfy external evidence requirements.
6. The Mac monitor cannot submit or authorize orders.
7. Webhook inputs cannot choose the in-process mock adapter.
8. Provider lot sizes, client equity and client risk-limit overrides are ignored.
9. Account-wide external positions or pending orders block candidates.
10. Risk reservations, journal writes and audit writes are atomic in the simulation path.

Do not enable Algo Trading for this monitor. Do not add a market-order call as a shortcut. A future execution release requires its own design and independent review.

## Known gaps

The following remain incomplete or unverified:

- verified Tanzania-serving legal entity and withdrawal rails;
- verified real commission, spread, swap, slippage and contract specifications;
- current news and macro evidence feeds;
- verified provider history and strategy edge;
- full broker-account and open-exposure reconciliation;
- supported native MT5 Python host integration on the target machine;
- native request translation and broker `order_check` against the actual demo account;
- stable authenticated HTTPS ingress, rate limiting and operational monitoring;
- backups/retention for an ongoing deployed service;
- real browser visual/accessibility verification on this sandboxed task host;
- PostgreSQL or a scalable multi-user deployment;
- forward demo study and withdrawal verification;
- any proof of profitability.

The MQL5 file snapshot is locally trusted telemetry, not cryptographically authenticated broker evidence. Tick server time is exposed but quote freshness is not certified by the monitor reader. Modeled stop loss is not a guaranteed fill price.

## Recommended next work for another agent

1. Re-run `scripts/acceptance_check.py` and require PASS before editing.
2. Review this report, `README.md`, `docs/AGENT_HANDOFF.md`, `docs/INTEGRATION_CONTRACT.md` and `docs/PRELIVE_CHECKLIST.md`.
3. Add the demo monitor to the local status/dashboard only as a clearly separate diagnostic source. Do not merge it into risk authorization.
4. Design a stable, authenticated ingress with secret rotation and logging; keep the public surface limited to the webhook route.
5. Implement current news/macro and broker-cost evidence interfaces with provenance and expiry.
6. Build demo-account reconciliation on a supported MT5 host and verify identity, currency, exposure, quotes and reconnect behavior.
7. Conduct a forward demo study with a defined strategy before discussing any execution path.
8. Keep live order submission out of scope until the future checklist is independently satisfied and reviewed.

## Independent-review questions

An incoming agent should answer these before claiming readiness:

- Can any HTTP or webhook field select mock evidence or bypass a veto?
- Can any code path call a real order submission function?
- Can duplicate or concurrent alerts reserve risk twice?
- Can stale, malformed or live-account monitor data appear healthy?
- Can secrets enter persistence or Git history?
- Does the dashboard clearly separate the $500 terminal diagnostic balance from simulation profiles?
- Are every broker cost and market-context input current, sourced and expiring?
- Does a supported MT5 host reproduce account identity and exposure without drift?
- Are claims limited to evidence actually reproduced?

## Sharing notes

Share the repository branch and this report. Do not share `.env`, `.sentinelfx-webhook-secret`, `.tradingview-local/`, SQLite databases, MT5 passwords, account IDs or temporary tunnel URLs.

The full historical implementation report remains in `docs/FULL_PROJECT_REPORT.md`. This dated report supersedes its older runtime/test counts and records the manually verified TradingView and MT5 progress completed after that report.
