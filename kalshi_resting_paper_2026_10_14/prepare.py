"""Capture frozen read-only production settings; write only the experiment."""
import hashlib,json,subprocess,time
from pathlib import Path
P=Path(__file__).resolve().parent
L=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1')
def capture():
    names=('kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-execution-observer.service','kalshi-tennis90-paper.service')
    return dict(ts=time.time(),hashes={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in L.iterdir() if f.suffix in ('.py','.json') or f.name=='dashboard.css'},flags={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in (L/'state').iterdir() if f.is_file() and f.name.startswith(('ARM','HALT'))},services={n:subprocess.check_output(['systemctl','--user','show',n,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True) for n in names},baseline={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in Path('/home/varma/research_checkpoints/kalshi_stable_baseline_2026_09_28/source').glob('*') if f.is_file()})
if __name__=='__main__':
    c=capture();(P/'preservation_before.json').write_text(json.dumps(c,indent=2)+'\n')
    s=json.loads((L/'state/status.json').read_text())
    assert time.time()-s['ts']<15 and not s.get('halted') and not s.get('error')
    for n in ('lanes.py','price_rules.py'):
        assert (P/'frozen_source'/n).read_bytes()==(L/n).read_bytes(),n+' not current'
    cfg=dict(version=1,paper_only=True,contracts=5,assets=list(s['assets']),variants=[dict(name='rest_3c_10s',discount='0.03'),dict(name='rest_1c_10s',discount='0.01')],rest_seconds=10,acceptance_delay_seconds=.25,cancel_latency_seconds=.25,max_public_requests_per_second=2,max_book_gap_seconds=4,max_event_delivery_seconds=2,trade_publication_grace_seconds=15,max_trade_pages=5,entry_days=14,shared_max_contracts=25,shared_exposure='25',shared_daily_loss='20',paper_cash_by_route=s['account']['balances'],live_source_hashes=c['hashes'],live_flags=c['flags'],frozen_source_hashes={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in (P/'frozen_source').glob('*.py')},comparison='first qualifying observed signal per market, two resting variants and synchronous one-shot paper IOC; actual live ledger separate',no_automatic_live_promotion=True)
    assert not (P/'frozen_config.json').exists(),'Refuse to replace frozen config'
    (P/'frozen_config.json').write_text(json.dumps(cfg,indent=2)+'\n')
    (P/'review_state.json').write_text(json.dumps(dict(first_fill_reported=False,first_settlement_reported=False,preliminary_20_reported=False,final_reported=False),indent=2)+'\n')
    print(json.dumps(dict(prepared=True,assets=cfg['assets'],paper_cash=cfg['paper_cash_by_route'],services=c['services'])))
