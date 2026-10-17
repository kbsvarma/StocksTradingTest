"""Post-result exploratory lane isolation; full replay, not ledger filtering.

Added only after seeing premium lane's loss in the frozen feed comparison.
This is not an independently validated recommendation or a revised primary test.
"""
import gzip
import json
from decimal import Decimal
import analyze as a
P=a.P
minutes=json.loads((P/'index_minutes.json').read_text())
bars={x['t']:x['close'] for x in minutes};means={x['t']:Decimal(x['mean']) for x in minutes}
cases=[('fees_only','opening',0),('plus1c','opening',.01),('plus2c','opening',.02),
       ('cap_plus1c','cap_cost',.01),('allcap_plus1c','allcap',.01),('allcap_plus2c','allcap',.02)]
results={};ledgers={}
for feed,refs in [('cf_close',None),('cf_opening_average',means)]:
    markets,_=a.build(bars,refs);results[feed]={};ledgers[feed]={}
    for lane in (a.r.Z2,a.r.Z3):
        results[feed][lane]={};ledgers[feed][lane]={}
        for label,mode,friction in cases:
            pool=a.allcap(markets) if mode=='allcap' else a.r.opportunities(markets,mode)[0]
            pool=[x for x in pool if x['lane']==lane]
            ledger,blocked=a.r.portfolio(pool,friction,start_cash=100)
            result=a.r.metrics(ledger);result['blocked']=blocked
            result['trades_per_calendar_day']=len(ledger)/((a.p.END-a.p.START)/86400)
            results[feed][lane][label]=result;ledgers[feed][lane][label]=ledger
        print(feed,lane,{c:{k:results[feed][lane][c][k] for k in ('trades','wins','losses','net')} for c,_,_ in cases},flush=True)
a.save('exploratory_lanes.json',{'post_hoc':True,'full_signal_replay':True,'results':results})
with gzip.open(P/'exploratory_lanes_ledgers.json.gz','wt') as f:json.dump(ledgers,f,separators=(',',':'))
