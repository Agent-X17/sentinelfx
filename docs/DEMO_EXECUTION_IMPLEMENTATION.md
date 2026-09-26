# Demo-only execution implementation

## Boundary

The general MT5 adapter still refuses `order_send`, and the local HTTP server has no execution route. The sole native submission call is in `engine/demo_execution_worker.py`, reached only by `scripts/execute_demo_proposal.py` after an approved, unexpired proposal and durable `PREPARED` record exist.

Defaults remain simulation, live execution off, proposals off, proposal kill switch on, and demo execution gate off.

## Timestamp model

`engine/hfm_demo_time.py` implements policy revision `hfm-demo-2026-v2`. It preserves raw broker seconds/milliseconds, applies the HFM seasonal UTC+2/UTC+3 rule on a private snapshot copy, excludes both DST transition uncertainty windows, requires three distinct advancing ticks, cross-checks `symbol_info_tick`, `copy_ticks_from`, and `copy_ticks_range`, and applies the unchanged 30-second freshness rule after normalization.

External NTP/HTTPS clock certification remains separate. Failure reports `EXTERNAL_CLOCK_NOT_CERTIFIED` and does not convert into a Level 1 pass. Level 1 is limited to the pinned demo identity/runtime and cannot be used as a live-account certification.

## Execution gates

The CLI and worker jointly require simulation mode, live execution disabled, explicit `DEMO_ONLY` tag, exact login and server fingerprint, demo trade mode, USD currency, connected terminal, AutoTrading on at execution time, balance and equity at or below USD 500, no current/recent external exposure, no emergency-stop file, valid audit chain, no unresolved prior attempt, unchanged package/build/policy bindings, approved unexpired proposal, exact confirmation phrase, fresh advancing ticks, broker `order_check`, and successful post-send reconciliation.

Each proposal can have only one attempt. Any timeout, crash after the native boundary, partial/placed response, missing deal identifier, identity drift, or non-unique reconciliation produces `UNRESOLVED_RECONCILIATION_REQUIRED` and blocks later attempts.
