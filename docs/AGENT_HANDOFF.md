# Independent agent handoff

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

## Objective

Verify that SentinelFX implements a safety-first TradingView → internal risk engine → MT5 validation → paper decision workflow, defaults to simulation, and provides no usable live-order path.

## Open the project

Project root: the directory containing this file's parent `docs/` folder. Read `README.md` first. The original supplied assets remain separate in `prompts/`; `combined-specification.txt` is the untouched source copy.

## Expected architecture

- `engine/webhook.py`: strict TradingView payload validation, age checks, idempotency and explicit symbol mapping.
- `engine/mt5.py`: read/check wrapper with structured failures. `order_send()` must always return `LIVE_EXECUTION_NOT_IMPLEMENTED`.
- `engine/bridge.py`: persists intake, gets broker truth, previews risk, runs MT5 `order_check` using the exact calculated volume, then calls the full application decision path.
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
