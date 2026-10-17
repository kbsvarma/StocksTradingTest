"""Re-run of sim_bps_last2weeks.py (2026-07-03) with the LIVE BOT'S OWN SPX/VIX.

Why a variant: the Jun-1 original pulled SPY/^GSPC/^VIX 1-minute bars from yfinance,
whose 1m retention is ~30 days — May 21-29 2026 is no longer retrievable, and no
sim output files were ever saved. Instead of a proxy, this rerun parses the live
bot's system.log (spx_spread_bot/logs/webull_live/vG_spx_w50_vix25/system.log):
every in-window scan line carries the SPX (and usually VIX) the bot actually saw,
plus the bot's own direction/VIX gate verdict for that scan. Option quotes come
from the local Databento SPXW puts cache (spxw_p_cbbo_1m/, cache-only, no network).

Logic per scan-minute where the bot itself reported "gates passed, hunting spread"
(a `no qualifying spread found` line) or entered (`SIGNAL`): evaluate the live-bot
strike scan on the Databento chain — target = round(SPX*0.99/5)*5, nearest 5 strikes,
width 50, mid credit >= 1.20, pick closest to 2.00. First qualifying minute = entry.
Then SL monitor at 2.0x entry mid (close cost = short_ask - long_bid) to 15:45 ET,
else EOD settle to intrinsic (using last logged SPX of the day).

Outputs (incremental, never overwrites earlier attempts):
  sim_rerun_logspx_results.csv  — one row per day
Run: /opt/anaconda3/bin/python3 sim_rerun_logspx_2026_07_03.py
"""
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
DATA = HERE / "spxw_p_cbbo_1m"
LOG = Path(
    "/Users/varmakammili/Documents/GitHub/StocksTradingTest/spx_spread_bot/logs/"
    "webull_live/vG_spx_w50_vix25/system.log"
)
OUT_CSV = HERE / "sim_rerun_logspx_results.csv"

DAYS = ["2026-05-21", "2026-05-22", "2026-05-26", "2026-05-27", "2026-05-28", "2026-05-29"]
WIDTH = 50
OTM = 0.01
TARGET = 2.00
MIN_CRED = 1.20
SL_MULT = 2.0

RX_NOSPREAD = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}):\d{2} .*no qualifying spread found \(SPX=([\d.]+) VIX=([\d.]+)\)")
RX_DIRSKIP = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}):\d{2} .*direction filter: SKIP — SPX ([\d.]+) < open ([\d.]+)")
RX_SIGNAL = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}):\d{2} .*SIGNAL: SPXW \S+\s+(\d+)/(\d+)P\s+credit=([\d.]+)\s+SPX=([\d.]+)\s+VIX=([\d.]+)")
RX_STRIKE = re.compile(r"P0?(\d{4,5})000$")


def parse_log():
    """Per day: {minute_et: dict(spx, vix, gate)} plus signal/skip info."""
    days = {d: {"minutes": {}, "signals": [], "dir_skips": 0, "spx_last": None} for d in DAYS}
    with open(LOG, errors="replace") as fh:
        for line in fh:
            m = RX_NOSPREAD.match(line)
            if m and m.group(1) in days:
                d, hhmm, spx, vix = m.group(1), m.group(2), float(m.group(3)), float(m.group(4))
                days[d]["minutes"].setdefault(hhmm, {"spx": spx, "vix": vix, "gate": "pass"})
                days[d]["spx_last"] = spx
                continue
            m = RX_DIRSKIP.match(line)
            if m and m.group(1) in days:
                d, hhmm, spx = m.group(1), m.group(2), float(m.group(3))
                days[d]["minutes"].setdefault(hhmm, {"spx": spx, "vix": None, "gate": "dir_skip"})
                days[d]["dir_skips"] += 1
                days[d]["spx_last"] = spx
                continue
            m = RX_SIGNAL.match(line)
            if m and m.group(1) in days:
                d, hhmm = m.group(1), m.group(2)
                sig = {
                    "hhmm": hhmm, "short": int(m.group(3)), "long": int(m.group(4)),
                    "credit": float(m.group(5)), "spx": float(m.group(6)), "vix": float(m.group(7)),
                }
                days[d]["signals"].append(sig)
                # SIGNAL proves the gate passed this minute — override any earlier dir-skip
                days[d]["minutes"][hhmm] = {"spx": sig["spx"], "vix": sig["vix"], "gate": "pass"}
                days[d]["spx_last"] = sig["spx"]
    return days


def load_chain_by_minute(day: str):
    ds = day.replace("-", "")
    fp = DATA / f"spxw_p_cbbo_{ds}.parquet"
    df = pd.read_parquet(fp)
    df = df[(df["bid_px_00"] > 0) & (df["ask_px_00"] > 0)].copy()
    df["k"] = df["symbol"].map(lambda s: (RX_STRIKE.search(s.strip()) or [None]) and (
        int(RX_STRIKE.search(s.strip()).group(1)) if RX_STRIKE.search(s.strip()) else None))
    df = df.dropna(subset=["k"])
    df["k"] = df["k"].astype(int)
    idx = pd.to_datetime(df.index, utc=True).tz_convert("America/New_York")
    df["et_min"] = idx.strftime("%H:%M")
    out = {}
    for hhmm, g in df.groupby("et_min"):
        out[hhmm] = g.drop_duplicates("k").set_index("k")[["bid_px_00", "ask_px_00"]]
    return out


def scan_entry(chain, spx):
    # Mirror the live IBKR scan (webull_bot/ibkr_market_data.py:281-285):
    # window = target_short + 5*i for i in [-5, 5], i.e. target +/- 25 pts
    target = round(spx * (1 - OTM) / 5) * 5
    cands = [target + 5 * i for i in range(-5, 6) if (target + 5 * i) in chain.index]
    best, best_diff = None, float("inf")
    for sk in cands:
        lk = sk - WIDTH
        if lk not in chain.index:
            continue
        sb, sa = float(chain.loc[sk, "bid_px_00"]), float(chain.loc[sk, "ask_px_00"])
        lb, la = float(chain.loc[lk, "bid_px_00"]), float(chain.loc[lk, "ask_px_00"])
        cmid = (sb + sa) / 2 - (lb + la) / 2
        if cmid < MIN_CRED:
            continue
        diff = abs(cmid - TARGET)
        if diff < best_diff:
            best_diff = diff
            best = {"short": sk, "long": lk, "cred_mid": round(cmid, 2), "cred_real": round(sb - la, 2)}
    return best


def run_day(day, info, rows):
    chain_by_min = load_chain_by_minute(day)
    gate_minutes = sorted(hhmm for hhmm, v in info["minutes"].items() if v["gate"] == "pass" and "10:30" <= hhmm <= "14:30")
    n_pass = len(gate_minutes)
    entered = None
    best_seen = 0.0
    for hhmm in gate_minutes:
        ch = chain_by_min.get(hhmm)
        if ch is None:
            continue
        spx = info["minutes"][hhmm]["spx"]
        # track best available mid for diagnostics (same +/-25pt window)
        target = round(spx * (1 - OTM) / 5) * 5
        for sk in [target + 5 * i for i in range(-5, 6) if (target + 5 * i) in ch.index]:
            lk = sk - WIDTH
            if lk in ch.index:
                cm = (float(ch.loc[sk, "bid_px_00"]) + float(ch.loc[sk, "ask_px_00"])) / 2 - (
                    float(ch.loc[lk, "bid_px_00"]) + float(ch.loc[lk, "ask_px_00"])) / 2
                best_seen = max(best_seen, cm)
        cand = scan_entry(ch, spx)
        if cand:
            entered = {"hhmm": hhmm, "spx": spx, **cand}
            break
    if entered is None:
        reason = (f"no entry — {n_pass} gate-pass min, best Databento mid ${best_seen:.2f} < {MIN_CRED}"
                  if n_pass else f"direction/VIX gate blocked all window ({info['dir_skips']} dir-skips)")
        rows.append({"date": day, "sim_status": reason, "sim_entry": "", "sim_credit_mid": None,
                     "sim_exit_reason": "", "sim_pnl": 0.0, "gate_pass_min": n_pass,
                     "dir_skips": info["dir_skips"], "best_mid_seen": round(best_seen, 2)})
        return
    # SL monitor to 15:45 ET
    trigger = SL_MULT * entered["cred_mid"]
    exit_cost, exit_reason = None, None
    for hhmm in sorted(chain_by_min):
        if hhmm <= entered["hhmm"] or hhmm > "15:45":
            continue
        ch = chain_by_min[hhmm]
        if entered["short"] not in ch.index or entered["long"] not in ch.index:
            continue
        cc = float(ch.loc[entered["short"], "ask_px_00"]) - float(ch.loc[entered["long"], "bid_px_00"])
        if cc >= trigger:
            exit_cost, exit_reason = cc, f"STOP_LOSS@{hhmm}"
            break
    if exit_cost is None:
        spx_close = info["spx_last"]
        if spx_close >= entered["short"]:
            exit_cost = 0.0
        elif spx_close >= entered["long"]:
            exit_cost = entered["short"] - spx_close
        else:
            exit_cost = WIDTH
        exit_reason = "EOD/EXPIRY"
    pnl = round((entered["cred_mid"] - exit_cost) * 100, 0)
    rows.append({"date": day, "sim_status": "TRADED",
                 "sim_entry": f"{entered['hhmm']} {entered['short']}/{entered['long']}",
                 "sim_credit_mid": entered["cred_mid"], "sim_exit_reason": exit_reason,
                 "sim_pnl": pnl, "gate_pass_min": n_pass, "dir_skips": info["dir_skips"],
                 "best_mid_seen": entered["cred_mid"]})


def main():
    days = parse_log()
    rows = []
    for day in DAYS:
        run_day(day, days[day], rows)
        pd.DataFrame(rows).to_csv(OUT_CSV, index=False)  # incremental
        print(rows[-1])
    print("\nlive signals seen in log:")
    for d in DAYS:
        for s in days[d]["signals"]:
            print(" ", d, s)


if __name__ == "__main__":
    main()
