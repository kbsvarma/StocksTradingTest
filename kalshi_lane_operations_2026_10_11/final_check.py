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
db=sqlite3.connect(f'file:{TEN}/paper.sqlite?mode=ro',uri=True);db.row_factory=sqlite3.Row
counts={r[0]:db.execute('SELECT count(*) FROM "'+r[0]+'"').fetchone()[0] for r in db.execute("select name from sqlite_master where type='table'")}
ev=[dict(r) for r in db.execute('SELECT event,series,start,updated,prior,last_poll,done FROM events')];receipts=[dict(r) for r in db.execute('SELECT id,url,started,received,duration,status,error FROM receipts')]
urls={}
for r in receipts:urls.setdefault(r['url'],[]).append(r)
stats={}
for url,rr in urls.items():
 good=sorted(r['received'] for r in rr if r['status']==200 and not r['error']);gaps=[b-a for a,b in zip(good,good[1:])]
 stats[url]=dict(count=len(rr),errors=sum(bool(r['error']) or r['status']!=200 for r in rr),first=min(good) if good else None,last=max(good) if good else None,max_gap=max(gaps) if gaps else None)
ten_status=json.loads((TEN/'status.json').read_text());save('tennis_status.json',ten_status)
paper=dict(ts=time.time(),counts=counts,clock=ten_status['clock'],heartbeat_age=time.time()-ten_status['heartbeat'],events=ev,http=stats,integrity=db.execute('PRAGMA quick_check').fetchone()[0],prematch_priors_due=sum(r['start']-1800<=now for r in ev),missing_due_priors=sum(r['start']-1800<=now and not r['prior'] for r in ev),note='No active score polls due yet. HTTP catalogue delays are not point feed delays. No feed, book or fill samples exist yet.')
save('tennis_audit.json',paper);db.close()
print('PRESERVATION',len(source),len(arms),len(baseline),len(tennis),'all pass','services unchanged',health['services_unchanged'],flush=True)
print('HEALTH',{k:health[k] for k in ('et','heartbeat_age','mode','armed','halted','error','limits','ui_health','ntp')},flush=True)
print('TENNIS',paper['counts'],'priors due',paper['prematch_priors_due'],'heartbeat',paper['heartbeat_age'],'integrity',paper['integrity'],flush=True)
