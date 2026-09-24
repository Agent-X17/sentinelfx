# SentinelFX — Small Capital Forex Research & Risk Engine

## Current safety review — supersedes earlier connected-workflow descriptions

- Real MT5 candidates now return NO_TRADE with REQUIRED_EXTERNAL_EVIDENCE_UNVERIFIED. Verified provider history, costs, news/macro context and account reconciliation are absent; connection alone cannot grant approval.
- Only an explicitly constructed in-process MockMT5Service in SIMULATION may use the synthetic bridge fixture. The server never constructs that adapter, and webhook fields cannot select it. Existing dashboard simulation exercises still work.
- Broker/account reads on the real bridge are diagnostic only; real account equity is not imported into the simulated ledger. ACCOUNT_LIVE retains its seeded simulation balance. Its risk calculation remains conservatively capped at the configured $300 profile basis.
- Auth failures use independent audit IDs and cannot reserve valid alert identities. Alert IDs take precedence over delivery headers. The top-level secret is removed even when an authentication header is also present. Do not put credentials in arbitrary metadata.
- All bridge database changes, including paper reservation, journal and audit, commit or roll back together. A process failure rolls back the candidate; retrying the alert is safe. SQLite holds its write lock during adapter calls, so a slow terminal can delay local requests. This is a local correctness measure, not a scalable deployment architecture.
- Terminal connection is rechecked on each real adapter operation. Namedtuple records retain their fields; numeric returns are preserved; false operations and nonzero/missing order-check return codes fail.
- Account-wide external positions and pending orders block candidates. Full terminal reconciliation, currency conversion, tick freshness validation, request translation and reconnect recovery remain future work. The unconditional real-evidence gate prevents approval until those integrations are reviewed.
- Raw intake rows show final blocked/processed status. Legacy audit and research records are preserved as historical data; they do not establish verified external evidence.
- No schema files or supplied prompt files were changed. Live order submission remains disabled.

SentinelFX is a working local decision-control application for small Forex accounts. It receives TradingView-style alerts, normalizes them, checks current MetaTrader 5 account and symbol facts when MT5 is enabled, applies deterministic risk rules, and records a paper/simulation decision with an audit trail.

The default mode is `SIMULATION`. Live order submission is not implemented: `MT5Service.order_send()` always refuses, `LIVE_EXECUTION_ENABLED=true` stops startup, and `LIVE_GATED` is reserved for a separately reviewed release.

## What is implemented

- Account profiles: `ACCOUNT_A=$50`, `ACCOUNT_B=$100`, `ACCOUNT_C=$150`, plus a simulation-only `ACCOUNT_LIVE=$300` placeholder pending verified reconciliation.
- Strict `RiskManager` veto rules and broker-grid `PositionSizer`.
- Authenticated TradingView webhook intake, freshness checks, idempotency, symbol normalization and explicit broker-symbol mapping.
- Optional read/check-only MT5 adapter for account, terminal, symbol, tick, positions, orders, history, rates, margin, profit and `order_check` operations.
- Exact-size workflow: risk preview calculates a lot size, MT5 checks that exact size, and the risk engine re-evaluates before an atomic paper/simulation record is created.
- SQLite persistence with ordered migrations; the repository boundary is ready for a future PostgreSQL implementation.
- Raw webhooks, normalized signals, broker/account snapshots, decisions, veto reasons, paper positions, provider state, journal entries and hash-chained audit logs.
- Local dashboard for account state, loss budgets, broker research, provider state, signals, veto reasons, bridge activity, symbol mappings and audit history.
- Deterministic tests for risk, persistence, concurrency, HTTP security, webhook validation, MT5 failures and live-execution refusal.

## Start locally on a Mac

Double-click `Start SentinelFX.command`, keep its Terminal window open, and visit [http://127.0.0.1:8765](http://127.0.0.1:8765).

Or run:

```sh
cd /path/to/forex-engine
python3 -B server.py
```

The default Mac experience needs only Python 3.9+ and a current browser. It runs with MT5 disabled and uses the existing simulator. If the browser says “connection refused,” the local server is not running; launch it again.

Useful commands:

```sh
# Clean, separate practice database
python3 -B server.py --port 8877 --db data/practice.sqlite3

# Complete automated suite
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -m unittest discover -s tests -v

# Database and research utilities
python3 -B manage.py init
python3 -B manage.py audit
python3 -B manage.py sample
python3 -B manage.py backtest --input examples/synthetic-candles.json
```

## Modes

| Mode | MT5 required | Behavior |
|---|---:|---|
| `DISCONNECTED` | No | Dashboard/research available; webhook candidates stop. |
| `SIMULATION` | No | Default local simulator. An enabled MT5 adapter can validate broker facts, but no order can be sent. |
| `PAPER_TRADING` | Yes | Requires an MT5 connection; decisions remain paper records. |
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

The secret may be supplied as `X-Webhook-Secret` or in the JSON payload. It is removed before persistence. TradingView must reach a public HTTPS receiver in real use, so a tunnel or deployed ingress is still needed. Only explicitly configured `WEBHOOK_ALLOWED_HOSTS` pass host validation. Set a strong secret whenever traffic can leave localhost.

Missing or stale timestamps, missing stops, invalid numbers, unknown or ambiguous symbols, duplicate IDs, unavailable MT5 state and every risk violation return a structured `NO_TRADE` or blocked response.

## Optional MT5 validation

Set:

```sh
export SYSTEM_MODE=PAPER_TRADING
export MT5_ENABLED=true
export MT5_TERMINAL_PATH='/path/to/terminal'
export TRADINGVIEW_WEBHOOK_SECRET='a-long-random-secret'
python3 -B server.py
```

The official MetaTrader5 Python integration and an initialized terminal must be installed on the host running this adapter. The normal Mac launcher intentionally does not install or configure a terminal. A common future deployment is to keep the dashboard/control service separate and run the MT5 adapter on a controlled Windows host.

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
