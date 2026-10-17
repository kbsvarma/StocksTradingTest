from pathlib import Path
import json,datetime as dt,numpy as np
P=Path(__file__).resolve().parent
x=json.load(open(P/'data/source_archive/hl_vs_kalshi_funding.json'));out={}
for coin,r in x.items():
 hd={int(t):v for t,v in r['hl']};matched=[];missing=[]
 for t,k in r['kalshi']:
  stamps=[int(t)-3600*i for i in range(8)]
  if all(s in hd for s in stamps):matched.append({'t':t,'hl':sum(hd[s] for s in stamps),'kalshi':k,'gap':sum(hd[s] for s in stamps)-k})
  else:missing.append(t)
 weeks={}
 for z in matched:
  d=dt.datetime.fromtimestamp(z['t'],dt.timezone.utc).date();w=(d-dt.timedelta(days=d.weekday())).isoformat();weeks.setdefault(w,[]).append(z)
 out[coin]={'matched_periods':len(matched),'excluded_incomplete':missing,'hl_annualized_pct':sum(z['hl'] for z in matched)/len(matched)*1095*100,'kalshi_annualized_pct':sum(z['kalshi'] for z in matched)/len(matched)*1095*100,'gap_annualized_pct':sum(z['gap'] for z in matched)/len(matched)*1095*100,'positive_gap_fraction':sum(z['gap']>0 for z in matched)/len(matched),'weekly':[{'week':w,'n':len(rr),'gap_apr':sum(z['gap'] for z in rr)/len(rr)*1095*100} for w,rr in weeks.items()],'matched':matched}
 print(coin,{k:v for k,v in out[coin].items() if k not in ['matched','weekly']})
(P/'funding_audit.json').write_text(json.dumps(out,indent=2)+'\n')
