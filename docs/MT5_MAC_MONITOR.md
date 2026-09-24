# MT5 Mac demo monitor

Status: compiled in MetaEditor with 0 errors and 0 warnings, attached to an EURUSD H1 demo chart, and verified to publish fresh $500 demo telemetry on 2026-09-25. No order submission is implemented.

The monitor is separate from the native Python MT5 adapter. It provides local diagnostics only; it cannot satisfy the risk engine's execution checks or update the simulation ledger.

## Install

1. In MT5 choose File > Open Data Folder, then MQL5 > Experts.
2. Copy `mt5/SentinelFX_DemoMonitor.mq5` into Experts.
3. Open it in MetaEditor and Compile. Require zero errors.
4. Return to MT5, refresh Expert Advisors and attach `SentinelFX_DemoMonitor` to an EURUSD chart on a demo account.
5. Leave Algo Trading disabled. The monitor uses timer events and local file writes only.
6. Check the Experts log for the startup message.

It writes `SentinelFX/demo-status.json` under MT5 Common/Files every two seconds. Only demo accounts are accepted. Disconnects and snapshots older than ten seconds fail closed. Deinitializing removes the snapshot.

## Read the snapshot

From the repository root:

```sh
python3 -B -c 'from engine.mt5_monitor import read_monitor; import json; print(json.dumps(read_monitor(), indent=2))'
```

The default reader path matches this Mac's MetaTrader Wine installation. Set `MT5_DEMO_MONITOR_FILE` to override it.

The file is locally trusted telemetry, not authenticated broker evidence. Orders and positions are counts only, quote freshness is not certified, and the data cannot approve risk or execution.
