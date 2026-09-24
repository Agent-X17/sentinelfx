# Requirements and implementation map

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

Authority: `specs/requirements.txt` is the attached build specification. `specs/research-context.txt` is the attached research narrative. Referenced conversation instructions are additional context, not permission to place trades.

| Specification area | Implemented surface | Boundary / qualification |
|---|---|---|
| 1. Starting capital and income reality | A/B/C plus MT5-backed ACCOUNT_LIVE, dashboard and simulator percentages, README | No income target optimization |
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
| 16. Execution policy | Simulation/paper/connected-disabled modes; exact-volume MT5 order check; live startup refusal | order_send always refuses; live execution is intentionally absent |
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
