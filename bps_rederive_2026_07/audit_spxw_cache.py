"""Full audit of the SPXW CBBO 1-min cache (puts + calls, every day-file).
Reports facts only; modifies nothing; fills nothing.

Checks per file:
 - readable, row count, expiry-in-symbol == file date (0DTE purity)
 - session coverage: first/last bar ET, minutes with data 09:31-16:00
 - strike coverage: min/max strike, n strikes
 - quote sanity: % crossed (bid>ask), % zero/NaN bid, % NaN ask
Calendar reconciliation:
 - expected SPXW expiry days per era vs files present (Fri-only 2013-2015;
   M/W/F 2016-2021 ex 3rd-Fridays; every weekday from 2022-04; ex holidays)
 - put/call file pairing
Output: audit_report.json + printed summary of every anomaly.
"""
import json
import numpy as np
import pandas as pd
from pathlib import Path

BASE = Path("../spx_spread_bot/backtests/narrow_width_databento_2026_05_21")
PUTS = BASE / "spxw_p_cbbo_1m"
CALLS = BASE / "spxw_c_cbbo_1m"
OUT = Path("audit_report.json")
ET = "America/New_York"

def audit_file(f):
    try:
        df = pd.read_parquet(f)
    except Exception as e:
        return {"file": f.name, "error": str(e)}
    r = {"file": f.name, "rows": int(len(df))}
    if len(df) == 0:
        r["error"] = "empty"
        return r
    fdate = f.stem.split("_")[-1]
    # expiry purity: symbol like 'SPXW  260522P07415000'
    exp = df["symbol"].str.extract(r"SPXW\s+(\d{6})")[0]
    bad_exp = (exp != fdate[2:]).sum()
    r["rows_wrong_expiry"] = int(bad_exp)
    t = df.index.tz_convert(ET)
    r["first_bar"] = str(t.min().time())
    r["last_bar"] = str(t.max().time())
    # minutes covered in RTH
    mins = pd.Series(t).dt.floor("min").nunique()
    r["distinct_minutes"] = int(mins)
    strikes = df["symbol"].str[-8:].astype(int) / 1000.0
    r["strike_lo"] = float(strikes.min()); r["strike_hi"] = float(strikes.max())
    r["n_strikes"] = int(strikes.nunique())
    bid, ask = df["bid_px_00"], df["ask_px_00"]
    both = bid.notna() & ask.notna()
    r["pct_crossed"] = float((bid[both] > ask[both]).mean() * 100) if both.any() else np.nan
    r["pct_bid_nan_or_zero"] = float(((bid.isna()) | (bid <= 0)).mean() * 100)
    r["pct_ask_nan"] = float(ask.isna().mean() * 100)
    return r

def third_friday(y, m):
    d = pd.Timestamp(y, m, 1)
    fri1 = d + pd.Timedelta(days=(4 - d.dayofweek) % 7)
    return fri1 + pd.Timedelta(days=14)

def expected_expiry_days():
    """Best-effort expected SPXW expiry calendar (structural; holidays via US bond/equity proxy)."""
    from pandas.tseries.holiday import USFederalHolidayCalendar
    cal = USFederalHolidayCalendar()
    hol = set(cal.holidays(start="2013-01-01", end="2026-06-01").date)
    days = pd.date_range("2013-04-05", "2026-05-29", freq="B")
    days = [d for d in days if d.date() not in hol]
    out = []
    for d in days:
        wd = d.dayofweek  # 0=Mon
        is_3rd_fri = d == third_friday(d.year, d.month)
        if d < pd.Timestamp("2016-01-01"):
            ok = wd == 4 and not is_3rd_fri
        elif d < pd.Timestamp("2022-04-18"):
            ok = wd in (0, 2, 4) and not is_3rd_fri
        else:
            ok = not is_3rd_fri  # daily era (3rd-fri SPXW PM exists post-2022? treat as expected-missing)
        if ok:
            out.append(d)
    return pd.DatetimeIndex(out)

def main():
    results = {}
    for side, D in (("P", PUTS), ("C", CALLS)):
        files = sorted(D.glob("*.parquet"))
        print(f"auditing {len(files)} {side} files ...")
        rows = [audit_file(f) for f in files]
        results[side] = rows
        errs = [r for r in rows if "error" in r]
        wrong_exp = [r for r in rows if r.get("rows_wrong_expiry", 0) > 0]
        thin = [r for r in rows if r.get("distinct_minutes", 999) < 300]
        crossed = [r for r in rows if (r.get("pct_crossed") or 0) > 1.0]
        late_start = [r for r in rows if r.get("first_bar", "09:31:00") > "09:35:00"]
        early_end = [r for r in rows if r.get("last_bar", "16:00:00") < "15:55:00"]
        print(f"  errors: {len(errs)}  wrong-expiry: {len(wrong_exp)}  thin(<300min): {len(thin)}")
        print(f"  crossed>1%: {len(crossed)}  late-start(>09:35): {len(late_start)}  early-end(<15:55): {len(early_end)}")
        for r in (errs + wrong_exp)[:10]:
            print("   !!", r)
        for label, lst in (("thin", thin), ("late_start", late_start), ("early_end", early_end), ("crossed", crossed)):
            for r in lst[:5]:
                print(f"   [{label}] {r['file']} mins={r.get('distinct_minutes')} first={r.get('first_bar')} last={r.get('last_bar')} crossed={r.get('pct_crossed'):.2f}%")

    # calendar reconciliation (puts as reference)
    have = pd.DatetimeIndex([pd.Timestamp(r["file"].split("_")[-1].split(".")[0]) for r in results["P"] if "error" not in r])
    exp_days = expected_expiry_days()
    missing = exp_days.difference(have)
    extra = have.difference(exp_days)
    print(f"\ncalendar: expected≈{len(exp_days)}  have={len(have)}  missing={len(missing)}  unexpected-extra={len(extra)}")
    ms = pd.Series(missing)
    if len(ms):
        print("missing by year:", ms.groupby(ms.dt.year).count().to_dict())
        print("first 15 missing:", [str(d.date()) for d in missing[:15]])
    # put/call pairing
    have_c = set(r["file"].replace("_c_", "_x_") for r in results["C"] if "error" not in r)
    have_p = set(r["file"].replace("_p_", "_x_") for r in results["P"] if "error" not in r)
    print(f"puts-without-calls: {len(have_p - have_c)}  calls-without-puts: {len(have_c - have_p)}")
    for x in sorted(have_p - have_c)[:10]:
        print("   put-only:", x)

    OUT.write_text(json.dumps(results))
    print(f"\nwrote {OUT}")

if __name__ == "__main__":
    main()
