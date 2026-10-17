"""Read archived market-data captures and emit compact top-five depth transitions.
No credentials, network, account or live service imports. Run with nice priority.
"""
from pathlib import Path
import collections,gzip,hashlib,json,sys
from decimal import Decimal
ROOT=Path(sys.argv[1]);OUT=Path(sys.argv[2]);END=int(sys.argv[3]);OUT.mkdir(parents=True,exist_ok=True)
manifest=[];counts=collections.Counter()
def units(v):
    n=Decimal(str(v))*1000000
    if n!=n.to_integral_value():raise ValueError('Quantity exceeds six decimal places')
    return int(n)
for path in sorted(ROOT.glob('capture_*.jsonl.gz')):
    if int(path.name.split('_')[1])>END:continue
    books={};seq={};valid={};cached={};threshold={};last={};rows=collections.defaultdict(list);complete=False;metadata=None;errors=0
    def top(levels):
        qty=0;cost=0.;cost1=0.;p1=None;p5=None;worst=-1.;worst1=None;worst5=None
        for p,n in sorted(levels.items(),reverse=True):
            if n<=0:continue
            if qty<1000000:
                cost1+=p*min(n,1000000-qty)
                if qty+n>=1000000:p1=cost1/1000000;worst1=p
            take=min(n,5000000-qty);cost+=p*take;qty+=take;worst=p
            if qty>=5000000:p5=cost/5000000;worst5=p;break
        return (max(levels) if levels else None,p1,worst1,p5,worst5),worst
    with gzip.open(path,'rt') as inp:
        for line in inp:
            if '"type":"orderbook_' not in line and '"type":"capture_' not in line:continue
            r=json.loads(line);kind=r['type'];now=r['client_received_at_ms'];m=r.get('msg',{});ticker=m.get('market_ticker')
            if kind=='capture_start':metadata=r;continue
            if kind=='capture_end':complete=r.get('complete_to_close',False);continue
            if kind in ('capture_error','capture_sequence_gap'):
                errors+=1
                for key in valid:valid[key]=False;rows[key].append([now]+[None]*10)
                continue
            if kind not in ('orderbook_snapshot','orderbook_delta'):continue
            counts['raw_frames']+=1
            sid=r.get('sid');sq=r.get('seq');prior=seq.get(sid);seq[sid]=sq
            if prior is not None and sq!=prior+1:
                errors+=1
                for key in valid:valid[key]=False;rows[key].append([now]+[None]*10)
            if kind=='orderbook_snapshot':
                books[ticker]={side:{float(p):units(n) for p,n in m.get(side+'_dollars_fp',[]) if units(n)>0} for side in ('yes','no')};valid[ticker]=True
                cached[ticker]={};threshold[ticker]={}
                for side in ('yes','no'):cached[ticker][side],threshold[ticker][side]=top(books[ticker][side])
            elif ticker not in books:continue
            else:
                side=m['side'];p=float(m['price_dollars']);n=books[ticker][side].get(p,0)+units(m['delta_fp'])
                if n<0:valid[ticker]=False;errors+=1
                if n<=0:books[ticker][side].pop(p,None)
                else:books[ticker][side][p]=n
                if p>=threshold[ticker][side]-1e-8:cached[ticker][side],threshold[ticker][side]=top(books[ticker][side])
            state=list(cached[ticker]['yes']+cached[ticker]['no'])
            send=r.get('sending_ts_ms',now)
            if not valid[ticker] or not 0<=now-send<=1000:state=[None]*10
            if ticker not in last or state!=last[ticker][1] or now-last[ticker][0]>=1000:
                rows[ticker].append([now,*state]);last[ticker]=(now,state)
    if not complete or errors or not metadata:
        counts['rejected_files']+=1;continue
    target=OUT/(path.name.replace('capture_','top_'))
    with gzip.open(target,'wt') as out:json.dump(dict(metadata=metadata,rows=rows),out,separators=(',',':'))
    counts['accepted_files']+=1;counts['compact_rows']+=sum(map(len,rows.values()))
    manifest.append(dict(source=str(path),source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),target=target.name,target_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),rows={k:len(v) for k,v in rows.items()}))
    print(path.name,sum(map(len,rows.values())),flush=True)
(OUT/'manifest.json').write_text(json.dumps(dict(manifest=manifest,counts=dict(counts)),indent=2));print(json.dumps(dict(counts)),flush=True)
