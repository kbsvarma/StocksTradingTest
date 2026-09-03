# Analyst Methodology — the loop doctrine

Every advisor session MUST work this way. This file is living doctrine: the
weekly review appends validated lessons to the LESSONS LOG below (additions
only — never rewrite history; the user prunes).

## The loops (run in order; a view may only be published after surviving all)

**Loop A — QUANT (computed, never vibed).** Read `quant.json` AND
`factor_sheet.json` from the context dir. The factor sheet is the
full-market cross-section (~1,500 liquid names, S&P 1500 + extras):
sector-neutral z-scores on literature-grounded factors — momentum 12-1
(Jegadeesh-Titman), 1m reversal, residual momentum (Blitz-Huij-Martens),
52w-high proximity (George-Hwang), low-vol (Ang et al), turnover anomaly —
combined with regime-conditioned weights (momentum de-weighted in stress
regimes per Daniel-Moskowitz momentum-crash evidence). Review LONG, SHORT
and SHOCK/PEAD sheets every session. Single-name discovery is data-driven
from this cross-section plus the news loop — never from memory of past
positions. Flags are candidate generators, not conclusions.

**Loop B — MACRO VERIFY (live web, nothing from memory).** For every
candidate thesis: verify the driving facts with current sources. MANDATORY
for event-driven theses: get market-implied odds (prediction markets, fed
funds futures) — never assume an event's probability from headlines. Check
official scenario analyses (EIA, SEP dots) for magnitude anchors. Date every
fact.

**Loop C — STRUCTURE (how to express it).** If options are considered,
run `vol_check --ticker X` FIRST: the cheap/rich verdict decides the
structure. CHEAP IV + defined catalyst → buy optionality (spreads cap cost).
RICH IV → shares with stops, or sell premium, or pass. Use option OI walls
for realistic targets. Across weekend/event gaps, defined-risk structures
ONLY — stops do not protect across gaps. Event trades carry a time stop just
after the catalyst: no theta camping.

**Loop D — ADVERSARIAL (try to kill it).** For each surviving idea, argue
the other side with sources: what flow/positioning props the other side
(reconstitution, buybacks, seasonality)? What does the counter-thesis price
in? If the kill attempt succeeds, the idea moves to REJECTED — and the brief
REPORTS the rejection with the reason. Showing dead ideas is part of the
product; it proves the bar exists.

**Publish.** Only then write views (max 1-3, conviction-bar rules in IPS),
with the full trade plan and machine-readable levels for the exit watcher.
Every event-driven view states the loss branch explicitly: what happens to
the position in the GOOD outcome for the other side, and what that costs.

## Standing principles (validated in live sessions)

1. **Hunt vol-asymmetry across markets.** The same catalyst can be free in
   one option market and fully priced in another (2026-06-12: IWM puts 26%
   IV = zero FOMC premium, while USO calls 53% IV had Iran fully priced).
   The edge is usually in the asleep market, not in predicting the event.
2. **Cross-asset consistency check.** When two assets price contradictory
   versions of the same macro variable, at least one is wrong — but first
   try the lens that makes them consistent (a geopolitical premium can
   explain what looks like a rates contradiction).
3. **Equities lag commodities on regime breaks.** Sector ETFs reprice
   slower than their commodity (XLE -7.7% vs crude -17.6%); the laggard
   leg is the trade after the event confirms.
4. **Prediction-market odds beat headline vibes.** Headlines said "deal
   this weekend"; Polymarket said 9%. Always look up the number.
5. **Bound hypothetical risk by policy:** a single event scenario loss is
   capped by the configured research-risk policy; total tracked exposure is
   visible in every brief. This is not personalized position sizing.
6. **Shares with fair-value exits are the default expression.** A
   single-name research view tests a valuation/catalyst gap around a stated
   scenario range (advisor/research/fair_value.py — range with
   methods disclosed, confidence graded). Options are the exception that
   must earn its place via vol_check. Factor rank alone is never a thesis —
   it nominates candidates; the analyst loops must find WHY the gap exists
   and why it should close.

## Pipeline + memory doctrine (2026-07-02, INTELLIGENCE_PLAN week 1)

**Three-stage mornings.** Synthesis (drafts views) → independent RED-TEAM
(fresh context, tries to kill each view: evidence audit, mandatory
disconfirmation, quant cross-check, level stress, crowding) → publish
(mechanical merge, journals views AND rejects, sends). Loop D inside one
session is preliminary only; the red-team verdict is the real bar. A healthy
kill/amend rate is 20-60%. Rejected ideas are journaled with code-stamped
reference prices — kills have a measurable counterfactual cost.

**Facts persist, opinions re-earn.** Dossiers
(advisor/data/knowledge/dossiers/) hold machine facts (facts.json, dated) and
narrative judgment (narrative.md, every bullet dated). A dossier opinion is
input to the loops, never a substitute: every published view re-verifies its
load-bearing facts live that day. Kill lists are append-only and cut both
ways — no re-pitching killed theses without their revisit-if condition, and
no staying dead on stale reasons. Post-earnings, bull/bear bullets are
UNVERIFIED until re-underwritten.

**Learning loop.** p_win (0.50-0.85) and source (candidate generator) are
mandatory on views; Brier calibration and by-source attribution run weekly
with hard sample gates (no conclusions n<15, no conviction changes n<30,
generators untouchable n<10). The exit watcher records level hits + MAE/MFE
mechanically; humans (weekly review) write final resolutions with outcome
tags.

**Reject-history safeguard.** Rejected longs are retained for counterfactual
scoring, but repeated rejects are not a short signal. The early 17/19
observation was a tiny selected sample without a registered control. The
`repeat_kill` candidate pathway is disabled until a prospective study with a
predeclared benchmark, costs, sample gate, and independent approval supports it.

## LESSONS LOG (weekly review appends; never edits prior entries)

## Technical-state and source doctrine (2026-09-03)

`technical.json` is a deterministic confirmation/risk layer built from the
same immutable OHLCV release as the factor sheet. It covers RSI14,
MACD(12,26,9), ADX14, ATR14, Bollinger position, Donchian55, the 20/50/200
trend stack, 63-day SPY-relative strength, volume confirmation, downside
volatility and 126-day drawdown. A technical setup can nominate or veto a
research candidate and can anchor volatility-aware levels. Its
`state_confidence` measures indicator completeness/alignment—not probability
of profit—and carries zero model weight until prospective validation passes.

Fundamental corroboration should prefer `edgar_fundamental.json` where
available. It derives growth, operating margin, ROE, FCF margin and debt/CFO
from SEC Company Facts and retains the latest filing date. Vendor profile and
estimate snapshots remain secondary, prospective-only observations.

Macro numbers should prefer `fred_macro.json` for Treasury yields,
breakevens, high-yield spreads, the 2s10s curve and financial conditions. The
source-health inventory is a publication input: missing/stale critical feeds
must degrade trust and cannot be papered over by an LLM web search.

- 2026-06-12 (founding session): four-loop process produced IWM 285/275
  put spread (cheap-vol FOMC expression) and killed USO calls (rich vol).
  Outcome pending — scorecard will judge.
- 2026-06-14 (week-1 review): IWM at 52w high (+4% 5d) moving against
  thesis; spread ~50% underwater heading into FOMC catalyst. Process
  observation: conditional watches (USO/XLE/GLD) correctly stayed on
  sideline — none triggered. Discipline validated. Risk observation:
  100% of active advisor exposure is one macro bet (short equity-beta via
  IWM puts into FOMC). Acceptable at $540/2.2% budget, but sets a
  precedent to flag concentration before adding a second directional
  trade on the same factor. Signal health baseline established
  (ic_validation.json); scipy needed for automated reruns.
