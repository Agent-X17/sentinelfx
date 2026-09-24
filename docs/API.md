# Local API

## Execution and evidence boundary

The default is `SIMULATION`. Live submission is unavailable: live-enabled startup and `LIVE_GATED` refuse, `order_send()` refuses, and no live-order HTTP route exists. A/B/C are $50/$100/$150 simulations; `ACCOUNT_LIVE` is a $300 simulation placeholder, never reconciled real equity.

Real MT5 reads are diagnostic only. External or unknown exposure blocks candidates. Otherwise the real bridge returns `REQUIRED_EXTERNAL_EVIDENCE_UNVERIFIED`, with diagnostic failures for stale/invalid ticks, symbol properties and account identity/currency. Account snapshot freshness and reconciliation remain unverified. Only an explicit in-process mock in SIMULATION can use synthetic bridge evidence; HTTP input cannot select it.

Webhook credential fields are recursively filtered, including nested lists; known authentication-secret strings are filtered too. Arbitrary free text is not guaranteed secret-free. Do not submit credentials in metadata. Failed authentication cannot reserve legitimate alert IDs. Alert IDs take precedence; without an ID, a stable signal-field hash is used. Delivery headers cannot change replay identity.

Intake, paper reservation, journal and audit commit or roll back together. SQLite holds its write lock during adapter calls. Native-call timeouts and worker isolation are missing; a slow terminal can block writes. This remains a local single-user prototype.

See [the review report](FULL_PROJECT_REPORT.md) and [future release checklist](PRELIVE_CHECKLIST.md) for verification and remaining work.


Base URL: `http://127.0.0.1:8765`.

## Read routes

- `GET /api/health` — process mode, live-execution flag and MT5 connection state.
- `GET /api/state` — dashboard data, public settings, CSRF token, accounts, decisions, signals and audit records.
- `GET /api/mt5/status` — structured MT5 state.
- `GET /api/sample` — synthetic evaluation payload.
- `GET /api/candle-sample` — synthetic candle fixture.

## TradingView route

`POST /api/webhook/tradingview` accepts a JSON object. Authentication uses `X-Webhook-Secret` or a `secret` JSON field. `alert_id` determines identity; without it a stable signal-field hash is used. `Idempotency-Key` is accepted for compatibility but does not change identity. The route performs:

1. host and secret validation;
2. schema, timestamp and symbol validation;
3. duplicate detection and raw persistence;
4. MT5 account/symbol/tick/position reads when connected;
5. real adapters stop with an evidence block; explicit in-process simulation mocks alone can reach risk preview and exact-volume `order_check`;
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

## HTTP response semantics

- 401: incorrect webhook authentication.
- 400: malformed JSON/object or payload.
- 422: stale/expired alerts or unmapped/ambiguous symbols.
- 409: duplicate delivery.
- 200: delivery processed, including a diagnostic/risk `NO_TRADE` block. Read the JSON decision, not just the HTTP status.
- 403: host/origin/CSRF rejection; 413: body limit; 503: unavailable bridge/storage or rollback.

All bridge responses include `order_sent: false`; processed mock decisions additionally identify `SYNTHETIC_TEST_FIXTURE`. Dashboard decisions identify synthetic simulation or unverified evidence. HTTP sockets have a 10-second inactivity timeout; this does not cancel native MT5 calls.
