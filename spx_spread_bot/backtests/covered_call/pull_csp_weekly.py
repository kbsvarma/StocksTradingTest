"""Pull weekly PUT + CALL quotes ~5% OTM each side for CSP-vs-CC comparison.

For each Monday: sell put ~5% below spot, sell call ~5% above spot.
Pull Monday 9:35 entry quotes for strikes spanning -7% to +7%.
Output: data/{ticker}_csp_weekly.parquet (puts AND calls, entry only;
        Friday settlement done via yfinance close in backtest).
"""
import os, sys, argparse, time
from datetime import datetime, timedelta, date
from pathlib import Path
import databento as db
import pandas as pd
import yfinance as yf

OUT = Path(__file__).parent / "data"
OUT.mkdir(exist_ok=True, parents=True)

GRID = {"WFC":1.0,"OXY":1.0,"GILD":1.0,"MO":1.0,"T":0.5,
        "BAC":1.0,"VZ":1.0,"XLF":1.0}

def sym(ticker, expiry, strike, right):
    return f"{ticker.ljust(6)}{expiry.strftime('%y%m%d')}{right}{int(round(strike*1000)):08d}"

def find_weeks(start, end):
    pairs=[]; d=start
    while d.weekday()!=0: d+=timedelta(days=1)
    while d<=end:
        fri=d+timedelta(days=4)
        if fri<=end: pairs.append((d,fri))
        d+=timedelta(days=7)
    return pairs

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--ticker",required=True)
    ap.add_argument("--start",default="2024-01-01")
    ap.add_argument("--end",default="2026-05-22")
    ap.add_argument("--confirm",action="store_true")
    ap.add_argument("--dry-cost",action="store_true")
    args=ap.parse_args()
    if not (args.confirm or args.dry_cost):
        print("need --confirm or --dry-cost"); sys.exit(1)

    ticker=args.ticker.upper(); grid=GRID.get(ticker,1.0)
    client=db.Historical(os.environ["DATABENTO_API_KEY"])
    hist=yf.Ticker(ticker).history(start=args.start,end=args.end,auto_adjust=False)
    hist.index=pd.to_datetime(hist.index).tz_localize(None)
    print(f"[{ticker}] {len(hist)} days, grid=${grid}")

    weeks=find_weeks(date.fromisoformat(args.start),date.fromisoformat(args.end))
    plan=[]
    for mon,fri in weeks:
        idx=hist.index.get_indexer([pd.Timestamp(mon)],method="bfill")[0]
        if idx<0 or idx>=len(hist): continue
        amon=hist.index[idx].date()
        if amon>mon+timedelta(days=2): continue
        spot=float(hist.iloc[idx]["Open"])
        # strikes from -7% to +7%
        lo=round(spot*0.93/grid)*grid; hi=round(spot*1.07/grid)*grid
        n=int((hi-lo)/grid)+1
        strikes=[round(lo+i*grid,2) for i in range(n) if lo+i*grid>0]
        syms=[sym(ticker,fri,k,"P") for k in strikes]+[sym(ticker,fri,k,"C") for k in strikes]
        plan.append((amon,fri,spot,syms))
    print(f"[{ticker}] {len(plan)} weeks")

    total=0.0; pulled=0; failed=0; rows=[]
    for i,(mon,fri,spot,syms) in enumerate(plan):
        s_iso=mon.strftime("%Y-%m-%dT13:35:00"); e_iso=mon.strftime("%Y-%m-%dT13:36:00")
        try:
            c=client.metadata.get_cost(dataset="OPRA.PILLAR",schema="cbbo-1m",symbols=syms,
                                       stype_in="raw_symbol",start=s_iso,end=e_iso)
        except Exception: failed+=1; continue
        total+=c
        if args.dry_cost:
            if i%25==0: print(f"  [{i+1}/{len(plan)}] {mon} ${total:.4f}",flush=True)
            continue
        try:
            data=client.timeseries.get_range(dataset="OPRA.PILLAR",schema="cbbo-1m",symbols=syms,
                                             stype_in="raw_symbol",start=s_iso,end=e_iso)
            df=data.to_df()
            if df.empty: failed+=1; continue
            df["monday"]=mon; df["friday"]=fri; df["spot"]=spot
            rows.append(df.reset_index()); pulled+=1
        except Exception: failed+=1
        if i%25==0: print(f"  [{i+1}/{len(plan)}] {mon} spot=${spot:.2f} ${total:.4f}",flush=True)

    if args.dry_cost:
        print(f"\n[{ticker} dry-cost] PROJECTED ${total:.3f}"); return
    if rows:
        pd.concat(rows,ignore_index=True).to_parquet(OUT/f"{ticker.lower()}_csp_weekly.parquet")
        print(f"\n[{ticker} done] {pulled} weeks, {failed} failed, ${total:.3f}")

if __name__=="__main__": main()
