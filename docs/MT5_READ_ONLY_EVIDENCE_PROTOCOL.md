# MT5 read-only evidence protocol

SentinelFX Phase 2 uses `sentinelfx.mt5.readonly-evidence.v1` between the localhost server and a short-lived worker on the supported MT5 host. The worker supports exactly two operations: `snapshot` and `order_check`. It has no order-submission operation. Every worker process initializes the terminal, performs one bounded read/check sequence, prints one JSON envelope, shuts down, and is terminated by the parent after the configured timeout.

## Snapshot envelope

The `snapshot` response contains a protocol name, operation name, capture timestamp, terminal status, terminal information, account information, the exact requested symbol properties, its current tick, all open positions, all pending orders, and orders/deals seen during the previous 24 hours. Account identity is read twice; an identity change or terminal disconnect fails the whole snapshot. The server also latches identity drift across workers until a local audited reset.

Strict validation requires:

- a snapshot no more than 30 seconds old and a tick no more than 30 seconds old;
- connected terminal with terminal AutoTrading proven off;
- a positive login and nonempty broker server matching local environment configuration;
- MT5 `trade_mode=0`, proving a demo account, USD currency, and valid balance, equity, margin, free margin, margin level and leverage;
- exact server-side symbol mapping, visible/full-trade symbol, fresh bid/ask, volume minimum/maximum/step, digits/point, stop/freeze levels and filling mode;
- empty current positions, current pending orders, recent deals and recent orders.

Any missing, malformed, stale, mismatched, uncertain or nonempty field produces `NO_TRADE`. Webhook fields cannot select an account, server, terminal path, evidence provider, or worker operation.

## Non-submitting order check

After normal risk checks calculate a conservative exact volume, the parent starts another worker with an allowlisted request: symbol, BUY/SELL direction, exact volume, price, stop, target, and the symbol's filling mode. The worker translates this to MT5 constants and calls only `order_check`. It reads account identity before and after the call. The parent requires a successful MT5 retcode, exact volume equality and unchanged latched identity.

`order_check` is a broker preflight only. It does not reserve or submit an order. The protocol has no `order_send` operation, and no proposal or approval path invokes submission.

## Concurrency and persistence

Snapshot acquisition and `order_check` run outside SQLite write transactions. Each is serialized by a nonblocking process lock and has a hard timeout. After `order_check`, SentinelFX opens the final proposal transaction, refreshes local risk state, repeats the complete conservative calculation, verifies that the recalculated volume equals the broker-checked volume, applies the one-active-proposal constraint, and writes the proposal and audit history.

Persisted and displayed evidence omits the login and broker server. Credential-like fields and configured values are redacted. Passwords and terminal credentials are never part of the protocol.

## Manual verification on the MT5 demo host

1. Use a controlled Windows host with the official MetaTrader5 Python package and a running MT5 terminal logged into the intended **demo** account. Keep the MT5 Algo Trading button off.
2. Keep the defaults disabled while checking configuration: `DEMO_TRADE_PROPOSALS_ENABLED=false` and `DEMO_TRADE_PROPOSAL_KILL_SWITCH=true`.
3. Set the values only in the local Windows PowerShell session. Never write identity or credentials into Git files:

   ```powershell
   $env:SYSTEM_MODE = "SIMULATION"
   $env:LIVE_EXECUTION_ENABLED = "false"
   $env:MT5_DIAGNOSTIC_MODE = "real"
   $env:MT5_TERMINAL_PATH = "C:\Path\To\terminal64.exe"
   $env:DEMO_EXPECTED_ACCOUNT_LOGIN = "your-demo-login"
   $env:DEMO_EXPECTED_BROKER_SERVER = "your-demo-server"
   $env:DEMO_TRADE_PROPOSALS_ENABLED = "false"
   $env:DEMO_TRADE_PROPOSAL_KILL_SWITCH = "true"
   ```
4. Start SentinelFX and inspect `/status` and `/api/health`. Confirm real diagnostic mode, no drift latch, and a fresh successful snapshot. Any failure must remain `NO_TRADE`.
5. Verify failure cases before enabling proposals: disconnect the terminal, switch account, enable Algo Trading, allow the tick to go stale, use an unmapped symbol, create a manual pending order, and create/close a manual demo position. Each case must block and leave zero proposals.
6. Return to the expected demo account, remove all exposure, keep Algo Trading off, restart the terminal/server as needed, and re-run the acceptance gate.
7. Only for a controlled localhost proposal test, set `DEMO_TRADE_PROPOSALS_ENABLED=true` and `DEMO_TRADE_PROPOSAL_KILL_SWITCH=false`, send one fresh authenticated alert, and inspect `http://127.0.0.1:8765/#proposals`.
8. Confirm the proposal states **DEMO ORDER NOT SENT — EXECUTION IS NOT IMPLEMENTED.** Clicking Approve changes only the audited status to `APPROVED_FOR_FUTURE_DEMO_EXECUTION`.

This procedure verifies proposal evidence. It does not approve or test execution.
