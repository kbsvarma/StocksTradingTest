# VERDICT — score-conditioned entry: KILLED (2026-08-05)

Pre-registration: PREREG_LIVESCORE.md (written before any data).
Cost: $10 (one month Basic, cancelled same day). Data retained locally.

## Headline

**Kalshi prices WTA in-match score states correctly to within ~1 cent.**
Nine states measured against realised outcomes on 148 joined matches:

| state | true win rate | Kalshi ask | gap | net@ask |
|---|---|---|---|---|
| set + on serve (CONTROL) | 78.9% | 79.4c | -0.5 | -1.55c |
| set + break | 89.1% | 89.2c | -0.2 | -0.76c |
| break in decider | 88.9% | 90.3c | -1.4 | -1.85c |
| serving for match | 92.5% | 93.5c | -1.1 | -1.41c |
| match point | 97.7% | 96.7c | +1.1 | +0.92c |
| set + double break | 98.4% | 97.1c | +1.3 | +1.13c |
| match point on return | 96.4% | 95.8c | +0.6 | +0.40c |
| match point in decider | 96.0% | 93.2c | +2.8 | +2.53c |

EVERY positive cell has a NEGATIVE Wilson lower bound -> none established.
Best (match point, +0.92c net) is WORSE than the existing 97-99 H10 lane
(+1.20c) which needs no score feed at all.

## The specific kill for Hypothesis B (user's idea)

Premise: a set-and-double-break lead sits at 90-95c while converting
~98%. FALSE. It trades at **97.1c** — the commanding position and the
expensive price arrive together. There is no cheap window.

## Data-quality finding (worth more than the verdict)

livetennisapi "reconstructed"/"mixed" tapes are UNUSABLE for point-level
states. Same state, three provenances:

| state | observed | mixed | reconstructed |
|---|---|---|---|
| match point | **97.9%** (n=192) | 93.1% | 84.1% |
| set + double break | **98.8%** (n=83) | 95.5% | 85.7% |

Modelled point sequences invent match points that never occurred, biasing
every state DOWNWARD. Pooling all 865 tapes gives match point = 93.5%,
which is wrong by 4.4pts. Only `point_source == "observed"` is usable
(231 of 1008 tapes here; coverage grows forward from ~2026-07-28).

**Parser validated**: observed match-point rate 97.9% reproduces Tennis
Abstract's independently published "a bit more than 97%" across ~16,000
WTA matches. Different data, different code, same answer.

## Why the live lane still works (mechanism corrected)

The 97-99c H10 lane is NOT exploiting a score-information gap — the
market has the score. It exploits **dog-holders declining to sell the
last 1-2 cents of a decided match**. Microstructure, not information.
This is why every score refinement adds nothing on top of it, and why
earlier price-path refinements (velocity, bands, stops, brackets) also
failed: there is no information edge to find, only a liquidity quirk at
the extreme.

## Status of the artefacts

- 1,008 tapes + index + Kalshi name map: retained locally, permanent.
- Subscription: cancelled. Collector continues on the FREE tier.
- Forward observed data (US Open, ~250 WTA matches) will re-test all
  nine states at zero cost. If the calibration finding holds there, this
  direction is closed for good.
