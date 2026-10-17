"""Audit existing separate-lane portfolio results; no network or bot changes."""
from pathlib import Path
import collections,hashlib,json,sys
P=Path(__file__).resolve().parent;SOURCE=P.parent;sys.path.insert(0,str(SOURCE))
from research import metrics

results=json.loads((SOURCE/'portfolio_results.json').read_text())
trades=json.loads((SOURCE/'portfolio_trades.json').read_text())
names=('premium','value','parallel_pair')
labels={'premium':'Strong cushion, z >=3','value':'Cheaper value, >=8-point modeled edge','parallel_pair':'Combined, one entry per market'}
out={'sizing':'One contract per entry, fees plus extra $0.01 cost per contract. Each standalone replay has max4/$4 reserves/$5 daily budget and starts with virtual$50 per cohort. Independent sum retains separate ledgers and may take two positions on the same market. No integration with current5ct live bot is implied.',
     'source_hashes':{n:hashlib.sha256((SOURCE/n).read_bytes()).hexdigest() for n in ('portfolio_results.json','portfolio_trades.json')},'scenarios':{}}
for scenario,groups in trades.items():
    out['scenarios'][scenario]={}
    for co,lanes in groups.items():
        dd={}
        for name in names:
            rows=lanes[name];m=metrics(rows);ref=results[scenario][co][name]
            assert len(rows)==len({r['ticker'] for r in rows})
            for key in ('n','wins','losses','net','drawdown'):assert m[key]==ref[key],(scenario,co,name,key)
            assert all(r['quantity']==1 for r in rows)
            dd[name]={**m,'cap_replay_net':ref['cap_replay_net'],'whole_cent_net':ref['whole_cent_net_after1c'],'stress2c_net':ref['stress2c']['net']}
        a={r['ticker']:r for r in lanes['premium']};b={r['ticker']:r for r in lanes['value']};c={r['ticker']:r for r in lanes['parallel_pair']}
        common=a.keys()&b.keys();summed=list(a.values())+list(b.values())
        dd['independent_sum']=metrics(summed)
        dd['overlap']={'duplicate_markets':len(common),'unique_markets':len(a.keys()|b.keys()),'same_side':sum(a[t]['side']==b[t]['side'] for t in common),
          'premium_first':sum(a[t]['entered']<b[t]['entered'] for t in common),'value_first':sum(b[t]['entered']<a[t]['entered'] for t in common),
          'pair_missing_union':sorted((a.keys()|b.keys())-c.keys()),'pair_added_outside_union':sorted(c.keys()-(a.keys()|b.keys())),
          'independent_extra_net':round(dd['independent_sum']['net']-dd['parallel_pair']['net'],5)}
        out['scenarios'][scenario][co]=dd
out['pooled_quote_50days']={name:metrics(sum((trades['quote'][co][name] for co in ('fit','selection','later')),[])) for name in names}
out['pooled_book_30days']={name:metrics(sum((trades['book'][co][name] for co in ('fit','selection')),[])) for name in names}
(P/'results.json').write_text(json.dumps(out,indent=2))
recent=out['scenarios']['quote']['later'];assert recent['overlap']['duplicate_markets']==28 and recent['overlap']['same_side']==28
assert recent['independent_sum']['n']==286 and recent['independent_sum']['net']==8.7048
assert not recent['overlap']['pair_missing_union'] and not recent['overlap']['pair_added_outside_union']

lines=['# Split the two experimental rules — October 2, 2026','','## Answer','','The combination is optional. It was a shared risk/duplicate-entry mechanism, not evidence that these rules must trade together. Separate evaluation makes the different loss patterns easier to assess. No running bot was changed for this comparison.','','## Same September12–October1 UTC period','','One contract per simulated entry; fees and an extra1c cost included. These recent fills use next-minute quote proxies, with no historical depth proof. These are not actual live profits.','','| Design | Trades | Trades/day | Wins / losses | Net | Historical drawdown |','|---|---:|---:|---:|---:|---:|']
for name in names:
 s=recent[name];lines.append(f"| {labels[name]} | {s['n']} | {s['n']/20:.2f} | {s['wins']} / {s['losses']} | ${s['net']:+.4f} | ${s['drawdown']:.4f} |")
s=recent['independent_sum'];lines.append(f"| Both independent ledgers, additive scenario | {s['n']} | {s['n']/20:.2f} | {s['wins']} / {s['losses']} | ${s['net']:+.4f} | ${s['drawdown']:.4f} |")
lines+=['','The independent sum has286 entries but only258 distinct markets. Both rules buy the same outcome in28 overlapping markets. Value enters first in26 of those, premium first in2. The combined design keeps the first position only; the separate-ledger scenario admits both. Its additional+$0.8081 comes with28 additional contract positions, rather than28 new market opportunities. It is not an equal-risk proof of improvement. The independent scenario retains each lane’s own budget, rather than enforcing the combined runner’s single shared budget.','','Combined-run attribution is not standalone performance: in the combined runner premium contributed119 entries/+$2.4608, and value139 entries/+$5.4359. Those totals omit opportunities taken first by the other rule. The standalone figures in the main table restore each lane’s full replay.','','## Include the earlier losses','','The recent zero-loss premium result is not representative of all available data. Pooled full-period quote results across the original15/15/20-day portfolio replays (Aug13–Oct1):','','| Standalone design | Trades | Wins / losses | Net after fees+1c | Drawdown |','|---|---:|---:|---:|---:|']
for name in ('premium','value'):
 s=out['pooled_quote_50days'][name];lines.append(f"| {labels[name]} | {s['n']} | {s['wins']} / {s['losses']} | ${s['net']:+.4f} | ${s['drawdown']:.4f} |")
lines+=['','These are pooled independent period replays with cash reset at each study boundary, not a newly run continuous50-day capital simulation. The rules were selected through retrospective exploration, so this is not untouched out-of-sample proof.','','Recorded-book check, Aug13–Sep11 (30 days):','','| Standalone design | Trades | Wins / losses | Net after fees+1c | Drawdown |','|---|---:|---:|---:|---:|']
for name in ('premium','value'):
 s=out['pooled_book_30days'][name];lines.append(f"| {labels[name]} | {s['n']} | {s['wins']} / {s['losses']} | ${s['net']:+.4f} | ${s['drawdown']:.4f} |")
lines+=['','Recorded depth still does not guarantee an IOC fill. Premium has fewer observed losses and much smaller net per trade; expensive contracts require sustained high accuracy. Value produced more net in this sample with much more frequent losses. Neither should inherit the current bot’s track record.','','## Execution sensitivities, September12–October1','','| Standalone design | Main net | Charge accepted fills at cap | Whole-cent rounding | Extra2c cost | Full-minute-delay quote net |','|---|---:|---:|---:|---:|---:|']
for name in ('premium','value'):
 s=recent[name];delay=out['scenarios']['quote_delay60']['later'][name];lines.append(f"| {labels[name]} | ${s['net']:+.4f} | ${s['cap_replay_net']:+.4f} | ${s['whole_cent_net']:+.4f} | ${s['stress2c_net']:+.4f} | ${delay['net']:+.4f} |")
lines+=['','The full-minute delay exceeds the live35-second freshness limit and is only a sensitivity. Value loses$1.7919 when that delayed scenario is also charged at its cap. The older Aug13–27 book sample charged at cap loses$0.0131 for premium and$0.9817 for value. These limitations remain after separating the lanes.','','## Recommendation','','Track the two strategies independently in paper evaluation before considering any combined allocation. Premium is the closer match to a preference for high win frequency, but it earns very little per contract and had historical losses. Value is the higher-return experimental candidate in these samples, with roughly10% recent losses. Splitting reports does not require splitting accounts, moving money, or changing the live bot. Running both as independent real-money order writers would still need shared account risk and duplicate-position coordination.','','The existing deployed paper runner is still the combined design. This request was evaluated as a what-if; neither paper rules nor live settings were modified. `results.json` contains the matching-period standalone, combined, overlap and sensitivity outputs. `analyze.py` reconciles the rows to the previously saved portfolio results.']
(P/'REPORT.md').write_text('\n'.join(lines)+'\n')
print(json.dumps({'recent':recent,'pooled50':out['pooled_quote_50days'],'pooled30books':out['pooled_book_30days']},indent=2))
