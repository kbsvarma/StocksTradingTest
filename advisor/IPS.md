# Investment Policy Statement — DRAFT v1 (2026-06-12)

Owner: Varma. System role: non-personalized research side tool.
This document governs every advisor session. The user edits it; the advisor
proposes changes but never applies them unilaterally.

## Phase (current): HUMAN-IN-THE-LOOP VALIDATION — first few weeks
- **No trade enters by itself. Including SPX.** The legacy bot's auto-entry
  is disabled (`run_days: []`); its monitoring/watchdog services stay up.
- Every idea arrives as a research report. The model pipeline has no proposal
  or execution permission; live execution is independently disabled.
- Exit criteria for this phase: decision journal shows a measured edge
  (hit rate + vs-SPY benchmark over a fair sample, reviewed weekly).
  Then we discuss what earns back automation.

## Research mandate
- **Universe**: full market — stocks (any cap/sector), ETFs, gold/metals,
  futures, FX, crypto. SPX options carry zero special gravity.
- **No legacy priors**: past repo/memory research conclusions have no
  standing weight in recommendations. Ideas must stand on current data;
  old names need fresh, shown evidence.
- **Conviction bar**: a publishable research view requires scenario entry,
  target, invalidation, catalyst, and bounded hypothetical risk. 0 ideas is a valid day and must
  be stated explicitly. Max 1-3 views per brief.
- **Options are never the default lens** (user rule, 2026-06-12): views are
  expressed in stocks/ETFs/assets first; an options structure appears only
  with specific, shown justification. Old-bot operational details stay out
  of briefs entirely.
- **Exit strategy is mandatory**: every recommendation carries numeric
  target_px and stop_px (plus time stop). The exit watcher
  (advisor/exit_watcher.py) monitors these levels live during RTH and
  records entry-zone, target, and stop observations. Research-only views are
  never worded as buy/sell/exit instructions; an actionable-idea label still
  requires canonical calibration and verified portfolio context. A view the
  watcher cannot observe is incomplete and excluded from calibration.
- **Carve-outs**: (1) actual holdings are always monitored and reported —
  coverage is duty, not bias; (2) engineering invariants never expire
  (tick alignment, no combo MARKET, SL-within-2s, symbol whitelist).

## Risk budget (user-confirmed 2026-06-12)
- **RESEARCH SCENARIO BUDGET: $25,000** — maximum combined hypothetical
  capital across tracked research views. This is a research constraint, not
  personalized sizing or authorization to deploy capital.
- Max loss per advisor-executed trade: $5,000 (rail-enforced).
- Daily realized loss cap: $600 — no new advisor executions past it.
- Max 1 advisor execution/day, 1 contract, 3 proposals/day (rail-enforced).
- Research views state modeled risk; any real allocation remains outside this
  system and requires the user's independent suitability and broker decision.
- Concentration: weekly review must flag when >80% of active risk is the
  same factor (short-vol / long-equity-beta).

## Accountability
- Every view journaled verbatim at publication (entry/target/stop/time-stop).
- Weekly review resolves calls against what was written — no revisionism —
  and benchmarks the basket vs holding SPY.
- Proposals expire (default 30 min, max 240); expired ≠ rejected, but both
  are tracked.

## Standing watches
(None yet. Daily briefs add them here via user agreement; each needs a
trigger condition, direction, and review date.)

## Change log
- 2026-06-12 v1 draft created with user-agreed scope from 2026-06-11 session.
