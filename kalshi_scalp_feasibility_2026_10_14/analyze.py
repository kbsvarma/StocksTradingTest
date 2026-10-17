from pathlib import Path
import collections,datetime,hashlib,json,math,random,statistics
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent
def et(t):return datetime.datetime.fromtimestamp(t/1000,ZoneInfo('America/New_York')).isoformat()
def main():
    result=json.loads((P/'results.json').read_text());runs=result['runs'];manifest=json.loads((P/'capture_manifest.json').read_text());cov=result['coverage'];start=min(x['first'] for x in cov);end=max(x['last'] for x in cov);hours=(end-start)/3600000
    elapsed=sum(max(0,min(x['last'],x['close']*1000)-x['first']) for x in cov)/3600000
    detail={};allpaths=sorted(P.glob('ledger_*.json'))
    for path in allpaths:
        name=path.stem[7:];rows=json.loads(path.read_text());fills=[r for r in rows if r['quantity']];m=runs[name]['all'];q=sum(r['quantity'] for r in fills)
        byreason={}
        for reason in sorted({r['end_reason'] for r in fills}):
            rr=[r for r in fills if r['end_reason']==reason];byreason[reason]=dict(trades=len(rr),net=round(sum(r['net'] for r in rr),6),mean_net=statistics.mean(r['net'] for r in rr))
        blocks=collections.defaultdict(float)
        for x in cov:blocks[int(x['close']//3600)]+=0
        for r in fills:blocks[int(r['signal_ts']//3600000)]+=r['net']
        vals=list(blocks.values());rng=random.Random(141026);boot=sorted(sum(rng.choices(vals,k=len(vals))) for _ in range(5000))
        detail[name]=dict(weighted_entry=sum(r['price']/10000*r['quantity'] for r in fills)/q if q else None,filled_per_recorded_24h=len(fills)/hours*24,attempts_per_recorded_24h=len(rows)/hours*24,win_rate=m['wins']/len(fills) if fills else None,by_exit=byreason,any_passive_target=sum(any(x['order']=='target' for x in r['legs']) for r in fills),one_hour_cluster_bootstrap_net95=[boot[124],boot[4874]],bootstrap_caution='Only roughly36h, not a regime-robust confidence interval; fixed-ledger resampling does not replay changing daily-risk brakes.',max_trade_delay_ms=max((r['max_trade_delay'] for r in rows),default=None),repeated_forced_sweeps=sum(r['force_number']>1 for r in fills))
    summary=dict(start_et=et(start),end_et=et(end),elapsed_hours=hours,captured_hours=elapsed,complete_windows=len(cov),asset_windows=2*len(cov),raw_files=len(manifest),rejected_files=[dict(file=x['file'],complete=x['complete'],bad=x['bad']) for x in manifest if not x['accepted']],raw_frames=sum(x['raw_frames'] for x in manifest),compact_rows=sum(x['compact_rows'] for x in manifest),details=detail)
    (P/'analysis.json').write_text(json.dumps(summary,indent=2))
    lines=['RESTING-ORDER SCALP BACKTEST — frozen exploratory rules',f'Historical capture: {et(start)} to {et(end)}',f'{len(cov)} completed capture windows; BTC and ETH, {2*len(cov)} asset windows; {hours:.3f} elapsed hours / {elapsed:.3f} captured hours.',f'Raw files {len(manifest)}; raw messages {summary["raw_frames"]:,}; incomplete/error rejections {len(summary["rejected_files"])}. WTI example from October14 excluded.','',
    'Design:2c five-second midpoint dip, net aggressive selling>=5ct, stable trailing benchmark-derived probability, passive ask-minus1c entry,5ct maximum,10s entry expiry; passive2/3c target,5c adverse-bid trigger,30/60s deadline. Targets can take fees if already marketable at acceptance. Cancellation races/partial fills included. At most one order/position per asset,10s cooldown. Repeated trades per15m window allowed.','Cash100 means simulated$100 capital with$20 ET daily-loss limit including open principal. Uncapped diagnostics do not impose these brakes. No live or existing paper strategy changed.','',
    'Filtered design,250ms action latency:','Target | Max hold | Attempts | Filled | W/L/flat | Net after fees | Net after0.25c per executed leg | Realized drawdown | Filled /24 elapsed recorded hours']
    for tp in (2,3):
        for t in (30,60):
            name=f'stable_tp{tp}_t{t}_L250_cash100';m=runs[name]['all'];d=detail[name];lines.append(f'{tp}c | {t}s | {m["attempts"]} | {m["filled"]} | {m["wins"]}/{m["losses"]}/{m["flat"]} | {m["net"]:+.4f} | {m["stress025"]:+.4f} | {m["drawdown"]:.4f} | {d["filled_per_recorded_24h"]:.2f}')
    name='stable_tp2_t60_L250_cash100';v=runs[name];m=v['all'];d=detail[name]
    lines+=['','PRIMARY2c/60s details:',json.dumps(v,indent=2),json.dumps(d,indent=2),'','Control / sensitivity:','Variant | Filled | W/L | Net after fees | After0.25c per leg | Daily-risk blocks']
    for name in ['diponly_tp2_t60_L250_cash100','stable_tp2_t60_L1000_cash100','stable_tp3_t60_L1000_cash100','stable_tp2_t60_L250_uncapped','stable_tp3_t60_L250_uncapped']:
        v=runs[name];m=v['all'];lines.append(f'{name} | {m["filled"]} | {m["wins"]}/{m["losses"]} | {m["net"]:+.4f} | {m["stress025"]:+.4f} | {v["risk_blocks"].get("budget",0)}')
    lines+=['','Interpretation limits:',
    '- All fills are counterfactual. Orders are not actually inserted; queue impact/hidden liquidity are not proven. Same-price prints consume initial visible queue; through-price fills credit only observed trade quantity. Neither entry nor target fills merely from quote touch.',
    '- Two markets over about36h cannot establish persistence. Later-time check startsOct4 00:00UTC; these historical data were used in other research and are not new prospective validation. No threshold selected after these returns; all predeclared variants retained.',
    '- Benchmark stability is a relative Gaussian-probability screen using trailing60second volatility, not a calibrated forecast of winning. One ETH capture has no recorded strike and cannot qualify for the filtered strategy. No future strike/value imputation.',
    '- Actual captured book sampling is100ms. Book queue reads below captured top10 levels are unknown and rejected/forced out, explicitly flagged. No re-use of the same displayed depth for repeated forced sweeps. Missing coverage and unresolved write-downs remain in P&L.',
    '- Maker fees modeled zero; taker exits0.07*q*p*(1-p) with rounding and cash precision. Stress deductions are fixed-ledger sensitivities, not new portfolio/risk-path simulations. Fees vary by series; current BTC/ETH metadata and official July7 fee schedule support this fee assumption.',
    '- Drawdown is on completed-trade P&L, not continuous liquidation equity. Stops use subsequent depth after cancel+submission latency, not guaranteed5c loss. Frequency is historical observed-hour normalization, not a forecast.',
    '- Sparse WTI/FX books do not support this same long-history, second-level two-leg backtest; BTC/ETH results cannot be presented as WTI/FX results.',
    '', 'Files:PLAN.txt,extract.py,replay.py,test_replay.py,audit.py,results.json,analysis.json,capture_manifest.json,ledger_*.json,audit.json. Original historical data and all losing/unfilled records retained.']
    (P/'BACKTEST_REPORT.txt').write_text('\n'.join(lines)+'\n')
    print(json.dumps(dict(coverage={k:v for k,v in summary.items() if k!='details'},primary=runs['stable_tp2_t60_L250_cash100'],primary_details=detail['stable_tp2_t60_L250_cash100']),indent=2))
if __name__=='__main__':main()
