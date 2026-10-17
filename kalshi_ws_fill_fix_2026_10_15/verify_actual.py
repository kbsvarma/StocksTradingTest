"""Verify actual production WS fill receipts against an independent listener and GETs."""
import argparse,datetime as dt,hashlib,json,sqlite3,sys,time
from contextlib import closing
from decimal import Decimal as D
from pathlib import Path
P=Path(__file__).resolve().parent;L=P.parent/'kalshi_revalidation_2026_09_12/live_v1';S=L/'state'
sys.path.insert(0,str(P/'candidate'))
from kalshi_client import Credentials,KalshiClientV2
from engine import Exchange
from stream_transport import FillState

def rows(path):
    if not path.exists():return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
def atomic(path,data):
    tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(data,indent=2)+'\n');tmp.replace(path)
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--wait-seconds',type=int,default=0);args=ap.parse_args()
    deadline=time.time()+min(55,args.wait_seconds)
    previous=P/'receipts/actual_verification.json'
    cached={r['order_id']:r for r in json.loads(previous.read_text()).get('proven',[])} if previous.exists() else {}
    while True:
        receipt=P/'receipts/deployment_receipt.json'
        if not receipt.exists():print(json.dumps(dict(status='deployment_pending')));return
        deployment=json.loads(receipt.read_text());cut=deployment['verified_ts']
        status=json.loads((S/'status.json').read_text());w=status['market_transport']
        healthy=(time.time()-status['ts']<15 and status['armed'] and not status['error'] and
                 not status['stopped'] and status['halted'] in ('','Order reconciliation pending'))
        if not healthy:
            observed=dict(ts=time.time(),status_age=time.time()-status['ts'],
                **{k:status[k] for k in ('armed','error','stopped','halted')})
            with (P/'receipts/verification_health_observations.jsonl').open('a') as f:
                f.write(json.dumps(observed)+'\n')
            if time.time()>=deadline:
                raise RuntimeError('Trader not healthy at verification deadline: '+json.dumps(observed))
            time.sleep(min(1,max(0,deadline-time.time())))
            continue
        assert w.get('fill_validation_version')==2
        with closing(sqlite3.connect((S/'orders.sqlite3').as_uri()+'?mode=ro',uri=True)) as db:
            records=[json.loads(x[0]) for x in db.execute('select record from orders')]
        recent=[r for r in records if r.get('created_ts',0)>=cut]
        frames=rows(S/'ws_fill_receipts.jsonl');independent=rows(P/'receipts/independent_fill_frames.jsonl')
        accepted=[r for r in frames if r['kind']=='fill_verification' and r['transport']=='websocket']
        chosen=[r for r in recent if r.get('initial_fill_transport')=='websocket' and
                any(x.get('order_id')==r.get('order_id') for x in accepted)]
        proven=[]
        if chosen:
            client=KalshiClientV2(Credentials.load(),dry_run=True,timeout=3);original=client._request
            def get_only(method,*a,**kw):
                assert method=='GET';return original(method,*a,**kw)
            client._request=get_only;ex=Exchange(client)
            try:
                for record in chosen:
                    oid=record['order_id']
                    known=cached.get(oid)
                    if known and all(D(known[a])==D(record[b]) for a,b in [('quantity','filled'),('cost','cost'),('fees','fees')]):
                        known['current_status']=record['status'];proven.append(known);continue
                    raw=[x for x in frames if x['kind']=='fill_frame' and x['frame']['msg'].get('order_id')==oid]
                    witness=[x for x in independent if x['frame'].get('type')=='fill' and x['frame']['msg'].get('order_id')==oid]
                    assert raw and witness,'Missing independent actual fill witness'
                    primary={x['frame']['msg']['trade_id']:x['frame']['msg'] for x in raw}
                    secondary={x['frame']['msg']['trade_id']:{k:v for k,v in x['frame']['msg'].items() if k!='user_id'} for x in witness}
                    assert primary==secondary,'Independent WS payload mismatch'
                    terminal=ex.order(oid);fills=ex.fills(oid)
                    rest={x.get('trade_id',x['fill_id']):x for x in fills}
                    assert set(primary)==set(rest),'Exchange fill IDs differ'
                    cache=FillState()
                    for x in raw:cache.apply(x['frame']['msg'],x['ts'])
                    proof=cache.confirmed(terminal,record)
                    assert proof is not None,'Actual raw frames did not pass exact terminal proof'
                    quantity=cost=fees=D(0)
                    for ident,msg in primary.items():
                        f=rest[ident]
                        for key in ('count_fp','yes_price_dollars','fee_cost'):assert D(msg[key])==D(f[key]),key
                        assert msg['order_id']==f['order_id']==oid
                        n=D(msg['count_fp']);px=D(msg['yes_price_dollars'])
                        quantity+=n;cost+=n*(px if record['side']=='yes' else 1-px);fees+=D(msg['fee_cost'])
                    assert quantity==D(record['filled'])==D(terminal['fill_count_fp'])
                    assert cost==D(record['cost']) and fees==D(record['fees'])
                    assert D(record['requested_count'])==5 and quantity<=5
                    initial=min(x['ts'] for x in accepted if x['order_id']==oid)
                    s=record['signal'];received=max(x['ts'] for x in raw)
                    event=max(x['frame']['msg']['ts_ms']/1000 for x in raw)
                    sent=max(x['frame'].get('sending_ts_ms',x['frame']['msg']['ts_ms'])/1000 for x in raw)
                    proven.append(dict(ticker=record['ticker'],side=record['side'],order_id=oid,
                        client_id=record['client_id'],quantity=str(quantity),cost=str(cost),fees=str(fees),entry=record['entry'],
                        actual_legacy_actions=sorted({x['action'] for x in primary.values()}),
                        initial_fill_transport=record['initial_fill_transport'],current_status=record['status'],
                        fill_ids=sorted(primary),independent_payloads_equal=True,rest_economics_equal=True,
                        timing_ms=dict(submit_to_ws_receipt=1000*(received-s['execution_submit_ts']),
                            exchange_event_to_ws_receipt=1000*(received-event),network_source_to_receipt=1000*(received-sent),
                            ws_receipt_to_verified=1000*(initial-received),
                            submit_to_local_confirmation=1000*(record['fill_confirmed_ts']-s['execution_submit_ts']),
                            qualified_to_submit=1000*(s['execution_submit_ts']-s['signal_qualified_ts'])),
                        production_frames=raw,independent_frames=witness,terminal_order=terminal,rest_fills=fills,
                        ledger_record=record,verification_rows=[x for x in accepted if x['order_id']==oid]))
            finally:client.session.close()
        verified_no=any(x['side']=='no' and x['actual_legacy_actions']==['sell'] for x in proven)
        summary=dict(ts=time.time(),status='ACTUAL_NO_WEBSOCKET_FILL_VERIFIED' if verified_no else 'WAITING_FOR_ACTUAL_NO_FILL',
            post_repair_markets=len(recent),initial_transports={r['ticker']:r.get('initial_fill_transport') for r in recent},
            stats=w['stats'],reasons=w.get('fill_verification_reasons'),audit=w.get('fill_audit'),proven=proven)
        atomic(P/'receipts/actual_verification.json',summary)
        cached={r['order_id']:r for r in proven}
        if verified_no or time.time()>=deadline:
            print(json.dumps({**{k:v for k,v in summary.items() if k!='proven'},
                'proven':[{k:v for k,v in r.items() if k not in ('production_frames','independent_frames','terminal_order','rest_fills','ledger_record','verification_rows')} for r in proven]},indent=2));return
        time.sleep(min(3,max(0,deadline-time.time())))
if __name__=='__main__':main()
