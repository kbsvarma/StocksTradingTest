# Pre-registration — score-conditioned fast entry (WTA)

**Written 2026-08-05, BEFORE any score data was pulled or inspected.**
Author: Claude. Trigger: user asked why tennis produces so few selections.

## Motivating measurement (already established, no new data)

Of 758 WTA matches in the 55-day archive, 551 reached the 97-99c favorite
band. Only 101 produced an entry under the live rule (10 consecutive
minute-closes in band). The 411 rejected matches split cleanly:

| group | n | favorite won | market life after first touch |
|---|---|---|---|
| settled fast (market closed <10 min) | 176 | **176/176 = 100%** | median 6 min, min 2 min |
| fell out of band | 235 | 224/235 = 95.3% | median 24 min, **min 10 min** |

The hold rule is effectively a market-lifetime filter. It correctly
excludes the 95.3% group (a money-loser at 98c) but forfeits a group that
never lost. Price, ask and spread at first touch are IDENTICAL between
the groups (medians 0.975 / 0.980 / 0.010), so the price path cannot
separate them. A velocity discriminator was tested on 2026-08-05 and
FAILED robustness (t=0.86; sign-flips across reference level and
tie-break convention) — recorded as a kill, not carried forward.

**Core hypothesis:** the separating information is the SCORE, which the
price does not fully impound. Evidence for the premise: those 176 matches
traded at 97-98c while winning 100%. A market that knew they were over
would quote 99.5c+.

## Data

- Kalshi: existing archive `data/games_KXWTAMATCH.jsonl` (1-min bid/ask
  candles, 758 events, ~55 days ending 2026-08-03) + market metadata for
  player names (`yes_sub_title`), tournament and round (`rules_primary`).
- Live Tennis API (livetennisapi.com), Basic tier: `GET /history/matches`
  and `/history/matches/{id}` for point-by-point. Base
  `https://api.livetennisapi.com/api/public/v1`, header `X-API-Key`.
- Join key: player full names + tournament + match date. Matches that
  cannot be joined with confidence are DROPPED and counted (a coverage
  number reported with the result, not hidden).

## Entry rule under test

At the FIRST minute-close where the favorite mid is in [0.97, 0.99),
spread <= 0.02, ask <= 0.99 — i.e. the same gate the live bot uses minus
the hold — read the score as of that timestamp and enter immediately at
ask if and only if the state qualifies. Hold to settlement. Taker fees
both sides. One entry per EVENT (earliest across mirror markets).

### Qualifying states (fixed now, not to be edited after seeing outcomes)

Let F = the favorite side (the side priced 97-99), O = opponent.

- **P1 (primary): MATCH POINT for F.** F can win the match on the next
  point — F leads the deciding set by the required margin-1 with the
  point score at 40-x (x<40), or advantage, or in a tiebreak F has >= 6
  points with a >= 1 lead and needs one more. Includes both F serving and
  O serving (break point that is also match point).
- **P2: F SERVING FOR THE MATCH**, no match point yet (F leads deciding
  set 5-x with x <= 4, or 6-5, and is the server this game).
- **P3: F UP A BREAK IN THE DECIDING SET** (F's game lead >= 2 in the
  deciding set), regardless of server.

P1 is the primary test. P2 and P3 are reported separately as secondary,
and a failure of P1 is NOT rescued by reporting P2/P3 as the headline.

## Gates (identical to every other verdict in this program)

1. n >= 100 entries for the primary state; secondaries need n >= 100 to
   be reportable at all.
2. Win rate strictly above break-even at the realized average entry
   price (~98c => ~98.1%), net of taker fees both sides.
3. Both halves of the sample (chronological 2/3 IS, 1/3 OOS) positive.
4. Day-clustered t >= 2 (t computed on per-day P&L, not per-trade).
5. Worst single day <= 25% of gross positive P&L.
6. Report the union with the existing H10 rule, and how many entries are
   NEW versus already captured.

## Falsification / kill conditions

- P1 win rate at or below break-even => hypothesis dead, score does not
  carry the information, logged as a kill and the lane is unchanged.
- P1 passes but n < 100 => insufficient, no deployment; revisit only
  after the US Open supplies more sample.
- Join coverage < 60% of archive matches => result is unreliable;
  report as inconclusive rather than as a pass.

## ADDENDUM 2026-08-05 (same day, still before any collected data)

External prior located AFTER writing the states above, recorded here
rather than used to rewrite them. Tennis Abstract (Sackmann), ~16,000
WTA matches since 2011: **players who hold match point win the match
"a bit more than 97%"** of the time. Single match points convert 80.8%,
multiple 91.9%. Serving at match point costs ~3% off the normal serve-
point rate (returning shows no such effect).

Break-even at a 98c entry is ~98.1%. So **P1 as written (any match
point) is now EXPECTED TO FAIL** — it is a ~-1c/ct trade tour-wide.
P1 remains the pre-registered primary and will be reported as such;
this addendum exists so the prior is on the record and a P1 failure
cannot later be spun as surprising.

The hypothesis survives only in tighter forms, which were already
listed or are strictly narrower than P1, and which will be reported as
secondary with their own n:
  - match point while ALSO up a break in the deciding set
  - multiple (>=2) simultaneous match points
  - match point on RETURN (no serving-nerves penalty)
Note the target group (176 matches that ENDED within 10 minutes) is
strictly narrower than "reached match point" — many matches reach match
point and continue for a long time. The state that predicts *imminent
conclusion* is what is being sought, not the state that predicts
*eventual victory*, which the price already knows.

## EXTENSION B 2026-08-05 (user-proposed, registered before data)

User: "if the fav or higher-ranked one comes in the 90 range then query
the API and see if it is getting there — like a set won and leading the
next." Registered as a SEPARATE hypothesis with its own gates. (User
said "2 sets won"; WTA is best-of-3, so the equivalent commanding state
is one set won + leading the second. Recorded so the test matches the
intent, not the literal wording.)

**Hypothesis B:** in the 90-97c band, score states denoting a commanding
position beat the price. The band matters because the price is
compressed there too — 92c covers "set + break in the 2nd", "double
break in the 3rd", and "won 1st 6-0, on serve in the 2nd" alike.

**Price-only baseline to beat** (measured 2026-08-05, no score, H10,
event-deduped, day-clustered t):

| band | n | W-L | net/ct | IS | OOS | day-t |
|---|---|---|---|---|---|---|
| 90-93 | 222 | 210-12 | +1.73c | +2.44 | +0.31 | 1.21 |
| 93-95 | 165 | 159-6 | +1.34c | +0.40 | +3.22 | 1.08 |
| 95-97 | 137 | 135-2 | +1.66c | +2.02 | +0.95 | 1.56 |
| 97-99 (live lane) | 101 | 101-0 | +1.20c | +1.21 | +1.18 | **7.23** |

All three 90s bands are positive but INDISTINGUISHABLE FROM NOISE. The
score's job is to lift one from t~1 to t>=2, i.e. to identify the subset
that carries the positive mean. A score rule that merely reproduces the
band average has added nothing.

**States (fixed now):**
- B1: one set won AND up a break in the current set
- B2: one set won AND up a double break in the current set
- B3: one set won AND serving for the match
- B4: one set won AND current set on serve (control — expected WORST;
  included so a "commanding" claim can be falsified against it)

**Prior working against B:** WTA break-back rates run 35-58% by game
(tennisratings.co.uk), so a single break lead is materially less secure
in the women's game than intuition suggests. B1 may therefore be priced
correctly at ~90-93c. B2/B3 are the likelier survivors.

**Gates:** identical to Extension A (n>=100 per state, both halves
positive, day-clustered t>=2, worst day <=25% of gross). Higher variance
is expected here — at 92c a loss costs 92c against a 8c win, so ~9x more
losers than the 97-99 lane. Sample requirements are therefore LARGER,
not smaller; n>=100 is a floor, not a target.

## Explicitly out of scope this pass

- WNBA / any non-tennis family (score semantics differ; fast-approach
  tested NEGATIVE there on 2026-08-05, -6.26c/ct).
- ATP (family is dead on its own backtests; re-opening requires its own
  prereg).
- Bands other than 97-99. Hold-length variations. Exit engineering,
  stop-losses, profit targets — all previously killed, not revisited.
- Live latency/fill modelling: this pass answers "is the information
  there," not "can we get filled." Fill feasibility is a separate live
  question and will NOT be assumed by this backtest.

## Known risk this backtest CANNOT settle

Kalshi market makers may also watch the score. If so, the 97-98c quote
exists BECAUSE of adverse selection, and a passing backtest would still
fail live on fills. The backtest measures information content only.
Any deployment must be tiny and must measure realized fill rate against
the signal, exactly as the maker-first upgrade was measured.
