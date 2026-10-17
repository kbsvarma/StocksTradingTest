"""Read-only verification of the migrated destination. Does not start services."""
from pathlib import Path
import json, subprocess, time, urllib.request
H=Path('/home/varma')
r={'checked_at': time.time(), 'units': {}, 'dashboards': {}}
for scope, names in [('system',['trendbot','trendbot-ibc','trendbot-xvfb','trendbot-dashboard']),('user',['advisor-terminal','advisor-quoted','advisor-exitwatch','advisor-intelligence.timer','kalshi-crypto-z2-live','kalshi-dashboard'])]:
    for n in names:
        cmd=['systemctl']+(['--user'] if scope=='user' else [])
        result=subprocess.check_output(cmd+['show',n,'-p','ActiveState','-p','SubState','-p','NRestarts','-p','UnitFileState'],text=True)
        r['units'][n]=dict(line.split('=',1) for line in result.splitlines() if '=' in line)
for port in [8503,8505,8507]:
    try:r['dashboards'][port]=urllib.request.urlopen(f'http://192.168.3.136:{port}/_stcore/health',timeout=5).read().decode()
    except Exception as e:r['dashboards'][port]=type(e).__name__
p=H/'kalshi_revalidation_2026_09_12/live_v1/state/status.json'
k=json.loads(p.read_text());r['kalshi']={key:k.get(key) for key in ['ts','mode','armed','halted','error','contracts','max_contracts','max_risk','daily_loss_budget','settled','attempts']};r['kalshi']['age_seconds']=time.time()-k['ts']
p=H/'trendbot/trend_bot/state/state.json';t=json.loads(p.read_text());r['trend']={key:t.get(key) for key in ['halt_entries','halt_reason','last_wake_status','last_wake']};r['trend']['heartbeat_age_seconds']=time.time()-(p.parent/'heartbeat').stat().st_mtime
p=H/'stockstest/advisor/data/quotes/latest.json';q=json.loads(p.read_text());r['advisor_quotes']={key:q.get(key) for key in ['as_of','ib_connected','market_open']}
r['advisor_pipeline']=json.loads((p.parent.parent/'pipeline_status.json').read_text())
r['migration_hold']=(H/'MIGRATION_HOLD').exists()
(H/'migration_2026_09_25/operational_verification.json').write_text(json.dumps(r,indent=2)+'\n')
print(json.dumps(r,indent=2))
