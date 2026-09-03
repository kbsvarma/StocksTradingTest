"""Daily ranked picks — deterministic levels, no model tokens.

The existing view pipeline is a veto machine: synthesis proposes, an
independent red-team kills, and most days nothing survives (4 published views
in two months). That is the right bar for high-conviction calls, but it never
produces enough resolved outcomes to calibrate confidence.

This module adds a second, lower-bar lane beside it. Every trading day it
ranks the candidate slate, attaches ATR-derived entry/stop/target levels and a
confidence score, and writes the top N. Volume is the point: ~10 picks/day
resolves into statistically usable calibration in months, not years.

Honesty rules baked in:
  * Levels are DETERMINISTIC (ATR + last close). No model writes a number here.
  * `score` is signal strength in [0,1] — NOT a probability.
  * `confidence_pct` appears only once pick_tracker has fit an empirical
    mapping; until then the field is null and `confidence_basis` says
    "uncalibrated". Never dress a raw score as a probability.
  * Picks are research_idea class. They do not touch the actionable_idea gate.

CLI: python -m advisor.research.picks [--top 10]
Writes advisor/data/research/picks_latest.json (+ dated copy) and appends
open picks to advisor/data/picks_ledger.jsonl.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from advisor.research.datastore import RESEARCH_DIR

ET = ZoneInfo("America/New_York")
OUT = RESEARCH_DIR / "picks_latest.json"
LEDGER = RESEARCH_DIR.parent / "picks_ledger.jsonl"
CALIBRATION = RESEARCH_DIR / "pick_calibration.json"

HORIZON_TD = 21          # trading days; matches the IC/validation framework
ATR_WINDOW = 20
STOP_ATR = 1.5           # stop distance in ATR units
TARGET_ATR = 3.0         # target distance -> 2:1 reward:risk by construction


def _atr(high, low, close, window: int = ATR_WINDOW):
    """True-range average per ticker over the wide panel.

    NOTE: these are wide frames (date x ticker), so the true range must be an
    ELEMENT-WISE max of the three candidate ranges. pd.concat(axis=1).max(axis=1)
    would collapse every ticker into one series per date.
    """
    prev_close = close.shift(1)
    hl = (high - low).abs()
    hc = (high - prev_close).abs()
    lc = (low - prev_close).abs()
    tr = hl.where(hl >= hc, hc)
    tr = tr.where(tr >= lc, lc)
    return tr.rolling(window, min_periods=max(5, window // 2)).mean()


def _calibration() -> dict:
    try:
        return json.loads(CALIBRATION.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def apply_calibration(score: float, cal: dict | None = None) -> tuple:
    """(confidence_pct, basis). None until an empirical mapping exists."""
    cal = _calibration() if cal is None else cal
    buckets = cal.get("buckets") or []
    if not cal.get("usable") or not buckets:
        return None, "uncalibrated"
    for b in buckets:
        if b["lo"] <= score <= b["hi"]:
            return round(b["hit_rate"] * 100, 1), (
                f"empirical n={b['n']} (calibration {cal.get('calibration_id','?')[:8]})")
    return None, "uncalibrated"


def build(top_n: int = 10, as_of: str | None = None,
          source: str = "live") -> dict:
    """Rank the slate and attach levels.

    `as_of` (YYYY-MM-DD) replays an ARCHIVED slate for backfill: the price
    panel is truncated to that date so no level can see the future. Backfilled
    rows are labeled `source="backfill"` end-to-end and are reported separately
    from live picks — a replayed track record is not a live one.
    """
    import pandas as pd
    from advisor.research.datastore import load_panel

    slate_p = (RESEARCH_DIR / f"candidates_{as_of}.json" if as_of
               else RESEARCH_DIR / "candidates_latest.json")
    try:
        slate = json.loads(slate_p.read_text())
    except (OSError, json.JSONDecodeError):
        return {"error": f"{slate_p.name} missing"}

    close, high, low = (load_panel("close"), load_panel("high"), load_panel("low"))
    if as_of:
        cut = pd.Timestamp(as_of)
        close, high, low = (close[close.index <= cut], high[high.index <= cut],
                            low[low.index <= cut])
        if len(close) < ATR_WINDOW + 5:
            return {"error": f"insufficient panel history at {as_of}"}
    atr = _atr(high, low, close)
    last_close, last_atr = close.iloc[-1], atr.iloc[-1]
    as_of_bar = str(close.index[-1].date())

    entries = slate.get("slate", [])
    if not entries:
        return {"error": "empty slate"}

    # raw signal strength: |composite| percentile within the slate, lifted by
    # independent-generator agreement. Explicitly NOT a probability.
    scored = []
    comps = [abs(e.get("detail", {}).get("score", 0) or 0) for e in entries]
    max_comp = max(comps) or 1.0
    for e in entries:
        t = e["ticker"]
        if t not in close.columns:
            continue
        px, a = last_close.get(t), last_atr.get(t)
        if not (isinstance(px, (int, float)) and isinstance(a, (int, float))) \
                or not (px > 0 and a > 0):
            continue
        buckets = e.get("buckets", [])
        short = any(b in ("tactical_short", "repeat_kill") for b in buckets)
        direction = "short" if short else "long"
        comp = abs(e.get("detail", {}).get("score", 0) or 0)
        confluence = min(len(buckets), 4) / 4.0
        score = round(min(1.0, 0.65 * (comp / max_comp) + 0.35 * confluence), 4)

        sign = -1 if short else 1
        stop = px - sign * STOP_ATR * a
        target = px + sign * TARGET_ATR * a
        conf_pct, basis = apply_calibration(score)
        scored.append({
            "ticker": t, "direction": direction,
            "score": score,
            "confidence_pct": conf_pct, "confidence_basis": basis,
            "ref_px": round(float(px), 2),
            "entry_low": round(float(px - 0.25 * a), 2),
            "entry_high": round(float(px + 0.25 * a), 2),
            "stop": round(float(stop), 2),
            "target": round(float(target), 2),
            "atr20": round(float(a), 2),
            "rr": round(TARGET_ATR / STOP_ATR, 2),
            "horizon_td": HORIZON_TD,
            "generators": buckets,
            "sector": e.get("detail", {}).get("sector"),
            "next_earnings": e.get("detail", {}).get("next_earnings"),
        })

    scored.sort(key=lambda x: -x["score"])
    picks = scored[:top_n]
    now = datetime.now(ET)
    result = {
        "as_of": now.isoformat(),
        "source": source,
        "price_bar": as_of_bar,
        "n_slate": len(entries), "n_picks": len(picks),
        "horizon_trading_days": HORIZON_TD,
        "levels_method": (f"deterministic: entry ±0.25*ATR20, stop {STOP_ATR}*ATR20, "
                          f"target {TARGET_ATR}*ATR20 (R:R {TARGET_ATR/STOP_ATR:.1f}); "
                          f"no model-authored numbers"),
        "score_method": ("0.65*|composite| percentile + 0.35*generator confluence; "
                         "signal strength in [0,1], NOT a probability"),
        "calibration": _calibration().get("calibration_id"),
        "class": "research_idea",
        "disclaimer": ("Research ideas, not personalized advice. Levels are "
                       "mechanical; confidence is empirical only when shown."),
        "picks": picks,
    }
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    day = as_of or now.date().isoformat()
    if source == "live":
        tmp = OUT.with_suffix(f".json.tmp.{os.getpid()}")
        tmp.write_text(json.dumps(result, indent=2) + "\n")
        os.replace(tmp, OUT)
    (RESEARCH_DIR / f"picks_{day}.json").write_text(
        json.dumps(result, indent=2) + "\n")
    _append_ledger(picks, now, as_of_bar, day=day, source=source)
    return result


def _append_ledger(picks: list, now: datetime, price_bar: str,
                   day: str | None = None, source: str = "live") -> int:
    """One open row per pick per day — the record that gets resolved later."""
    day = day or now.date().isoformat()
    existing = set()
    if LEDGER.exists():
        for line in LEDGER.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("type") == "pick":
                existing.add((r.get("date"), r.get("ticker")))
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with LEDGER.open("a", encoding="utf-8") as f:
        for p in picks:
            if (day, p["ticker"]) in existing:
                continue        # idempotent: a same-day rerun must not double-log
            f.write(json.dumps({
                "type": "pick", "id": f"P-{day}-{p['ticker']}",
                "date": day, "issued_ts": now.isoformat(), "price_bar": price_bar,
                "source": source,
                "ticker": p["ticker"], "direction": p["direction"],
                "score": p["score"], "confidence_pct": p["confidence_pct"],
                "ref_px": p["ref_px"], "stop": p["stop"], "target": p["target"],
                "horizon_td": p["horizon_td"], "generators": p["generators"],
                "status": "open",
            }) + "\n")
            n += 1
    return n


def backfill(top_n: int = 10) -> dict:
    """Replay every archived slate so calibration has data now, not in months."""
    dates = sorted(p.stem.split("_", 1)[1]
                   for p in RESEARCH_DIR.glob("candidates_2*.json"))
    done, failed = 0, []
    for d in dates:
        r = build(top_n, as_of=d, source="backfill")
        if "error" in r:
            failed.append((d, r["error"]))
        else:
            done += 1
    print(f"backfill: {done} slates replayed, {len(failed)} skipped")
    for d, e in failed[:5]:
        print(f"  skip {d}: {e}")
    return {"replayed": done, "skipped": len(failed)}


def main() -> int:
    top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 10
    if "--backfill" in sys.argv:
        backfill(top)
        return 0
    res = build(top)
    if "error" in res:
        print("picks:", res["error"])
        return 1
    print(f"picks: {res['n_picks']} of {res['n_slate']} slate names "
          f"(bar {res['price_bar']}, horizon {res['horizon_trading_days']}td)")
    for p in res["picks"]:
        conf = (f"{p['confidence_pct']}%" if p["confidence_pct"] is not None
                else f"score {p['score']:.2f} (uncal)")
        print(f"  {p['ticker']:<6} {p['direction']:<5} {conf:<22} "
              f"entry {p['entry_low']}-{p['entry_high']}  stop {p['stop']}  "
              f"tgt {p['target']}  [{','.join(p['generators'][:2])}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
