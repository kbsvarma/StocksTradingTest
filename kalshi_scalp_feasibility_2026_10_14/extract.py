"""Offline fixed-point reconstruction; writes only the research directory."""
from pathlib import Path
import collections,datetime,gzip,hashlib,heapq,json,math,pickle,time
P=Path(__file__).resolve().parent
SRC=Path('/home/varma/kalshi_btc_eth_quant_2026_10_02/forward_state/data')
def fixed(s,n=4):
    s=str(s);neg=s.startswith('-');s=s.lstrip('-');a,_,b=s.partition('.')
    x=int(a)*10**n+int((b+'0'*n)[:n]);return -x if neg else x
def stamp(s):return int(datetime.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()*1000)
def extract(path):
    books={};assets={};cf={0:{},1:{}};lastcf={};sigma={};lastbook={};lastsnap=-10**20;hist={0:collections.deque(),1:collections.deque()};flow={0:collections.deque(),1:collections.deque()};rows=[];meta=None;seq={};counts=collections.Counter();bad=collections.Counter();last=None;first=None;complete=False;seen=set();lag=[];rawcount=0
    def emit(now):
        for a,ticker in assets.items():
            if ticker not in books:continue
            book=books[ticker];yy=heapq.nlargest(10,book['yes']);nn=heapq.nlargest(10,book['no'])
            if not yy or not nn:continue
            if yy[0]+nn[0]>10000:counts['crossed_sample']+=1;continue
            ys=[[x,book['yes'][x]] for x in yy];ns=[[x,book['no'][x]] for x in nn];mid=(yy[0]+10000-nn[0])/2
            h=hist[a]
            while len(h)>1 and h[1][0]<=now-5000:h.popleft()
            mid5=h[0][1] if h and now-5300<=h[0][0]<=now-5000 else None
            while flow[a] and flow[a][0][0]<now-2000:flow[a].popleft()
            buy=sum(v[2] for v in flow[a] if v[1]==1);sell=sum(v[2] for v in flow[a] if v[1]==-1)
            age=now-lastcf.get(a,-10**20);diff=rng=None
            if age<=1500 and a in sigma:
                c=cf[a];latest=max(c);prev=latest-5000
                if prev in c:
                    rem=(meta[a]['close_ms']-now)/1000;vol=sigma[a]
                    if rem>0 and vol>0 and meta[a].get('floor_strike'):
                        strike=meta[a]['floor_strike'];f=lambda price,sec:.5*(1+math.erf(math.log(price/strike)/(vol*math.sqrt(sec)*math.sqrt(2))))
                        diff=f(c[latest],rem)-f(c[prev],rem+5);recent=[f(v,rem) for t,v in c.items() if t>=prev];rng=max(recent)-min(recent)
            rows.append([now,0,a,ys,ns,now-lastbook.get(a,now),age,diff,rng,mid5,buy,sell])
            h.append((now,mid))
    with gzip.open(path,'rb') as inp:
        for line in inp:
            if not line.strip():continue
            r=json.loads(line);rawcount+=1;k=r['type'];now=r['client_received_at_ms'];m=r.get('msg',{});counts[k]+=1
            if first is None:first=now
            if last is not None and now<last:bad['clock_backwards']+=1
            if last is not None and now-last>2000:bad['receive_gap_gt2s']+=1
            last=now
            if meta and now-lastsnap>=100:
                emit(now);lastsnap=now
            if 'sid' in r and 'seq' in r:
                sid=r['sid'];q=r['seq']
                if sid in seq and q!=seq[sid]+1:bad['sequence_gap']+=1
                seq[sid]=q
            if k=='capture_start':
                meta={}
                for market in r['markets']:
                    a=0 if market['ticker'].startswith('KXBTC') else 1
                    meta[a]={**market,'close_ms':stamp(market['close_time']),'open_ms':stamp(market['open_time'])};assets[a]=market['ticker']
            elif k=='orderbook_snapshot':
                ticker=m['market_ticker'];books[ticker]={s:{fixed(x):fixed(q,2) for x,q in m.get(s+'_dollars_fp',[]) if fixed(q,2)>0} for s in ('yes','no')};lastbook[0 if ticker.startswith('KXBTC') else 1]=now
            elif k=='orderbook_delta':
                ticker=m['market_ticker']
                if ticker not in books:bad['delta_without_snapshot']+=1;continue
                b=books[ticker][m['side']];p=fixed(m['price_dollars']);q=b.get(p,0)+fixed(m['delta_fp'],2)
                if q<0:bad['negative_depth']+=1
                if q<=0:b.pop(p,None)
                else:b[p]=q
                lastbook[0 if ticker.startswith('KXBTC') else 1]=now
            elif k=='trade':
                tid=m.get('trade_id')
                if tid in seen:counts['duplicate_trade']+=1;continue
                seen.add(tid)
                if m.get('is_block_trade'):continue
                a=0 if m['market_ticker'].startswith('KXBTC') else 1;side=1 if m['taker_side']=='yes' else -1;src=m.get('ts_ms',m['ts']*1000)
                p=fixed(m['yes_price_dollars']);q=fixed(m['count_fp'],2);rows.append([now,1,a,src,side,p,q,tid]);flow[a].append((now,side,q));lag.append(now-src)
            elif k=='cfbenchmarks_value':
                a=0 if m['index_id']=='BRTI' else 1;raw=json.loads(m['data']);src=int(raw['time']);val=float(raw['value'])
                if now-src>1500:counts['stale_benchmark']+=1;continue
                lastcf[a]=src
                c=cf[a];c[src]=val
                for t in list(c):
                    if t<src-60000:del c[t]
                if len(c)==61 and max(c)-min(c)==60000:
                    vv=[c[t] for t in sorted(c)];rr=[math.log(y/x) for x,y in zip(vv,vv[1:])];mean=sum(rr)/60;sigma[a]=math.sqrt(sum((x-mean)**2 for x in rr)/60)
                else:sigma.pop(a,None)
            elif k=='capture_end':complete=r.get('complete_to_close',False)
            elif k in ('capture_error','capture_sequence_gap','error'):bad[k]+=1
    hard={k:v for k,v in bad.items() if k!='receive_gap_gt2s'}
    accepted=bool(complete and not hard)
    info=dict(file=path.name,bytes=path.stat().st_size,source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),first=first,last=last,complete=complete,accepted=accepted,bad=dict(bad),counts=dict(counts),raw_frames=rawcount,compact_rows=len(rows),trade_delay_min=min(lag) if lag else None,trade_delay_max=max(lag) if lag else None)
    if accepted:
        dest=P/'compact'/(path.stem.replace('.jsonl','')+'.pkl.gz');dest.parent.mkdir(exist_ok=True)
        with gzip.open(dest,'wb',compresslevel=1) as out:pickle.dump(dict(meta=meta,rows=rows,info=info),out,protocol=5)
        info['target']=dest.name
    return info
def main():
    (P/'compact').mkdir(exist_ok=True);manifest=[];start=time.time()
    existing={r['file']:r for r in json.loads((P/'capture_manifest.json').read_text())} if (P/'capture_manifest.json').exists() else {}
    for i,path in enumerate(sorted(SRC.glob('*.gz'))):
        if path.name in existing:
            manifest.append(existing[path.name]);continue
        r=extract(path);manifest.append(r);(P/'capture_manifest.json').write_text(json.dumps(manifest,indent=2))
        if i%5==0 or not r['accepted']:print(json.dumps(dict(i=i+1,file=path.name,accepted=r['accepted'],bad=r['bad'],elapsed=round(time.time()-start))),flush=True)
    print(json.dumps(dict(done=True,files=len(manifest),accepted=sum(r['accepted'] for r in manifest),raw=sum(r['raw_frames'] for r in manifest),elapsed=time.time()-start)),flush=True)
if __name__=='__main__':main()
