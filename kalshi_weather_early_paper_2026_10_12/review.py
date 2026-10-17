"""Read-only consistent paper snapshot, independent accounting and receipt audit."""
import datetime as dt
from decimal import Decimal,ROUND_CEILING
import hashlib,json,sqlite3,statistics,subprocess,time,zlib,sys
from pathlib import Path
from zoneinfo import ZoneInfo
from engine import reconstruct,book,payout,choose,SCENARIOS,ts,day
from store import status

P=Path(__file__).resolve().parent
def quantiles(values):
    a=sorted(values)
    return {'n':len(a),'median':statistics.median(a),'p95':a[int(.95*(len(a)-1))],'max':max(a)} if a else {'n':0}
def main():
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y_%m_%d_%H%M%S');r=P/'reviews'/stamp;r.mkdir(parents=True)
    src=sqlite3.connect((P/'paper.sqlite').as_uri()+'?mode=ro',uri=True);dst=sqlite3.connect(r/'snapshot.sqlite');src.backup(dst);dst.close();src.close()
    now=time.time();db=sqlite3.connect((r/'snapshot.sqlite').as_uri()+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row
    def rows(q,args=()):return [dict(x) for x in db.execute(q,args)]
    cache={}
    def payload(h):
        if h not in cache:
            raw=zlib.decompress(db.execute('SELECT body FROM payloads WHERE hash=?',(h,)).fetchone()[0]);assert hashlib.sha256(raw).hexdigest()==h;cache[h]=json.loads(raw)
        return cache[h]
    cfg=json.loads((P/'frozen_config.json').read_text());training=json.loads((P/'training.json').read_text())
    for name,expected in cfg['frozen_files'].items():assert hashlib.sha256((P/name).read_bytes()).hexdigest()==expected,name
    meta={x['key']:json.loads(x['value']) for x in rows('SELECT * FROM meta')}
    receipts={x['id']:x for x in rows('SELECT * FROM receipts')};decisions=rows('SELECT * FROM decisions');attempts={x['id']:x for x in rows('SELECT * FROM attempts')};positions=rows('SELECT * FROM positions')
    service=subprocess.check_output(['systemctl','--user','show','kalshi-weather-early-paper.service','-p','ActiveState','-p','SubState','-p','MainPID','-p','NRestarts'],text=True)
    out={'captured_utc':dt.datetime.fromtimestamp(now,dt.timezone.utc).isoformat(),'captured_et':dt.datetime.fromtimestamp(now,ZoneInfo('America/New_York')).isoformat(),'service':service,'heartbeat_age':now-meta['heartbeat'],'clock':meta.get('clock'),'integrity':db.execute('PRAGMA integrity_check').fetchone()[0],'elapsed_hours':(now-meta['started'])/3600,'config_hash':meta['config_hash'],'portfolios':{},'financial_orders':0}
    assert out['integrity']=='ok'
    out['trades']=positions
    for scenario in SCENARIOS:
        ps=[p for p in positions if p['scenario']==scenario];ss=sorted([p for p in ps if p['settled'] is not None],key=lambda p:(p['settled'],p['id']));aa=[a for a in attempts.values() if a['scenario']==scenario]
        eq=peak=dd=Decimal(0)
        for p in ss:eq+=Decimal(str(p['net']));peak=max(peak,eq);dd=max(dd,peak-eq)
        out['portfolios'][scenario]={'attempts':sum(a['status']!='blocked' for a in aa),'blocked':sum(a['status']=='blocked' for a in aa),'filled':len(ps),'settled':len(ss),'open':len(ps)-len(ss),'pending':sum(a['status']=='pending' for a in aa),'wins':sum(p['payout']==p['qty'] for p in ss),'losses':sum(p['payout']==0 for p in ss),'net':float(eq),'drawdown':float(dd),'fees':sum(p['fee'] for p in ss),'friction':sum(p['friction'] for p in ss),'by_city':{c:{'trades':sum(p['series']==c for p in ps),'settled':sum(p['series']==c for p in ss),'net':sum(p['net'] for p in ss if p['series']==c)} for c in cfg['cities']}}
    qualified=[]
    for d in decisions:
        if d['reason']!='eligible':continue
        data=json.loads(d['data']);pick=data['pick'];s=data['source'];sr=receipts[s['receipt']]
        point=next(x for x in payload(sr['hash'])['timeseries'] if x['t']/1000==s['source_t'])
        cr=receipts[data['config_receipt']];rr,why=reconstruct(point,payload(cr['hash'])['calibrations'],sr['received'])
        assert why is None and rr['forecast']==s['forecast'] and point.get('v') is None
        assert cr['received']<=d['created'] and sr['received']<=d['created'] and not s['censored']
        assert 0<=d['created']-sr['received']<=10 and s['first_seen']<=sr['received']
        post=receipts[data['postbook_receipt']];books={x['ticker']:x for x in payload(post['hash'])['orderbooks']};assert sr['received']<=post['started']<=post['received']<=d['created']
        vr=receipts[data['volume_receipt']];boundary=data['volume_boundary'];volumes={m['market_ticker']:sum(float(c.get('volume_fp') or 0) for c in m['candlesticks'] if boundary-600<c['end_period_ts']<=boundary) for m in payload(vr['hash'])['markets']};assert vr['received']<=d['created']
        marketr=receipts[data['catalogue_receipt']];ms=[m for m in payload(marketr['hash'])['markets'] if m['event_ticker']==d['event']]
        found,_=choose(ms,books,s,training,d['created'],volumes,cfg);assert found==pick
        pubs=rows('SELECT * FROM publications WHERE series=? AND source=? ORDER BY received,id',(d['series'],s['source_t']))
        prebook=None
        if data['prebook_receipt']:
            pre=receipts[data['prebook_receipt']];preb=next((x for x in payload(pre['hash'])['orderbooks'] if x['ticker']==pick['ticker']),{})
            prebook,_=book(preb,pick['side'],pick['cap'],cfg['contracts'])
            assert pre['received']<=sr['started']
        qualified.append({'decision_id':d['id'],'event':d['event'],'ticker':pick['ticker'],'side':pick['side'],'source_first_seen':s['first_seen'],'created':d['created'],'source_to_decision_seconds':d['created']-s['first_seen'],'signal_ask':pick['ask'],'pre_source_ask':prebook['ask'] if prebook else None,'canonical_first_seen':pubs[0]['received'] if pubs else None,'publication_lead_seconds':pubs[0]['received']-s['first_seen'] if pubs else None,'forecast':s['forecast'],'canonical':pubs[0]['value'] if pubs else None})
    out['qualified_signals']=qualified
    checks=[]
    for p in positions:
        a=attempts[p['attempt']];pick=json.loads(a['data']);bs=rows('SELECT * FROM book_checks WHERE attempt=? ORDER BY phase',(a['id'],));assert len(bs)==2
        vals=[];brs=[]
        for b in bs:
            br=receipts[b['receipt']];raw=next(x for x in payload(br['hash'])['orderbooks'] if x['ticker']==p['ticker']);v,why=book(raw,p['side'],pick['cap'],p['qty']);assert why is None and b['reason'] is None
            assert a['due']<=br['started']<=br['received']<=a['due']+cfg['execution_deadline']
            assert br['duration']<=5 and float(json.loads(br['headers']).get('Age') or 0)<=1
            vals.append(v);brs.append(br)
        assert brs[1]['started']-brs[0]['received']>=1 and p['entered']<pick['close']
        mr=receipts[json.loads(bs[0]['data'])['market_receipt']];m=next(x for x in payload(mr['hash'])['markets'] if x['ticker']==p['ticker']);assert m['status']=='active' and not m.get('settlement_ts') and ts(m['close_time'])>p['entered']
        assert p['price']==max(pick['ask'],*(v['vwap'] for v in vals))
        price=Decimal(str(p['price']));f=(Decimal('.07')*p['qty']*price*(1-price)).quantize(Decimal('.0001'),rounding=ROUND_CEILING)
        assert f==Decimal(str(p['fee'])) and abs(float(price*p['qty']+f+Decimal(str(p['friction'])))-p['cost'])<1e-9
        if p['settled'] is not None:
            sr=receipts[p['settlement_receipt']];m=next(x for x in payload(sr['hash'])['markets'] if x['ticker']==p['ticker']);assert payout(m,p['side'])*p['qty']==p['payout'];assert abs(p['net']-(p['payout']-p['cost']))<1e-9
        checks.append({'attempt':a['id'],'scenario':a['scenario'],'decision_to_fill_seconds':p['entered']-a['created'],'minimum_depth':min(v['depth'] for v in vals)})
    out['fill_checks']=checks
    first={};revisions=[]
    for s in rows('SELECT * FROM sources ORDER BY received,id'):
        k=(s['series'],s['source']);data=json.loads(s['data'])
        if k not in first:first[k]={**s,'parsed':data}
        elif data['forecast']!=first[k]['parsed']['forecast']:revisions.append({'series':s['series'],'source':s['source'],'received':s['received'],'initial':first[k]['parsed']['forecast'],'new':data['forecast']})
    arrivals=[];matches=[]
    for (series,source),s in first.items():
        d=s['parsed'];pubs=rows('SELECT * FROM publications WHERE series=? AND source=? ORDER BY received,id',(series,source))
        if not s['censored']:
            prior=d.get('previous_poll') or {};arrivals.append({'series':series,'source':source,'first_seen':s['received'],'pending':d['pending'],'vendor_delay_upper':s['received']-d['vendor_received'],'vendor_delay_lower':max(0,prior.get('started',0)-d['vendor_received'])})
        if d['pending'] and not s['censored'] and pubs:matches.append({'series':series,'source':source,'forecast':d['forecast'],'canonical':pubs[0]['value'],'matches':abs(d['forecast']-pubs[0]['value'])<=.011,'lead_seconds':pubs[0]['received']-s['received']})
    out['source_arrivals']=arrivals;out['reconstruction_checks']=matches;out['forecast_revisions']=revisions
    out['feed']={'new_complete_source_minutes':len(arrivals),'vendor_to_first_seen_upper_seconds':quantiles([x['vendor_delay_upper'] for x in arrivals]),'pending_to_observed_publication_seconds':quantiles([x['lead_seconds'] for x in matches]),'canonical_matches':sum(x['matches'] for x in matches),'canonical_comparisons':len(matches),'forecast_revision_records':len(revisions)}
    cycles=rows('SELECT * FROM cycles ORDER BY created,id');paired=[];seen=set();comparisons=0
    for cycle in cycles:
        if not cycle['pre_receipt']:continue
        data=json.loads(cycle['data']);pre=receipts[cycle['pre_receipt']];post=receipts[cycle['post_receipt']]
        before={x['ticker']:x for x in payload(pre['hash'])['orderbooks']};after={x['ticker']:x for x in payload(post['hash'])['orderbooks']}
        comparisons+=len(before.keys() & after.keys())
        for series,source in data['sources'].items():
            key=(series,source['source_t'])
            if key in seen or source['first_seen_receipt']!=source['receipt']:continue
            sr=receipts[source['receipt']];assert pre['received']<=sr['started']<=sr['received']<=post['started']
            choices=[]
            for m in data['markets'].get(series,[]):
                q,_=book(before.get(m['ticker'],{}),'yes');a,_=book(after.get(m['ticker'],{}),'yes')
                if q and a and .1<=(q['bid']+q['ask'])/2<=.9:choices.append((abs((q['bid']+q['ask'])/2-.5),m['ticker'],q,a))
            if not choices:continue
            _,ticker,b,a=min(choices,key=lambda x:(x[0],x[1]));seen.add(key)
            paired.append({'series':series,'source':source['source_t'],'ticker':ticker,'before_ask':b['ask'],'after_ask':a['ask'],'yes_ask_change':round(a['ask']-b['ask'],4),'source_receipt_to_after_book_seconds':post['received']-sr['received'],'forecast':source['forecast'],'limitation':'Diagnostic near-50c strike chosen from preceding book; not a qualified trade or proof that news was unpriced.'})
    out['paired_book_observations']=paired
    out['book_collection']={'cycles':len(cycles),'matched_ticker_book_pairs':comparisons,'new_source_near_50c_pairs':len(paired),'source_to_after_book_seconds':quantiles([p['source_receipt_to_after_book_seconds'] for p in paired]),'pairs_with_ask_movement':sum(abs(p['yes_ask_change'])>1e-9 for p in paired)}
    out['decision_counts']=rows('SELECT reason,COUNT(*) n FROM decisions GROUP BY reason');out['execution_failures']=rows("SELECT scenario,status,reason,COUNT(*) n FROM attempts WHERE status!='filled' GROUP BY scenario,status,reason")
    out['http_errors']=rows('SELECT id,url,received,status,error FROM receipts WHERE error IS NOT NULL OR status!=200')
    out['http_durations']=quantiles([x['duration'] for x in receipts.values()]);out['receipt_count']=len(receipts)
    preserved=json.loads((P/'preservation_before.json').read_text());bad={f:expected for f,expected in preserved['files'].items() if hashlib.sha256(Path(f).read_bytes()).hexdigest()!=expected}
    current=subprocess.check_output(['systemctl','--user','show','kalshi-crypto-z2-live.service','kalshi-dashboard.service','kalshi-tennis90-paper.service','-p','Id','-p','ActiveState','-p','MainPID','-p','NRestarts'],text=True)
    out['existing_bots']={'protected_files':len(preserved['files']),'hash_mismatches':bad,'service_identity_unchanged':current==preserved['services']}
    out['milestones']={'preliminary20':out['portfolios']['early']['settled']>=20,'main14days100':now-meta['started']>=14*86400 and out['portfolios']['early']['settled']>=100,'entry_cutoff':meta['started']+14*86400}
    out['limitations']=['Depth-supported paper fills, not actual exchange fills.','Public first-arrival times are bounded by polling; source clock not independently verified.','Unsettled holdings contribute no realized P&L.','Forward model differs from historical receipt-margin sensitivities; no historical profit imported.']
    out['snapshot_sha256']=hashlib.sha256((r/'snapshot.sqlite').read_bytes()).hexdigest()
    out['snapshot_retained']='--keep-snapshot' in sys.argv
    out['receipt_record_location']=str(P/'paper.sqlite')
    (r/'audit.json').write_text(json.dumps(out,indent=2)+'\n')
    lines=['WEATHER EARLY PAPER REVIEW',out['captured_et'],'','Scenario | Attempts | Fills | Settled | W/L | Net after fees/friction | Open/pending']
    for s,v in out['portfolios'].items():lines.append(f"{s} | {v['attempts']} | {v['filled']} | {v['settled']} | {v['wins']}/{v['losses']} | ${v['net']:+.4f} | {v['open']}/{v['pending']}")
    lines+=['','TRADES (all retained in audit.json)']
    for p in positions:lines.append(f"{p['scenario']} {p['ticker']} {p['side']} {p['qty']} @ {p['price']:.4f}; fee {p['fee']:.4f}; friction {p['friction']:.4f}; net {p['net'] if p['net'] is not None else 'UNSETTLED'}")
    if not positions:lines.append('No paper fills yet; $0 realized is not evidence of an edge.')
    lines+=['','FEED',json.dumps(out['feed'],indent=2),'','PAIRED LIVE BOOKS',json.dumps(out['book_collection'],indent=2),'','EXECUTION',json.dumps(out['execution_failures'],indent=2),'','PRESERVATION',json.dumps(out['existing_bots'],indent=2),'','LIMITATIONS',*out['limitations']]
    (r/'REPORT.txt').write_text('\n'.join(lines)+'\n');(P/'latest_review.json').write_text(json.dumps({'directory':str(r),'captured_utc':out['captured_utc']},indent=2)+'\n')
    print(json.dumps({k:out[k] for k in ['captured_et','heartbeat_age','portfolios','feed','book_collection','decision_counts','execution_failures','existing_bots','milestones']},indent=2))
    db.close()
    # Routine review copies are temporary; all original receipts remain in the
    # append-only experiment DB. Keep full copies at requested milestones only.
    if not out['snapshot_retained']:(r/'snapshot.sqlite').unlink()
if __name__=='__main__':main()
