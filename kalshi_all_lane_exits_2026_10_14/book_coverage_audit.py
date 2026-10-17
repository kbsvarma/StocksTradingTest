from core import *
import sqlite3
def main():
 rows=json.loads((P/'actual_entries.json').read_text());old=json.loads((R/'kalshi_z_time_research_2026_10_03/book_coverage.json').read_text());old['overlap_actual_holding']=sum(r['entered']<=old['max_ts'] and r['close']>=old['min_ts'] for r in rows)
 db=sqlite3.connect('file:'+str(R/'kalshi_resting_paper_2026_10_14/paper.sqlite')+'?mode=ro',uri=True);db.execute('pragma query_only=on');out=[]
 for r in rows:
  path='/markets/'+r['ticker']+'/orderbook';tt=[x[0] for x in db.execute('select received from receipts where path=? and status=200 and received>=? and received<? order by received',(path,r['entered'],r['close']))]
  if tt:out.append(dict(ticker=r['ticker'],won=r['won'],books=len(tt),first=tt[0],last=tt[-1],holding_start=r['entered'],holding_end=r['close'],max_gap=max([tt[0]-r['entered']]+[b-a for a,b in zip(tt,tt[1:])]+[r['close']-tt[-1]])))
 db.close();save('book_coverage_audit.json',dict(older_book_archive=old,resting_observer_partial_coverage=out,continuous_depth_source='kalshi_btc_eth_quant_2026_10_02/forward_state/data; see prior capture_manifest and depth_grid provenance',continuous_actual_cohort=20,continuous_actual_losses=2,entry_observer_limit='Records opposite-side entry asks for a short eligibility window; cannot be used as held-side continuous exit bids.'))
 print(json.dumps(dict(old_overlap=old['overlap_actual_holding'],partial_resting_markets=len(out),partial_resting_losses=sum(not r['won'] for r in out),largest_span=max((x['last']-x['first'] for x in out),default=0))))
if __name__=='__main__':main()
