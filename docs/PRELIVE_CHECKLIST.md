# Future separate release checklist

This is a review checklist, not permission to trade or a claim of production readiness.
Creating MT5/TradingView accounts does not complete these controls. This release
allows local simulation and optional broker diagnostics only. Real candidates remain
blocked. A future release needs independent design, implementation and review.

| Area | Current classification | Acceptance evidence for a future release |
|---|---|---|
| Decimal risk and sizing; A/B/C limits | Implemented, locally tested | Preserve regression coverage and conservative rounding; independently review broker-specific monetary calculations |
| Final RiskManager veto | Implemented, locally tested | Every future execution path must re-evaluate stored state and reserve exposure atomically |
| Live-enabled startup, LIVE_GATED and order_send refusals | Implemented, locally tested | Keep refusals until a separately reviewed release explicitly replaces them |
| Dashboard examples and mock bridge approvals | Simulated only | Never use fictional history/costs/news as external evidence |
| ACCOUNT_LIVE | Simulated only; $300 profile basis | Stable account identity, reconciled equity/currency/exposure, snapshot age, restart and manual-trade handling required |
| Real MT5 reads and tick/property diagnostics | Implemented checks, fixture-tested only | Controlled demo-terminal tests with stale ticks, disconnects, restarts, account switches, invalid symbols and external exposure |
| Native broker order-request translation | Missing | Typed requests; broker filling modes, sessions, precision, stop/freeze levels; demo validation with exact volume |
| Native-call isolation and cancellation | Missing; worker approach is a design proposal | Bounded work, no stuck SQLite writers, recovery, concurrent-delivery tests and versioned state revalidation |
| Broker entity serving Tanzania | Missing | Dated source documents stored and independently reviewed; user-specific serving entity confirmed |
| Tanzania withdrawal/payment evidence | Manual record interface only | Actual evidence, amount/cost/time/method and independent review; no automatic approval from a checked box |
| Real provider history and edge | Missing | Provenance, drawdown including floating losses, costs, independent samples and sustained demo forward study |
| News, macro, commission, swap and slippage evidence | Missing | Named sources, timestamps, freshness/expiry rules, outage behavior, conservative uncertainty blocks |
| Currency conversion | Limited USD major-pair model | Verified conversions, cent-account denomination handling and broker profit/margin comparisons |
| Webhook validation, replay identity and credential filtering | Implemented, locally tested | Signed delivery or reviewed ingress authentication; nonce/replay policy; rate limits; secret rotation; adversarial tests |
| HTTPS, user authentication and network deployment | Missing | TLS, identity/access model, reverse-proxy trust, restricted endpoints, threat review and monitoring |
| SQLite atomicity and audit chain | Implemented, locally tested | Preserve rollback; audit chain is not administrator-proof; external anchoring is a proposal |
| Backup/restore and retention | Missing | Tested restores, consistency checks, recovery targets, retention policy and disaster exercise |
| PostgreSQL | Design target only | Repository implementation, migrations, concurrency/rollback and restore tests before claiming support |
| Operational observability | Missing | Structured redacted logs, metrics, alerts, incident procedure and documented safe shutdown |
| Live order submission and reconciliation | Intentionally absent | Human confirmation, kill switch, uncertain-fill recovery, durable order identity, restart reconciliation and independent review |

Never remove the real-evidence gate merely because a terminal connects or a test
fixture passes. Missing/stale evidence must remain an explicit NO_TRADE condition.
Stops model risk; they do not guarantee a fill or cap real gap losses.
