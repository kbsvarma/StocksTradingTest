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
