# MT5 timestamp candidate policy — revision 1

Recorded: 2026-09-26. Status: **DRAFT / INACTIVE / NO_TRADE**.

This is a read-only design and evidence register, not a loadable policy or
permission to normalize production timestamps. No activation mechanism is added.
The observed HFM timestamp discrepancy remains blocked. This document supersedes
the conversational suggestion that seasonal guard windows and observed offsets
alone could authorize conversion: they cannot establish API field semantics.

## External evidence

### E1 — MQL5 moderator statement

Source: https://www.mql5.com/en/forum/516531#comment_60465455
Thread checked on 2026-09-26. Alain Verleyen is labelled Moderator.
The thread's reply #7, posted 2026-09-23, quotes his statement dated
2026-09-22 17:38:

> The problem is the documentation is wrong. The data returned from MT5 has the broker server time zone, not UTC.

His 2026-09-25 reply further distinguishes UTC query parameters from returned
broker-server timestamps. Treat this as strong supporting evidence, not a complete
MetaQuotes specification or proof of employment/authority to issue one. The
thread also contains another contributor's conflicting claim about query inputs;
do not merge those claims into one authoritative statement.

The statement does not fully specify tick fields, fall-back repeated-hour
encoding, runtime/version coverage, or an authoritative policy reference.
The published copy_ticks_from documentation still describes returned data as UTC:
https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksfrom_py
That conflict remains open; neither source silently overrides the other.

### E2 — HFM written reply supplied by the operator

Provenance: text pasted by the operator in this task, recorded 2026-09-26.
Original email date, headers, message identifier and authenticated archive digest
are unavailable here. Recording date is not the email's publication date.

HFM confirms the relevant MT5 server currently uses UTC+3 in summer and UTC+2
in winter; DST starts on the last Sunday in March and ends on the last Sunday
in October. HFM explicitly does not provide a separate documented Python API
timestamp policy and refers the operator to MetaTrader documentation.

This supports the server-clock schedule only. Exact transition hours, repeated
hour handling and a locally verified binding to the full server fingerprint
remain missing. Do not publish private server/account identifiers. An original
email can be retained privately for provenance without committing its headers.

### E3 — Windows observations

Previously supplied diagnostics report package 5.0.6180, terminal build 6182,
EURUSD, internally agreeing time/time_msc and an apparent difference near +10,800
seconds. These support E1 but do not independently prove an offset. Repeated
identical ticks are not independent samples. Delivery age changes the observed
difference even if the underlying clock convention is constant.

## Proposed policy record (design only)

Candidate identifier: `hfm-mt5-tick-time-candidate`; revision: `1`;
status: `DRAFT_UNVERIFIED`; activation: `FORBIDDEN`.

A future reviewed record must include all of the following, with no wildcard
bindings or defaults for missing evidence:

- Full locally computed broker-server SHA-256 fingerprint, expected demo identity
  check, and exact symbol. A shortened displayed fingerprint is insufficient.
- Exact MetaTrader5 package version, Python interpreter version, terminal build
  and relevant runtime platform. Observed MT5 versions above are evidence scope,
  not approved bindings; Python scope must be captured again.
- Policy revision, immutable content digest, reviewer, source identifiers and
  authentic archived source digests, publication/retrieval dates, review expiry.
- Explicit field semantics for symbol_info_tick time/time_msc and independently
  tested copy_ticks_from/copy_ticks_range input and output behavior.
- Broker-authored DST rules, dated applicability, UTC validity intervals with
  inclusive starts/exclusive ends, transition encoding, and uncertainty bounds.
- References to distinct-tick evidence and independently checked UTC clock
  evidence, including measurement errors, capture bounds and expiry.
- Last verified identity/runtime/policy/offset binding and a latched rejection
  state. A restart must not silently discard an unresolved change.

E2 implies calendar transition dates 2026-03-29 and 2026-10-25. These are derived
calendar dates, **not verified UTC transition instants**. Do not invent an hour,
map HFM to a geographic timezone without evidence, or generate active intervals
from those dates. A broad guard window may add protection later but cannot fill
the API-semantics or DST-evidence gaps.

## Required read-only proof before any future activation review

1. Capture UTC wall-clock before/after and monotonic elapsed time around each
   read, with independent UTC accuracy evidence and bounded uncertainty. A
   configured time-service name or internally consistent elapsed time alone is
   insufficient. Record exact versions, symbol and local identity binding.
2. Observe at least ten distinct advancing ticks over at least 60 seconds in
   each tested seasonal state. This is a proposed minimum, not proof by count.
   Require positive integer fields and time_msc // 1000 == time. Record repeated
   ticks but exclude them from the distinct count. Timeout, incomplete coverage
   or regressions leave the candidate unverified.
3. Retrieve matching ticks through copy_ticks_from and copy_ticks_range with
   explicitly UTC-aware query bounds. Preserve query bounds and unmodified
   results. Match time_msc, prices, flags and available volume fields; timestamp
   alone is not a unique tick ID. Ambiguous matches fail the proof. Test bounded
   query windows to establish inclusion behavior instead of assuming it.
4. Agreement across APIs establishes internal consistency only. Require
   independent evidence tying the same identifiable tick to UTC, or an
   authoritative, scoped API specification plus independently validated runtime
   observations. A second read from the same feed or a similar price from another
   feed is not an independent time anchor. Record unresolved uncertainty.
5. Establish UTC+2 and UTC+3 separately, including historical transition samples
   if available without orders. Lack of quotes during a transition is missing
   evidence, not a successful boundary test. Require fall-back interpretation
   and spring gap behavior; never infer them from the Windows timezone.
6. Quantify offset agreement using the independent reference and its declared
   error bounds. Never estimate an offset by rounding raw_tick - host_now, fit
   an offset merely to pass freshness, or auto-learn changes from incoming ticks.

## Proposed evaluator behavior

First validate status, provenance, expiry, exact bindings, independent clock
evidence and DST applicability. Reject the entire policy on missing, malformed,
mismatched, stale, uncertain or changed evidence. Check bindings even when an
uncorrected timestamp happens to pass freshness; no fallback can bypass policy
validation. Existing offline synthetic policy tests do not meet these new gates.

Preserve `raw_time` and `raw_time_msc` unchanged. Only after verified field
semantics permit it, derive a separate `normalized_utc_candidate` using the
documented interval offset. Require exactly one permitted UTC interpretation.
Zero interpretations, multiple interpretations, transition ambiguity or an
unexpected offset change must latch NO_TRADE pending explicit re-verification.
Do not automatically adopt a new offset, including at an expected DST change.

After verified conversion, apply the unchanged 0 <= age <= 30 second rule.
If measurement uncertainty permits a future or older-than-30-second tick, reject
it rather than adding tolerance. Only then may a separate `normalized_utc`
field be marked verified. An inactive candidate may never produce that mark,
freshness PASS, or proposal eligibility. Keep diagnostic hypotheses explicitly
labelled unverified. Order/deal history semantics need separate evidence; tick
proof does not certify them.

## Required future tests (not implemented by this documentation change)

| Case | Required outcome |
| --- | --- |
| Candidate status, even with all other fields populated | NO_TRADE; no activation |
| Fully verified UTC+2 / UTC+3 synthetic fixtures | Separate correct UTC result; raw unchanged |
| Stable unverified +3 hours | NO_TRADE |
| Exact age 0 and 30 seconds / future or >30 seconds | Pass only valid verified boundaries / reject |
| Fall-back two interpretations; spring gap; unknown transition hour | NO_TRADE |
| Before/at/after verified half-open interval boundaries | Unique interpretation only; change latch enforced |
| Unexpected offset change, including after restart | Latched NO_TRADE |
| Changed broker fingerprint, demo identity or symbol | NO_TRADE |
| Changed MT5 package, Python version or terminal build | NO_TRADE |
| Expired/tampered/incomplete policy or missing original provenance | NO_TRADE |
| time_msc/time disagreement, repeated-only samples, cross-API mismatch | Proof rejected |
| Uncertain/drifting host clock, stale evidence, ambiguous same-tick match | NO_TRADE |
| Invalid policy with a seemingly fresh uncorrected tick | NO_TRADE; no fallback |

No runtime edits accompany this design. No order submission, execution route,
proposal creation or policy loader is introduced. Required settings remain:

```text
SYSTEM_MODE=SIMULATION
LIVE_EXECUTION_ENABLED=false
DEMO_TRADE_PROPOSALS_ENABLED=false
DEMO_TRADE_PROPOSAL_KILL_SWITCH=true
```
