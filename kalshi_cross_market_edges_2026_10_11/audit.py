"""Independent ledger identities, chronology, ownership and budget audit."""
from pathlib import Path
import gzip,json,math,collections
from datetime import datetime
from zoneinfo import ZoneInfo
P=Path(__file__).resolve().parent;ET=ZoneInfo('America/New_York')
checks=0;rows_seen=0;errors=[]
def ck(b,msg):
 global checks
 checks+=1
 if not b:errors.append(msg)
def day(t):return datetime.fromtimestamp(t,ET).date().isoformat()
for f in sorted(P.glob('*ledgers.json.gz')):
 data=json.load(gzip.open(f,'rt'))
 for key,rows in data.items():
  if key.startswith('POOL/'):continue
  if f.name.startswith('perp_'):
   last=0
   for r in rows:
    rows_seen+=1;ck(r['decision']<r['entry']<r['exit'],(f.name,key,'timing'));ck(r['entry']>=last,(f.name,key,'overlap'));last=r['exit']
    gross=r['side']*r['qty']*(r['exit_price']-r['price']);ck(abs(gross-r['gross'])<1e-8,(f.name,key,'gross'));ck(abs(r['net']-(gross+r['funding']-r['fees']-r['slippage']))<1e-8,(f.name,key,'net'));ck(r['qty']>=1 and r['qty']==int(r['qty']),(f.name,key,'integer'));ck(r['margin']<=r['capital']*.5+1e-7,(f.name,key,'margin'));ck(r['estimated_risk']<=.25+1e-7,(f.name,key,'risk'))
   continue
  seen=set();cash=100;active=[];real=collections.defaultdict(float)
  for r in sorted(rows,key=lambda r:(r['entered'],r['asset'],r['owner'],r['ticker'],r['side'])):
   rows_seen+=1;ck(r['owner'] not in seen,(f.name,key,'duplicate'));seen.add(r['owner']);ck(r['t']<r['entered']<r['settled'],(f.name,key,'timing'))
   ck(r['entry']<=r['cap']+1e-8,(f.name,key,'cap'));fe=math.ceil((.07*r['mult']*r['qty']*r['entry']*(1-r['entry'])-1e-12)*10000)/10000
   ck(abs(fe-r['fee'])<1e-8,(f.name,key,'fee'));ck(abs(r['net']-(r['qty']*r['payout']-r['qty']*(r['entry']+r['friction'])-fe))<1e-8,(f.name,key,'net'))
   if 'source_t' in r:ck(r['source_t']<=r['t']-int(key.split('/lag')[1].split('/')[0]),(f.name,key,'source_lag'));ck(r['prematch_t']<r['source_t'],(f.name,key,'prior'))
   if f.name.startswith('tennis'):
    ck(r['date']==day(r['entered']),(f.name,key,'actual_entry_date'))
    if '/selection/' in key:ck(r['settled']<1791000000,(f.name,key,'selection_settlement_boundary'))
   if 'source_last' in r:ck(r['source_last']<=r['t']-r['lag_minutes']*60,(f.name,key,'weather_lag'))
   for a in active:
    if a['settled']<=r['entered']:cash+=a['qty']*a['payout'];real[day(a['settled'])]+=a['net']
   active=[a for a in active if a['settled']>r['entered']];cost=sum(a['cost'] for a in active)+r['cost'];qty=sum(a['qty'] for a in active)+r['qty']
   ck(cost<=25+1e-7 and qty<=25,(f.name,key,'exposure'));ck(real[day(r['entered'])]-cost>=-20-1e-7,(f.name,key,'daily_risk'));cash-=r['cost'];ck(cash>=2-1e-7,(f.name,key,'cash'));active.append(r)
summary=dict(rows=rows_seen,assertions=checks,errors=errors)
(P/'audit.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(dict(rows=rows_seen,assertions=checks,errors=len(errors))))
assert not errors,errors[:10]
