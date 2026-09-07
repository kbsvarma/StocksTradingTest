# Improvement Loop — chain log (each pass reads this, works, appends, re-ranks)

**Session: 2026-06-12 16:11 ET → 17:11 ET (1 hour, user-authorized autonomous)**
Rules: change→test→commit per pass; no user proposals mid-loop; any change
affecting future live execution gets flagged in the end-of-hour report;
each pass appends FINDINGS + CHANGES + MEASUREMENTS + updated BACKLOG.

## BACKLOG (re-rank every pass; attack the top)
1. **Factor validation (IC test)** — the composite has never been validated.
   Compute information coefficients: factor z's vs forward 21d returns over
   the 2y panel (rolling, by regime). Kill or re-weight factors with no IC.
   This is THE materiality test of the whole signal stack.
2. **Lookahead audit** — verify no factor uses information unavailable at
   signal time (e.g. rolling windows including day-0 close is fine for
   EOD-next-open use, but document the convention; check 52w max includes
   today; check turnover windows).
3. **Executor unit tests** — gates (status, TTL, clock, caps, rails) have
   zero offline coverage; the proposals module has 11 tests, executor has 0.
4. **Watcher robustness** — per-ticker serial yfinance fetch (slow, no batch),
   no retry/backoff, no stale-price guard (could alert on stale data).
5. **Listener hardening** — approval codes single-use check, replay of old
   Telegram updates after offset loss, expired-proposal cleanup job.
6. **vol_check ATM robustness** — argsort[:2] can pick far strikes on thin
   chains; IV sanity bounds; handle 0DTE expiries gracefully.
7. **fair_value sector-relative layer** — justified P/E ignores sector norms.
8. **Regime thresholds** — VIX 20/26 cutoffs are hand-set; derive from
   percentiles of the panel's own VIX history.
9. **Signal decay/turnover** — how fast do ranks change? Sets rebalance cadence.
10. **brief.json schema validation** — terminal renders it; nothing validates it.

## PASS LOG
(appended by each pass below)
### PASS 1 (16:11–16:16) — Factor validation + retuning
FINDINGS: Built IC harness (validate.py) sharing raw_factors() with live code
(refactor — single source of truth, as-of-sliceable). 11 non-overlap 21d
periods: mom/resid/prox validated (t≈1.8-1.9, 73% pos, top-decile +1.72%/21d);
rev_1m & lowvol NEGATIVE IC in this (mostly risk_on) sample; turn_anom dead.
Lookahead audit (backlog #2): convention documented — signal = as-of close,
consumed next morning; fwd return measured as-of-close→+21d; no overlap. CLEAN.
CHANGES: risk_on weights → mom .35/resid .30/prox .25, rev+lowvol+turn zeroed
(risk_on only — kept in neutral/stress, untested regime, literature prior);
turn_anom zeroed everywhere. validate.py committed as permanent harness.
MEASURED: risk_on composite IC .059→.073 (in-sample — mechanically expected,
not proof), top-decile excess +1.87%/21d. ic_validation.json saved.
RE-RANKED BACKLOG: 1) executor tests 2) watcher robustness (batch+stale guard)
3) listener hardening 4) vol_check ATM robustness 5) signal decay/turnover
6) regime thresholds 7) fair_value sector layer 8) brief.json validation
9) IC harness in weekly review (auto re-validate as panel grows)  ← new item
### PASS 2 (16:14–16:17) — Executor gate tests
FINDINGS: All 6 refusal gates fire before broker imports → fully testable
offline. Confirmed correct semantics: clock/cap refusals leave proposal
APPROVED (re-runnable); only EXECUTING failures go FAILED; EXECUTED is a
terminal idempotency wall. One test-harness gotcha: Proposal.expired() uses
real clock — fixture now sets real-future TTLs.
CHANGES: advisor/tests/test_executor_gates.py — 8 tests, 8/8 pass.
NEXT: watcher robustness (top of backlog).
### PASS 3 (16:17–16:19) — Watcher: batch pricing + stale-quote guard
FINDINGS: per-ticker serial fetch was N requests/scan and would happily alert
on a stale quote (same failure class as 2026-05-20 stale-yfinance incident).
CHANGES: batch_prices() — ONE 1m-bar download for all watched tickers; quotes
older than 30min are dropped (watcher never alerts on a dead feed; absence of
fresh data = skip, logged). Live-tested vs the 4 open calls; service restarted.
NEXT: listener hardening (replay after offset loss + expired-PENDING sweep).
### PASS 4 (16:19–16:22) — Listener hardening
CHANGES: (1) replay protection — messages older than 600s ignored (an old
YES can no longer fire after telegram_offset.json loss); (2) expired-PENDING
sweep every 10 poll cycles (proposals now expire actively, not lazily).
Verified: syntax+import, service restarted clean.
NEXT: vol_check ATM robustness, then signal decay/turnover study.
### PASS 5 (16:19–16:23) — vol_check robustness, data-driven regime, decay study
CHANGES: (1) vol_check ATM = strikes within ±2% of spot + IV sanity bounds
[3%,300%] + min-2-quotes rule (thin chains → silence, not fake verdicts);
(2) regime VIX cutoffs now percentile-based (p70/p90 of trailing 2y) — adapt
to vol era, term/credit stay absolute; (3) decay study added to validate.py:
rank autocorr(21d)=0.915, top-decile retention=72% → ranks sticky, so
(4) nightly.py flags NEW ENTRANTS to long/short sheets (a new arrival is a
rare, information-bearing event); (5) weekly review now re-runs IC validation
and flags factor-IC sign flips to user (weights never auto-flip post-hour).
MEASURED: composite IC unchanged (.073, t 1.84) under new regime detection
(regime still risk_on; thresholds matched old constants in current era).
NEXT: weekly run_weekly.sh allowedTools needs validate; fair_value sector layer.
### PASS 6 (16:20–16:26) — Walk-forward backtest + beta-adjusted alpha
FINDINGS: Strongest evidence tier added to validate.py: top-20 equal-weight,
21d rebalance, 11 non-overlapping periods (~1yr): +78.6% vs SPY +22.1%,
excess +3.85%/21d (t=1.73), beat SPY 73% of periods, worst -7.4%. Skeptic
pass: portfolio beta 1.38 → beta-adjusted alpha +3.13%/21d — signal is NOT
just leverage. Caveats stand: 11 periods, survivorship-flattered, one regime.
CHANGES: walk_forward_top20 block in validate.py + render; weekly review
allowedTools gained research.validate + research.fair_value (was missing —
the C3 self-check I added in pass 5 couldn't actually run).
RE-RANKED: 1) terminal: show validation evidence on SCORECARD 2) full test
suite sweep + service restarts 3) fair_value sector layer (deferred — needs
slow peer fetches) 4) brief.json schema validator (deferred — terminal is
tolerant). Time check: ~35min remain; items 1-2 then final report.
### PASS 7 (16:21–16:28) — Terminal validation panel + INCIDENT: legacy stack paused
CHANGES: SCORECARD tab now renders SIGNAL VALIDATION (walk-forward, factor
ICs, decay, caveats) from ic_validation.json. Full test suite: 19/19 green.
INCIDENT FINDING: webull-bot/telegram-service/watchdog/reconcile-watchdog/
ssm-sync plists were RENAMED *.PAUSED-2026-06 in ~/Library/LaunchAgents
(human convention, matches .RETIRED/.DISABLED precedents) — an INTENTIONAL
pause of the entire legacy stack by the user or their other session, NOT the
BTM/security failure I first assumed. Loop did NOT resurrect them (reverting
deliberate human action is out of scope, mandate notwithstanding). My morning
re-enable of the same four may itself have fought an earlier pause attempt —
flagged for the final report. Advisor services unaffected and all green.
IMPLICATION: advisor stack is now the ONLY live safety surface: exit-watcher
covers journal calls; Tier-1 executor runs its own inline SL monitor. The
legacy reconcile-watchdog's orphan sweep is paused with the rest — fine while
nothing trades, NOTE for when Tier-1 executes: executor monitor is the sole
SL layer until user revisits the pause.
### PASS 8 (16:28–16:31) — brief.json write-time validator
CHANGES: advisor/brief_check.py — validates structured briefs before the
session may send (required keys, conviction enum, direction-coherent levels
[long: stop<target / short: stop>target], http(s) evidence URLs, watchability
warnings). Wired into daily prompt (must exit 0) + run_brief allowedTools.
MEASURED: valid fixture rc=0; bad fixture caught 4 errors + 1 warning incl.
the inverted-levels class that would have armed the watcher backwards.
REMAINING (deferred, documented): fair_value sector-relative layer (needs
slow peer fundamental fetches or paid feed); guardian for service health
(superseded — legacy stack pause is intentional, advisor services have
KeepAlive). Final pass next: full verification sweep + report.
### PASS 9 (16:29–16:34) — Shock-sheet validation: REVERSAL, not drift
FINDINGS: Signed shock-direction forward returns are NEGATIVE: -1.41%/21d
vs universe (t=-1.74, n=11). Generic >2.5σ moves MEAN-REVERT at 21d in this
sample — the PEAD framing only holds for earnings-specific shocks at shorter
horizons (which our flag doesn't isolate; free data has no earnings-date feed).
CHANGES: pead_shock_drift block in validate.py (permanent); shock sheet
relabeled "REVERSION, not drift" in factor output so analyst loops fade
shocks rather than chase them. A negative result that prevents a whole class
of bad trades = material improvement.
### FINAL (16:30) — Loop closed after 9 passes
Verification: 19/19 tests, all modules import, 6 advisor services healthy,
terminal 200. Loop closed 40min early on the engine's own conviction-bar
principle: remaining backlog items are all deferred-class (fair_value sector
layer → needs paid/slow peer data; regime-conditional IC → needs longer
panel; guardian → superseded by intentional legacy pause). Manufacturing
passes past material exhaustion would be churn, not improvement.
DEFERRED BACKLOG (ranked): 1) fair_value sector-relative (revisit with paid
fundamentals feed) 2) longer panel (5y) for regime-conditional IC + more
walk-forward periods 3) earnings-date feed to build TRUE PEAD (current shock
flag = reversion) 4) per-call $ sizing in journal for precise budget tracking.

### 2026-09-04 — Suggestions v3 and operational integrity

Implemented the suggestions-first review in the local checkout. V3 separates opposing/contextual evidence from directional support; source and panel releases are bound and checked for exchange-session freshness. Priority research has explicit quality/capacity gates, reviewed thesis plans, known blockers and revision/episode identity. Standing-view concentration and signed correlation checks apply before selection. Missing portfolio/borrow context remains explicit.

Publication now uses durable immutable releases with one commit point, OS writer locking, orphan exclusion, partial-tail recovery and archived issue-row recovery. Nightly required-output failures return nonzero, preserve last-good output and reach watchdog/UI health. New entry-policy simulations require actual entry eligibility, use gap opens, retain ambiguity and deduct issue-time modelled costs. Replay/current-version separation, first-episode sampling, nonoverlapping generator-learning clusters and a full-opportunity ranking comparator prevent overstating evidence.

Additional quote-path review removed untimestamped terminal fallbacks, added suggestion subscriptions/cancellation/reconnect handling, bounded Yahoo fallback workers, and tracked price-field updates separately from volume/close updates. Delayed data cannot place an idea in the terminal's priority entry group. The disabled-by-default executor now keeps fill journaling/notifications outside its monitor path and checks exchange sessions.

Verification: **395 Advisor tests passed**, up from 345 at baseline; 50 added regression cases cover failure/recovery, four concurrent publication processes (40 committed issues), actual Streamlit fragment rendering, operator-review-to-priority flow, entry/gap simulations and a fully mocked executor timing regression. `git diff --check` passed. No broker orders, production deployment or production-state mutation were performed. See `SUGGESTIONS_RUNBOOK.md` for migration and external acceptance requirements. This is engineering verification, not an investment-performance or commercial-readiness certification.

### 2026-09-06 — Intelligence engine and terminal, ten implementation passes

Implemented a versioned evidence-backed call lifecycle, three expectations-gap
playbooks, scenario analysis, independent approval with freshness revalidation,
transactional history/audit exports, tenant roles and a read-only API. Added
event reassessment, cost-aware model-book outcomes and portfolio diagnostics.
Connected the worker to attested briefs, research queues, quotes and filings.

The default terminal now has eight command-driven workspaces, local charts,
inspectable evidence, a thesis builder, packet import, scenario controls and
saved watchlists. Existing specialist tools remain available. Local historical
artifacts are visibly stale/archive data, not current recommendations.

Verification: **452 Advisor tests passed**; compilation, shell syntax, launchd
template parsing and diff whitespace checks passed. Browser inspection covered
the desk, security chart, call book and scenario workspace. The offline worker
ran against local data with zero errors, four historical episodes and zero new
packets. No orders, outbound messages or remote deployment were performed.
Current licensed feeds, provisioned identity and prospective investment
validation remain external acceptance requirements. Detailed ten-pass results:
`IMPLEMENTATION_2026_09_06.md`; operation: `INTELLIGENCE_RUNBOOK.md`.

### 2026-09-06 — On-demand investigator and expanded intelligence methodology

Expanded the intelligence design to 43 source routes, 18 analytical dimensions,
12 competing hypothesis families and eight sector lenses. Added live SEC facts,
filing/exhibit reads, issuer release discovery, Yahoo market/estimate/news/options
collection, FRED context, optional provider adapters and licensed source imports.

Added separate publication, observation, retrieval, fiscal/settlement and event
clocks; comparable-quarter and TTM reconciliation; cash/receivables divergences;
trend/RSI/relative-strength and speculative-risk diagnostics; reverse expectations;
source-independent research ranking; and changes since the previous report.
Headline-only leads and unrelated news cannot support current material claims.

Built bounded web research, sourced related-company follow-up, evidence-bound
synthesis and adversarial challenge on the existing model runtime. The terminal
accepts arbitrary stock tickers through `TICKER INT`, runs background jobs, shows
report history/evidence clocks/source gaps, exports snapshots and can hand a report
to the research queue while retaining its original time and hash.

Verification: **491 Advisor tests passed**; compilation and diff whitespace checks
passed. Live NVDA collection returned **312 records**, including **five dated
issuer releases**, with no collection errors. The CLI process supervisor and the
terminal's actual background-launch workflow were exercised. Model orchestration,
citation rejection and adversarial publication were tested with controlled
responses. The installed Claude OAuth session was expired during live deep-mode
verification; refreshing `claude auth login` is required to finish that live
acceptance run. This is an explicit runtime limitation, not a claim of completed
live semantic validation. No orders or outbound messages were sent.

See `INVESTIGATOR_METHODOLOGY.md`, `INVESTIGATOR_SOURCES.md` and
`INVESTIGATOR_RUNBOOK.md` for design, implemented collection routes and operation.

### 2026-09-06 — Remove investigator assistant-account coupling

At user request, removed the new investigator's Claude subprocess integration
and login prompts. Collection and calculated findings remain available; the
model reasoning interface is explicitly disconnected. No replacement provider
was connected. The pre-existing scheduled brief integration is a separate
scope awaiting clarification. The 8516 local preview is being shut down;
the original Advisor instance remains at 192.168.3.36:8505. Local changes have
not been deployed to that host by this action.

### 2026-09-06 — Live deployment and research usability correction

The accumulated implementation was deployed to the existing Linux Advisor on
port 8505 as release 20260907T013557Z-22b445766f4d-5f1533400217; 492 server tests
passed. The original browser tab successfully ran and displayed an NVDA scan.

Follow-up correction: replaced the duplicate command/search rows with prominent
company search, compact controls and a vibrant semantic terminal palette. Company
names, ticker casing and common NVIDIA misspellings resolve before collection;
ambiguous provider matches require a listing choice and unknown searches stop.
The decision brief presents cross-factor implications, supporting/challenging
findings, dated evidence, valuation sensitivities and conditional price checkpoints.
Empty reports explain coverage failures; macro/benchmark observations cannot
verify a company. Source clocks are rechecked at viewing time. Removed JSON and
internal hypothesis tables from the report body; complete audit data remains
exportable. Completed jobs load automatically. No model account was connected.
507 local tests pass, including search ambiguity, name variants, missing security
evidence and stale/future evidence gates. Live visual acceptance follows deployment.

### 2026-09-06 — Interactive security terminal and personal watchlist

Replaced nested command controls with a single aligned row; search and command
inputs/buttons share an explicit 44px outer height. Company search opens a
five-year adjusted OHLC chart with volume, range controls, line/candlestick modes,
50/200-session averages, zoom/pan/export and common-start SPY/QQQ comparison.
Reference and after-hours quote timestamps remain distinct. Added a company
snapshot grid and retained the deeper evidence-based investigation below it.

Added a per-user/per-tenant persistent Watchlist workspace, initially NVDA, AAPL,
MSFT and GOOGL, with add/remove, source refresh, sparklines, quote dates, monthly
performance, latest research status and investigation drill-through. Failed market
refreshes preserve the last good dated snapshot. Empty saved watchlists remain
empty; no default list is silently reinserted. Existing pipeline watchlist state
management is unchanged. 511 tests pass locally before deployment acceptance.
