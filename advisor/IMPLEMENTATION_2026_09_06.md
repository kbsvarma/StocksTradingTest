# Advisor intelligence implementation

User-authorized September 6, 2026. Preserve the existing working tree, publication controls, research mandate, and disabled execution. New recommendations remain research until independently approved evidence supports promotion. No fabricated production data.

## Ten implementation passes

1. Unified versioned call contract and deterministic lifecycle.
2. Claim evidence packets and point-in-time verification.
3. Expectations-gap underwriting and three explicit playbooks.
4. Scenario payoffs and cohort-specific forecast evaluation.
5. Transactional call history, roles, reviews and audit export.
6. Event dependencies, materiality and reassessment.
7. Cost-aware call outcomes, benchmarks and portfolio context.
8. Integration with current artifacts and scheduled pipeline.
9. Command desk, security workspace, evidence and performance terminal.
10. End-to-end regression, browser verification and visual refinement.

All ten passes below are implemented and locally verified. Software completeness does not establish investment edge, licensed data rights or a deployed identity-provider integration.

## Baseline

The previous review passed 395 tests. Local runtime: Python 3.13, Streamlit 1.45.1 and Plotly 5.24.1; deployment dependencies are separately pinned. Historical local artifacts are not current live output.

## Pass results

### 1. Unified calls and lifecycle

Added a versioned call contract with stable episode identity, explicit intent,
instrument restrictions, finite JSON validation and deterministic lifecycle
transitions. Quote presentation distinguishes fresh live observations from
reference data. Contract and transition regression cases pass.

### 2. Evidence that can be inspected

Added sealed source snapshots, numeric claim value/unit/period matching,
independent semantic review bound to claim/source hashes, and point-in-time
and freshness checks. Missing sources fail closed without crashing the audit.
The terminal exposes excerpts, source bindings, review status and history.

### 3. Expectations-gap underwriting

Implemented earnings continuation, fundamental revision and sector repricing
playbooks. Required measurements, comparable periods, pre-event consensus,
discovery windows and sourced costs gate completeness. Operating-model
sensitivity and explicit thesis/invalidation questions replace unsupported
conviction labels. Complete packets require independent review.

### 4. Scenarios and forecast evaluation

Added weighted net scenario payoffs and adverse sensitivity, with invalid
weights and negative modeled payoff checks. Forecast diagnostics separate
playbook/model/instrument/horizon/event cohorts, use a time split and a
training base-rate comparator, and exclude overlapping holdout windows.
Diagnostics do not automatically promote an unvalidated model.

### 5. Enterprise application foundations

Added transactional SQLite revisions, optimistic concurrency, append-only
audit records, hash-chain exports and consistent backups. Verified identity
maps to tenant/role policy; actual submitters and editors cannot approve their
own calls. Review recomputes evidence and underwriting at approval time.
Added an authenticated tenant-scoped read-only API and OS-account review CLI.
Isolation, spoofing, revision integrity and backup/restore tests pass.

### 6. Event-driven reassessment

Added idempotent event ingestion with per-call receipts, dependency-driven
review, explicit entry confirmations, observed gap prices and minute-level
expiry. Risk crossings remain observable while an active call is under
review. Filing and quote inventories include tracked calls; filing intake
extends to weekday 06:00–22:00 ET. Generic event interfaces support future
licensed guidance, estimates and macro adapters.

### 7. Outcomes and portfolio context

Added cost-aware quote-observed model-book outcomes, prospective/retrospective
separation, unique episode accounting, matched-window benchmark checks and
issue-date clustered descriptive uncertainty. Fresh verified holdings enable
name/sector/gross mandate diagnostics. Missing portfolio context stays visible;
overlapping calls are not compounded into a fictitious portfolio track record.

### 8. Existing pipeline integration

Connected attested briefs through a publication bridge that preserves final
amendments and independently generated claim checks. Wired the offline worker
into brief/nightly workflows, analyst research queues, quotes and filings.
Added optional systemd/launchd scheduler templates. Existing artifacts remain
readable, with legacy identity deduplication and explicit archive labels.
The actual local worker completed with zero errors and no network calls:
four historical episodes, zero new packets. No scheduler was installed.

### 9. Bloomberg-inspired terminal workspace

Replaced the default terminal shell with eight lazy workspaces: Desk, Call
Book, Security, Evidence, Catalysts, Performance, Research Lab and Operations.
Added ticker commands, a market tape with data ages, local price/volume charts,
call inspection, saved watchlists, a thesis builder, packet import and an
interactive scenario calculator. Existing specialist tools remain accessible.
Role and tenant controls gate review and legacy operator functions.

### 10. Regression and visual refinement

Exercised every workspace with real Streamlit AppTest execution and inspected
the running terminal in the browser. Verified security command navigation,
actual chart rendering, call book, scenario controls and final desk layout.
Fixed second-ticker selection, workspace persistence feedback, keyboard input
visibility and command-bar spacing. Added failure regressions for missing
sources, stale approval, impersonation, backdated issuance, duplicate events,
review-time risk crossings and same-day expiry.

## Final verification

- **452 Advisor tests passed**, compared with the 395-test baseline.
- Python compilation, shell syntax checks and launchd template parsing passed.
- `git diff --check -- advisor` passed.
- Local Streamlit preview: `http://127.0.0.1:8516/`.
- Local worker reported healthy; no orders or outbound messages were sent.

## Delivery scope

Implemented in the existing local checkout while preserving prior changes.
The preview correctly shows stale/archive data, 45 discovery candidates and
four historical episodes, with no current approved calls. New research ideas
require fresh evidence and independent review; scenario weights are analyst
assumptions, not calibrated probabilities.

Current licensed feeds, provider-specific event adapters, IdP provisioning
(including the Streamlit authentication dependency), target-host deployment
and prospective performance validation remain external acceptance work.
No claim of proven alpha, deployed enterprise readiness or an objectively
measured 9.5 product rating is made. Setup and operational details are in
[INTELLIGENCE_RUNBOOK.md](INTELLIGENCE_RUNBOOK.md); packet requirements are in
[PACKET_SPEC.md](intelligence/PACKET_SPEC.md).
