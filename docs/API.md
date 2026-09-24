# Local API

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

Base URL: `http://127.0.0.1:8765`.

## Read routes

- `GET /api/health` — process mode, live-execution flag and MT5 connection state.
- `GET /api/state` — dashboard data, public settings, CSRF token, accounts, decisions, signals and audit records.
- `GET /api/mt5/status` — structured MT5 state.
- `GET /api/sample` — synthetic evaluation payload.
- `GET /api/candle-sample` — synthetic candle fixture.

## TradingView route

`POST /api/webhook/tradingview` accepts a JSON object. Authentication uses `X-Webhook-Secret` or a `secret` JSON field. An `Idempotency-Key` header is optional; otherwise `alert_id` is preferred. The route performs:

1. host and secret validation;
2. schema, timestamp and symbol validation;
3. duplicate detection and raw persistence;
4. MT5 account/symbol/tick/position reads when connected;
5. risk preview and exact-volume MT5 `order_check`;
6. full risk re-evaluation and atomic paper/simulation persistence.

No route submits an MT5 order.

## Dashboard mutation routes

All other POST routes require the `X-CSRF-Token` returned by `/api/state` and same-origin requests:

- `/api/evaluate`
- `/api/scenario`
- `/api/close`
- `/api/review`
- `/api/withdrawals`
- `/api/research`
- `/api/backtest`

Malformed JSON, oversized bodies, invalid hosts and cross-origin mutations fail closed. Domain validation failures return a JSON `NO_TRADE` response. Unexpected errors roll back the operation and return HTTP 503 without sending any broker order.

## Decision shape

Processed bridge responses contain:

```json
{
  "accepted": true,
  "status": "processed",
  "raw_webhook_id": "uuid",
  "decision": {
    "final_decision": "BUY | SELL | NO_TRADE | WATCHLIST",
    "direction": "BUY | SELL",
    "main_reason": "...",
    "main_risk": "...",
    "mt5_validation_result": {},
    "risk_check_result": [],
    "position_size_result": "0.11",
    "beginner_explanation": "..."
  },
  "decision_detail": {}
}
```

`BUY` or `SELL` means an approved simulation/paper candidate. It never means that a live order was sent.
