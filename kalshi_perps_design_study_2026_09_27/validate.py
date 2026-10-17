from pathlib import Path
p=Path(__file__).resolve().parent;s=(p/'backtest.py').read_text();ns={'__file__':str(p/'backtest.py')};exec(s[:s.index('\nI={')]+s[s.index('\ndef leg_pnl'):s.index('\ndef finish')],ns);globals().update(ns)
errors=[];controls=[]
for f in (P/'trades').glob('*.json'):
 rows=load(f);prev=0;longsum=shortsum=0
 for r in rows:
  if not r['entry_ts']<r['exit_ts'] or r['entry_ts']<prev:errors.append((f.name,'chronology'))
  prev=r['exit_ts'];legs=r.get('legs') or [r['leg']]
  if sum(l['count']*l['entry'] for l in legs)>50+1e-9:errors.append((f.name,'notional'))
  total=sum(leg_pnl(l,r['exit_ts'])['net'] for l in legs)
  if abs(total-r['net'])>1e-8:errors.append((f.name,'pnl'))
  for l in legs:
   if l['signal_ts']>=l['entry_ts']:errors.append((f.name,'signal'))
   if 'stop' in l and l['entry']*l['count']*l['stop']>1+1e-8:errors.append((f.name,'risk_sizing'))
  if 'leg' in r:
   l=r['leg'];q=Q[l['ticker']][l['entry_ts']]
   for side in [1,-1]:
    ll=dict(l,side=side,entry=q[1] if side==1 else q[0]);v=leg_pnl(ll,r['exit_ts'])['net']
    if side==1:longsum+=v
    else:shortsum+=v
 if rows and 'leg' in rows[0]:controls.append(dict(name=f.stem,net=sum(r['net'] for r in rows),same_windows_long=longsum,same_windows_short=shortsum,best3=sorted([r['net'] for r in rows],reverse=True)[:3]))
assert not errors,errors
(P/'validation.json').write_text(json.dumps({'errors':errors,'files':len(list((P/'trades').glob('*.json')))},indent=2));(P/'controls.json').write_text(json.dumps(controls,indent=2))
for r in sorted(controls,key=lambda r:r['net'],reverse=True)[:12]:print(r)
