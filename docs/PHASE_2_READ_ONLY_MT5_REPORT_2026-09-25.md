# SentinelFX Phase 2 report — verified read-only MT5 evidence

## Outcome

SentinelFX can now accept a strictly validated, isolated MT5 demo snapshot as evidence for a local `DEMO_TRADE_PROPOSAL`. A separate worker can perform the exact-volume broker `order_check`. Neither proposal creation nor approval submits an order.

**DEMO ORDER NOT SENT — EXECUTION IS NOT IMPLEMENTED.**

The feature defaults remain unchanged:

```text
DEMO_TRADE_PROPOSALS_ENABLED=false
DEMO_TRADE_PROPOSAL_KILL_SWITCH=true
```

## Files changed

- `engine/mt5_evidence.py`: versioned protocol, strict snapshot validation, exact request validation and order-check validation.
- `engine/mt5_worker.py`: bounded `snapshot` and `order_check` operations; no submission operation.
- `engine/isolated_mt5.py`: process isolation, timeout/busy handling, identity drift latch and validated read-only check method.
- `engine/mt5.py`: adds read-only recent-order history retrieval. Its pre-existing `order_send` method still refuses.
- `engine/bridge.py`: real evidence routing, strict failure behavior, out-of-transaction reads/check and proposal integration.
- `engine/service.py`: final in-transaction risk recalculation and exact checked-volume comparison.
- `tests/test_mt5_evidence.py`: success, failure, redaction, no-submission-source and integration fixtures.
- `README.md`, `docs/API.md`, `docs/AGENT_HANDOFF.md`, `docs/CURRENT_PROGRESS_REPORT_2026-09-25.md`, `docs/NEXT_AGENT_PROMPT.txt`, `docs/PRELIVE_CHECKLIST.md`, `docs/REQUIREMENTS.md`, `docs/VERIFICATION.md`: current behavior and limits.
- `docs/MT5_READ_ONLY_EVIDENCE_PROTOCOL.md`: protocol and Windows demo-host verification procedure.

## Database changes

None. Phase 2 reuses the durable proposal and append-only history schema from migration 004. No credential or full account identifier is added to persistence.

## Evidence and behavior

The snapshot must prove the expected local login/server pair, MT5 demo mode, USD currency, balances and margin, connected terminal with AutoTrading off, exact server-mapped symbol, fresh bid/ask, volume grid, digits/point, stop/freeze levels, filling mode, and zero open positions, pending orders or recent external activity. Any missing, stale, malformed, mismatched or uncertain field returns `NO_TRADE` and creates no proposal.

The server calculates a conservative volume under the existing profile cap and stricter 0.25% proposal risk limit. The actual $500 balance is displayed as redacted account evidence but does not loosen the existing `$50/$100/$150/$300` simulation profile caps. The exact volume, prices and filling mode are validated against the snapshot and passed to an isolated MT5 `order_check`. The final database transaction recalculates risk and requires the same volume before writing a proposal.

## Verification performed

```sh
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -B scripts/acceptance_check.py
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -B -m unittest discover -s tests -v
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -B -m py_compile server.py manage.py engine/*.py tests/*.py
node --check static/app.js
git diff --check
```

Results:

- acceptance: `ACCEPTANCE RESULT: PASS`;
- Python: 201 tests, zero failures/errors;
- Python compilation: pass;
- frontend JavaScript syntax: pass;
- patch whitespace validation: pass;
- live-enabled startup and `LIVE_GATED`: refused;
- `/api/execute`: absent/404;
- `MT5Service.order_send`: returns `LIVE_EXECUTION_NOT_IMPLEMENTED`.

The intended Windows MT5 terminal was unavailable on the development Mac. Native terminal behavior remains to be reproduced manually; it is not claimed as verified here.

## Start and review locally on the supported MT5 host

Follow `docs/MT5_READ_ONLY_EVIDENCE_PROTOCOL.md`. Keep Algo Trading off. Set terminal path and expected demo identity only in the local PowerShell environment. First run with proposal creation disabled and the kill switch active, then reproduce every failure case. After successful host verification, enable proposal creation and clear the kill switch only for a controlled localhost test. Start `python -B server.py`, send one fresh authenticated TradingView alert, and open:

```text
http://127.0.0.1:8765/#proposals
```

Do not commit the environment values, database, terminal credentials or webhook secret.

## What Approve does

Approve requires localhost Host/Origin checks, the current CSRF token, an active feature flag, a cleared kill switch, an unexpired pending proposal, the daily count limit and a local reason. It changes the durable state to `APPROVED_FOR_FUTURE_DEMO_EXECUTION` and adds proposal-history and chained-audit records. It does not contact MT5, create a paper position, call `order_check` again or submit an order.

## Security findings

- The worker exposes only `snapshot` and `order_check`; source tests assert that it contains no submission symbol.
- The only production `order_send` definition is the pre-existing refusal. No proposal or approval call site invokes it.
- MT5 identity and terminal path come from local configuration, never webhook input.
- Snapshot acquisition and broker checking run outside SQLite write locks with a nonblocking single-worker lock and timeout.
- Identity is checked during the snapshot, latched across workers and checked before/after `order_check`.
- Login/server values and credential-like fields are omitted or redacted from UI, API, database and audit output.
- Existing synthetic research/news/cost assumptions remain visibly modeled inputs. They are not proof of profitability or current external market research.
- This host could not verify the official MetaTrader5 package or the actual broker terminal.

## Remaining requirements before one manually confirmed demo order

1. Reproduce the complete snapshot, every failure case and exact-volume `order_check` on the intended Windows demo host.
2. Independently review native filling-mode behavior, stop/freeze rules, symbol suffix mapping, broker costs and reconnect/account-switch behavior.
3. Replace or independently verify modeled news, macro, commission, swap, slippage, provider-history and strategy-edge evidence with timestamped sources.
4. Complete an extended forward-demo study and define loss/recovery procedures.
5. Design a separate one-order demo-execution release with explicit local confirmation, durable order identity, uncertain-fill handling, restart reconciliation, post-trade reconciliation and an immediate kill switch.
6. Add tests and an independent security/code review for that separate release.

No manually approved demo order can be submitted by this phase.
