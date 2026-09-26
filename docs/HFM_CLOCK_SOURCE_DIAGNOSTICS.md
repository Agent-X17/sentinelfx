# HFM external-clock diagnostics

This local, read-only command explains why the HFM verifier cannot obtain the
independent UTC evidence required by the existing policy. It does not connect
to MT5, normalize a timestamp, alter Windows, or submit an order.

The source list is fixed in code:

- `time.windows.com` using NTP over UDP
- `time.cloudflare.com` using NTP over UDP
- `www.cloudflare.com` using a TLS-validated HTTPS `Date` header
- `www.microsoft.com` using a TLS-validated HTTPS `Date` header

Only allowlisted status names, elapsed durations, and HTTPS status codes are
printed. Exceptions, addresses, proxy values, headers, cookies, certificates,
credentials, environment variables, broker identity, and terminal paths are
never included. Two consistent sources using the same method are required.
One successful source never produces a pass.

## Windows commands

Open PowerShell in `C:\SentinelFX\sentinelfx`, restore the safe settings used by
the existing verifier, and run:

```powershell
$env:SYSTEM_MODE = "SIMULATION"
$env:LIVE_EXECUTION_ENABLED = "false"
$env:DEMO_TRADE_PROPOSALS_ENABLED = "false"
$env:DEMO_TRADE_PROPOSAL_KILL_SWITCH = "true"
$env:MT5_DIAGNOSTIC_MODE = "real"

.\.venv\Scripts\python.exe -B scripts\verify_mt5_readonly.py --clock-diagnostics --report-file "$env:TEMP\sentinelfx-clock-diagnostic.json"
.\.venv\Scripts\python.exe -B scripts\probe_hfm_clock_environment.py --report-file "$env:TEMP\sentinelfx-clock-environment.json"
```

An exit code of 1 is expected when the result is `BLOCKED_NO_TRADE`; it is an
observed safety decision, not a request to change firewall, proxy, certificate,
time-service, or MT5 settings. Keep the earlier blocked report unchanged.

The first report records the four classified source outcomes. The second adds
boolean DNS results, whether UDP was attempted, whether Python detected proxy
configuration without disclosing it, certificate-validation state, endpoint
reachability, and total elapsed time.

## Interpretation

`DNS_FAILURE` isolates name resolution. NTP `TIMEOUT` commonly means no UDP/123
reply, while `SOCKET_FAILURE` is another redacted socket failure.
`RESPONSE_INVALID` means a reply failed protocol validation.
`PROXY_FAILURE`, `TLS_FAILURE`, and `CERTIFICATE_FAILURE` distinguish HTTPS
setup failures without printing sensitive internals. `HTTP_STATUS`, missing or
invalid `Date`, and `CLOCK_DISAGREEMENT` show that an endpoint was reached but
did not supply acceptable UTC evidence.

No diagnostic result changes the clock policy. Timestamp normalization remains
blocked unless the separate production clock evidence check passes under its
unchanged two-source and two-second uncertainty requirements.
