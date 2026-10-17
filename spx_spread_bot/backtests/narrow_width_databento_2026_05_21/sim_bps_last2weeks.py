"""Simulate the LIVE bot's BPS design over the last 2 weeks (May 21-29 2026).

Live config (from webull_bot/config.yaml + startup banner):
  width=50, OTM=1.0%, entry 10:30-14:30 ET, min_credit=1.20, target=2.00,
  VIX gate 12-25, direction filter SPX>=open, SL=2.0x, no PT, EOD close 15:45, 1 contract.

Entry credit: realistic = short_bid - long_ask (sell short@bid, buy long@ask).
SL monitor:  close_cost = short_ask - long_bid (debit to close); fires at >= 2.0x entry credit.
EOD/expiry:  if no SL, exit at 15:45 mark (short_ask - long_bid), or settle to SPX close intrinsic.
"""
import re, sys
from datetime import datetime
from pathlib import Path
import pandas as pd
import yfinance as yf

DATA = Path(__file__).parent / "spxw_p_cbbo_1m"
# "after 22" subset by default; pass --all for all 6 days
_ALL = ["20260521","20260522","20260526","20260527","20260528","20260529"]
DAYS = _ALL if "--all" in sys.argv else ["20260526","20260527","20260528","20260529"]
WIDTH=50; OTM=0.01; TARGET=2.00; SL_MULT=2.0
VIX_LO=12.0; VIX_HI=25.0
# min_credit set per-run below
MIN_CRED=1.20

RX = re.compile(r"P0?(\d+)000$")
def strike_of(s):
    m=RX.search(s.strip()); return int(m.group(1)) if m else None

# SPX proxy via SPY 1-min (×10), calibrated per-day to real ^GSPC/SPY basis
def load_spx_vix():
    chunks=[]
    for s,e in [("2026-05-20","2026-05-27"),("2026-05-27","2026-05-30")]:
        c=yf.Ticker("SPY").history(start=s,end=e,interval="1m",auto_adjust=False)
        if not c.empty: chunks.append(c)
    spy=pd.concat(chunks)
    spy=spy[~spy.index.duplicated(keep="first")]
    spy.index=pd.to_datetime(spy.index)
    # per-day calibration ratio = real SPX close / (SPY close * 10)
    gspc_d=yf.Ticker("^GSPC").history(start="2026-05-20",end="2026-05-30",auto_adjust=False)
    gspc_d.index=pd.to_datetime(gspc_d.index).tz_localize(None)
    spy_d=yf.Ticker("SPY").history(start="2026-05-20",end="2026-05-30",auto_adjust=False)
    spy_d.index=pd.to_datetime(spy_d.index).tz_localize(None)
    ratio={}
    for d in gspc_d.index:
        try: ratio[d.date()]=float(gspc_d.loc[d,"Close"])/(float(spy_d.loc[d,"Close"])*10.0)
        except KeyError: ratio[d.date()]=1.0
    spx=spy.copy()
    for col in ["Open","High","Low","Close"]:
        spx[col]=[spy[col].iloc[i]*10.0*ratio.get(spy.index[i].date(),1.0) for i in range(len(spy))]
    vix = yf.Ticker("^VIX").history(start="2026-05-20", end="2026-05-30", auto_adjust=False)
    vix.index = pd.to_datetime(vix.index).tz_localize(None)
    return spx, vix

def minute_chain(df, hhmm_utc):
    snap = df[df.index.strftime("%H:%M")==hhmm_utc]
    if snap.empty: return None
    snap = snap.copy(); snap["k"]=snap["symbol"].map(strike_of)
    snap = snap.dropna(subset=["k"]).drop_duplicates("k").set_index("k")
    return snap[["bid_px_00","ask_px_00"]]

_SPXVIX=None
def run():
    global _SPXVIX
    if _SPXVIX is None: _SPXVIX=load_spx_vix()
    spx_1m, vix_d = _SPXVIX
    rows=[]
    for ds in DAYS:
        fp = DATA/f"spxw_p_cbbo_{ds}.parquet"
        if not fp.exists(): continue
        df = pd.read_parquet(fp)
        df = df[(df["bid_px_00"]>0)&(df["ask_px_00"]>0)]
        d = datetime.strptime(ds,"%Y%m%d").date()
        # SPX open + intraday (ET) — yfinance index is tz-aware ET
        day_spx = spx_1m[spx_1m.index.strftime("%Y-%m-%d")==str(d)]
        if day_spx.empty:
            rows.append({"date":d,"status":"no SPX 1m data","pnl":None}); continue
        spx_open = float(day_spx.iloc[0]["Open"])
        # VIX (daily) for gate
        try: vix = float(vix_d.loc[pd.Timestamp(d),"Open"])
        except KeyError: vix=float("nan")
        # VIX gate
        if not (VIX_LO <= vix <= VIX_HI):
            rows.append({"date":d,"spx_open":spx_open,"vix":vix,"status":f"VIX gate fail ({vix:.1f})","pnl":0.0}); continue
        # Scan entry from 10:30 ET (=14:30 UTC) to 14:30 ET (=18:30 UTC) every minute
        entered=None
        for hh in range(14,19):
            for mm in range(0,60):
                if hh==14 and mm<30: continue   # window opens 10:30 ET = 14:30 UTC
                utc=f"{hh:02d}:{mm:02d}"
                et_hh = hh-4
                # direction filter: SPX at this ET minute >= open
                et_ts = day_spx[day_spx.index.strftime("%H:%M")==f"{et_hh:02d}:{mm:02d}"]
                if et_ts.empty: continue
                spx_now=float(et_ts.iloc[0]["Close"])
                if spx_now < spx_open: continue  # direction filter SKIP
                chain=minute_chain(df,utc)
                if chain is None: continue
                # Bot logic: target 1% OTM, scan nearest 5 strikes, pick mid closest to $2.00 with mid>=min
                target_short=round(spx_now*(1-OTM)/5)*5
                avail=sorted(chain.index)
                cands=sorted(avail,key=lambda s:abs(s-target_short))[:5]
                best=None; best_diff=float("inf")
                for sk in cands:
                    lk=sk-WIDTH
                    if lk not in chain.index: continue
                    sb=float(chain.loc[sk,"bid_px_00"]); sa=float(chain.loc[sk,"ask_px_00"])
                    lb=float(chain.loc[lk,"bid_px_00"]); la=float(chain.loc[lk,"ask_px_00"])
                    cmid=((sb+sa)/2)-((lb+la)/2)
                    if cmid < MIN_CRED: continue
                    diff=abs(cmid-TARGET)
                    if diff<best_diff:
                        best_diff=diff
                        best={"short":sk,"long":lk,"cred":sb-la,"cred_mid":cmid}
                if best is None: continue
                entered={"utc":utc,"et":f"{et_hh:02d}:{mm:02d}","short":best["short"],"long":best["long"],
                         "cred":best["cred"],"cred_mid":best["cred_mid"],"spx_now":spx_now}
                break
            if entered: break
        if entered is None:
            # diagnostic: how many in-window minutes passed direction filter, and best credit seen
            dir_pass=0; best_seen=0.0
            for hh in range(14,19):
                for mm in range(0,60):
                    if hh==14 and mm<30: continue
                    et_hh=hh-4
                    et_ts=day_spx[day_spx.index.strftime("%H:%M")==f"{et_hh:02d}:{mm:02d}"]
                    if et_ts.empty: continue
                    if float(et_ts.iloc[0]["Close"])<spx_open: continue
                    dir_pass+=1
                    ch=minute_chain(df,f"{hh:02d}:{mm:02d}")
                    if ch is None: continue
                    spxn=float(et_ts.iloc[0]["Close"]); tk=round(spxn*(1-OTM)/5)*5
                    for sk in sorted(ch.index,key=lambda s:abs(s-tk))[:5]:
                        lk=sk-WIDTH
                        if lk not in ch.index: continue
                        cm=((float(ch.loc[sk,"bid_px_00"])+float(ch.loc[sk,"ask_px_00"]))/2)-((float(ch.loc[lk,"bid_px_00"])+float(ch.loc[lk,"ask_px_00"]))/2)
                        best_seen=max(best_seen,cm)
            reason=f"dir-pass {dir_pass}min, best credit ${best_seen:.2f}<{MIN_CRED}" if dir_pass>0 else "direction filter blocked all window (SPX<open)"
            rows.append({"date":d,"spx_open":round(spx_open,0),"vix":round(vix,1),"status":reason,"pnl":0.0}); continue
        # Monitor SL from entry to 15:45 ET (19:45 UTC)
        e=entered; sl_trigger=SL_MULT*e["cred_mid"]; exit_cost=None; exit_reason=None
        e_hh=int(e["utc"][:2]); e_mm=int(e["utc"][3:])
        for hh in range(e_hh,20):
            for mm in range(0,60):
                if hh==e_hh and mm<=e_mm: continue
                if hh==19 and mm>45: break
                utc=f"{hh:02d}:{mm:02d}"
                chain=minute_chain(df,utc)
                if chain is None: continue
                if e["short"] not in chain.index or e["long"] not in chain.index: continue
                cc=float(chain.loc[e["short"],"ask_px_00"])-float(chain.loc[e["long"],"bid_px_00"])  # debit to close
                if cc>=sl_trigger:
                    exit_cost=cc; exit_reason="STOP_LOSS"; break
            if exit_cost is not None: break
        if exit_cost is None:
            # EOD close at 15:45 (19:45 UTC) — settle to SPX close intrinsic
            spx_close=float(day_spx.iloc[-1]["Close"])
            if spx_close>=e["short"]: exit_cost=0.0
            elif spx_close>=e["long"]: exit_cost=e["short"]-spx_close
            else: exit_cost=WIDTH
            exit_reason="EOD/EXPIRY"
        pnl=(e["cred_mid"]-exit_cost)*100
        rows.append({"date":d,"spx_open":round(spx_open,0),"spx_entry":round(e["spx_now"],0),"vix":round(vix,1),
                     "entry_et":e["et"],"short":e["short"],"long":e["long"],
                     "cred_mid":round(e["cred_mid"],2),"cred_real":round(e["cred"],2),
                     "exit_cost":round(exit_cost,2),"reason":exit_reason,"pnl":round(pnl,0),"status":"TRADED"})
    return pd.DataFrame(rows)

if __name__=="__main__":
    pd.set_option("display.width",220); pd.set_option("display.max_columns",20)
    daylabel = "all 6 days (May 21-29)" if "--all" in sys.argv else "days after May 22 (May 26-29)"
    print("="*100)
    print(f"min_credit COMPARISON — {daylabel}  |  50pt/1%OTM/10:30/SL2.0x/VIX12-25/dir-filter")
    print("="*100)
    for mc in [1.00, 1.20, 1.50]:
        MIN_CRED=mc
        # inject into module global used inside run()
        globals()["MIN_CRED"]=mc
        df=run()
        traded=df[df["status"]=="TRADED"]
        p=traded["pnl"] if len(traded) else pd.Series([],dtype=float)
        print(f"\n### min_credit = ${mc:.2f} ###")
        show=df.reindex(columns=["date","spx_open","vix","short","cred_mid","pnl","status"])
        print(show.to_string(index=False))
        if len(traded):
            print(f"  → traded {len(traded)}, total=${p.sum():+,.0f}, avg=${p.mean():+.0f}, win%={(p>0).mean()*100:.0f}%, worst=${p.min():+,.0f}")
        else:
            print(f"  → traded 0 (no day met ${mc:.2f} credit)")
