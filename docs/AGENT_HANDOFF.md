# Independent agent handoff

## Execution and evidence boundary

The default is `SIMULATION`. Live submission is unavailable: live-enabled startup and `LIVE_GATED` refuse, `order_send()` refuses, and no live-order HTTP route exists. A/B/C are $50/$100/$150 simulations; `ACCOUNT_LIVE` is a $300 simulation placeholder, never reconciled real equity.

Real MT5 reads are diagnostic only. External or unknown exposure blocks candidates. Otherwise the real bridge returns `REQUIRED_EXTERNAL_EVIDENCE_UNVERIFIED`, with diagnostic failures for stale/invalid ticks, symbol properties and account identity/currency. Account snapshot freshness and reconciliation remain unverified. Only an explicit in-process mock in SIMULATION can use synthetic bridge evidence; HTTP input cannot select it.

Webhook credential fields are recursively filtered, including nested lists; known authentication-secret strings are filtered too. Arbitrary free text is not guaranteed secret-free. Do not submit credentials in metadata. Failed authentication cannot reserve legitimate alert IDs. Alert IDs take precedence; without an ID, a stable signal-field hash is used. Delivery headers cannot change replay identity.

Intake, paper reservation, journal and audit commit or roll back together. SQLite holds its write lock during adapter calls. Native-call timeouts and worker isolation are missing; a slow terminal can block writes. This remains a local single-user prototype.

See [the review report](FULL_PROJECT_REPORT.md) and [future release checklist](PRELIVE_CHECKLIST.md) for verification and remaining work.


## Objective

Verify that SentinelFX implements a safety-first TradingView → internal risk engine → MT5 validation → paper decision workflow, defaults to simulation, and provides no usable live-order path.

## Open the project

Project root: the directory containing this file's parent `docs/` folder. Read `README.md` first. The original supplied assets remain separate in `prompts/`; `combined-specification.txt` is the untouched source copy.

## Expected architecture

- `engine/webhook.py`: strict TradingView payload validation, age checks, idempotency and explicit symbol mapping.
- `engine/mt5.py`: read/check wrapper with structured failures. `order_send()` must always return `LIVE_EXECUTION_NOT_IMPLEMENTED`.
- `engine/bridge.py`: persists intake and diagnostics. Real adapters block; only explicit simulation mocks reach exact-volume order check and full risk re-evaluation.
- `engine/domain.py`: deterministic `RiskManager` and `PositionSizer`; vetoes cannot be overridden by scores or input lot sizes.
- `engine/service.py`: persistent controls, duplicate checks, account/provider suspension, paper-position reservation, veto storage and audit logging.
- `migrations/003_trading_bridge.sql`: webhook, mapping, snapshot, risk, paper trade, journal and veto tables.
- `server.py`: localhost dashboard/API and authenticated webhook boundary.

## Verification commands

```sh
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -m unittest discover -s tests -v
node --check static/app.js
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -m py_compile server.py manage.py engine/*.py tests/*.py
```

Start a clean instance:

```sh
SYSTEM_MODE=SIMULATION MT5_ENABLED=false LIVE_EXECUTION_ENABLED=false \
python3 -B server.py --port 8765 --db /tmp/sentinelfx-agent-review.sqlite3
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
