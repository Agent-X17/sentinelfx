# SentinelFX system-readiness review — 2026-09-24

## 1. Did the previous hardening pass leave only external onboarding?

**NO.** Base 09adec9 still had internal replay/identity weaknesses, native calls
under the database lock, unbounded native calls, migration startup races and no
verified backup/restore path. Its earlier report described several of these;
it did not substantiate connection-only readiness.

## 2. Overall assessment

This pass strengthens the existing local simulator and diagnostic receiver in place.
All 170 tests pass; runtime and eight-page DOM checks pass. **It is still not honest
to call this fully ready except connections.** Full terminal/order reconciliation,
native broker request translation and real evidence adapters are software work for
a separately reviewed release, not merely account credentials. They remain blocked
here. No risk limits, existing migrations, original prompt assets or user database
records were changed.

## 3. Remaining internal defects found

- Oversized IDs could share a truncated identity with a valid ID; malformed input
  could reserve the corrected alert's identity.
- Redacting IDs before computing identity could collapse different alerts.
- Delimiter-based fallback identities were ambiguous and formatting-sensitive.
- Empty expiry silently fell back to a fresh default; malformed mappings could crash.
- Mock terminal equity overwrote the simulation ledger on each mock candidate.
- Real adapter work ran under the SQLite writer lock, with no native deadline.
- Initial migration checks raced, and initial schema creation was not wholly atomic.
- No newer-schema/startup integrity refusal or verified backup/restore existed.
- Secret values in object keys, duplicate JSON keys and simultaneous header/body
  secret values needed stronger handling.
- Diagnostic account identity allowed truthy invalid types; identity drift during
  and across collections was not modeled.

## 4. Fixes implemented now

Introduced versioned hashed alert identity and canonical JSON fallback identity,
computed before redaction. Legacy keys remain recognized; rejected malformed
deliveries use separate rejection identities. Revalidation/deduplication happen
inside the commit transaction. Expiry and mapping validation fail closed.

Kept ACCOUNT_LIVE at its stored simulation balance; mock data no longer overwrites
ledger equity. Added bounded recursive filtering of credential fields/known values
including keys, rejected duplicate JSON keys and sanitized parser-error responses.

The server uses a fixed diagnostic subprocess with a five-second deadline, one
active worker and fail-closed busy/timeout/failure states. Native calls occur outside
the database lock; real results can only persist NO_TRADE. Identity changes within
and across reads block, with a latch for known drift. Disconnection remains distinct
from drift. This is detection, not full account reconciliation.

Migration version checks and SQL execution now share a write transaction. Bounded
retry handles initial WAL contention. Newer schemas, database integrity failures
and damaged audit chains refuse startup. Verified backup/restore uses SQLite's
consistent backup API, owner-private destination permissions and new paths only.

Dashboard pages retain simulation warnings, show snapshot time/refresh, expose
recorded diagnostic reasons and explain missing rollout work. Integration seams and
backup recovery instructions are documented in INTEGRATION_CONTRACT.md.

## 5. Files changed

Runtime: engine/bridge.py, config.py, diagnostics.py, redaction.py, storage.py,
webhook.py; new engine/isolated_mt5.py and engine/mt5_worker.py; manage.py, server.py,
static/app.js.

Tests: new tests/test_readiness.py and tests/dashboard_dom.cjs.

Documentation: README.md; docs/AGENT_HANDOFF.md, API.md, FINAL_PRELIVE_REVIEW.md,
FULL_PROJECT_REPORT.md, NEXT_AGENT_PROMPT.txt, PRELIVE_CHECKLIST.md, REQUIREMENTS.md,
VERIFICATION.md; new INTEGRATION_CONTRACT.md and this report.

## 6. Tests added or updated

28 new Python regression tests, retaining all 142 prior tests. They cover identity
poisoning/canonicalization/legacy compatibility, expiry/mappings, secret hygiene,
mock ledger isolation, real calls outside transactions, worker isolation/drift,
diagnostic failures, concurrent migration, corruption refusal and backup/restore.
The DOM harness is now reproducible from the repository.

## 7. Exact test results

Requested unittest command: **170 tests in 4.486 seconds; OK, zero failures/errors**.
Python compilation and JavaScript syntax passed. The initial concurrent WAL test
intermittently failed with database-is-locked; after bounded retry, the focused
regression and complete suite passed. This report does not conceal that finding.

## 8. Runtime verification results

Fresh temporary SQLite instance on localhost:8877 with SIMULATION and MT5/live
disabled. Root, health/state and assets loaded; audit integrity valid. Webhooks:
bad secret 401; valid but disconnected 200/NO_TRADE; duplicate 409; missing stop 400.
Mock success and stale 422 cases pass in HTTP tests. Actual startup refuses both
LIVE_EXECUTION_ENABLED=true and LIVE_GATED with exit 1. order_send refuses;
/api/execute, /api/live and /api/order_send return 404. Native diagnostic subprocess
actually runs and reports MT5_PACKAGE_UNAVAILABLE on this Mac. Backup/restore CLI
round-trip and restored audit check passed. User working data was untouched.

## 9. Browser verification results

Agent-browser with downloaded headless Chromium still failed before page load:
macOS bootstrap_check_in Permission denied (1100). No visual/mobile/accessibility
pass is claimed. Compensating DOM integration passed navigation through all eight
sections, evidence notices, simulated open/close through the actual API and no
script errors. tests/dashboard_dom.cjs records the reproducible workflow.

## 10. Documentation/report corrections

Updated current docs for isolated diagnostics outside the DB lock, timeout/drift
states, hashed identity, backup/restore and current verification. Marked the previous
pre-live report explicitly historical. Added the integration contract so an account
connection cannot be mistaken for enabling a complete real trading path.

## 11. What now remains after this pass

External work: MT5 installation/terminal testing on the intended host, TradingView
delivery/HTTPS hosting/secrets, genuine broker/provider/news/macro/cost/legal and
withdrawal evidence, and operational deployment validation.

Software work still required for a real connected approval release: versioned
account/exposure/order reconciliation across restarts/manual trades; native request
translation with broker precision/session/fill rules; implemented evidence adapters
with provenance and expiry; production authentication, rate limiting, monitoring
and deployment recovery controls. Full real-browser testing also remains unverified.
These cannot be truthfully relabeled as credentials alone. Current diagnostic-only
operation deliberately remains blocked and does not need those features to deny
trades. PostgreSQL, multi-user operation and live execution are not implemented.

Redaction cannot recognize unknown secrets in arbitrary prose. Idempotency is not
a signed nonce scheme. Audit hashes are not administrator-proof. Scheduled backup
retention/off-host storage remain operational setup. Real native integration has
not been validated on a terminal; worker timing may need host-specific review.

## 12. Live order submission

**Still disabled.** A/B/C remain $50/$100/$150, ACCOUNT_LIVE a $300 simulation
placeholder, default mode SIMULATION. Live-enabled startup and LIVE_GATED refuse,
order_send always refuses and no live-order HTTP endpoint exists. Mock selection
is impossible from webhook input. Real evidence gaps always block. No real credentials
were added and no live orders were sent.

Branch: prelive-hardening. Base: 09adec9. The commit containing this report is titled
`Strengthen system readiness, isolated diagnostics and persistence safety`.
