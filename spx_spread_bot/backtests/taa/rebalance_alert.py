"""Monthly rebalance alert for the dual-momentum rotation.

Run this on the last trading day of each month. It:
  1. Pulls fresh daily data for the 22-ETF universe (yfinance).
  2. Computes current momentum ranking, picks Top-N (default 6) above cash.
  3. Compares to your saved holdings (state.json) and prints BUY/SELL/HOLD actions.
  4. Saves new state. Optional Telegram alert if TG_TOKEN + TG_CHAT env vars set.

Usage:
  python rebalance_alert.py                 # show current target + diff vs saved
  python rebalance_alert.py --commit         # also save the new state as current
  python rebalance_alert.py --capital 25000 --topn 6 --lookbacks 6
"""
import argparse, json, os, sys
from pathlib import Path
import numpy as np, pandas as pd

HERE = Path(__file__).parent
STATE = HERE / "rotation_state.json"
UNIVERSE = ["XLK","XLF","XLE","XLV","XLI","XLY","XLP","XLU","XLB",
            "SPY","QQQ","IWM","MDY","EFA","EEM","TLT","IEF","LQD","HYG",
            "GLD","DBC","VNQ"]
CASH = "SHY"


def fetch_monthly():
    import yfinance as yf
    data = yf.download(UNIVERSE + [CASH], period="3y", interval="1d",
                       auto_adjust=True, progress=False)
    px = data["Close"].copy()
    px.index = pd.to_datetime(px.index).tz_localize(None)
    return px.resample("ME").last()


def momentum(px, lbs):
    i = len(px) - 1
    out = {}
    for t in UNIVERSE + [CASH]:
        s = px[t]
        if i < max(lbs) or pd.isna(s.iloc[i]):
            continue
        vals = [s.iloc[i]/s.iloc[i-lb]-1 for lb in lbs
                if not pd.isna(s.iloc[i-lb]) and s.iloc[i-lb] > 0]
        if vals:
            out[t] = float(np.mean(vals))
    return out


def target(px, topn, lbs):
    m = momentum(px, lbs)
    cash_m = m.get(CASH, 0.0)
    ranked = sorted([t for t in UNIVERSE if t in m], key=lambda t: m[t], reverse=True)
    picks = []
    for t in ranked[:topn]:
        picks.append(t if m[t] > cash_m else "CASH")
    # pad with CASH if fewer than topn candidates
    picks += ["CASH"] * (topn - len(picks))
    return picks, m, cash_m


def live_price(t):
    import yfinance as yf
    try:
        return float(yf.Ticker(t).fast_info.get("lastPrice"))
    except Exception:
        return None


def send_telegram(msg):
    tok = os.environ.get("TG_TOKEN"); chat = os.environ.get("TG_CHAT")
    if not tok or not chat:
        return False
    import urllib.request, urllib.parse
    data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
    try:
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=10)
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=25000)
    ap.add_argument("--topn", type=int, default=6)
    ap.add_argument("--lookbacks", default="6", help="comma list, e.g. 6 or 3,6,12")
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()
    lbs = tuple(int(x) for x in args.lookbacks.split(","))

    px = fetch_monthly()
    picks, m, cash_m = target(px, args.topn, lbs)
    as_of = px.index[-1].date()

    prev = {}
    if STATE.exists():
        prev = json.loads(STATE.read_text()).get("holdings", {})
    prev_set = set(prev.keys())
    new_set = set(p for p in picks if p != "CASH")

    lines = [f"DUAL-MOMENTUM REBALANCE — as of {as_of}  (Top-{args.topn}, lookbacks {lbs})"]
    lines.append("=" * 64)
    per = args.capital / args.topn
    lines.append(f"TARGET ({args.topn} slots, ${per:,.0f} each):")
    for t in picks:
        if t == "CASH":
            lines.append(f"  CASH  (no asset above cash momentum for this slot)")
        else:
            lp = live_price(t)
            sh = int(per // lp) if lp else "?"
            lines.append(f"  {t:<5} mom {m[t]*100:+5.1f}%   {sh} sh @ ${lp:.2f}" if lp else f"  {t}")
    sells = prev_set - new_set
    buys = new_set - prev_set
    holds = prev_set & new_set
    lines.append("")
    lines.append(f"SELL : {sorted(sells) if sells else '(none)'}")
    lines.append(f"BUY  : {sorted(buys) if buys else '(none)'}")
    lines.append(f"HOLD : {sorted(holds) if holds else '(none)'}")
    if not sells and not buys and prev:
        lines.append(">> No change this month. Do nothing.")
    msg = "\n".join(lines)
    print(msg)

    if send_telegram(msg):
        print("\n[telegram alert sent]")

    if args.commit:
        STATE.write_text(json.dumps({
            "as_of": str(as_of), "topn": args.topn, "lookbacks": list(lbs),
            "holdings": {p: m.get(p, 0.0) for p in picks if p != "CASH"},
        }, indent=2))
        print(f"\n[state committed -> {STATE.name}]")


if __name__ == "__main__":
    main()
