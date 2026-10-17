"""Bounded public GET check for October 18-19 NFL; no order or service APIs."""
import json,time,hashlib,collections,datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
P=Path(__file__).resolve().parent
RAW=P/'raw';RAW.mkdir(exist_ok=True)
ET=ZoneInfo('America/New_York')
BASE='https://external-api.kalshi.com/trade-api/v2'
session=requests.Session();last=0;receipts=[]
def get(url,params=None):
    global last
    time.sleep(max(0,last+1-time.monotonic()));last=time.monotonic()
    sent=time.time();r=session.get(url,params=params,timeout=20);received=time.time()
    r.raise_for_status();body=r.json()
    rec=dict(url=r.url,sent=sent,received=received,latency_ms=(received-sent)*1000,status=r.status_code,headers={k:v for k,v in r.headers.items() if k.lower() in ('date','age','cache-control','last-modified')},response=body)
    name=hashlib.sha256((r.url+str(sent)).encode()).hexdigest()+'.json'
    (RAW/name).write_text(json.dumps(rec));receipts.append({k:v for k,v in rec.items() if k!='response'})
    return body
def canon(x):return {'JAC':'JAX','WSH':'WAS','LA':'LAR'}.get(x,x)
def sides(book):
    b=book.get('orderbook_fp',{})
    out={}
    for side,opp in [('yes','no'),('no','yes')]:
        own=sorted([(float(p),float(n)) for p,n in b.get(side+'_dollars',[]) if float(n)>0],reverse=True)
        asks=sorted([(round(1-float(p),4),float(n)) for p,n in b.get(opp+'_dollars',[]) if float(n)>0])
        remaining=5.;cost=0.;worst=None
        for p,n in asks:
            q=min(remaining,n);cost+=p*q;remaining-=q;worst=p
            if remaining<1e-9:break
        out[side]=dict(bid=own[0][0] if own else None,ask=asks[0][0] if asks else None,top_ask_depth=asks[0][1] if asks else 0,
                       five_vwap=cost/5 if remaining<1e-9 else None,five_worst_price=worst if remaining<1e-9 else None,
                       spread=round(asks[0][0]-own[0][0],4) if own and asks else None)
    return out
def main():
    out=dict(asof=time.time(),games=[],unmatched=[],errors=[])
    markets=[];cursor=''
    while True:
        x=get(BASE+'/markets',dict(series_ticker='KXNFLGAME',status='open',limit=1000,**({'cursor':cursor} if cursor else {})))
        markets+=x['markets'];cursor=x.get('cursor','')
        if not cursor:break
    out['series']=get(BASE+'/series/KXNFLGAME')['series']
    out['fee_changes']=get(BASE+'/series/fee_changes',{'series_ticker':'KXNFLGAME'})
    groups=collections.defaultdict(list)
    for m in markets:groups[m['event_ticker']].append(m)
    for date in ('20261017','20261018','20261019'):
        schedule=get('https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard',{'dates':date,'limit':100})
        for g in schedule.get('events',[]):
            comp=g['competitions'][0];teams={r['homeAway']:canon(r['team']['abbreviation']) for r in comp['competitors']}
            matches=[(ev,ms) for ev,ms in groups.items() if ev.startswith('KXNFLGAME-26OCT'+date[-2:]) and {canon(m['ticker'].rsplit('-',1)[1]) for m in ms}==set(teams.values())]
            if len(matches)!=1:out['unmatched'].append(dict(id=g['id'],name=g['name'],matches=len(matches)));continue
            ev,ms=matches[0]
            row=dict(event=ev,espn_id=g['id'],name=g['name'],start=g['date'],start_et=datetime.datetime.fromisoformat(g['date'].replace('Z','+00:00')).astimezone(ET).isoformat(),teams=teams,status=g['status'],volume_contracts=sum(float(m.get('volume_fp') or 0) for m in ms),outcomes=[])
            for m in ms:
                try:
                    books=get(BASE+'/markets/'+m['ticker']+'/orderbook',{'depth':100})
                    row['outcomes'].append(dict(ticker=m['ticker'],title=m['title'],team=canon(m['ticker'].rsplit('-',1)[1]),volume_contracts=float(m.get('volume_fp') or 0),books=sides(books),rules_secondary=m.get('rules_secondary'),close_time=m['close_time']))
                except Exception as exc:out['errors'].append(dict(ticker=m['ticker'],error=str(exc)))
            out['games'].append(row);print('GAME',ev,row['start_et'],row['volume_contracts'],flush=True)
    out['games'].sort(key=lambda g:(g['start'],g['event']))
    out['request_receipts']=receipts
    (P/'current.json').write_text(json.dumps(out,indent=2)+'\n')
    print('COMPLETE',len(out['games']),'games',len(out['unmatched']),'unmatched',len(out['errors']),'errors',flush=True)
if __name__=='__main__':main()
