# SentinelFX — Agent start here

Repository:

https://github.com/Agent-X17/sentinelfx

Working branch:

https://github.com/Agent-X17/sentinelfx/tree/prelive-hardening

Latest Phase 2 report:

https://github.com/Agent-X17/sentinelfx/blob/prelive-hardening/docs/PHASE_2_READ_ONLY_MT5_REPORT_2026-09-25.md

MT5 evidence protocol and host-verification instructions:

https://github.com/Agent-X17/sentinelfx/blob/prelive-hardening/docs/MT5_READ_ONLY_EVIDENCE_PROTOCOL.md

Implementation commit:

https://github.com/Agent-X17/sentinelfx/commit/ced7922

Report commit:

https://github.com/Agent-X17/sentinelfx/commit/b4eb5d655c2564f3128c531522d7796806a35ca5

## What is complete

- Manual-reviewed demo trade proposals.
- Strict, versioned, read-only MT5 demo evidence.
- Isolated exact-volume MT5 `order_check`.
- Demo identity, freshness, terminal, symbol, tick and exposure checks.
- Local dashboard review with approve, reject, cancel and expiry actions.
- Audit logging, CSRF, replay protection, redaction and kill switch.
- Acceptance result: PASS, 201 tests passed.

## Agent instructions

1. Read the Phase 2 report and MT5 protocol linked above.
2. Run:

   ```sh
   PYTHONPYCACHEPREFIX=/tmp/sentinelfx-pycache python3 -B scripts/acceptance_check.py
   ```

3. Confirm the result is `ACCEPTANCE RESULT: PASS` before making changes.
4. Review the real MT5 evidence path on the intended Windows demo host.
5. Test disconnection, stale data, account switching, live-account rejection, Algo Trading enabled, current exposure, recent exposure and order-check failure. Every case must return `NO_TRADE` and create no proposal.
6. Report findings without printing account numbers, broker server values, passwords, webhook secrets or tokens.

## Safety limits

- Do not call MT5 `order_send()`.
- Do not add an execution endpoint.
- Do not submit any demo or live order.
- Do not enable Algo Trading or automatic trading.
- Keep `DEMO_TRADE_PROPOSALS_ENABLED=false` by default.
- Keep `DEMO_TRADE_PROPOSAL_KILL_SWITCH=true` by default.
- Do not weaken freshness, identity, exposure, risk, CSRF, localhost, duplicate, audit or redaction checks.

**DEMO ORDER NOT SENT — EXECUTION IS NOT IMPLEMENTED.**

The next safe step is manual verification of the read-only evidence protocol on the intended Windows MT5 demo host. Do not claim readiness for execution.
