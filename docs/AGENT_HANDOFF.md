# Independent agent handoff

Current Windows blocker diagnostics: [HFM external-clock diagnostics](HFM_CLOCK_SOURCE_DIAGNOSTICS.md)
adds redacted, CLI-only classification for the two fixed NTP and two fixed
HTTPS sources. It does not change normalization, freshness, proposals, the
kill switch, execution, Windows, or MT5. Real Windows results are still needed.
The complete repository suite currently passes: `280 tests`; the acceptance
gate also passes.

Implementation update: [HFM read-only normalization](HFM_READONLY_NORMALIZATION.md)
adds an explicit CLI-only policy and measurements after the operator requested
implementation. Default application/proposal timestamp evaluation is unchanged.
Real Windows validation remains outstanding; do not assume a PASS.

Latest timestamp evidence/design (2026-09-26):
[inactive candidate policy revision 1](MT5_TIMESTAMP_CANDIDATE_POLICY.md).
Records the MQL5 moderator's broker-time statement and the operator-supplied HFM
email. These are supporting evidence, not activation approval. Requires distinct
tick/copy_ticks proof, exact Python/MT5/build/server bindings, independent timing
evidence and rejection of DST ambiguity or offset changes. No runtime changes.

Current Windows blocker: see [MT5 timestamp investigation](MT5_TIMESTAMP_INVESTIGATION.md). A roughly three-hour future tick remains NO_TRADE; no trusted nonzero offset is available. The read-only verifier now captures per-call UTC bounds, runtime/build identity, progression and apparent-difference stability, and can write a redacted support report. HFM's dated GMT+2/GMT+3 server-clock policy is context only because it does not define Python tick epoch semantics. Keep proposals disabled and the kill switch active.

Start with [CURRENT_PROGRESS_REPORT_2026-09-25.md](CURRENT_PROGRESS_REPORT_2026-09-25.md). It records the latest repository acceptance result plus the manually verified TradingView delivery and Mac MT5 demo-monitor progress. It supersedes older runtime and test counts in historical reports.

## Execution and evidence boundary

The default is `SIMULATION`. Live submission is unavailable: live-enabled startup and `LIVE_GATED` refuse, `order_send()` refuses, and no live-order HTTP route exists. A/B/C are $50/$100/$150 simulations; `ACCOUNT_LIVE` is a $300 simulation placeholder, never reconciled real equity.

Real MT5 reads/checks are eligible only for proposal evidence through `sentinelfx.mt5.readonly-evidence.v1`. Missing, stale, mismatched, non-demo, exposed, or uncertain evidence returns `NO_TRADE`; a separately isolated exact-volume `order_check` is mandatory. HTTP input cannot select the adapter, identity, or evidence source.

Webhook credential fields are recursively filtered, including nested lists; known authentication-secret strings are filtered too. Arbitrary free text is not guaranteed secret-free. Do not submit credentials in metadata. Failed authentication cannot reserve legitimate alert IDs. Alert IDs take precedence; without an ID, a stable signal-field hash is used. Delivery headers cannot change replay identity.

Real diagnostics are collected outside the SQLite write transaction. The server uses a separate diagnostic process with a five-second deadline and one active worker; timeout/failure/busy states block candidates. Intake is then revalidated and deduplicated inside an atomic transaction. Mock paper reservation, journal and audit still commit or roll back together. This remains a local single-user prototype; real execution and reconciliation are not implemented.

Operator additions from the final delivery pass:

- `python3 -B server.py --demo --port-fallback` creates a fresh timestamped database with three synthetic decisions and prints every relevant URL and runtime fact.
- `/status` is a human-readable local status page; `/api/health` is the machine-readable version.
- `MT5_DIAGNOSTIC_MODE=disabled|mock|real` selects an explicit diagnostic boundary. `mock` is synthetic and is always stopped by `REQUIRED_EXTERNAL_EVIDENCE_UNVERIFIED`.
- `scripts/test_webhook.py` creates a fresh local TradingView-style alert. External TradingView delivery still requires a separately installed HTTPS tunnel, a strong secret, and the exact `WEBHOOK_ALLOWED_HOSTS` value.
- Real diagnostic identity drift can be reset only through the CSRF-protected local endpoint with an exact confirmation string. The request is written to the audit chain before the latch is cleared.
- `scripts/acceptance_check.py` is the single current release gate. It creates its own timestamped database, exercises runtime and webhook failures, proves live refusal, and runs all tests last.

See [the review report](FULL_PROJECT_REPORT.md) and [future release checklist](PRELIVE_CHECKLIST.md) for verification and remaining work.


## Objective

Verify that SentinelFX implements a safety-first TradingView → internal risk engine → MT5 validation → paper decision workflow, defaults to simulation, and provides no usable live-order path.

## Open the project

Project root: the directory containing this file's parent `docs/` folder. Read `README.md` first. The original supplied assets remain separate in `prompts/`; `combined-specification.txt` is the untouched source copy.

## Expected architecture

- `engine/webhook.py`: strict TradingView payload validation, age checks, idempotency and explicit symbol mapping.
- `engine/mt5.py`: read/check wrapper with structured failures. `order_send()` must always return `LIVE_EXECUTION_NOT_IMPLEMENTED`.
- `engine/bridge.py`: persists intake and diagnostics. Explicit simulation mocks and strictly validated isolated real snapshots can reach a non-submitting exact-volume check and final risk re-evaluation; every other real adapter blocks.
- `engine/domain.py`: deterministic `RiskManager` and `PositionSizer`; vetoes cannot be overridden by scores or input lot sizes.
- `engine/service.py`: persistent controls, duplicate checks, account/provider suspension, paper-position reservation, veto storage and audit logging.
- `migrations/003_trading_bridge.sql`: webhook, mapping, snapshot, risk, paper trade, journal and veto tables.
- `server.py`: localhost dashboard/API and authenticated webhook boundary.

## Verification commands

Run the acceptance gate first:

```sh
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -B scripts/acceptance_check.py
```

The current verified result after clock-source diagnostic coverage is `280 tests`
and `ACCEPTANCE RESULT: PASS`.

```sh
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -m unittest discover -s tests -v
node --check static/app.js
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -m py_compile server.py manage.py engine/*.py tests/*.py
```

Start a clean, populated instance:

```sh
SYSTEM_MODE=SIMULATION MT5_DIAGNOSTIC_MODE=mock LIVE_EXECUTION_ENABLED=false \
python3 -B server.py --demo --port 8765
```

Then open `http://127.0.0.1:8765/` and verify Overview, Account simulator, Broker comparison, Provider research, Signal decisions, Research library, Withdrawal tests and Audit trail.

## Safety cases to inspect

1. `Settings(... live_execution_enabled=True).validate()` refuses construction.
2. `Settings(mode="LIVE_GATED").validate()` refuses construction.
3. `MT5Service.order_send({})` refuses regardless of input.
4. An incorrect webhook secret is persisted as rejected and returns `NO_TRADE`.
5. Duplicate, stale, unmapped and missing-stop alerts fail closed.
6. Disconnected or restricted MT5 state blocks the candidate.
7. The mock MT5 `order_check_requests[0]["volume"]` equals the final calculated position size.
8. A broker minimum that exceeds the risk budget returns `NO_TRADE`.
9. Open risk is reserved atomically, and concurrent candidates cannot exceed the one-position limit.
10. Audit integrity failure prevents approval.

## Known limits

The MT5 adapter is not enabled by the Mac launcher. Real news/macro feeds, exact Tanzania legal entity, withdrawal rails, commission/swap configuration, public HTTPS ingress, user authentication and production deployment are not complete. The runtime MT5 broker profile is therefore unverified. These are intentional visible limits, not evidence of live readiness.

## Manual-confirmed demo proposal handoff

- Migration `004_demo_trade_proposals.sql` adds durable proposals and append-only transition history.
- `engine/service.py` owns stricter proposal sizing, persistence, expiry, and local review transitions.
- `engine/bridge.py` creates proposals only when the explicit feature flag is on, the kill switch is clear, expected demo identity matches, timestamps are fresh, exposure is empty, all normal risk checks pass, and a read-only order check confirms the exact volume.
- `server.py` exposes only `/api/demo-proposals/review`; the existing local Host/Origin and CSRF checks apply. There is no execution endpoint.
- `static/app.js` adds the Demo proposals review page and permanent no-send warning.
- Approval changes state only. `MT5Service.order_send()` still refuses and no new call site exists.
- Phase 2 adds `sentinelfx.mt5.readonly-evidence.v1`: strict real snapshot validation plus a separately isolated, non-submitting exact-volume `order_check`. Evidence acquisition runs outside database write locks and the final proposal transaction recalculates risk against the checked volume. See `MT5_READ_ONLY_EVIDENCE_PROTOCOL.md`.
- The implementation is fixture-tested but not yet reproduced against the intended Windows demo terminal. Do not claim execution readiness or weaken any failure to make a proposal appear.
