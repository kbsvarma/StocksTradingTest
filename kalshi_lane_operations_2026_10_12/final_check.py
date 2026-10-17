"""Read-only service, source preservation and paper-receipt audit."""
import hashlib,json,sqlite3,subprocess,time,requests,statistics
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent;LIVE=P.parent/'kalshi_revalidation_2026_09_12/live_v1';TEN=P.parent/'kalshi_tennis_paper_2026_10_11'
def save(n,x):(P/n).write_text(json.dumps(x,indent=2)+'\n')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def check(base,manifest):return {n:sha(base/n)==h for n,h in manifest.items()}
source=check(LIVE,json.loads((P/'source_hashes.json').read_text()));arms=check(LIVE/'state',json.loads((P/'arm_halt_hashes.json').read_text()))
cp=P.parent/'research_checkpoints/kalshi_stable_baseline_2026_09_28';baseline=check(cp/'source',json.loads((cp/'manifest.json').read_text())['sha256'])
tennis=check(TEN,json.loads((TEN/'deployment_manifest.json').read_text()))
receipt=json.loads((P/'receipt.json').read_text());services={}
for n in receipt['services']:
 services[n]=dict(x.split('=',1) for x in subprocess.check_output(['systemctl','--user','show',n,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True).splitlines())
s=json.loads((LIVE/'state/status.json').read_text());now=time.time()
health=dict(ts=now,et=datetime.fromtimestamp(now,ZoneInfo('America/New_York')).isoformat(),heartbeat_age=now-s['ts'],mode=s['mode'],armed=s['armed'],stopped=s['stopped'],halted=s['halted'],error=s['error'],limits={k:s[k] for k in ('contracts','max_contracts','max_risk','daily_loss_budget')},assets={a:{k:v[k] for k in ('enabled','blocked')} for a,v in s['assets'].items()},account=s['account'],active=s['active'],settled=s['settled'],attempts=s['attempts'],realized_pnl=s['realized_pnl'],services=services,services_unchanged=services==receipt['services'],source=source,arms=arms,baseline=baseline,tennis_files=tennis,ui_health={str(port):requests.get('http://'+('192.168.3.136' if port==8507 else '127.0.0.1')+':'+str(port)+('/_stcore/health' if port==8507 else '/'),timeout=5).status_code for port in (8507,8516)},ntp=subprocess.check_output(['timedatectl','show','-p','NTPSynchronized'],text=True).strip(),latest_fills=[{k:r.get(k) for k in ('ticker','asset','created_ts','status','filled','pnl')} for r in s['trades'] if float(r.get('filled',0))>0][-5:],observer=json.loads((P.parent/'kalshi_execution_retry_2026_10_02/observer_state/status.json').read_text()))
save('final_health.json',health)
assert all(source.values()) and all(arms.values()) and all(baseline.values()) and all(tennis.values()) and health['services_unchanged']
print('PRESERVATION',len(source),len(arms),len(baseline),len(tennis),'all pass','services unchanged',health['services_unchanged'],flush=True)
print('HEALTH',{k:health[k] for k in ('et','heartbeat_age','mode','armed','halted','error','limits','ui_health','ntp')},flush=True)
