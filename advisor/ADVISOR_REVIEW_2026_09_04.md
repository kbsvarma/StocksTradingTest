# Advisor review: suggestions first, operations second

Review date: 2026-09-04. Scope: local source review of candidate generation, ranked picks, terminal recommendations, outcome tracking, nightly orchestration and the optional executor. No production artifacts were available at the default local research path, so this is not an assessment of today's actual picks or deployed service health. No trading or deployment changes were made.

The strongest next investment is suggestion quality and lifecycle integrity. Existing protections worth preserving include independent red-team publication, research/actionable separation, calibration version checks, opportunity-set archives, and disabled-by-default execution. Adding more indicators should follow fixing the interpretation and evaluation of existing evidence.

## Suggestions: prioritized findings

### 1. P1 — Opposing evidence currently increases conviction

`research/generators.py:199–246`: the strongest directional generator determines direction, but confluence counts every family regardless of direction or strength. Reproduced directly with the scorer:

| Inputs | Output direction | Score |
|---|---|---:|
| Tactical short, percentile .95 | Short | .7125 |
| Same short + cheap-quality long, percentile .90 | Short | .8375 |
| Same short + directionless squeeze flag | Short | .8375 |

Count only qualified, directionally compatible evidence as support. Show opposing evidence separately; a close long/short contest should become “conflicted” and require review. Directionless flags should describe risk or context until an explicit compatibility rule is validated. Add regression tests for these exact examples and deterministic tie handling.

### 2. P1 — Stale candidates can be issued with fresh timestamps

`research/nightly.py:126–173` catches candidate-build failures and continues to picks. `research/picks.py:131–151` loads `candidates_latest.json` without checking its age or matching it to the price-panel release. A failed refresh can therefore combine yesterday's evidence with today's levels. `terminal.py:1322` displays the date but does not expire this list.

Bind every pick to an immutable candidate release and panel build, with per-source observation timestamps. Refuse a new current list when required inputs are stale or incompatible; retain the prior list visibly marked stale. Use exchange-session freshness rules so weekends do not create false outages. Test candidate failure, interrupted refresh and stale optional feeds.

### 3. P2 — Relative rank is being asked to stand in for recommendation quality

`research/picks.py:255–256` sorts by score and fills available slots under concentration caps; there is no minimum thesis/evidence threshold. Percentiles from different populations are documented but still compete numerically. A high rank in a weak or narrowly covered pool need not be a strong opportunity.

Keep broad candidates for research measurement, but introduce a smaller priority list with explicit evidence completeness, source freshness, directional agreement, catalyst and entry-state requirements. Allow zero qualifying suggestions. Separate signal strength, evidence quality and actionability; do not combine them into an invented probability. Validate any new ranking weights prospectively against the archived opportunity set.

### 4. P2 — Every generator gets the same trade template

`research/picks.py:39–42, 200–229` gives all names a 21-session horizon, 1.5 ATR stop, 3 ATR target and symmetric entry band. The displayed 2:1 reward/risk is built into the template, not evidence of an attractive thesis. Earnings dates are carried through but do not change these levels or eligibility.

Create separately versioned research templates for momentum, earnings drift and valuation ideas. Require a stated catalyst, invalidation condition and horizon rationale before promotion to the priority list. Mark event exposure explicitly. Compare templates out of sample before changing the live policy; wider stops alone are not an established improvement.

### 5. P2 — Diversification only considers the newly selected list

`research/picks.py:331–390` applies sector, lead-generator and absolute pairwise-correlation caps within today's picks. It does not include standing recommendations or holdings. Missing sectors share an “unknown” bucket. Absolute underlying correlation also ignores the proposed trade direction.

Enrich sector metadata for every candidate, then calculate incremental exposure against standing views and verified holdings when available. Report signed exposure, shared macro drivers and missing correlation coverage. Use direction-aware exposure checks while retaining separate gross concentration limits. With no verified portfolio, label diversification as list-only.

### 6. P1 — The learning record does not model the displayed entry plan

`research/picks.py:408–445` records the prior close but omits entry bands. `research/pick_tracker.py:63–164` evaluates subsequent bars as though exposure began at that close, without checking an entry trigger; stops are booked at their exact level even across gaps. Consequently, these outcomes cannot establish the performance of following the displayed suggestions.

Maintain separate signal returns and entry-policy simulations. Record issue time, entry bands, activation/expiry, costs and borrow assumptions. Start simulated exposure only after an eligible entry; model gaps and retain same-bar ambiguity. Snapshot cost inputs at issue time. Also mature the no-stop comparator independently: an early-resolved stop with `unstopped_return_pct=None` is currently skipped on later resolve runs, so that comparator can remain missing permanently.

### 7. P2 — Repeated appearances and same-day changes need explicit identity

`research/picks.py:408–427` deduplicates ledger rows by date and ticker, while the latest list can change on a rerun. A changed direction or level can appear in the UI while the ledger retains the first version. Reissuing sticky names each day also creates overlapping observations, not independent experiments.

Use immutable suggestion IDs with revision IDs and links to predecessor suggestions. Distinguish “new,” “unchanged,” “updated,” “invalidated” and “expired.” Record the displayed revision in outcomes. Report unique thesis episodes and evaluate uncertainty with date/ticker clustering instead of treating every daily row as independent.

### 8. P2 — The terminal needs a decision-oriented suggestion list

`terminal.py:1388–1434` emphasizes rank, score, mechanical levels and generator labels. These explain the algorithm but do not fully answer why to research a name now. Standing-view cards also evaluate the entry band before independently checking current stop/target crossings, relying on the exit watcher to update state first (`terminal.py:361–389`).

Use three visible groups: priority research, waiting for a trigger, and blocked/stale. Each row should show: what changed, thesis/catalyst, supporting and opposing evidence, current price versus entry, event exposure, invalidation, freshness, and the exact blocker to promotion. Put generator decomposition in expandable details. Compute lifecycle state in one shared function for the UI and watcher, with invalidation ahead of entry classification. Show “why excluded” for near misses.

## Operations: prioritized findings

### 9. P1 before enabling execution — Monitoring starts after blocking work

`executor.py:229–277` fetches market data, persists/journals and sends Telegram before starting the position monitor. Telegram's HTTP timeout alone is 35 seconds (`telegram_io.py:51`), inconsistent with the executor's stated approximately two-second monitoring invariant. A journal exception can also prevent reaching monitor startup.

Persist the minimal fill record and establish confirmed protection/monitor ownership immediately. Move enrichment and notifications off that critical path. Test slow quotes, failed journal writes, notification timeouts and process termination after fill. Execution is disabled in the reviewed config, so this is a prerequisite to enablement, not evidence of a current unprotected live position.

### 10. P2 — Nightly success does not mean a usable suggestion release

`research/nightly.py:126–173` logs multiple downstream failures and still returns zero. Existing watchdogs may detect some symptoms, but the job's exit status does not establish candidate/pick freshness.

Publish a structured stage manifest with required/optional outcomes, coverage, release IDs and last-good times. Return failure when a required deliverable is missing; represent partial optional coverage as degraded. Alert on persistent missing output and recovery. Add service objectives for suggestion freshness, required-feed coverage and publication completion, rather than process liveness alone.

### 11. P2 — Atomic individual files do not make an atomic research release

`research/datastore.py:201–203` resolves the current build separately for every panel load. If the pointer switches between reads, one computation can mix releases. Picks publish the latest JSON before ledger append (`research/picks.py:317–324`), allowing a crash to expose suggestions without their issue records.

Resolve one immutable build at run start and pass it through the pipeline. Publish a manifest only after candidates, picks and issue records are durable. Make retries idempotent by release/suggestion ID and provide startup reconciliation for incomplete commits. Extend the existing publication-commit pattern to daily picks rather than inventing a second consistency model.

## Recommended implementation order

1. Fix directional confluence, stale-input issuance and suggestion revision identity.
2. Add the shared lifecycle classifier and the decision-oriented list, including honest empty/degraded states.
3. Repair entry-policy evaluation and complete comparator maturation; then compare ranking/template variants prospectively.
4. Add atomic research releases and stage-level operational health.
5. Before any execution enablement, remove blocking work before monitor startup and exercise broker reconciliation/recovery scenarios.

Acceptance should mean more reproducible and better-explained suggestions first. Improved investment performance requires independent outcome evidence; passing software tests cannot establish it.

## Verification

The targeted existing suite passed: **59 tests** across `test_generators.py`, `test_picks_scoring.py`, `test_executor_gates.py`, `test_quote_freshness.py`, and `test_actionability.py`, using `/opt/anaconda3/bin/python -m pytest`. The default system Python lacked pytest. The directional-confluence examples above were independently reproduced by invoking the actual scorer. Other findings are source-path analysis, not production incident reproductions. No full-suite, broker integration, deployed-service or investment-performance validation was performed.

## Implementation follow-through

The subsequent implementation addresses the eleven findings above and adds
quote-event freshness, subscription lifecycle, bounded fallback workers,
first-episode learning discipline and reviewed-plan import. The final local
Advisor suite passed 395 tests (345 baseline plus 50 new regression cases),
including concurrent publication and Streamlit rendering. See
`SUGGESTIONS_RUNBOOK.md` and the 2026-09-04 entry in `IMPROVEMENT_LOG.md` for
behavior, migration and remaining deployed/prospective acceptance. The original
review findings above are retained as the pre-change record.
