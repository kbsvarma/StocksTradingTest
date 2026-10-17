"""Readiness check by default. --execute only arms after observed live checks pass."""
import argparse,datetime as dt,json,os,subprocess,sys,time
from pathlib import Path
from core import digest,ts,dec
from executor import bundle_hash
from kalshi_client import KalshiClient,Credentials
P=Path(__file__).resolve().parent
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--execute',action='store_true');a=ap.parse_args()
    cfg=json.loads((P/'config.json').read_text());s=json.loads((P/'status.json').read_text())
    reasons=list(s.get('blockers',[]))
    if time.time()-s['heartbeat']>15:reasons.append('observer heartbeat stale')
    if s['config_hash']!=digest(cfg):reasons.append('frozen configuration mismatch')
    if not s.get('operational_checks_pass'):reasons.append('required live verification incomplete')
    if not s.get('ntp') or s.get('dropped_frames') or s.get('frame_errors'):reasons.append('clock or raw receipt failure')
    if (P/'HALT').exists():reasons.append('manual halt')
    if time.time()>=ts(cfg['entry_cutoff']):reasons.append('weekend cutoff')
    c=KalshiClient(Credentials.load(),base_url='https://external-api.kalshi.com',dry_run=True,timeout=3)
    b=c._request('GET','/portfolio/balance',params=dict(subaccount=0,exchange_index=0));cash=dec(b['balance_dollars'])
    if cash<dec('52'):reasons.append('insufficient cash for NFL max exposure plus existing-bot buffer')
    exchange=c.exchange_status()
    if not exchange.get('exchange_active') or not exchange.get('trading_active'):reasons.append('exchange inactive')
    out=dict(ready=not reasons,blockers=sorted(set(reasons)),contracts=5,free_cash=str(cash),activated=False)
    if a.execute and not reasons:
        r=subprocess.run([sys.executable,'-m','unittest','discover','-s',str(P),'-p','test_*.py'],cwd=P,capture_output=True,text=True)
        (P/'activation_tests.log').write_text(r.stdout+r.stderr)
        if r.returncode:raise SystemExit('Tests failed; not armed. See activation_tests.log')
        # Recheck freshness after tests; no network or slow work after this point.
        s=json.loads((P/'status.json').read_text())
        if time.time()-s['heartbeat']>15 or not s.get('operational_checks_pass'):raise SystemExit('Verification expired; not armed')
        token=dict(at=time.time(),expires=ts(cfg['entry_cutoff']),config_hash=digest(cfg),bundle_hash=bundle_hash(P),verified_report=s)
        tmp=P/'ARMED.tmp';tmp.write_text(json.dumps(token,indent=2));os.chmod(tmp,0o600);tmp.replace(P/'ARMED.json');out['activated']=True
    print(json.dumps(out,indent=2))
    if a.execute and reasons:raise SystemExit(2)
if __name__=='__main__':main()
