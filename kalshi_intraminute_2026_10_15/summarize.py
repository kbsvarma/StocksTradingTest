from pathlib import Path
import json,gzip,datetime,hashlib,collections
import numpy as np
P=Path(__file__).resolve().parent;R=P.parent
def main():
 lines=['INTRAMINUTE CRYPTO ENTRY REPLAY — 2026-10-15 ET','',
 'FINDING: intraminute checks increase modeled trade count, but the profit gain',
 'does not repeat in the newer dense-book sample. Keep the live design unchanged.',
 'Seven-day all4 cached-quote model:84fills/84W0L/$26.3127 ->127fills/126W1L/$34.2758.',
 'That is43additional modeled fills (+6.14/day in this sample), net improvement$7.9631.',
 'With3s delay the improvement falls to$1.0879. Seven-day block uncertainty includes0.',
 'Newer BTC/ETH dense sample: same cached-quote change19fills/$0.2072 ->27/-$6.7483.',
 'Updating both z and the live quote:19fills/18W1L/$0.2072 ->37/32W5L/-$13.8639.',
 'These final figures preserve frozen-signal retries; earlier provisional figures',
 'with extra z revalidation are retained separately and are not the primary result.',
 '',
 'No live changes. Results are modeled full-depth fills, not submitted orders.',
 'Current BTC original z3, ETH/SOL/XRP original z2, premium z3 all; five contracts.',
 'Original85–<95c/cap93.9c; premium93–<98c/cap97.5c; same tick rounding and min(ask+1c,cap).',
 'Time3–10min; shared25contracts/$25reserved/$20daily including open risk;',
 'premium25/$25/$25 subordinate shared20. Initial free crypto cash$62.6859.',
 'Portfolio models crypto only; it does not guarantee capacity beside live commodities/FX.',
 'Current7% quadratic taker fee with fixed-point rounding; fee multiplier1 verified all4.',
 '',
 'CAUSAL INPUTS AND LIMITATIONS',
 'Coinbase completed-minute reference and61 closes/60-return population sigma retained.',
 'Subminute price is CF relative movement anchored to each Coinbase minute close.',
 'No interpolation from candle highs/lows and no future bars. This is a feed proxy,',
 'not an exact historical Coinbase stream replay. Sparse CF assumes1s/3s publication',
 'delay; dense CF uses recorded arrival times. Entry observations occur after1s/3s',
 'transport delay inside25s; five displayed contracts required. Snapshot depth can',
 'disappear before a real order arrives; these are simulations, not guaranteed fills.',
 'Control=current completed-minute quotes/z. Cached-quote candidates update z every',
 '30/10/1s but retain completed-minute market quotes. Stream-quote candidates update',
 'both z and current market quotes, with unchanged bands and caps.',
 'Attempt scheduling is bounded to one qualification per lane/minute and24book',
 'checks/25s; at most one successful five-contract entry per market. This model',
 'does not invent rejected IOC responses or claim all displayed depth would fill.',
 'Sparse archive books are33s apart: missing coverage is unknown, not a live miss.',
 'In particular, minute versus intraminute alignment to sparse snapshots can create',
 'an apparent fill advantage. Do not convert sparse increases into a trade/day forecast.',
 'Dense BTC/ETH data is the stronger execution check; no equal-quality SOL/XRP archive.',
 'Each window uses its recorded official settlement. This is a full input replay,',
 'not filtering old live ledger z values. Historical periods are not the Oct15 lull.',
 '']
 uncertainty={}
 for prefix in ('sparse','dense','sparse_stream','dense_stream'):
  data=json.loads((P/(prefix+'_results.json')).read_text());rs=data['results'];ll=json.load(gzip.open(P/(prefix+'_ledgers.json.gz'),'rt'))
  fmt=lambda t:datetime.datetime.fromtimestamp(t,datetime.timezone.utc).isoformat()
  lines += [prefix.upper(),f"UTC coverage {fmt(data['first'])} to {fmt(data['last'])}; {data['days']:.3f}days; windows{data['coverage']}",f"Book-gap seconds {data['book_gap_seconds']}"]
  if prefix=='sparse_stream':
   lines += ['INCONCLUSIVE: not a zero-trade or zero-P&L strategy outcome.',
    '563qualifying episodes at1s checks; none has a subsequent recorded book inside',
    'the25s deadline. There are33s between book snapshots, so fully streaming fills',
    'and net cannot be estimated for all4 from this archive. Raw zero counts in the',
    'machine ledger mean no verifiable fills; do not treat these as economic returns.','']
   uncertainty[prefix]=dict(status='insufficient_subsequent_book_coverage',net_estimate=None)
   continue
  lines+=['Checks | delay | fills | W/L | net after fees | fees | drawdown | net extra trades']
  for delay in (1,3):
   for step in (60,30,10,1):
    x=rs[f'{step}s_delay{delay}_friction0']
    lines.append(f"{step}s | {delay}s | {x['trades']} | {x['wins']}/{x['losses']} | ${x['net']:+.4f} | ${x['fees']:.4f} | ${x['drawdown']:.4f} | {x['comparison']['net_trade_change']:+d}")
  lines+=['','Per asset: baseline ->1s checks (1s delay), net after fees']
  base=rs['60s_delay1_friction0'];fast=rs['1s_delay1_friction0']
  for a in ('BTC','ETH','SOL','XRP'):
   b=base['by_asset'][a];f=fast['by_asset'][a]
   lines.append(f"{a}: {b['trades']}({b['wins']}W/{b['losses']}L) ${b['net']:+.4f} -> {f['trades']}({f['wins']}W/{f['losses']}L) ${f['net']:+.4f}; trade delta{f['comparison']['net_trade_change']:+d}; net delta${f['comparison']['net_pnl_change']:+.4f}")
  lines+=['Comparison '+json.dumps(fast['comparison']), 'Baseline observations '+json.dumps(base['signals']),'1s observations '+json.dumps(fast['signals']), '1s risk '+json.dumps(fast['risk']),f"1s observed signal-to-fill wait median{fast['median_wait']}s p95{fast['p95_wait']}s", '1c extra friction: baseline'+str(rs['60s_delay1_friction0.01']['net'])+';1s'+str(rs['1s_delay1_friction0.01']['net']), 'Chronological halves1s '+json.dumps([fast['first_half'],fast['second_half']]),'']
  # Calendar-block uncertainty for the paired AFTER-FEES difference; exploratory.
  blocks=collections.defaultdict(float)
  for label,sign in [('60s_delay1_friction0',-1),('1s_delay1_friction0',1)]:
   for x in ll[label]:
    d=datetime.datetime.fromtimestamp(x['close']-900,datetime.timezone.utc).date().isoformat();blocks[d]+=sign*x['net']
  days=[(datetime.datetime.fromtimestamp(data['first'],datetime.timezone.utc).date()+datetime.timedelta(days=i)).isoformat() for i in range((datetime.datetime.fromtimestamp(data['last']-1,datetime.timezone.utc).date()-datetime.datetime.fromtimestamp(data['first'],datetime.timezone.utc).date()).days+1)]
  vv=np.array([blocks[d] for d in days]);rng=np.random.default_rng(20261015);dist=rng.choice(vv,size=(10000,len(vv)),replace=True).sum(axis=1)
  uncertainty[prefix]=dict(calendar_days=len(vv),paired_net_delta=float(vv.sum()),conditional_day_bootstrap_95_total=np.quantile(dist,[.025,.975]).tolist(),warning='Few correlated calendar blocks; conditions on historical fills and risk path; not proof of an edge.')
 lines+=['UNCERTAINTY',json.dumps(uncertainty,indent=2),'',
 'COVERAGE:664archived windows/asset versus672calendar slots in the seven-day',
 'period. Eight missing slots per asset are excluded, not assigned profits/losses.',
 'CF history coverage exceeds99.97%; missing source seconds never create fills.',
 'Proxy tracking error at minute boundaries has95th-percentile equivalent z',
 'error approximately0.26–0.34. Near-threshold signal counts can change under an',
 'exact Coinbase streaming feed. Real dense CF arrival median79–80ms,p95~109ms.',
 '', 'VERIFICATION', (P/'validation.json').read_text(), (P/'raw_book_audit.json').read_text(),'','See source_audit.json for price-proxy tracking and real CF receipt delays.', 'See *_ledgers.json.gz for every fill/fee/outcome, *_opportunities.json.gz for rejected portfolio candidates.', 'Frozen PLAN, CHANGELOG and read-only production source copies retained. No orders, source changes, flags, or service restarts.']
 (P/'REPORT.txt').write_text('\n'.join(lines)+'\n');(P/'uncertainty.json').write_text(json.dumps(uncertainty,indent=2))
 sources=[R/'kalshi_z_time_research_2026_10_03/features.pickle',R/'kalshi_z_time_research_2026_10_03/input_sources/0/books.jsonl.gz',R/'kalshi_loss_review_2026_10_14/recent_features.pickle']+list((P/'cf_seconds').glob('*.gz'))+list((P/'source').glob('*.py'))
 manifest={str(f.relative_to(R)):dict(bytes=f.stat().st_size,sha256=hashlib.sha256(f.read_bytes()).hexdigest()) for f in sources}
 (P/'input_manifest.json').write_text(json.dumps(manifest,indent=2))
 print('Report and manifests saved')
if __name__=='__main__':main()
