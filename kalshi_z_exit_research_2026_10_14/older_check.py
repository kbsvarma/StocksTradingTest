"""No retuning: test the early-selected rule on reused older crypto archives."""
import pickle,json,gzip,math
from pathlib import Path
from replay import simulate,metrics
P=Path(__file__).resolve().parent;ROOT=P.parent
selected=json.loads((P/'selection.json').read_text())['selected']
markets={m['ticker']:m for m in pickle.loads((ROOT/'kalshi_btc_eth_quant_2026_10_02/features.pickle').read_bytes())}
ledgers=json.load(gzip.open(ROOT/'kalshi_loss_review_2026_10_14/older_crypto_ledgers.json.gz'))['fees1c']
def bid(c,side,s):
 try:b,a=float(c['b'+s]),float(c['a'+s]);return (b if side==1 else 1-a) if 0<=b<=a<=1 else None
 except (TypeError,KeyError,ValueError):return None
out={};paths={}
for period,configs in ledgers.items():
 for kind in ['baseline','btc_z300']:
  name=period+'_'+kind;rows=[]
  for x in configs[kind]:
   m=markets[x['ticker']];ff={f['t']:f for f in m['features']};entry=ff[x['t']]
   r=dict(ticker=x['ticker'],asset=x['asset'],lane=x['lane'],entered=x['entered'],close=x['close'],settled=x['settled'],
      side=x['side'],quantity=x['quantity'],cost=x['entry']*x['quantity'],entry_fee=x['fee'],entry_friction=.01*x['quantity'],
      hold_net=x['net'],won=x['won'],entry_z=x['z'],entry_gate=3 if x['lane']=='premium_z3' or (kind=='btc_z300' and x['asset']=='BTC') else 2,
      ranges=m['ranges'],multiplier=m.get('multiplier',1),path=[])
   for t in range((int(x['entered'])//60+1)*60,int(x['close']),60):
    f=ff.get(t);rem=(x['close']-t)/60
    z=x['side']*(f['spot']/entry['ref']-1)/(f['sigma']*math.sqrt(rem)) if f else None
    r['path'].append(dict(t=t,z=z,z_frozen_sigma=x['side']*(f['spot']/entry['ref']-1)/(entry['sigma']*math.sqrt(rem)) if f else None,
       bid_close=bid(m['candles'].get(t),x['side'],'c'),bid_next_open=bid(m['candles'].get(t+60),x['side'],'o'),bid_delayed_open=bid(m['candles'].get(t+120),x['side'],'o')))
   rows.append(r)
  paths[name]=rows;out[name]={}
  for mode,slip,lag in [('exit1c',.01,0),('exit2c',.02,0),('delay60',.01,60)]:
   result=[simulate(r,selected,slip,lag) for r in rows]
   out[name][mode]=dict(metrics=metrics(result),assets={a:metrics([r for r in result if r['asset']==a]) for a in sorted({r['asset'] for r in result})},ledger=result,
      missing_source_minutes=sum(p['z'] is None for r in rows for p in r['path']))
  m=out[name]['exit1c']['metrics'];print(name,{k:m[k] for k in ['trades','losses_if_held','hold_net','net','delta','losers_improved','winners_cut','winners_turned_losses']})
(P/'older_results.json').write_text(json.dumps(out,indent=2)+'\n')
with gzip.open(P/'older_paths.json.gz','wt') as f:json.dump(paths,f)
