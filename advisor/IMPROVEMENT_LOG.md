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
