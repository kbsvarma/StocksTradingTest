"""Causal receipt-clock check of static maker/fallback on full BTC L2 tapes.

Fills remain simulated: public matching, unknown hidden queue, and hypothetical
own-order acceptance cannot be proved by another participant's market data.
"""
import collections, gzip, hashlib, json, time, urllib.request
from pathlib import Path
import screen as s
P=s.P
def bbo(book):
    if not book['yes'] or not book['no']:return None
    return max(book['yes']),1-max(book['no'])
def walk(book,dr,qty=1):
    levels=sorted((1-p,q) for p,q in book['no' if dr==1 else 'yes'].items())
    cost=0;left=qty
    for p,q in levels:
        if not 0<p<1:continue
        n=min(q,left);cost+=n*p;left-=n
        if left<1e-8:return cost/qty
    return None
def replay(path):
    book={'yes':{},'no':{}};state={};seq={};counts=collections.Counter();bad=False;seen=set();result=None
    for line in gzip.open(path,'rt'):
        r=json.loads(line);t=r['client_received_at_ms']/1000;kind=r['type'];msg=r.get('msg',{})
        if kind=='capture_start':
            meta=next(m for m in r['markets'] if m['ticker'].startswith('KXBTC15M'))
            ticker=meta['ticker'];close=s.ts(meta['close_time'][:10])+int(meta['close_time'][11:13])*3600+int(meta['close_time'][14:16])*60
            T=close-720;state=dict(ticker=ticker,close=close,decision=T,first=t)
        if 'sid' in r and 'seq' in r:
            sid=r['sid'];old=seq.get(sid)
            if old is not None and r['seq']!=old+1:counts['sequence_gap']+=1;bad=True
            seq[sid]=r['seq']
        if kind=='capture_sequence_gap':bad=True
        # Execute scheduled actions using the book known BEFORE receiving this frame.
        if state and t>=T and 'side' not in state and bbo(book) and not bad:
            b,a=bbo(book);dr=1 if b+a>1 else -1
            state.update(side=dr,limit=b if dr==1 else 1-a,signal_time=t,signal_bid=b,signal_ask=a)
        if 'side' in state and t>=T+1 and 'submitted' not in state:
            dr=state['side'];px=walk(book,dr);limit=state['limit'];ours='yes' if dr==1 else 'no'
            state.update(submitted=t,post_only_rejected=px is None or px<=limit+1e-8,
                queue_ahead=book[ours].get(limit,0),remaining=1.,filled=0.,immediate_price=px,consumed=0.)
        if 'submitted' in state and t>=T+62:
            if bad:state['invalid_sequence']=True
            state['fallback_price']=walk(book,state['side'],state['remaining']) if state['remaining']>1e-8 else None
            state['fallback_at']=t;break
        if msg.get('market_ticker')!=state.get('ticker'):continue
        if kind=='orderbook_snapshot':
            book={side:{float(p):float(q) for p,q in msg.get(side+'_dollars_fp',[]) if float(q)>0} for side in ('yes','no')};counts['snapshots']+=1
        elif kind=='orderbook_delta':
            side=msg['side'];p=float(msg['price_dollars']);q=book[side].get(p,0)+float(msg['delta_fp'])
            if q<-1e-7:counts['negative_depth']+=1;bad=True
            if q<=1e-8:book[side].pop(p,None)
            else:book[side][p]=q
            counts['deltas']+=1
        elif kind=='trade' and 'submitted' in state and T+1<=t<=T+61 and not state['post_only_rejected']:
            tradeid=msg.get('trade_id')
            if tradeid in seen:continue
            seen.add(tradeid);dr=state['side'];ag=msg.get('taker_side');p=float(msg.get('yes_price_dollars') or msg.get('yes_price')/100)
            px=p if dr==1 else 1-p;qty=float(msg.get('count_fp',msg.get('count',0)))
            if ag!=('no' if dr==1 else 'yes'):continue
            if px>state['limit']+1e-8:continue
            if px<state['limit']-1e-8:
                fill=min(state['remaining'],qty)
            else:
                before=state['consumed'];state['consumed']+=qty
                fill=min(state['remaining'],max(0,state['consumed']-max(before,state['queue_ahead'])))
            state['filled']+=fill;state['remaining']-=fill
    if 'submitted' not in state or state['first']>T or 'fallback_at' not in state:return dict(file=path.name,excluded='incomplete entry interval',state=state)
    cache=P/'forward_metadata';cache.mkdir(exist_ok=True);target=cache/(state['ticker']+'.json')
    if target.exists():m=json.loads(target.read_text())
    else:
        u='https://external-api.kalshi.com/trade-api/v2/markets/'+state['ticker']
        for attempt in range(6):
            try:
                with urllib.request.urlopen(u,timeout=20) as h:m=json.load(h)
                break
            except Exception:
                if attempt==5:raise
                time.sleep(min(8,2**attempt))
        target.write_text(json.dumps(m))
    result=m['market'].get('result');state['result']=result
    if result not in ('yes','no'):return dict(file=path.name,state=state,excluded='not settled')
    won=int((result=='yes')==(state['side']==1));state['won']=won
    im=state['immediate_price'];fp=state.get('fallback_price');qty=state['filled'];remain=state['remaining']
    state['immediate_pnl']=won-im-s.fee(im)-.0025 if im is not None else None
    if remain<1e-8 or fp is not None:
        cost=qty*state['limit']+remain*(fp or 0);fee=s.fee(fp,remain) if remain>1e-8 else 0
        state['maker_pnl']=won-cost-fee-.0025
    return dict(file=path.name,counts=dict(counts),state=state,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
if __name__=='__main__':
    out=[]
    for path in sorted((P/'forward_sample').glob('*.gz')):
        r=replay(path);out.append(r);print(path.name, r.get('excluded'),r['state'].get('maker_pnl'),flush=True)
    good=[r['state'] for r in out if not r.get('excluded') and not r['state'].get('invalid_sequence')]
    summary=dict(n=len(good),maker_net=sum(r.get('maker_pnl',0) for r in good),immediate_net=sum(r.get('immediate_pnl',0) or 0 for r in good),
        maker_quantity=sum(r.get('filled',0) for r in good),post_only_rejects=sum(r['post_only_rejected'] for r in good),
        warning='Tiny one-session simulation, not independent strategy validation or actual fills; cancellations ahead ignored conservatively, acceptance/cancel latency assumed1s.')
    s.save('forward_book_check.json',dict(summary=summary,segments=out));print(json.dumps(summary,indent=2))
