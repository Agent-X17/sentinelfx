# Requirements and implementation map

## Execution and evidence boundary

The default is `SIMULATION`. Live submission is unavailable: live-enabled startup and `LIVE_GATED` refuse, `order_send()` refuses, and no live-order HTTP route exists. A/B/C are $50/$100/$150 simulations; `ACCOUNT_LIVE` is a $300 simulation placeholder, never reconciled real equity.

Real MT5 reads are diagnostic only. External or unknown exposure blocks candidates. Otherwise the real bridge returns `REQUIRED_EXTERNAL_EVIDENCE_UNVERIFIED`, with diagnostic failures for stale/invalid ticks, symbol properties and account identity/currency. Account snapshot freshness and reconciliation remain unverified. Only an explicit in-process mock in SIMULATION can use synthetic bridge evidence; HTTP input cannot select it.

Webhook credential fields are recursively filtered, including nested lists; known authentication-secret strings are filtered too. Arbitrary free text is not guaranteed secret-free. Do not submit credentials in metadata. Failed authentication cannot reserve legitimate alert IDs. Alert IDs take precedence; without an ID, a stable signal-field hash is used. Delivery headers cannot change replay identity.

Real diagnostics are collected outside the SQLite write transaction. The server uses a separate diagnostic process with a five-second deadline and one active worker; timeout/failure/busy states block candidates. Intake is then revalidated and deduplicated inside an atomic transaction. Mock paper reservation, journal and audit still commit or roll back together. This remains a local single-user prototype; real execution and reconciliation are not implemented.

See [the review report](FULL_PROJECT_REPORT.md) and [future release checklist](PRELIVE_CHECKLIST.md) for verification and remaining work.


Authority: `specs/requirements.txt` is the attached build specification. `specs/research-context.txt` is the attached research narrative. Referenced conversation instructions are additional context, not permission to place trades.

| Specification area | Implemented surface | Boundary / qualification |
|---|---|---|
| 1. Starting capital and income reality | A/B/C plus simulation-only ACCOUNT_LIVE, dashboard and simulator percentages, README | No income target optimization |
| 2. Hard risk rules | RiskManager, policy, persistent loss/exposure accounting | Conservative limits may be tightened; increases are not implemented |
| 3. Position sizing | Decimal sizing, contract-aware pip value, costs, downward grid rounding, margin | USD accounts, three major pairs; no arbitrary cross-currency conversion |
| 4. Small vs cent | Two explicitly synthetic fixtures, calculator and comparison | Real broker contract structures are unverified |
| 5. Broker diligence | Eight brands, entity mapping, source/evidence JSON, weighted score logic, full field template | No automatic data collection or current broker verification; scores withheld until evidence verified |
| 6. Withdrawal workflow | Nine-step form, persisted results, fees/dates/evidence | Records actions; does not initiate transfers or approve increased funding |
| 7. Provider research | Research cards, complete field template, metrics, sample/history checks, warnings | One fictional fixture; no real provider selection/recommendation |
| 8. Copy controls | Independent sizing, drawdown/forbidden-behavior/data-quality breakers, loss caps, allocation cap, manual review | One position maximum; no unattended resume; no configured external copy adapters |
| 9. Signal interface | TradingView webhook, manual adapter, normalizer, freshness/idempotency validation, raw+normalized persistence | Public HTTPS ingress is not deployed |
| 10. Scoring | Weighted evidence, penalties, WATCHLIST, veto before persistence | Deterministic synthetic evidence; no AI policy override |
| 11. Strategy library | 15 templates, editable rules, cost analytics, implemented MA OHLC backtester | Other methodologies remain templates; no edge claims; regime/parameter analysis requires research |
| 12. Performance database | All named tables, JSON research fields, three migrations, foreign keys and indexes | SQLite implemented. PostgreSQL migration documented, not built/tested |
| 13. Learning | Sample count, Wilson interval, in/out-of-sample separation, no automatic risk increase | No automatic learned weight promotion; protects against spurious small-sample learning |
| 14. Modular architecture | Domain services, webhook/bridge, read-check MT5 adapter, application orchestration, repository, research services, HTTP boundary | News/macro data remain synthetic inputs, not integrated feeds |
| 15. UI | All eight pages, calculations, account state, raw signals, reasons, research/withdrawal forms, audit export | Recent decisions/outcomes/audit UI is capped at 100; full history remains in SQLite |
| 16. Execution policy | Simulation/paper/connected-disabled modes; mock-only exact-volume order check; live startup refusal | order_send always refuses; live execution is intentionally absent |
| 17. Tests | Domain, randomized invariants, concurrency, persistence, HTTP, security, backtest/no-lookahead | Model correctness is not proof of real-world fills or investment performance |
| 18. Final principle | Fail-closed response and atomic audited decision path | Audit persistence is required before returning approval |

## Decision ordering

1. Validate account certainty, currency, freshness and status.
2. Validate broker constraints and, for nonsynthetic records, exact Tanzania entity and evidence.
3. Normalize and validate required signal fields, prices and timestamps.
4. Validate provider eligibility, sample size, history and risk flags.
5. Validate current market/news/macro context, session, liquidity, spread, execution quality, regime evidence and conflicts.
6. Calculate stop distance and all-in cost per lot; bound risk by account and provider budgets.
7. Calculate safe broker-grid size; enforce minimum, maximum, exposure, correlation, margin and projected margin level.
8. Apply RiskManager veto; evidence scoring can only further reject or watchlist.
9. Persistent safety gate checks duplicates, immutable simulation boundary, provider count, account daily latch and audit integrity.
10. Atomically record the decision, provider suspension if necessary, open simulated position if approved, and audit snapshot. No scores or client values can reverse a veto.

## Explicit limits, rather than silent placeholders

- The product is a working local simulation/research implementation, not a production/live trading stack.
- Broker evidence, regulator checks and Tanzania withdrawals have not been re-researched or verified here. Uncertainty remains visible.
- Broker weighting is implemented but no seeded brand is eligible for a score; missing evidence is not scored as zero or invented.
- Provider historical metric fields are flexible JSON. Real provider histories, drawdown series and performance feeds require authorized imports.
- No artificial equity/history chart is shown as real market performance. Account charts use actual recorded simulation outcomes.
- Backtests do not auto-promote strategies or providers. A null Sharpe/Sortino is preferable to a meaningless estimate.
- Withdrawal success is evidence, not an automatic funding recommendation.
- Account suspensions and audit records cannot be silently cleared through the dashboard.
