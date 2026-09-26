# HFM read-only timestamp verification

Policy revision: `hfm-readonly-2026-v1`. Scope: explicit Windows CLI diagnostics
only. No application/HTTP/proposal path loads this policy. The old default
verifier still rejects unexplained nonzero offsets. This mode implements the
operator's subsequent request for conditional normalization, superseding the
inactive-only candidate design for this CLI scope.

## Evidence and limitations

The [evidence register](MT5_TIMESTAMP_CANDIDATE_POLICY.md) records the moderator
statement and HFM email supplied by the operator. Returned server-wall-time
encoding is the scoped working interpretation, not a claim of a revised official
MetaQuotes specification. The HFM email supports UTC+2/UTC+3 seasonal clocks but
does not guarantee Python API semantics. Repeated measurements and exact
cross-API tick matches corroborate this interpretation; they are not independent
proof of how the broker generated a tick.

The old UTC-only interpretation caused the observed HFM value to appear about
three hours in the future. This implementation subtracts the seasonal offset
only when every diagnostic check passes. It does not round a measured difference
to three hours or learn an offset from incoming data.

The policy expires at the end of 2026. HFM's last-Sunday rules give March 29 and
October 25 for 2026. Because the exact transition hour is unknown, verification
blocks from **Saturday 00:00 UTC through Tuesday 00:00 UTC** around each date:
`[2026-03-28T00:00Z, 2026-03-31T00:00Z)` and
`[2026-10-24T00:00Z, 2026-10-27T00:00Z)`. Both host capture bounds (including
uncertainty) and normalized tick dates must be outside the guard. No autumn
repeated hour is guessed. No geographic Windows timezone supplies the policy.

The verifier requires 3–5 snapshots with at least three distinct advancing
ticks over two seconds. Every tick must match exactly one tick in both
copy_ticks_from and copy_ticks_range, including timestamp, prices, flags and
volume fields. Query dates are actual UTC-aware datetimes, not shifted server
values. A 30-second query window and 10,000-row cap bound evidence; incomplete
or ambiguous matches block. Collection workers retain their five-second timeout.

Before and after collection, two readings each from time.windows.com and
time.cloudflare.com check the Windows clock. If the network blocks UDP port 123,
the verifier uses TLS-authenticated HTTPS Date responses from Cloudflare and
Microsoft instead. It never falls back after malformed or disagreeing NTP data.
NTP services must agree within 250 ms with combined clock error no more than
500 ms. HTTPS services must agree within one second with conservative whole-second
and network uncertainty no more than two seconds. The full uncertainty interval
is used by freshness validation, so this fallback does not add age tolerance.
Each clock process has a ten-second deadline. NTP is not authenticated; HTTPS
authenticates the named web services through the normal TLS trust store. The
threat model assumes a trusted local machine, CA store and network. No system
clock is adjusted. Unavailable sources, redirects to other hosts, clock steps,
stale evidence or uncertainty cause NO_TRADE.

Raw time/time_msc are preserved. Reports separately show expected offset, measured
raw-minus-host difference, an unverified UTC candidate, verified normalized UTC,
age, uncertainty and final result. A raw-minus-host difference includes delivery
age, so exact equality to 7,200/10,800 is not required. The whole age uncertainty
interval must lie within **0 through 30 seconds**, at capture and final review.
There is no extra future tolerance or expanded freshness window.

The policy binds the full locally computed server fingerprint, symbol, Python
version, MT5 package version and terminal build. The usual demo-account identity,
connection, Algo Trading off and exposure checks also run. A private copy of the
snapshot is normalized solely for these existing read-only checks; raw snapshots
are never rewritten or passed into the proposal path. This does not establish
the returned order/deal history timestamp convention.

## Windows setup (one-time local policy)

Use the existing PowerShell session with the private expected demo login, server
and terminal path already configured. Never send those values here. Leave MT5
Algo Trading off. Run from the repository folder. Keep:

```powershell
$env:SYSTEM_MODE = "SIMULATION"
$env:LIVE_EXECUTION_ENABLED = "false"
$env:DEMO_TRADE_PROPOSALS_ENABLED = "false"
$env:DEMO_TRADE_PROPOSAL_KILL_SWITCH = "true"
$env:MT5_DIAGNOSTIC_MODE = "real"
```

The versions below are the operator's previously reported runtime, not values
automatically learned from the terminal. A mismatch must be investigated before
changing the policy. Create a local policy once (the block refuses overwrite):

```powershell
if (-not $env:DEMO_EXPECTED_BROKER_SERVER) { throw "STOP / NO_TRADE: expected demo server is missing" }
$hfmFolder = Join-Path $env:LOCALAPPDATA "SentinelFX"
New-Item -ItemType Directory -Force $hfmFolder | Out-Null
$hfmPolicyPath = Join-Path $hfmFolder "hfm-readonly-policy.json"
if (Test-Path -LiteralPath $hfmPolicyPath) { throw "Policy already exists; keep the existing file" }
$hfmHasher = [System.Security.Cryptography.SHA256]::Create()
$hfmHash = [BitConverter]::ToString($hfmHasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($env:DEMO_EXPECTED_BROKER_SERVER))).Replace("-", "").ToLowerInvariant()
$hfmHasher.Dispose()
$hfmPolicy = @{
    schema = "sentinelfx.hfm-readonly-policy.v1"
    revision = "hfm-readonly-2026-v1"
    scope = "READ_ONLY_DIAGNOSTIC"
    year = 2026
    server_fingerprint = $hfmHash
    symbol = "EURUSD"
    package_version = "5.0.6180"
    terminal_build = 6182
    python_version = "3.14.7"
}
$hfmJson = $hfmPolicy | ConvertTo-Json
[IO.File]::WriteAllText($hfmPolicyPath, $hfmJson, [Text.UTF8Encoding]::new($false))
```

Run the existing verifier with the explicit local policy:

```powershell
.\.venv\Scripts\python.exe -B scripts\verify_mt5_readonly.py --symbol "EURUSD" --time-samples 5 --hfm-policy "$env:LOCALAPPDATA\SentinelFX\hfm-readonly-policy.json" --report-file "$env:TEMP\sentinelfx-hfm-readonly-report.json"
```

Expected result: either a read-only timestamp PASS with raw/normalized values,
or BLOCKED / NO_TRADE with a reason. No pass is promised: market closure, repeated
ticks, history-query behavior, blocked NTP, changed versions or stale data can
all block. Never combine this mode with `--order-check`.

The verifier creates a sibling `.state.json` containing policy digest and last
offset, and an exclusive `.lock` while running. All failures after locking,
including interrupted collection, leave a persistent block. A previous offset
or policy change cannot silently become a new baseline. There is no reset,
approval or auto-recovery command. Preserve the report and state for developer
review; do not delete state to make a test pass. After a resolved failure or
season change, a developer must explicitly review evidence and archive the old
state before establishing a new local baseline. Missing state on first use is
enrollment, not historical proof. Local file integrity relies on OS permissions.

Keep the generated JSON report. It omits the login, server name and terminal
path. The policy/state are local files outside the checkout and must not be
committed. A PASS does not authorize proposals, execution or trading.

## Local validation — 2026-09-26

All commands ran with the four safety environment values shown above. The
initial sandbox-only full-suite attempt failed on four localhost socket binding
errors; after network permission was granted, the complete suite passed.
No native MT5 connection or public NTP query was made during these tests.

```text
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -B -m unittest discover -s tests -v
Ran 255 tests in 5.223s
OK

PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -B scripts/acceptance_check.py
PASS  final test suite — Ran 255 tests in 5.177s; OK
ACCEPTANCE RESULT: PASS

node --check static/app.js
exit 0; no output

git diff --check
exit 0; no output
```

The 19 added synthetic tests cover UTC+2/UTC+3, both guarded transition
boundaries and the repeated autumn hour, changed/missing policy, offset mismatch,
persisted change rejection, clock packets/drift/uncertainty, fresh/stale/future
boundaries, server/account/runtime mismatch, disconnection, exact distinct tick
and cross-API checks, UTC request parameters, redacted reports and policy expiry.

AST inspection found zero production submission call sites and an unchanged
existing submission-refusal method. `server.py`, `engine/config.py` and
`.env.example` are unchanged. Acceptance separately confirms the execution route
is absent and the existing refusal works. Actual Windows/HFM results remain
unverified until the operator runs the documented command.
