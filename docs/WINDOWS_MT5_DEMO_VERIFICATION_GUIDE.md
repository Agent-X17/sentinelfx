# Windows MT5 demo verification guide

Current Windows blocker: see [MT5 timestamp investigation](MT5_TIMESTAMP_INVESTIGATION.md). A roughly three-hour future tick remains NO_TRADE; no trusted nonzero offset is available. Keep proposals disabled and the kill switch active.

This guide verifies read-only evidence. It does not enable proposals or trading.

Keep these values for every step:

```text
DEMO_TRADE_PROPOSALS_ENABLED=false
DEMO_TRADE_PROPOSAL_KILL_SWITCH=true
```

## 1. Install the required software

Use a 64-bit Windows 10 or Windows 11 computer. Install:

1. Your broker's official MetaTrader 5 desktop terminal.
2. 64-bit Python 3.9 or newer from python.org. Select **Add Python to PATH** during installation.
3. Git for Windows, or download the SentinelFX branch as a ZIP file.
4. A current web browser.

Do not install an Expert Advisor for this test. Do not copy an MT5 password into SentinelFX.

## 2. Put SentinelFX in a simple local folder

Use `C:\SentinelFX\sentinelfx`. Avoid `Program Files`, OneDrive and shared folders.

With Git, open PowerShell and run:

```powershell
New-Item -ItemType Directory -Force C:\SentinelFX | Out-Null
Set-Location C:\SentinelFX
git clone --branch prelive-hardening --single-branch https://github.com/Agent-X17/sentinelfx.git
Set-Location C:\SentinelFX\sentinelfx
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-mt5.txt
```

If you use a ZIP file, extract it so `server.py` is located at `C:\SentinelFX\sentinelfx\server.py`, then run the last three commands from that folder.

## 3. Start MT5 safely

1. Open MT5 normally.
2. Select **File → Login to Trade Account**.
3. Enter the demo credentials directly into MT5.
4. Select the exact demo server provided by the broker.
5. Confirm the account is labelled **Demo**. Never use a funded or real account for this verification.
6. Wait until prices move in Market Watch and the connection indicator shows connected.
7. Write the login number and exact server name in a private password manager or on private paper. Do not put them in GitHub, screenshots, prompts or source files.

## 4. Keep automatic trading off

1. On the MT5 toolbar, make sure **Algo Trading** is off.
2. Open **Tools → Options → Expert Advisors**.
3. Clear **Allow algorithmic trading**.
4. Remove any Expert Advisor from the chart.
5. Do not click New Order, Buy or Sell during the clean verification.

SentinelFX never receives the MT5 password. Its worker supports only snapshot reads and `order_check`.

## 5. Enter local settings

Use a new PowerShell window. The first block contains only fixed safe values:

```powershell
Set-Location C:\SentinelFX\sentinelfx

$env:SYSTEM_MODE = "SIMULATION"
$env:LIVE_EXECUTION_ENABLED = "false"
$env:MT5_DIAGNOSTIC_MODE = "real"
$env:WEBHOOK_ALLOWED_HOSTS = ""
$env:DEFAULT_ACCOUNT_PROFILE = "ACCOUNT_LIVE"
$env:DEMO_TRADE_PROPOSALS_ENABLED = "false"
$env:DEMO_TRADE_PROPOSAL_KILL_SWITCH = "true"
$env:DATABASE_PATH = "data\windows-mt5-verification.sqlite3"
```

Now enter the private values through local prompts. The prompt labels below are placeholders; enter the real values only on your own Windows computer:

```powershell
$env:MT5_TERMINAL_PATH = Read-Host "<YOUR_MT5_TERMINAL64_EXE_PATH>"
$env:DEMO_EXPECTED_ACCOUNT_LOGIN = Read-Host "<YOUR_DEMO_LOGIN>"
$env:DEMO_EXPECTED_BROKER_SERVER = Read-Host "<YOUR_DEMO_BROKER_SERVER>"
$privateSecret = Read-Host "<YOUR_LONG_RANDOM_WEBHOOK_SECRET>" -AsSecureString
$secretPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($privateSecret)
try { $env:TRADINGVIEW_WEBHOOK_SECRET = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPointer) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPointer) }
Remove-Variable privateSecret, secretPointer
```

The real values are not part of the PowerShell command history. They still exist temporarily in this process environment, so do not show this PowerShell window, environment dump or process details in a screenshot. Close the window after the test to remove them from that process.

## 6. Run the safe account check

In MT5 Market Watch, find the exact broker symbol, including any suffix such as `.a`. Replace only the placeholder below:

```powershell
.\.venv\Scripts\python.exe -B scripts\verify_mt5_readonly.py --symbol "<YOUR_EXACT_MT5_SYMBOL>" --time-samples 3 --report-file "$HOME\Desktop\sentinelfx-mt5-time-report.json"
```

This captures Windows UTC immediately before and after every MT5 read. It prints the raw tick fields, package version, terminal build, exact symbol, a redacted server fingerprint, tick progression, and the apparent-difference spread. It does not infer or apply an offset. The report excludes the login, server name, password, webhook secret and terminal path.

For the current unexplained future-timestamp case, the expected safe result is `BLOCKED / NO_TRADE`, `UNAVAILABLE_NO_INDEPENDENT_BROKER_EVIDENCE`, and `Offset use: NONE`. A stable-looking result is diagnostic evidence only. Send the redacted report to broker or MetaQuotes support if requested; do not send screenshots containing the private setup prompts.

The safe result is:

```text
SNAPSHOT: PASS
Demo identity: MATCHED (value hidden)
Terminal: CONNECTED; Algo Trading: OFF
Fresh quote: YES
Open positions: 0
Pending orders: 0
Recent external exposure: 0
RESULT: PASS — READ-ONLY EVIDENCE ONLY
DEMO ORDER NOT SENT — EXECUTION IS NOT IMPLEMENTED.
```

The verifier never prints the login, server, password, webhook secret or terminal path. A failure prints `RESULT: BLOCKED / NO_TRADE` and a reason. Do not weaken a setting to make it pass.

## 7. Verify the non-submitting broker check

Do this only on the clean demo account while its market is open:

```powershell
.\.venv\Scripts\python.exe -B scripts\verify_mt5_readonly.py --symbol "<YOUR_EXACT_MT5_SYMBOL>" --order-check
```

A pass includes:

```text
ORDER_CHECK: PASS (non-submitting)
After order_check — open positions: 0; pending orders: 0
```

If it is blocked while Algo Trading is off, record the reason and stop. Do not enable Algo Trading to force a pass.

In MT5, open **View → Toolbox → Trade**. Confirm there are zero positions and zero pending orders before and after the command. Open **History** and confirm the command created no deal or order.

## 8. Start SentinelFX

Keep MT5 open. In the same PowerShell window containing the settings, run:

```powershell
.\.venv\Scripts\python.exe -B server.py --port 8765 --db data\windows-mt5-verification.sqlite3
```

Keep that window open. Visit:

```text
http://127.0.0.1:8765/status
http://127.0.0.1:8765/
```

The status page should show:

- System mode: `SIMULATION`
- MT5 diagnostic mode: `real`
- MT5 status: `MT5_CONNECTED`
- MetaTrader5 package: `installed`
- Real MT5 host support: `available`
- Demo proposals: `disabled`
- Proposal kill switch: `ACTIVE`
- Live execution: `DISABLED — not implemented`

The Demo proposals page must be empty. Because proposals remain disabled, a valid webhook must still finish blocked/`NO_TRADE`; this is expected during this verification phase.

## 9. Send safe local webhook tests

Open a second PowerShell window. Read the secret privately into that process, then run:

```powershell
Set-Location C:\SentinelFX\sentinelfx
$privateSecret = Read-Host "<YOUR_LONG_RANDOM_WEBHOOK_SECRET>" -AsSecureString
$secretPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($privateSecret)
try { $env:TRADINGVIEW_WEBHOOK_SECRET = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($secretPointer) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($secretPointer) }
Remove-Variable privateSecret, secretPointer
.\.venv\Scripts\python.exe -B scripts\test_webhook.py --url http://127.0.0.1:8765/api/webhook/tradingview --secret $env:TRADINGVIEW_WEBHOOK_SECRET
```

With proposals disabled, the final result remains `NO_TRADE`/blocked. Use the standalone verifier in step 6 to prove that the correct demo identity passed without displaying it.

## 10. Failure checklist

Restore the correct private value after each setting test. Never use a real account.

| Test | Safe method | Expected SentinelFX result |
|---|---|---|
| Wrong account login | Temporarily set `DEMO_EXPECTED_ACCOUNT_LOGIN` to an intentionally wrong demo number, then run step 6. | `BLOCKED / NO_TRADE`, `MT5_ACCOUNT_MISMATCH`; no proposal. |
| Wrong broker server | Temporarily set `DEMO_EXPECTED_BROKER_SERVER` to an intentionally wrong name, then run step 6. | `BLOCKED / NO_TRADE`, `MT5_ACCOUNT_MISMATCH`; no proposal. |
| Real/non-demo account | Do not log into a real account. Run the automated test named below. | Automated fixture must pass by producing `MT5_ACCOUNT_NOT_CONFIRMED_DEMO`; no proposal. |
| MT5 disconnected | Keep MT5 open, temporarily disconnect the Windows network, and immediately run step 6. Restore the network afterward. | `BLOCKED / NO_TRADE`; disconnected, initialization or worker failure; no proposal. If the terminal has not noticed yet, wait 31 seconds and repeat. |
| Stale price/snapshot | Disconnect the demo machine from the internet, wait at least 60 seconds and run step 6. Also run the automated stale test. | `MT5_TICK_STALE_OR_FUTURE`, snapshot stale, or disconnected; all are blocked; no proposal. |
| MT5 restart | Stop SentinelFX, close MT5, reopen MT5 on the same demo login, wait for fresh prices, then restart SentinelFX and run step 6. | Before fresh reconnection: blocked. After the same demo identity and fresh prices return: the read-only verifier may pass. No proposal in either case. |
| Account switch | With SentinelFX still running, send one fresh alert to latch the expected demo identity. Switch MT5 to a different demo login and send another fresh alert. | `MT5_ACCOUNT_CHANGED` or `MT5_ACCOUNT_MISMATCH`; drift latch active; blocked. Return to the expected account and restart SentinelFX before continuing. |
| Existing open demo trade | Perform this last and only on the demo account. Manually open the broker's minimum demo position, then run step 6. | `MT5_EXTERNAL_EXPOSURE`; blocked; no proposal. After closing it, recent exposure may remain blocked for 24 hours. |
| Pending demo order | Perform this last and only on the demo account. Manually create a minimum pending order, then run step 6. | `MT5_EXTERNAL_EXPOSURE`; blocked. Cancel it; recent order history may remain blocked for 24 hours. |
| Manual trade opened in MT5 | Open a minimum manual demo position after the clean tests, run step 6, close it, and run again. | Open and recently closed manual activity both block as `MT5_EXTERNAL_EXPOSURE`; no proposal. |
| Wrong symbol mapping | Run the webhook tool with `--case unmapped`, or run step 6 with a symbol that is not the exact Market Watch symbol. | `SYMBOL_UNMAPPED`, symbol missing or `MT5_SYMBOL_IDENTITY_UNVERIFIED`; blocked. Do not edit the database to force a match. |
| Invalid lot size | Run the automated exact-grid test below. Webhook-supplied lot sizes are ignored. | `MT5_ORDER_CHECK_VOLUME_GRID_INVALID`; no MT5 check submission and no proposal. |
| Duplicate TradingView alert | Run the duplicate command below. | HTTP 409, `DUPLICATE_WEBHOOK`, `NO_TRADE`; only one intake identity is retained. |
| Kill switch enabled | Confirm `/status` shows `ACTIVE`. Run the automated kill-switch test below. | Test passes; creation and approval stay blocked. With proposals also disabled, the dashboard contains no proposal. |

Commands for the safe automated cases:

```powershell
# Live/non-demo, stale and volume-grid fixtures
.\.venv\Scripts\python.exe -B -m unittest tests.test_mt5_evidence.EvidenceValidationTests

# Kill-switch creation and approval refusal
.\.venv\Scripts\python.exe -B -m unittest tests.test_demo_proposals.DemoProposalTests.test_kill_switch_blocks_creation_and_approval

# Duplicate alert against the running local server
.\.venv\Scripts\python.exe -B scripts\test_webhook.py --url http://127.0.0.1:8765/api/webhook/tradingview --secret $env:TRADINGVIEW_WEBHOOK_SECRET --case duplicate
```

## 11. Evidence to save

Create a folder outside the Git repository, such as `C:\SentinelFX-Evidence\<DATE>`.

Save these items:

1. A screenshot of MT5 with Algo Trading visibly off. Crop or cover the login and server.
2. A screenshot of MT5 Toolbox → Trade showing zero positions and zero pending orders before and after `order_check`. Crop account identity.
3. A screenshot of MT5 History showing no new deal/order at the `order_check` time. Crop account identity.
4. The safe verifier output showing PASS. It is designed to contain no identity values.
5. The `/status` page showing SIMULATION, real diagnostics, proposals disabled, kill switch active and live execution disabled.
6. The empty Demo proposals dashboard page.
7. One screenshot or text file for every BLOCKED reason in the table.
8. The automated test output showing `OK`.
9. The Git commit ID from `git rev-parse HEAD`.

Never save or share raw worker JSON, the PowerShell environment, MT5 login windows, passwords, full account numbers, broker server names, webhook secrets or terminal paths. Do not put the evidence folder in Git.

## 12. Final pass/fail decision

Pass this read-only phase only when every item below is true:

- [ ] The intended account is visibly a demo account.
- [ ] Algo Trading stayed off for every test.
- [ ] `DEMO_TRADE_PROPOSALS_ENABLED` stayed `false`.
- [ ] `DEMO_TRADE_PROPOSAL_KILL_SWITCH` stayed `true`.
- [ ] `LIVE_EXECUTION_ENABLED` stayed `false`.
- [ ] The redacted snapshot verifier passed on the expected clean demo account.
- [ ] The optional `order_check` passed without enabling Algo Trading.
- [ ] MT5 showed zero new positions, pending orders, deals and orders after `order_check`.
- [ ] Every wrong, stale, disconnected, switched, exposed, unmapped, duplicate and invalid case blocked or returned `NO_TRADE`.
- [ ] No proposal was created.
- [ ] No private value appeared in a screenshot, log, source file, prompt or Git history.
- [ ] Automated tests passed.

If any item fails, stop. Keep proposals disabled and keep the kill switch active. This phase does not make SentinelFX ready to trade.
