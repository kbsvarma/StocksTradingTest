"""Complete actual-ledger exit grid, explicitly one-minute quote proxy."""
from core import *
def main():
 d=pickle.load(gzip.open(P/'inputs.pkl.gz'));rr=d['actual'];cfg=rules();paths={r['ticker']:path(r,d) for r in rr};out=[];ledgers={};losses=[r for r in rr if not r['won']]
 for c in cfg:
  rows=[exit_result(r,c,paths[r['ticker']]) for r in rr];ledgers[c['id']]=rows
  out.append(dict(config=c,all=metrics(rows),early=metrics([x for x in rows if next(r['close'] for r in rr if r['ticker']==x['ticker'])<SPLIT]),later=metrics([x for x in rows if next(r['close'] for r in rr if r['ticker']==x['ticker'])>=SPLIT]),assets={a:metrics([x for x in rows if x['asset']==a]) for a in ASSETS},lanes={l:metrics([x for x in rows if x['lane']==l]) for l in ['cushion_z2','premium_z3']}))
 ranked=sorted(out,key=lambda x:(-x['early']['delta'],x['early']['winners_cut'],x['config']['id']));selected=ranked[0]['config'];save('actual_minute_selection.json',dict(selected=selected,method='Early beforeOct8 only; later evaluated separately. Existing data previously explored, not a pristine holdout.'))
 save('actual_minute_results.json',out)
 with gzip.open(P/'actual_minute_ledgers.json.gz','wt') as f:json.dump(ledgers,f)
 save('loss_inventory.json',losses)
 print('selected',selected)
 for c in [selected]+[x for x in cfg if x['id'] in ['drop_2_g0_p0','drop_2_g120_p60','drop_1_g0_p0','price_0.1_g0_p0']]:
  v=next(x for x in out if x['config']==c);print(c['id'],json.dumps(v['all']), 'early/later',v['early']['delta'],v['later']['delta'],flush=True)
 print('best hindsight',[(x['config']['id'],x['all']['net'],x['all']['winners_cut']) for x in sorted(out,key=lambda x:-x['all']['net'])[:5]],flush=True)
if __name__=='__main__':main()
