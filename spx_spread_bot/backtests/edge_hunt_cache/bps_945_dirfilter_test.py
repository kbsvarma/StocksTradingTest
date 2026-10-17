"""Test: 9:45 BPS entry, direction filter ON vs OFF.

Models the legacy bot's structure but entering at 9:45 (not 10:30):
  - 50pt width, scan for credit closest to $2.00 (>= $1.20 min), ~1% OTM band
  - VIX gate 12-25, skip Thursday
  - SL 2.0x entry credit (intraday minute monitoring)
  - hold to SL or cash-settle at SPX close
  - Direction filter toggle: ON = enter only if SPX@9:45 >= SPX open
"""
import re
from datetime import datetime
from pathlib import Path
import pandas as pd

CACHE = Path(__file__).parent
DATA  = CACHE.parent / "narrow_width_databento_2026_05_21" / "spxw_p_cbbo_1m"
SPX   = pd.read_parquet(CACHE.parent / "narrow_width_databento_2026_05_21" / "spot_cache_GSPC.parquet")
VIX   = pd.read_parquet(CACHE / "vix_panel.parquet")

WIDTH=50; TARGET=2.00; MIN_CRED=1.20; SL_MULT=2.0
ENTRY_HH,ENTRY_MM = 13,45   # 9:45 ET = 13:45 UTC
EOD_HH,EOD_MM = 19,45       # 15:45 ET

# Real intraday SPY (DBEQ.BASIC): 9:30 open + entry-bar close → true direction filter.
# Map UTC HH:MM -> the close used as the "spot at entry" for that entry time.
_SPYP = CACHE / "spy_1m_open945.parquet"
SPYDIR = {}   # date -> {"open":x, "13:45":x, "14:30":x}
if _SPYP.exists():
    _s = pd.read_parquet(_SPYP)
    for dt, g in _s.groupby("date"):
        rec={}
        op=g[g["hm"]=="13:30"]["open"]
        if len(op): rec["open"]=float(op.iloc[0])
        for hm in ("13:45","14:30"):
            c=g[g["hm"]==hm]["close"]
            if len(c): rec[hm]=float(c.iloc[0])
        SPYDIR[dt]=rec

RX=re.compile(r"P0?(\d+)000$")
def strike(s):
    m=RX.search(s.strip()); return int(m.group(1)) if m else None

def vix_at(d):
    if d not in VIX.index: return None,None
    vo=float(VIX.loc[d,"vix_open"]); idx=VIX.index.get_indexer([d],method="ffill")[0]
    vp=float(VIX.iloc[idx-1]["vix_close"]) if idx>=1 else None
    return vo,vp

def run(direction_filter, years, entry_hh=13, entry_mm=45):
    ENTRY_HH, ENTRY_MM = entry_hh, entry_mm
    files=sorted(DATA.glob("spxw_p_cbbo_*.parquet"))
    rows=[]
    for fp in files:
        ds=fp.stem.split("_")[-1]
        try: d=datetime.strptime(ds,"%Y%m%d").date()
        except: continue
        if d.year not in years: continue
        if d.weekday()==3: continue  # noThu
        ts=pd.Timestamp(d)
        if ts not in SPX.index: continue
        spx_open=float(SPX.loc[ts,"Open"]); spx_close=float(SPX.loc[ts,"Close"])
        vo,vp=vix_at(ts)
        if vo is None or vp is None: continue
        if not (12.0<=vo<=25.0): continue
        df=pd.read_parquet(fp,columns=["symbol","bid_px_00","ask_px_00"])
        df=df[(df["bid_px_00"]>0)&(df["ask_px_00"]>0)]
        if df.empty: continue
        df["k"]=df["symbol"].map(strike); df["mid"]=(df["bid_px_00"]+df["ask_px_00"])/2
        df=df.dropna(subset=["k"]); df["hm"]=df.index.strftime("%H:%M")
        ent=df[df["hm"]==f"{ENTRY_HH:02d}:{ENTRY_MM:02d}"].drop_duplicates("k").set_index("k")
        if ent.empty: continue
        # SPX at entry via parity proxy: use SPX open as ref for direction (we don't have intraday SPX
        # here; approximate 9:45 spot from the ATM put strike where mid is small). Use a strike anchor:
        # pick the strike nearest where put mid ~ (ATM). Simpler: estimate spot from richest near-ATM.
        # We DON'T have intraday SPX in this cache; use SPX open as the 9:45 proxy for strike selection,
        # and apply direction filter using close-vs-open as a same-day-down proxy is wrong.
        # Instead: derive 9:45 spot from deep-ITM put parity if available, else skip direction filter day.
        ks=sorted(ent.index)
        # 9:45 spot estimate: strike where put mid crosses ~ (spot-K)=0 -> use highest strike with mid< (K*0.002)+small
        # fallback: spot_open
        spot945=spx_open
        # direction filter needs 9:45 spot vs open; we lack it -> approximate using SPX High/Low? skip-safe:
        # We APPROXIMATE 9:45 spot with open (so filter ~ "enter always" unless we can't). To make the
        # filter meaningful, derive 9:45 spot from put-call parity is impossible (no calls). So we use a
        # conservative proxy: if the day's close < open by >0.3%, treat as a 'down day' the filter would
        # likely have skipped. This is imperfect but directionally tests the hypothesis.
        target_short=round(spot945*0.99/5)*5
        cands=sorted(ks,key=lambda x:abs(x-target_short))[:5]
        best=None;bd=1e9
        for sk in cands:
            lk=sk-WIDTH
            if lk not in ent.index: continue
            cred=float(ent.loc[sk,"mid"]-ent.loc[lk,"mid"])
            if cred<MIN_CRED: continue
            dd=abs(cred-TARGET)
            if dd<bd: bd=dd;best=(sk,lk,cred)
        if best is None: continue
        sk,lk,cred=best
        # REAL direction filter: skip if SPY at 9:45 < SPY at 9:30 open (no hindsight).
        if direction_filter:
            rec=SPYDIR.get(d.isoformat())
            bar=f"{ENTRY_HH:02d}:{ENTRY_MM:02d}"
            if not rec or "open" not in rec or bar not in rec: continue
            if rec[bar] < rec["open"]: continue   # spot@entry below open → down day, skip
        # SL monitor: walk minutes after entry (string-time compare avoids tz issues)
        post=df[(df["hm"]>f"{ENTRY_HH:02d}:{ENTRY_MM:02d}") & (df["hm"]<=f"{EOD_HH:02d}:{EOD_MM:02d}")]
        piv=post.pivot_table(index=post.index,columns="k",values=["bid_px_00","ask_px_00"],aggfunc="last")
        sl_trig=SL_MULT*cred; exit_cost=None
        try:
            sa=piv[("ask_px_00",sk)]; lb=piv[("bid_px_00",lk)]
            cc=(sa-lb); cc=cc[cc.notna()]
            br=cc[cc>=sl_trig]
            if not br.empty: exit_cost=float(br.iloc[0])
        except KeyError: pass
        if exit_cost is None:
            # settle at close
            if spx_close>=sk: exit_cost=0.0
            elif spx_close>=lk: exit_cost=sk-spx_close
            else: exit_cost=WIDTH
        pnl=(cred-exit_cost)*100
        rows.append({"date":d,"year":d.year,"pnl":pnl,"sl":exit_cost>=sl_trig})
    return pd.DataFrame(rows)

def rpt(df,label):
    if df.empty: print(f"  {label}: NO trades"); return
    p=df["pnl"]
    print(f"  {label}: n={len(df)} total=${p.sum():+,.0f} avg=${p.mean():+.0f} "
          f"win={(p>0).mean()*100:.0f}% worst=${p.min():+,.0f} sl_fires={df['sl'].sum()}")
    for y,g in df.groupby("year"):
        gp=g["pnl"]; print(f"    {y}: n={len(g):3d} ${gp.sum():+,.0f} avg=${gp.mean():+.0f} worst=${gp.min():+,.0f}")

if __name__=="__main__":
    # Real direction filter only exists where we have intraday SPY (DBEQ.BASIC: 2023-03-28+).
    yrs=set(range(2023,2027))
    print("="*92)
    print("CLEAN A/B · BPS 50pt · ~1% OTM · SL 2.0× · VIX 12-25 · noThu · 9:45 entry · 2023-2026")
    print("Direction filter = REAL intraday SPY: enter only if SPY@9:45 close >= SPY@9:30 open")
    print("="*92)
    for hh,mm,lbl in [(13,45,"9:45"),(14,30,"10:30")]:
        print(f"\n--- ENTRY {lbl} ---")
        off=run(False,yrs,hh,mm); on=run(True,yrs,hh,mm)
        rpt(off,f"FILTER OFF")
        if not off.empty:
            p=off["pnl"];w=p[p>0];l=p[p<0]
            print(f"      avg win ${w.mean():+.0f}×{len(w)} | avg loss ${l.mean():+.0f}×{len(l)} | SL {off['sl'].mean()*100:.0f}%")
        rpt(on ,f"FILTER ON ")
        if not on.empty:
            p=on["pnl"];w=p[p>0];l=p[p<0]
            print(f"      avg win ${w.mean():+.0f}×{len(w)} | avg loss ${l.mean():+.0f}×{len(l)} | SL {on['sl'].mean()*100:.0f}%")
        skipped=off[off["date"].isin(set(off["date"])-set(on["date"]))]
        if not skipped.empty:
            sp=skipped["pnl"]
            print(f"  SKIPPED by filter: n={len(skipped)} total=${sp.sum():+,.0f} avg=${sp.mean():+.0f} "
                  f"win={(sp>0).mean()*100:.0f}% — {'GOOD skips (filter avoided net losers)' if sp.mean()<0 else 'BAD skips (gave up net profit)'}")
