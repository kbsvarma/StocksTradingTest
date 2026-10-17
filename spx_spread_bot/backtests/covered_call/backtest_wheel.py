"""Full WHEEL backtest on a single ticker.

State machine:
  CASH state  -> sell weekly CSP ~OTM% below spot.
     - if Fri close >= strike: keep premium, stay CASH
     - if Fri close <  strike: ASSIGNED, buy 100 sh at strike, go to SHARES state
  SHARES state -> sell weekly CC ~OTM% above cost-anchor (or spot).
     - if Fri close <= strike: keep premium, stay SHARES (collect div too)
     - if Fri close >  strike: CALLED AWAY, sell 100 sh at strike, go to CASH

Tracks: cumulative premium, realized stock P&L, dividends, final equity.
Compares to buy-and-hold 100 shares over same window.
Premium taken at BID (realistic sell).
"""
import argparse, re
from pathlib import Path
import pandas as pd
import yfinance as yf

DATA = Path(__file__).parent / "data"
RX = re.compile(r"\s(\d{6})([PC])(\d{8})$")
def parse(s):
    m=RX.search(s.strip())
    return (m.group(2), int(m.group(3))/1000) if m else None

def nearest(series, target):
    ks=list(series.index)
    return min(ks, key=lambda k: abs(k-target)) if ks else None

def run(ticker, otm=0.05):
    fp = DATA / f"{ticker.lower()}_csp_weekly.parquet"
    df = pd.read_parquet(fp)
    df = df[df["bid_px_00"]>0].copy()
    parsed=df["symbol"].map(parse); df=df[parsed.notna()].copy()
    df["side"]=parsed[parsed.notna()].map(lambda x:x[0])
    df["strike"]=parsed[parsed.notna()].map(lambda x:x[1])

    df["monday"]=pd.to_datetime(df["monday"])
    hist_start=str(df["monday"].min().date())
    t=yf.Ticker(ticker)
    hist=t.history(start=hist_start,end="2026-05-23",auto_adjust=False)
    hist.index=pd.to_datetime(hist.index).tz_localize(None)
    divs=t.dividends; divs.index=pd.to_datetime(divs.index).tz_localize(None)

    weeks=sorted(df["monday"].unique())
    state="CASH"; shares=0; cost_anchor=None
    cum_prem=0.0; realized=0.0; cum_div=0.0
    assignments=0; callaways=0; weeks_traded=0
    first_spot=None; last_close=None
    yearly={}  # year -> dict of prem/realized/div

    def addy(yr, key, val):
        yearly.setdefault(yr, {"prem":0.0,"realized":0.0,"div":0.0,"assign":0,"callaway":0})
        yearly[yr][key]+=val

    for mon in weeks:
        g=df[df["monday"]==mon]
        spot=float(g["spot"].iloc[0]); fri=pd.Timestamp(g["friday"].iloc[0]); yr=mon.year
        if first_spot is None: first_spot=spot
        fi=hist.index.get_indexer([fri],method="ffill")[0]
        if fi<0: continue
        fclose=float(hist.iloc[fi]["Close"]); last_close=fclose
        if state=="SHARES" and shares>0:
            wk_div=divs[(divs.index>pd.Timestamp(mon)-pd.Timedelta(days=3))&(divs.index<=fri)].sum()
            cum_div+=wk_div*shares; addy(yr,"div",wk_div*shares)

        if state=="CASH":
            puts=g[g["side"]=="P"].drop_duplicates("strike").set_index("strike")["bid_px_00"]
            pk=nearest(puts, spot*(1-otm))
            if pk is None: continue
            prem=float(puts.loc[pk]); cum_prem+=prem*100; weeks_traded+=1; addy(yr,"prem",prem*100)
            if fclose < pk:
                shares=100; cost_anchor=pk; state="SHARES"; assignments+=1; addy(yr,"assign",1)
        else:
            calls=g[g["side"]=="C"].drop_duplicates("strike").set_index("strike")["bid_px_00"]
            anchor=max(spot, cost_anchor)
            ck=nearest(calls, anchor*(1+otm))
            if ck is None: continue
            prem=float(calls.loc[ck]); cum_prem+=prem*100; weeks_traded+=1; addy(yr,"prem",prem*100)
            if fclose > ck:
                realized += (ck - cost_anchor)*100; addy(yr,"realized",(ck-cost_anchor)*100)
                shares=0; cost_anchor=None; state="CASH"; callaways+=1; addy(yr,"callaway",1)

    unrealized = (last_close - cost_anchor)*100 if state=="SHARES" else 0.0
    wheel_total = cum_prem + realized + unrealized + cum_div
    bh = (last_close - first_spot)*100 + divs[(divs.index>=hist.index[0])&(divs.index<=hist.index[-1])].sum()*100

    yrs = (hist.index[-1]-hist.index[0]).days/365.25
    cap = first_spot*100  # capital reference (100 shares worth at start)
    print(f"\n{'='*78}\n{ticker} WHEEL — sell {otm*100:.0f}% OTM weekly, 2024-2026\n{'='*78}")
    print(f"  weeks traded:    {weeks_traded}")
    print(f"  assignments:     {assignments}  call-aways: {callaways}")
    print(f"  ending state:    {state} ({'holding '+str(shares)+' sh' if shares else 'in cash'})")
    print(f"  cum premium:     ${cum_prem:+,.0f}")
    print(f"  realized stock:  ${realized:+,.0f}")
    print(f"  unrealized:      ${unrealized:+,.0f}")
    print(f"  dividends:       ${cum_div:+,.0f}")
    print(f"  ---")
    print(f"  WHEEL total:     ${wheel_total:+,.0f}   ({wheel_total/cap*100/yrs:+.1f}%/yr on ${cap:,.0f})")
    print(f"  Buy&Hold total:  ${bh:+,.0f}   ({bh/cap*100/yrs:+.1f}%/yr)")
    print(f"  Wheel vs B&H:    ${wheel_total-bh:+,.0f}  ({'WHEEL WINS' if wheel_total>bh else 'B&H WINS'})")
    print(f"  Premium yield alone: {cum_prem/cap*100/yrs:.1f}%/yr")
    print(f"  --- per-year (wheel components) ---")
    for yr in sorted(yearly):
        y=yearly[yr]; tot=y["prem"]+y["realized"]+y["div"]
        print(f"    {yr}: prem=${y['prem']:+,.0f} realized=${y['realized']:+,.0f} div=${y['div']:+,.0f} "
              f"= ${tot:+,.0f}  (assign={y['assign']}, calledaway={y['callaway']})")

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--tickers",default="WFC")
    ap.add_argument("--otm",type=float,default=0.05)
    a=ap.parse_args()
    for t in a.tickers.split(","):
        run(t.strip(), a.otm)
