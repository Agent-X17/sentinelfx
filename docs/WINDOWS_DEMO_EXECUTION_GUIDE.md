# Windows HFM demo-only workflow

This workflow can submit one order only to the configured, proven MT5 demo account. It is local and CLI-only. The normal `MT5Service.order_send()` refusal and the absence of an HTTP execution endpoint remain unchanged.

## Non-negotiable timing condition

Run the Level 1 verification while EURUSD is open and producing ticks. Three distinct advancing ticks are required. On weekends or a closed market the correct result is `HFM_DEMO_MARKET_CLOSED_OR_NO_ADVANCING_TICK`; do not bypass it.

## 1. Open PowerShell and load the checkout

```powershell
Set-Location "C:\SentinelFX\sentinelfx"
git pull --ff-only origin prelive-hardening
git log -1 --oneline
```

The commit shown must include the demo-only completion change supplied by the developer.

## 2. Restore private values in this PowerShell window

Do not type the prompt prefix (`PS C:\...>`). Paste only the commands.

```powershell
$env:SYSTEM_MODE = "SIMULATION"
$env:LIVE_EXECUTION_ENABLED = "false"
$env:MT5_DIAGNOSTIC_MODE = "real"
$env:DEMO_TRADE_PROPOSALS_ENABLED = "false"
$env:DEMO_TRADE_PROPOSAL_KILL_SWITCH = "true"
$env:DEMO_EXECUTION_GATED = "false"
$env:DEMO_ACCOUNT_TAG = "DEMO_ONLY"
$env:WEBHOOK_ALLOWED_HOSTS = ""
$env:DEFAULT_ACCOUNT_PROFILE = "ACCOUNT_LIVE"
$env:DEMO_EXPECTED_ACCOUNT_LOGIN = (Read-Host "Enter exact DEMO login").Trim()
$env:DEMO_EXPECTED_BROKER_SERVER = (Read-Host "Enter exact DEMO server name").Trim()
$env:MT5_TERMINAL_PATH = (Read-Host "Paste full terminal64.exe path").Trim('"')
$env:DEMO_EMERGENCY_STOP_PATH = "$env:LOCALAPPDATA\SentinelFX\DEMO_EMERGENCY_STOP"
New-Item -ItemType Directory -Force "$env:LOCALAPPDATA\SentinelFX" | Out-Null
```

Never enter the account password into SentinelFX. Log in to the demo account inside MT5 itself.

## 3. Create the versioned Level 1 policy once

Use the existing reviewed v1 policy you already created. Pick a new output path; the command refuses to overwrite it.

```powershell
$v1 = (Read-Host "Paste the existing HFM read-only policy JSON path").Trim('"')
$env:HFM_DEMO_POLICY_PATH = "$env:LOCALAPPDATA\SentinelFX\hfm-demo-policy-v2.json"
& .\.venv\Scripts\python.exe -B scripts\create_hfm_demo_policy.py --source-readonly-policy "$v1" --output "$env:HFM_DEMO_POLICY_PATH" --symbol "EURUSD" --hfm-evidence-date "2026-09-26"
```

If the file already exists, do not delete or replace it. Continue using the same file, or ask the developer to review why a replacement is needed.

## 4. Verify demo Level 1 with AutoTrading OFF

In MT5, confirm the exact demo login/server and turn **Algo Trading / AutoTrading OFF**. Confirm there are no open positions or pending orders.

```powershell
& .\.venv\Scripts\python.exe -B scripts\verify_hfm_demo_level1.py --symbol "EURUSD" --time-samples 5 --hfm-policy "$env:HFM_DEMO_POLICY_PATH" --report-file "$env:LOCALAPPDATA\SentinelFX\hfm-demo-level1-report.json"
```

Continue only if it prints `PASS — DEMO LEVEL 1 ONLY`. `EXTERNAL_CLOCK_NOT_CERTIFIED` is allowed for this demo-only level. It does not mean live or real-money readiness.

## 5. Create and approve one proposal

Start SentinelFX locally with proposals enabled and the proposal kill switch deliberately cleared for this short session:

```powershell
$env:DEMO_TRADE_PROPOSALS_ENABLED = "true"
$env:DEMO_TRADE_PROPOSAL_KILL_SWITCH = "false"
$env:DEMO_EXECUTION_GATED = "false"
$env:TRADINGVIEW_WEBHOOK_SECRET = (Read-Host "Enter your private local webhook secret").Trim()
& .\.venv\Scripts\python.exe -B server.py --port 8765
```

Send the intended TradingView alert to the local URL shown by the server. Review the symbol, direction, stop, target, calculated risk and exact lot size in the local dashboard. Approve it only if every value is correct. Copy the proposal ID. Approval does not send an order.

Stop the server with `Ctrl+C` after approval. There is no execution HTTP endpoint.

## 6. Re-verify immediately with AutoTrading ON

Turn MT5 AutoTrading ON. Leave the terminal connected to the same demo account. Then run:

```powershell
& .\.venv\Scripts\python.exe -B scripts\verify_hfm_demo_level1.py --symbol "EURUSD" --time-samples 5 --require-autotrading --hfm-policy "$env:HFM_DEMO_POLICY_PATH" --report-file "$env:LOCALAPPDATA\SentinelFX\hfm-demo-execution-preflight.json"
```

Continue only after `PASS — DEMO LEVEL 1 ONLY`.

## 7. Enable the one-shot gate and execute the approved proposal

First confirm the emergency-stop file does not exist:

```powershell
Test-Path -LiteralPath $env:DEMO_EMERGENCY_STOP_PATH
```

It must print `False`. Set the gate and paste the approved proposal ID:

```powershell
$env:DEMO_EXECUTION_GATED = "true"
$proposalId = (Read-Host "Paste the approved proposal ID").Trim()
& .\.venv\Scripts\python.exe -B scripts\execute_demo_proposal.py --proposal-id "$proposalId" --confirm "EXECUTE DEMO $proposalId" --report-file "$env:LOCALAPPDATA\SentinelFX\demo-execution-report.json"
```

Possible outcomes:

- `CONFIRMED_DEMO_EXECUTED`: one demo submission was confirmed and reconciled.
- `BLOCKED_NO_TRADE`: no order was submitted.
- `UNRESOLVED_RECONCILIATION_REQUIRED`: stop. Do not retry. Inspect the MT5 demo account and give the report to the developer.

## 8. Restore the safe defaults immediately

```powershell
$env:DEMO_EXECUTION_GATED = "false"
$env:DEMO_TRADE_PROPOSALS_ENABLED = "false"
$env:DEMO_TRADE_PROPOSAL_KILL_SWITCH = "true"
$env:LIVE_EXECUTION_ENABLED = "false"
$env:SYSTEM_MODE = "SIMULATION"
```

For an immediate local emergency stop at any time:

```powershell
New-Item -ItemType File -Force $env:DEMO_EMERGENCY_STOP_PATH | Out-Null
```

Do not remove that file until the cause has been reviewed. The workflow never supports a real/live/funded account.
