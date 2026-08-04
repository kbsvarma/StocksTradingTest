# Kalshi Sports — Late-Favorite Hunt (pre-registered 2026-08-03)

Hypothesis: sports favorite-longshot bias (heavily documented in betting
economics) appears in Kalshi's in-game markets: favorites trading 85-99c
during games are underpriced because casual flow buys underdog lottery
tickets. Same mechanism class as the (now dead) crypto 15M edge, but:
event-driven settlement (no macro-regime exposure), far deeper books.

## Data
Settled markets + 1-min in-game candles, last ~55d:
KXMLBGAME (recurring daily), KXWNBAGAME, KXATPMATCH, KXWTAMATCH,
KXUFCFIGHT, KXWCGAME (one-off tournament; validation mass only).
Sports markets close EARLY when the winner is declared -> time-to-close
is endogenous. NO time-to-close entry anchors (lookahead). Entry rules
must be price-path-only:

## Pre-registered entry family (per sport, own parameters)
At 1-min candle close t: favorite side price in band [B_lo, B_hi),
held >= H consecutive minutes since FIRST crossing B_lo (no flip since),
spread <= S. Enter taker at ask, hold to settlement. Surface:
- B in {85-90, 90-93, 93-95, 95-97, 97-99}
- H in {3, 5, 10}
- S: per-sport from observed spread distributions
- Also the mirror (buy the DOG in band mirror) to test bias direction.

## Gates (unchanged program discipline)
Taker fees; day-clustered t >= 2; IS/OOS split at 2/3 of sample; BH-FDR
within family; minimum n per cell 100; verdicts recorded win or lose.
NEW gate learned from crypto post-mortem: report each cell's worst-DAY
concentration (edge must not live in one slate/tournament).

## Explicitly out of scope this pass
Pre-game entries, totals/spreads families, cross-market consistency arb
(phase 2 if moneylines show anything), live bot design.

## Addendum 2026-08-03: whale-print study (pre-registered, user-directed)
Hypothesis: large aggressive prints ("leader prints") in game markets carry
information; following (or fading — two-sided) beats the contemporaneous
price. Data: full anonymized trade tape for settled MLB+WNBA games (tennis
if signal). Definitions set BEFORE outcomes: leader print = taker size >=
p95 / p99 of that family's print sizes (and burst variant: >=K same-side
prints in M minutes; block trades analyzed separately via is_block_trade).
Test: entry at the post-print quote in the taker's direction, hold to
settle; CONTROL = same-price/same-time-bucket baseline WR so the flow
signal is measured NET of price level. Gates: fees, day clustering,
IS/OOS 2/3, BH-FDR, both directions, n>=100/cell.
