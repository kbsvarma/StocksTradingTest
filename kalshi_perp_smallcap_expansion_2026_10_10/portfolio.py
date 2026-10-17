from engine import *

assets=[('KXSILVERPERP','h_reversal24'),('KXPALLADIUMPERP','m_reversal240')]
data={}
for t,rule in assets:
 q,f=load(t,60);h,_=load(t,3600);hour=rule.startswith('h_');source=h if hour else q
 data[t]=(q,f,indicators(t,source,3600 if hour else 60,'hour' if hour else 'minute'),source)
res={};led={};paths={}
for name,a,b in [('early',START,split(assets[0][0])),('later',split(assets[0][0]),END),('full',START,END)]:
 for revalidate in [False,True]:
  for lev,slip,delay in [(2,1,60),(2,5,60),(2,1,120),(3,1,60),(3,5,60),(3,1,120)]:
   pieces=[]
   suffix=f'{name}/L{lev}/slip{slip}/delay{delay}/revalidate{int(revalidate)}'
   for t,rule in assets:
    q,f,i,source=data[t]
    for cap in [50,100]:
     key=f'{t}/{rule}/capital{cap}/'+suffix
     r,l,p=run(t,rule,q,f,i,60,a,b,lev,slip,delay,signal_Q=source,capital=cap,daily_budget=cap*.06,reserve=cap*.2,dense_path=(cap==50),revalidate=revalidate)
     res[key]=r;led[key]=l;paths[key]=p
     if cap==50:pieces.append((r,l,{x['t']:x['equity'] for x in p}))
   start=min(x[0]['start'] for x in pieces);end=max(x[0]['end'] for x in pieces);joint=[];missing=0
   for s in range(start,end+1,60):
    vals=[50 if s<r['start'] else 50+r['net'] if s>r['end'] else p.get(s) for r,l,p in pieces]
    if any(v is None for v in vals):missing+=1;continue
    joint.append({'t':s,'equity':sum(vals)})
   rows=sorted([x for _,ll,_ in pieces for x in ll],key=lambda r:(r['entry'],r['ticker']));peak=100.;dd=0.;weeks={};prev=100.
   for x in joint:
    peak=max(peak,x['equity']);dd=max(dd,peak-x['equity']);d=dt.datetime.fromtimestamp(x['t'],ET).date();w=(d-dt.timedelta(days=d.weekday())).isoformat();weeks[w]=weeks.get(w,0)+x['equity']-prev;prev=x['equity']
   key='COMBINED100/'+suffix;res[key]={'start':start,'end':end,'trades':len(rows),'wins':sum(r['net']>0 for r in rows),'net':sum(r['net'] for r in rows),'drawdown':dd,'without_best3':sum(r['net'] for r in rows)-sum(sorted((r['net'] for r in rows),reverse=True)[:3]),'fees':sum(r['fees'] for r in rows),'slippage':sum(r['slippage'] for r in rows),'funding':sum(r['funding'] for r in rows),'weekly':weeks,'missing_joint_marks':missing,'max_trade_loss':min([0]+[r['net'] for r in rows])};led[key]=rows;paths[key]=joint
   print(key,len(rows),round(res[key]['net'],4),round(dd,4),round(res[key]['without_best3'],4),flush=True)
save('portfolio_results.json',res);save('portfolio_ledgers.json.gz',led);save('portfolio_paths.json.gz',paths)
