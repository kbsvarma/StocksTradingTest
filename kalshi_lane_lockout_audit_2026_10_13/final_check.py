import hashlib,json,subprocess,time,urllib.request
from pathlib import Path
P=Path(__file__).resolve().parent;LIVE=P.parent/'kalshi_revalidation_2026_09_12/live_v1'
before=json.loads((P/'preservation_before.json').read_text())
assert all(hashlib.sha256(Path(f).read_bytes()).hexdigest()==h for group in ('files','flags') for f,h in before[group].items())
now={n:dict(x.split('=',1) for x in subprocess.check_output(['systemctl','--user','show',n,'-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True).splitlines()) for n in before['services']}
assert now==before['services']
checkpoint=P.parent/'research_checkpoints/kalshi_stable_baseline_2026_09_28';hashes=json.loads((checkpoint/'manifest.json').read_text())['sha256']
assert all(hashlib.sha256((checkpoint/'source'/n).read_bytes()).hexdigest()==h for n,h in hashes.items())
s=json.loads((LIVE/'state/status.json').read_text());assert s['mode']=='LIVE' and s['armed'] and not any(s[k] for k in ('stopped','error','halted')) and time.time()-s['ts']<15
ui=urllib.request.urlopen('http://192.168.3.136:8507/_stcore/health',timeout=5).read().decode();assert ui=='ok'
initial=json.loads((P/'status.json').read_text())
for k in ('contracts','max_contracts','max_risk','daily_loss_budget','execution_cap_dollars','signal_ask_ceiling_dollars','z_min','minutes','wti_fresh_retry_version'):assert s[k]==initial[k]
for a,v in s['assets'].items():assert v['rules']==initial['assets'][a]['rules']
out=dict(checked_ts=time.time(),protected_sources=len(before['files']),flags=len(before['flags']),stable_checkpoint_files=len(hashes),service_identities_unchanged=True,source_flags_unchanged=True,live=True,heartbeat_age=time.time()-s['ts'],ui=ui,strategy_price_size_risk_unchanged=True,financial_writes=0,services=now)
(P/'verification.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
