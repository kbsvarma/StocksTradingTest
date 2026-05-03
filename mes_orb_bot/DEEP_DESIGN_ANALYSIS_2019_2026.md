# MES ORB Bot: Deep Design Analysis (2019-2026)

Date: 2026-04-26
Scope: `mes_orb_bot` strategy and system design review using `data/mes_1min.csv` (2019-05-05 to 2026-04-24)

## 1) What The Bot Is Trying To Achieve

Primary objective:
- Trade a simple, mechanical intraday edge on MES using Opening Range Breakout (ORB)
- Keep decision logic deterministic and operational risk low
- Prioritize survival (bounded downside, single-trade/day behavior) over high turnover

Design philosophy in current code:
- Event-driven state machine (`bot.py`) for production safety
- Strategy logic isolated (`strategy.py`) for testability
- Broker concerns isolated (`broker.py`) for operational containment
- Replay harness (`replay_test.py`) to validate state transitions without IBKR

## 2) Baseline Strategy Modeled For Analysis

Mode matched to current config intent:
- Range: 09:30-10:00 (lock at 10:00)
- Entry cutoff: 15:30
- Hard close: 15:30
- Target: `3.0 x range_width`
- Stop: opposite side of opening range
- Width filter: 5-30 points
- Trend filter: only trade in direction of prior day close vs prior-prior close
- Cost model in this review: `$5.50` round-trip (`$4.50` commission + `$1.00` slippage)

## 3) Core Quant Findings

### 3.1 Baseline quality

Baseline (trend filter ON, both directions, hard close 15:30):
- FULL 2019-2026: `1000` trades, avg `-$0.36/trade`, net `-$362.50`, Sharpe `-0.04`
- TRAIN 2019-2022: avg `-$2.77/trade`, net `-$1,450.75`, Sharpe `-0.26`
- TEST 2023-2026: avg `+$2.29/trade`, net `+$1,088.25`, Sharpe `0.19`

Interpretation:
- Current design has recent edge but weak long-horizon stability.
- Edge is regime-dependent; baseline is not robust enough for confidence at scale.

### 3.2 Directional asymmetry is the biggest issue/opportunity

Baseline side split (full period):
- LONG: `586` trades, avg `+$2.27/trade`, net `+$1,330.75`
- SHORT: `414` trades, avg `-$4.09/trade`, net `-$1,693.25`

Short-side exhaustive search result:
- No short-only configuration achieved positive train + positive test robustness under tested grid.

Interpretation:
- Short logic is structurally weak across this sample.
- Keeping symmetric long/short design is hurting expectancy.

### 3.3 Ablation highlights (OOS 2023-2026)

Selected OOS comparisons:
- Baseline: avg `+$2.29`, Sharpe `0.19`, net `+$1,088`
- No trend filter (both sides): avg `+$4.39`, Sharpe `0.41`, net `+$2,952`
- Long-only (trend-filtered): avg `+$9.22`, Sharpe `0.65`, net `+$2,573`
- Long-only + max range 25 + hard close 15:15: avg `+$11.53`, Sharpe `0.81`, net `+$2,973`

Bootstrap confidence (OOS mean/trade 95% CI):
- Baseline: `[-6.54, +11.23]` (includes 0)
- Long-only trend: `[-1.06, +19.81]` (near 0)
- Long-only + maxw25 + hc15:15: `[+1.20, +21.87]` (excludes 0)

Interpretation:
- Moving to long-only is the dominant strategic improvement.
- Tightening max width and earlier hard close improves statistical confidence.

### 3.4 Weekday structure is real enough to test live

Long-only trend weekday subset with best stability:
- Trade only: Monday/Tuesday/Wednesday/Friday (skip Thursday)
- TRAIN avg: `+$0.61/trade`
- TEST avg: `+$9.30/trade`
- Year consistency: `6/8` positive years

Interpretation:
- Thursday behaves differently and hurts baseline behavior.
- This filter may be useful, but should be deployed as a feature-flag experiment, not hardcoded first.

## 4) Why The Current Design Underperforms In Some Regimes

1. Symmetric direction assumption is wrong for this dataset.
- Long and short ORB behave differently due to index drift and intraday structure.

2. Exit logic is overly dependent on hard close and stops.
- Baseline exits are mostly STOP + HARD_CLOSE; TARGET hit-rate is low (~7%).
- That means timing and holding policy dominate results more than breakout target logic.

3. Static parameters across all regimes.
- Same width/close logic applied across vastly different volatility years.

4. Research/live parity gap risk.
- Existing research scripts used hard close 15:45 in places historically, while runtime moved to 15:30.
- This increases deployment risk if not explicitly harmonized per experiment.

## 5) Design Improvements Recommended (Ranked)

### Priority A (high impact, low complexity)

1. Add directional mode control and set default to long-only.
- New config: `DIRECTION_MODE = BOTH|LONG_ONLY|SHORT_ONLY`
- Recommended default for next paper phase: `LONG_ONLY`

2. Add optional tighter volatility gating.
- New config: `MAX_OPENING_RANGE_POINTS = 25` (test variant)
- Keep current `30` as control.

3. Add hard-close schedule variants behind config.
- New config: `HARD_CLOSE_TIME` alternatives (`15:15`, `15:30`)
- Test `15:15` as candidate variant.

### Priority B (stability / anti-overfit)

4. Add weekday filter feature flag.
- New config: `ALLOWED_WEEKDAYS = [Mon,Tue,Wed,Fri]` optional
- Start as experiment only.

5. Add regime-health guard.
- Rolling 60-trade average net/trade guard:
  - If rolling expectancy < 0 for N windows, auto-halt strategy for session/day.

### Priority C (architecture quality)

6. Single-source strategy engine for both backtest and live.
- Eliminate divergence risk by reusing identical decision/exit primitives.

7. Extend replay harness to cover new knobs.
- Add deterministic scenarios for:
  - long-only gating
  - weekday gating
  - hard-close variant behavior

## 6) Suggested Next Implementation Plan

1. Implement feature flags (no behavior change by default):
- `DIRECTION_MODE`
- `ALLOWED_WEEKDAYS`
- optional `HARD_CLOSE_TIME` variants through config only

2. Add `strategy_lab_v3` / `backtest_v3` using exact runtime rules:
- hard close 15:30 baseline parity
- same stop/target and fill assumptions as bot

3. Run controlled A/B paper deployment (minimum 6-8 weeks):
- Control: current baseline
- Variant A: long-only
- Variant B: long-only + maxw25 + hard close 15:15
- Variant C: long-only + skip Thu

4. Promotion criteria:
- Positive net expectancy after costs
- Lower max drawdown than control
- No operational regressions in logs/events/recovery

## 7) Bottom Line

The bot has a viable core concept, but current symmetric direction design is leaving money on the table and reducing robustness.

Most defensible next move:
- Shift to a configurable long-only ORB architecture,
- test tighter range and earlier hard-close variants,
- keep weekday/regime filters as controlled experiments.

This preserves the bot's reliability-first design while materially improving expected edge and confidence.
