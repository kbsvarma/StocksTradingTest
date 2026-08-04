# Sports Late-Favorite Hunt — First Surface Results (2026-08-03)

## ⚠️ CORRECTION (2026-08-03 pm — Codex audit, verified)
**Every event carries TWO mirror markets (one per player/team); the
original surface below counted per-MARKET, so all n's are ~2x overstated
("417/417" = ~229 unique matches). Per-contract EV is unaffected (same
trade observed twice); statistical weight is not. Corrected, auditable
event-deduped replay of the DEPLOYED bot rule: `replay_dedup.py`.**

Corrected survivors (one entry per match, taker at ask, fees, hold to
settle; "strict" variants in replay_dedup.py keep 0 losses too):

| family | cell | n | W-L | net/ct | OOS/ct |
|---|---|---|---|---|---|
| WTA  | 97-99 H10 | 101 | 101-0 | +1.20c | +1.18c |
| WNBA | 97-99 H5  | 57  | 57-0  | +1.14c | +1.13c |
| WC*  | 97-99 H5  | 71  | 71-0  | +1.38c | +1.28c |

**ATP main: NEGATIVE in every cell** (H10 deduped: 110-2, −0.57c net,
−4.09c OOS; H5: 244-8, −1.83c, −4.61c OOS) — was wrongly live 12:44-14:51,
removed. Ladder: ATP favorites touching 97c lose 1.6% vs WTA 0.8%.
Bot also ran H5 while WTA's validated cell is H10 — fixed 14:51.
ITF/ITFW/Challengers had NO historical backtest (paper only) — pulled
from live 15:08 pending own-family verdicts (partial ITF: 140-1, +0.44c,
OOS −1.10 on deployed rule — not clean). Live = WTA-only until gates pass.

**Honest economics at 10 contracts (validated families only):**
WTA ~1.8 signals/day × +1.20c × 10ct ≈ $0.22/day taker; +WNBA (if
re-added) ≈ $0.34/day; maker-first (sim, optimistic) ≈ $0.5-0.65/day.
The "$10-13/day" stated at launch was the 100-CONTRACT paper pace
misapplied to the 10ct bot — wrong by ~20x. $10/day at this edge needs
~100ct positions across several validated families (~$700 bankroll,
one ~$97 loss every 2-3 weeks) — a future decision, not current state.

Maker-first entry (rest bid at ask−1c, cancel on weaken, 120s taker
fallback) simulated +0.7-0.95c/ct uplift on minute candles (fill proxy
OPTIMISTIC — real maker fills unproven). Enabled live 14:58 with user go.

**Small-tour FINAL verdicts (own 55d candle pulls, event-deduped,
replay_dedup.py, 2026-08-03 evening): ALL FAIL the 97-99 live cell.**
| family | n | W-L | net/ct | OOS |
|---|---|---|---|---|
| ATP Challenger | 337 | 330-7 | −0.89c | −0.60c |
| WTA Challenger | 94 | 93-1 | +0.09c | +1.05c |
| ITF (12,602 games) | 1,390 | 1377-13 | +0.30c | −0.09c |
| ITF Women (7,456) | 1,089 | 1075-14 | −0.10c | −0.22c |
Strict variants don't save them (ITF strict 1158-9 +0.49, OOS −0.02).
ITF/ITFW 95-97 H10 mildly positive both halves (+0.49/+0.76, +0.32/+0.69)
but t≈1.0 — watcher-only. Tennis conclusion: the endgame-favorite edge is
a MAIN-TOUR WTA phenomenon (deep books, dog-holders); small tours have
~1-2.6% comeback rates at prices that leave no room. Live = WTA + WNBA.

**Metals verdict (2026-08-03 night, user ask "gold/silver/copper 11pm"):
BOTH FORMS DEAD.** Hourly metals (the 11pm expiries): ~600-800ct/hour
total volume, 4-5c spreads — fails the spread gate structurally, no
backtest needed. Daily metals (settle 5pm ET on COMEX close), gas-cell
replay (95-99 fav, spread<=2c, ask<=98.5, hold to settle) on 55d pulls
(gold 1,250 / silver 1,260 / copper 1,430 markets):

| series | mkts | W-L | net/ct | days | mean/day | day-t | worst day |
|---|---|---|---|---|---|---|---|
| GOLD   | 583 | 566-17 | −0.11c | 31 | −2.1c | −0.06 | −834c |
| SILVER | 654 | 635-19 | +0.16c | 31 | +3.3c | +0.12 | −521c |
| COPPER | 317 | 308-9  | −0.02c | 25 | −0.2c | −0.01 | −255c |

Strikes within a day settle on ONE price → effective n = days, not
markets; day-t ≈ 0 everywhere; one bad day erases ~13 average days.
Mechanism: gas settles on a STICKY ADMINISTRATIVE SURVEY (quasi-knowable
by evening, retail still discounts the favorite); metals settle on a
live futures price with pros on every strike — no harvestable hour.
Gas remains the only commodity lane.

*The tables below are the ORIGINAL per-market (double-counted) surface —
kept for the audit trail; directionally right, n's inflated 2x.*

Data: 5,156 settled games, 55d, 1-min in-game candles (final 6h windows).
Families: MLB 1,400 · WTA 1,516 · ATP 1,480 · WC 312 · WNBA 280 · UFC 168.
Method: pre-registered (RESEARCH_PLAN.md) price-path entries, both
directions, taker+fees, day-clustered t, IS/OOS 2/3 split, BH-FDR,
worst-day concentration gate. In-band median spread: 1.0c everywhere.

## SURVIVORS — one mechanism, three families ("sports lock lane")
Buy the 97-99c favorite that has HELD the band N consecutive minutes
(the game is effectively decided; casual money still holds the dog):

| family | cell | n | WR | net/ct | t(day) | IS/OOS | worst-day |
|---|---|---|---|---|---|---|---|
| WTA   | 97-99 H10 | 183 | 100% | +1.21c | +27.2 | +/+ | 9%  |
| WNBA  | 97-99 H5  | 108 | 100% | +1.14c | +22.3 | +/+ | 6%  |
| WC*   | 97-99 H5  | 126 | 100% | +1.36c | +21.9 | +/+ | 10% |

*WC = 2026 World Cup, tournament OVER — validation mass only.

- Entry timing: median 21-45 MIN before close (not last-second sniping;
  tennis set+break, hoops 4Q blowouts, soccer late 2-goal leads).
- 417/417 combined; Wilson lower bound ~99.1% vs break-even ~98.1%
  (fee at 98c is only ~0.14c — the fee-cheap zone again).
- Mechanistic coherence: MLB same cell FAILS (+0.13c, OOS -1.0) —
  baseball has 9th-inning comebacks; clock/set sports have sticky leads.
  ATP weaker than WTA (not passing). UFC n too small.
- No macro-regime exposure (the crypto killer does not apply here).

## Rejected this pass
All DOG cells (underdog side): sign-flip IS/OOS everywhere, 0 FDR.
Broad WTA favorite bias at 90-97c: positive but t<2 — watch, don't trade.
MLB everything. 85-95c bands generally.

## Known tail (not yet observed in sample)
Tennis retirement/injury mid-"decided" match; hoops/soccer freak
comebacks. Expect a true WR ~99.3-99.7, not 100 — losses will be -97c
when they come. Same shape as every edge in this class.

## Next (pre-registered before any build talk)
1. Forward paper week on NEW games (WTA daily ~4.7 signals, WNBA ~2.9).
2. In-game order-book watcher: record depth at 97-99c during live games
   (capacity + fill-reality; books did 3-9M contracts/game).
3. ATP/MLB re-test with the fresh week folded in.
NO bot, NO capital until (1)+(2) hold.
