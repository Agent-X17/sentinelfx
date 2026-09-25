# MT5 timestamp investigation — read-only verification remains blocked

DEMO ORDER NOT SENT — EXECUTION IS NOT IMPLEMENTED.

## Finding and limits

The Windows operator observed EURUSD tick age around -10,797 seconds after reporting successful Windows time synchronization. This proves a disagreement between the returned tick epoch and the host epoch; it does **not** establish its cause or a trustworthy three-hour correction. The previous conversational conclusion that a stopped Windows Time service explained the discrepancy was premature. A configured time source name alone is not independent proof of absolute clock accuracy.

The current adapter copies native tick fields unchanged. The existing freshness check interprets `time_msc / 1000` as epoch seconds and compares it to UTC. It does not apply Nairobi's timezone or a local datetime conversion. A local timezone display setting therefore cannot explain that subtraction by itself. Possible broker/runtime timestamp behavior or host clock problems still require measurement on the Windows host. This development Mac cannot reproduce that terminal.

Primary documentation:

- [MetaQuotes Python copy_ticks_from](https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksfrom_py) describes terminal tick data as UTC and requires UTC input dates.
- [Python symbol_info_tick](https://www.mql5.com/en/docs/python_metatrader5/mt5symbolinfotick_py) returns the latest tick, with both time fields.
- [MqlTick fields](https://www.mql5.com/en/docs/constants/structures/mqltick) describes `time` as the last price update time and `time_msc` as that time in milliseconds.
- [TimeGMT](https://www.mql5.com/en/docs/dateandtime/timegmt) is calculated using the computer's time and timezone. It is not an independent authenticated UTC source.

No broker/runtime-specific authoritative exception to the UTC contract, independently authenticated broker offset, or matching independently timestamped tick is available in this task. A chart clock, `TimeCurrent`, another tick from the same terminal, a stable difference from the PC clock, or an operator-entered offset cannot supply that missing trust.

## Implemented correction

The safe correction is stricter interpretation and diagnostics, **not** automatic broker-offset normalization:

1. Require native positive integer `time` and `time_msc`, with bounded ranges. Do not accept strings, booleans, floats, missing fields, NaN or infinity.
2. Require `time_msc // 1000 == time`. Previously a valid-looking millisecond field could hide a conflicting seconds field, and malformed millis could fall back to seconds.
3. Interpret the documented UTC epoch without adding or subtracting the host timezone. Keep raw fields unchanged. The separately named UTC candidate is not represented as verified when it fails validation.
4. Preserve the exact `0 <= age <= 30 seconds` rule, including rejection of future timestamps by even one millisecond.
5. The worker records wall-clock start/end and monotonic elapsed time. Reject backward clock steps, elapsed discrepancies greater than 250 ms, inconsistent capture time, missing evidence, and snapshots outside 30 seconds. This validates elapsed-time consistency only; it does not prove absolute UTC accuracy or authorize an offset.
6. The verifier prints only allowlisted numeric raw timestamps, UTC candidate, normalized UTC when valid, clock consistency, trusted-offset availability and freshness. It does not print the account identity, terminal path or entire native snapshot.
7. `--time-samples 3` obtains three bounded snapshots one second apart. Every snapshot must pass. A later pass cannot erase an earlier failure. It cannot be combined with `--order-check` and never learns an offset.

The production snapshot validator uses the same timestamp evaluator. The worker and validator must be updated together: older envelopes lacking `clock_observation` fail closed. No database migration, HTTP route, offset environment variable, external time network request, or execution feature was added.

## Requested nonzero-offset acceptance: not implemented

There is no trustworthy nonzero-offset source to validate. Adding a test that labels a made-up offset as trusted would not establish one. Stable nonzero offsets are therefore explicitly tested to remain blocked. The requested acceptance of a stable independently verified broker offset is still outstanding.

A future implementation requires an independently authenticated source that binds timestamp semantics/offset to the exact broker feed, symbol, terminal/package runtime and validity interval, with explicit UTC uncertainty. Ideally it supplies the UTC timestamp for the **same identifiable tick**, not a similar price from another feed. Source authenticity, expiry, replay protection and identity binding must be validated outside webhook input. Offset stability alone is insufficient. Any offset or clock change must latch a block pending re-verification. Both tick freshness and recent exposure history query semantics must be verified; correcting only tick time while querying order/deal history on the wrong time basis is unsafe.

Do not loosen the freshness window, round an observed age to a timezone, subtract three hours, clear the kill switch or enable proposals to make this pass. The current Windows case remains NO_TRADE.

## Windows verification

Update the existing checkout to the reviewed change first. In the original PowerShell session, configure the private terminal path and expected identity using the existing guide. Keep:

```text
DEMO_TRADE_PROPOSALS_ENABLED=false
DEMO_TRADE_PROPOSAL_KILL_SWITCH=true
LIVE_EXECUTION_ENABLED=false
SYSTEM_MODE=SIMULATION
```

Keep MT5 Algo Trading off and use the exact Market Watch symbol. Run:

```powershell
.\.venv\Scripts\python.exe -B scripts\verify_mt5_readonly.py --symbol "EURUSD" --time-samples 3
```

If the quote is still in the future, expect a raw timestamp and UTC candidate, `Trusted offset status: UNAVAILABLE_NO_INDEPENDENT_BROKER_EVIDENCE`, normalized timestamp unavailable, and `BLOCKED / NO_TRADE`. Save only the time diagnostic/result lines. Do not include earlier private prompts, account/login, server, terminal path, passwords or secrets. Do not run order-check after a blocked result.

A PASS means only the current documented-UTC read-only evidence checks passed. It is not execution readiness and does not resolve a previously unexplained broker timestamp anomaly by itself.

## Changed files

- `engine/mt5_time.py`: strict interpretation, redacted evidence, elapsed clock validation.
- `engine/mt5_evidence.py`: shared timestamp gate and required clock observation.
- `engine/mt5_worker.py`: wall/monotonic collection measurements.
- `scripts/verify_mt5_readonly.py`: time diagnostics and bounded repeated reads.
- `tests/test_mt5_time.py`: timestamp, clock, verifier, redaction and no-submission tests.
- `tests/test_mt5_evidence.py`: native integer timestamps and clock fixture.
- `docs/MT5_TIMESTAMP_INVESTIGATION.md`, `docs/MT5_READ_ONLY_EVIDENCE_PROTOCOL.md`, `docs/WINDOWS_MT5_DEMO_VERIFICATION_GUIDE.md`, `docs/AGENT_HANDOFF.md`: current protocol, usage and unresolved trust requirement.

## Tests and safety proof

New tests cover documented UTC acceptance, the exact 30-second boundary, future rejection without independent evidence, stable/unstable offset rejection, offset changes, seconds/milliseconds inconsistency, malformed fields, host clock steps, capture/source disagreement, missing clock evidence, timezone independence, ignored offset claims, output redaction and a multi-sample failure latch.

An AST test checks production engine, scripts and server call sites for submission calls. The existing refusal implementation and HTTP routes are unchanged. No native MT5 connection or order was made on this Mac. Full command results are recorded below after verification.

## Final verification results (Mac, 2026-09-25)

The initial sandboxed run could not bind localhost and reported four HTTP-related errors. After granting network permission for the existing localhost tests, the complete rerun passed. No test was skipped or weakened to work around that restriction.

```sh
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -B -m unittest discover -s tests -v
```

```text
Ran 226 tests in 5.014s

OK
```

There are 25 new tests (201 previous tests plus 25). No stable **nonzero** offset acceptance test is claimed: independently trusted offset evidence remains unavailable, so that case correctly remains rejected.

```sh
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -B scripts/acceptance_check.py
```

Final output:

```text
PASS  live-enabled startup refusal — exit 1; Live execution is not implemented
PASS  LIVE_GATED startup refusal — exit 1; LIVE_GATED is reserved
PASS  live execution impossible — both startup modes refuse, order_send refuses, /api/execute is 404
PASS  final frontend syntax — JavaScript syntax valid
PASS  final test suite — Ran 226 tests in 5.107s; OK

ACCEPTANCE RESULT: PASS
```

```sh
node --check static/app.js
PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -B -m py_compile server.py manage.py engine/*.py scripts/*.py tests/*.py
git diff --check
git diff --exit-code -- engine/config.py .env.example server.py engine/mt5.py
```

All four commands exited 0 with no output. The final command confirms unchanged safety defaults, routes and the existing submission-refusal method. The acceptance gate calls that refusal method only; no native submission call occurs. Changes are local and have not been committed or pushed by this task.
