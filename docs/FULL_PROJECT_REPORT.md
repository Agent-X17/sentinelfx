# SentinelFX — Full Project Truth Report

Report date: 2026-09-24  
Project folder: `/Users/princebenny/Documents/Codex/2026-09-21/referenced-chatgpt-conversation-this-is-an-2/outputs/forex-engine`  
Review archive: `/Users/princebenny/Documents/Codex/2026-09-21/referenced-chatgpt-conversation-this-is-an-2/outputs/sentinelfx.zip`

## 1. Purpose of this report

This document is a candid handoff for another coding agent. It records what SentinelFX currently is, what was implemented, what was tested, which claims are supported, which parts are synthetic, what was deliberately disabled, and what remains incomplete.

The project is a local safety, research, simulation and decision-control prototype. It is not a production trading service and it cannot send live broker orders.

The most important current fact is:

> A real MT5 connection is insufficient for approval. The real-terminal bridge is deliberately stopped with `REQUIRED_EXTERNAL_EVIDENCE_UNVERIFIED`. Only explicit in-process mock fixtures can traverse the complete TradingView-to-paper-decision bridge.

## 2. User objective preserved

SentinelFX was built for a Tanzanian learner using very small capital. Its priority order is:

1. preserve capital;
2. learn;
3. prove an edge;
4. become consistent;
5. compound;
6. increase capital;
7. only then consider larger income.

The fixed small-account profiles remain:

| Profile | Seed equity | Maximum modeled risk per trade | Daily loss cap | Weekly loss cap |
|---|---:|---:|---:|---:|
| `ACCOUNT_A` | $50 | $0.25 | $1.00 | $2.00 |
| `ACCOUNT_B` | $100 | $0.50 | $2.00 | $4.00 |
| `ACCOUNT_C` | $150 | $0.75 | $3.00 | $6.00 |

`ACCOUNT_LIVE` is currently a $300 simulation placeholder. It is not synchronized with a verified trading account. The risk basis is capped conservatively with `min(current equity, starting equity/profile basis)`.

No part of the implementation treats $50–$100 per day as a realistic required return from these accounts.

## 3. Authoritative source material

The supplied material is preserved in separate files:

- `prompts/build-prompt.txt`
- `prompts/master-agent-prompt.txt`
- `prompts/execution-agent-prompt.txt`
- `prompts/instructions-preamble.txt`
- `prompts/combined-specification.txt`
- `specs/requirements.txt`
- `specs/research-context.txt`

The build, master-agent and execution-agent prompts were intentionally kept separate. Runtime code does not concatenate them or use a language model to override deterministic risk controls.

## 4. Current architecture

The intended flow is:

```text
TradingView alert
    ↓
HTTP webhook authentication and validation
    ↓
Idempotency and explicit symbol mapping
    ↓
MT5 diagnostic reads
    ↓
Real adapter: NO_TRADE because external evidence is incomplete
    OR
Explicit test mock: risk preview and exact-volume order_check
    ↓
Full deterministic RiskManager re-evaluation
    ↓
Atomic simulation/paper record, journal and audit
```

The dashboard simulator has a separate explicit synthetic path:

```text
Dashboard scenario or calculator
    ↓
Synthetic broker/provider/market fixture
    ↓
The same deterministic domain RiskManager and PositionSizer
    ↓
APPROVED_SIMULATED_TRADE, WATCHLIST or NO_TRADE
```

A displayed BUY or SELL in the bridge response means an approved simulation candidate. It never means an order was sent.

## 5. Implementation by file

### Core domain

`engine/domain.py`

Implements:

- Decimal-based money and position calculations;
- account profiles and policy limits;
- signal normalization and validation;
- stop-first sizing;
- contract-aware pip values;
- spread, commission, slippage and swap cost modeling;
- downward broker-grid rounding;
- margin and projected margin-level checks;
- daily, weekly and provider budgets;
- account, provider, market, evidence and execution vetoes;
- BUY/SELL candidate approval only after all deterministic gates;
- WATCHLIST for insufficient evidence after otherwise safe gates;
- NO_TRADE for hard failures.

RiskManager remains the final authority. Scores and client input cannot reverse its vetoes.

### Application service

`engine/service.py`

Implements:

- persistent account refresh;
- realized loss accounting;
- open-risk and used-margin reservation;
- daily latch and weekly suspension;
- provider circuit breakers;
- duplicate signal handling;
- decision persistence;
- simulated position open/close;
- paper-trade records;
- veto-reason records;
- withdrawal evidence records;
- strategy/provider/broker research records;
- dashboard state snapshots;
- exact checked-volume requirement for trusted bridge approvals.

The `preview()` calculation cannot authorize or persist a position. `evaluate()` repeats the decision before persistence.

### TradingView intake

`engine/webhook.py`

Implements:

- required-field validation;
- BUY/SELL validation;
- timezone-aware freshness validation;
- future-alert rejection;
- stop-loss requirement;
- numeric validation;
- canonical symbol normalization;
- explicit one-to-one MT5 mapping;
- unmapped and ambiguous symbol rejection;
- alert-ID-first idempotency;
- HMAC constant-time shared-secret comparison.

A top-level JSON `secret` is removed. Nested credential-like fields and known authentication-secret strings are filtered before webhook persistence and audit. Free text under arbitrary keys is not guaranteed secret-free; do not put credentials in metadata.

### MT5 adapter

`engine/mt5.py`

Implements optional wrappers for:

- initialize and shutdown;
- terminal/account/symbol/tick reads;
- symbol selection;
- positions and orders;
- deal history and rate history;
- margin and profit calculation;
- order check.

Safety behavior:

- missing package and connection failures become structured errors;
- terminal connectivity is rechecked for each real adapter call;
- namedtuple result fields are retained;
- numeric return values are retained;
- false operations fail;
- missing/nonzero order-check return codes fail;
- `order_send()` always returns `LIVE_EXECUTION_NOT_IMPLEMENTED`.

The current bridge does not construct a complete native MT5 market-order request. It uses a simplified request only with the mock adapter. This is safe because all real adapters are blocked before order check, but it is incomplete future integration work.

### Bridge orchestration

`engine/bridge.py`

Implements:

- raw webhook persistence;
- duplicate rejection;
- secret removal;
- structured rejection statuses;
- MT5 status, symbol, tick, account, positions and orders reads;
- account-wide external exposure block;
- unknown-exposure block;
- unconditional real-adapter evidence block;
- exact mock position-size order check;
- final risk re-evaluation;
- paper decision, journal and audit persistence;
- explicit `order_sent: false`;
- explicit `SYNTHETIC_TEST_FIXTURE` label.

Each bridge request uses one SQLite write transaction. Nested application transactions share that thread-local connection. This closes races between duplicate detection, preview, size check, reservation, journal and audit. If journal/audit persistence fails, the entire candidate rolls back.

Tradeoff: MT5/mock calls occur while the SQLite write lock is held. A slow or stuck terminal can delay other local writers. This is acceptable for the current single-user local safety prototype but is not a scalable production design.

### Persistence

`engine/storage.py` and `migrations/*.sql`

SQLite is used with:

- WAL mode;
- foreign keys;
- immediate write transactions;
- ordered migrations;
- JSON payload documents;
- indexes;
- an application hash chain for audit entries;
- triggers preventing application updates/deletes of audit rows;
- a unique open-position constraint.

Migrations currently applied to a fresh database: `1, 2, 3`.

The schema includes accounts, providers, broker profiles, strategies, raw/normalized signals, decisions, risk checks, veto reasons, positions, paper trades, signal outcomes, journal entries, broker/account snapshots, withdrawal tests, backtests, replay sessions, symbol mappings, settings, loss locks and audit logs.

PostgreSQL is not implemented or tested. The storage layer is only a boundary for future porting.

The audit chain detects ordinary alteration but is not externally anchored. A person with database-file and code access can rewrite the database and recompute hashes. It must not be described as administrator-proof.

### Local HTTP server

`server.py`

Implements:

- localhost-only binding;
- static dashboard assets;
- health/state/MT5/sample endpoints;
- TradingView webhook endpoint;
- CSRF token for dashboard mutations;
- host validation;
- origin validation for dashboard mutations;
- CSP and no-store headers;
- request-size limit;
- strict JSON parsing;
- structured fail-closed errors;
- research/backtest/withdrawal endpoints;
- no live execution endpoint.

Webhook authentication failure returns 401, malformed input 400, stale/unmapped input 422, duplicates 409, and processed diagnostic/risk blocks 200 with NO_TRADE. HTTP acceptance never means order approval. See API.md.

The server has no TLS, user accounts, sessions suitable for a network service, reverse-proxy trust configuration, rate limiting, native-call timeouts, metrics or production logging. HTTP socket inactivity is bounded at 10 seconds.

### Dashboard

`static/index.html`, `static/app.js`, `static/style.css`

Pages include:

- overview;
- account simulator;
- broker comparison;
- provider research;
- signal decisions;
- strategy research/backtests;
- withdrawal evidence;
- audit trail.

The dashboard states:

- system mode;
- MT5 status;
- live send off;
- webhook authentication state;
- real evidence unverified;
- simulator data synthetic;
- BUY/SELL does not mean order sent;
- recent raw alerts, normalized signals, mappings and risk checks.

The dashboard must be served by `server.py`. Opening `static/index.html` directly as a `file://` URL produces unstyled text and a permanent “LOADING MODE” because `/style.css`, `/app.js` and `/api/state` are server routes.

The user encountered exactly this condition in the supplied screenshot. The page was opened with a browser “File” address instead of `http://127.0.0.1:<port>/`.

### Research and backtesting

`engine/research.py`

Includes:

- supplied trade-observation cost analysis;
- chronological synthetic OHLC moving-average research;
- in/out-of-sample separation;
- explicit modeled costs;
- drawdown, profit factor and Wilson interval;
- doubled-cost sensitivity;
- no-lookahead regression coverage.

This is a research tool, not proof of a profitable strategy. Other listed strategy families remain templates.

## 6. Data models and seeded information

The seeded broker brands are research placeholders with uncertain status. They are not current broker recommendations.

The demo cent and demo standard broker records are synthetic fixtures. Their contract sizes and conditions are for deterministic exercises.

The demo provider is fictional. Its history and quality metrics are invented for tests.

The TradingView provider created inside the mock bridge uses invented history solely to satisfy deterministic fixture gates. The real-adapter block occurs before that provider can be created or used.

No real news, macro, legal-entity, commission, swap, slippage, provider-performance or withdrawal facts are automatically fetched.

## 7. Account and risk behavior

Implemented protections include:

- 0.5% default profile risk;
- conservative daily and weekly loss caps;
- provider-specific budgets;
- one open position;
- reserved open risk;
- transaction-cost cap;
- spread threshold;
- reward/risk threshold;
- correlation and volatility gates;
- market/session/liquidity checks;
- provider history and drawdown gates;
- forbidden strategy flags;
- margin allocation and projected margin level;
- never rounding position size up;
- no approval when the broker minimum exceeds risk;
- daily stop latch;
- weekly suspension requiring review;
- provider suspension after repeated losses;
- no client override of stored equity or budgets.

Profits do not erase gross loss consumption. Risk does not automatically increase after profits because profile basis is capped.

Modeled stops are not guaranteed fill prices. Real gaps and slippage can exceed the model.

## 8. Work completed over the project history

### Initial local simulator

Built:

- deterministic risk and sizing domain;
- SQLite schema and audit trail;
- A/B/C accounts;
- synthetic cent/standard brokers;
- provider research and circuit breakers;
- position lifecycle;
- strategy templates and backtesting;
- dashboard;
- HTTP security controls;
- Mac launcher;
- tests and documentation.

### Trading workflow upgrade

Added:

- runtime configuration modes;
- TradingView webhook validation;
- symbol mapping;
- optional MT5 wrapper;
- bridge orchestration;
- migration 003;
- raw webhooks and normalized signals;
- account/broker snapshots;
- risk checks, journal, paper trades and veto reasons;
- ACCOUNT_LIVE placeholder;
- exact calculated-volume check;
- separate prompt assets;
- dashboard workflow status.

### Compatibility fixes

Fixed:

- retained databases missing ACCOUNT_LIVE;
- exact-volume preflight;
- duplicate alert ID behavior;
- launcher naming and executable permission;
- SentinelFX branding.

### Safety hardening review

Fixed:

- fabricated live evidence could no longer approach approval;
- real adapters are now blocked for incomplete evidence;
- MT5 order-check result codes are validated;
- namedtuple MT5 responses retain fields;
- numeric/false MT5 returns are handled correctly;
- terminal disconnect is detected per operation;
- failed auth cannot poison a real alert ID;
- alert ID wins over changeable delivery header;
- top-level body secret is removed;
- external positions and pending orders block;
- unknown exposure blocks;
- concurrent delivery is serialized;
- journal failure rolls back the entire candidate;
- external webhook hosts require a secret;
- fixture results declare their synthetic source and `order_sent: false`;
- dashboard displays the unverified-evidence boundary.

No schema migration was added for this hardening pass.

## 9. Testing truth

The current review passes **142 tests**, including all 125 baseline tests and 17 new pre-live regressions. Exact commands and runtime evidence are in [VERIFICATION.md](VERIFICATION.md). These test deterministic local behavior, not broker fills, profitability or production security.

## 10. Runtime verification truth

A fresh `/tmp/sentinelfx-final-prelive.sqlite3` instance started on port 8877 with SIMULATION and MT5/live disabled. Root, JavaScript, CSS, health and state returned 200; audit integrity and migrations 1–3 passed. No live endpoint exists (404). Separate actual startup processes refused both live-enabled configuration and LIVE_GATED with exit code 1.

Actual Chromium browser verification is blocked by the macOS sandbox: `bootstrap_check_in ... Permission denied (1100)`. Agent-browser and an independent downloaded headless Chromium both failed before page load. Do not claim a browser visual/accessibility pass. See VERIFICATION.md for separate DOM-harness results and their limits.

The launcher now optionally falls back from occupied port 8765 to an available localhost port. Open the printed URL; direct file opening is unsupported.

## 11. Live-execution boundary

All of the following are true:

- default mode is `SIMULATION`;
- `LIVE_EXECUTION_ENABLED=true` refuses startup;
- `LIVE_GATED` refuses startup;
- `MT5Service.order_send()` always refuses;
- there is no `/api/execute` route;
- dashboard research cannot grant execution permission;
- a real MT5 adapter is blocked for missing external evidence;
- no broker credentials were added;
- no live order was sent during development or verification.

This project is incapable of live order submission in its current form.

## 12. Known limitations and unresolved weaknesses

### Real broker and market integration

- No real MT5 terminal was used for the recorded verification.
- No real broker account was connected.
- Native MT5 request translation is incomplete.
- Real-adapter tick age, quote, symbol-property and account-identity/currency diagnostic checks exist, but have only fixture verification. Account snapshot freshness is not established; reconciliation remains blocked.
- Trading session schedules and fill policies are not fully validated.
- Currency conversion beyond the supported USD major-pair model is incomplete.
- Full reconnect, restart and manual-terminal reconciliation is not implemented.
- Any external position or pending order currently blocks all new bridge candidates rather than calculating combined exposure.
- Real commission, swap and slippage are unavailable.
- News and macro feeds are unavailable.
- Exact Tanzania-serving legal entity and withdrawal rails are unverified.
- Provider history is unavailable.
- The real bridge therefore always returns NO_TRADE after diagnostic reads.

### Atomicity and performance

- The SQLite write lock spans external adapter calls.
- A slow MT5 call can block dashboard writes.
- No adapter call timeout/cancellation layer exists.
- No job queue or worker isolation exists.
- The approach is appropriate only for the current local single-user prototype.

### Webhook and network security

- Local webhook authentication may be disabled by leaving the secret blank.
- External allowed hosts require a secret, but Host headers alone are not a production trust boundary.
- No TLS is provided.
- No rate limiting exists.
- No signed nonce store exists beyond alert ID/stable-field idempotency. Changing delivery headers cannot bypass identity.
- Authentication failures return HTTP 401; processed risk blocks still return HTTP 200 with NO_TRADE.
- Nested credential fields and known webhook-secret values are recursively filtered. Unknown secrets embedded in arbitrary free text can still persist.
- No production secret manager exists.
- No public TradingView HTTPS ingress was deployed.

### Persistence and audit

- SQLite is local and single-host.
- PostgreSQL is unimplemented.
- No backup/restore workflow is implemented.
- No data retention or archival policy exists.
- Audit hashes are not externally anchored.
- A local administrator can alter files and recompute a chain.
- Recent dashboard lists are capped; full data remains in SQLite.
- The working database contains the user’s prior local simulations and should not be shared casually.
- The ZIP excludes `data/`, so a reviewer using only the ZIP gets a fresh database and not the working history.

### Product and UI

- The app must be served; direct file opening does not work.
- The launcher selects an available port when 8765 is occupied. It does not stop or identify the old process; users must open the newly printed URL.
- Visual browser verification is incomplete.
- Accessibility has only limited automated/structural verification.
- No user authentication or multi-user isolation exists.
- Error logging is intentionally quiet in the local HTTP server, which makes diagnosis harder.
- API errors sometimes hide internal detail behind generic 503 responses.
- Implementation documents were reconciled in this pass. Supplied prompt/specification assets are preserved requirements and are not claims of implementation.
- The Python package name in `pyproject.toml` remains `small-capital-forex-engine` even though product branding is SentinelFX.
- The project directory remains named `forex-engine`.

### Strategy and performance claims

- There is no proof of profitable edge.
- Synthetic backtests are not live evidence.
- Synthetic provider metrics are not evidence.
- No forward demo study has been completed.
- No withdrawal test has been verified by this code review.
- No seeded broker is recommended.
- No performance guarantee is made.

### Source control and reproducibility

The project is now a public Git repository at https://github.com/Agent-X17/sentinelfx. Baseline commit f7e3985 contains the initial combined implementation; earlier edits were not individually committed. This pass is a separate branch/commit. See FINAL_PRELIVE_REVIEW.md for provenance.

## 13. Current data and archive handling

Working database files:

- `data/engine.sqlite3`
- `data/engine.sqlite3-shm`
- `data/engine.sqlite3-wal`

These retain local state. Do not delete them to clear a risk suspension or audit issue.

The review ZIP intentionally excludes:

- `data/`;
- `__pycache__/`;
- bytecode;
- `.DS_Store`;
- Git metadata.

This makes the ZIP safer to share and reproducible from a fresh database, but it does not include the user’s working records.

## 14. How another agent should start

Read in this order:

1. this report;
2. `README.md`;
3. `docs/VERIFICATION.md`;
4. `docs/AGENT_HANDOFF.md`;
5. `docs/REQUIREMENTS.md`;
6. `docs/NEXT_AGENT_PROMPT.txt`;
7. `prompts/README.md`;
8. all three separate prompt files;
9. `engine/domain.py`;
10. `engine/service.py`;
11. `engine/bridge.py`;
12. `engine/mt5.py`;
13. migrations and tests.

Run:

```sh
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -m unittest discover -s tests -v
node --check static/app.js
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -m py_compile server.py manage.py engine/*.py tests/*.py
```

Use a fresh temporary database and an available port for runtime verification. Do not delete the working database.

## 15. Recommended next improvements

Priority order:

1. Add adapter timeouts and move diagnostic MT5 calls outside the SQLite write transaction while preserving a safe version/revalidation protocol.
2. Extend existing blocked-candidate normalized-signal/risk rows into a versioned diagnostic snapshot/reconciliation design.
3. Validate existing tick-age/currency/property diagnostics on a real demo terminal; implement market-session and account freshness checks.
4. Design a typed native MT5 request translator, but keep order submission disabled.
5. Implement explicit terminal/account reconciliation with stable account identity and restart behavior.
6. Add a real evidence-provider interface for costs, news, macro context, legal entity and provider history.
7. Add public ingress with TLS, rate limits, signed requests and nonce controls; review remaining free-text secret leakage.
8. Verify documented HTTP response semantics against real TradingView delivery/retry behavior.
9. Add backup/restore, retention and externally anchored audit export.
10. Verify launcher behavior on additional Mac installations.
11. Perform a real browser accessibility and interaction review.
12. Preserve Git review history and require verification evidence for future changes.

Do not remove the real-evidence block until every required source is implemented, tested and reviewed.

## 16. Statements the next agent must not make

Do not claim:

- that SentinelFX is production ready;
- that it is a live trading bot;
- that MT5 execution works end to end;
- that a real broker is verified;
- that Tanzania withdrawals are verified;
- that the system is profitable;
- that browser visuals were fully tested;
- that PostgreSQL is supported;
- that the audit log is tamper-proof;
- that ACCOUNT_LIVE represents a real synchronized account;
- that all historical changes have Git provenance.

## 17. Final truth statement

SentinelFX is a substantial, working local simulation and risk-control codebase with deterministic Decimal sizing, persistent safety state, a usable dashboard, webhook validation, an MT5 diagnostic boundary, atomic paper-decision persistence, backtesting/research tools and 142 passing tests.

Its safety posture is intentionally conservative: live submission is impossible, real-terminal candidates are blocked, unknown evidence becomes NO_TRADE, and synthetic fixtures are labeled.

It still requires significant integration, security, reconciliation, operational and real-world verification work before it could be considered a demo-connected trading control system, and much more before any live-execution discussion.


Current pass: [FINAL_PRELIVE_REVIEW.md](FINAL_PRELIVE_REVIEW.md). Future gates: [PRELIVE_CHECKLIST.md](PRELIVE_CHECKLIST.md). Browser verification is still incomplete; this is not a production-readiness sign-off.
