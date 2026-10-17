from pathlib import Path
import json, math
import numpy as np
import pandas as pd

P=Path(__file__).resolve().parent
SOURCE=P.parent/'chpy_review_2026_09_28'
RAW={s:pd.read_csv(SOURCE/'data'/f'{s}.csv',index_col=0,parse_dates=True) for s in ['CHPY','SMH','SOXX']}
DATES=RAW['CHPY'].index; N=len(DATES)
ISS=pd.read_csv(SOURCE/'issuer_distributions.csv')
EX=np.array([DATES.get_loc(pd.Timestamp(x)) for x in ISS.ex_date])
DATA={}
for s,raw in RAW.items():
    f=raw.reindex(DATES).copy()
    assert f.Close.notna().all() and not f.index.duplicated().any()
    assert (f['Stock Splits']==0).all()
    div=f.Dividends.to_numpy().copy()
    due=np.array([DATES.searchsorted(x+pd.Timedelta(days=5)) for x in DATES])
    if s=='CHPY':
        for r in ISS.itertuples():
            k=DATES.get_loc(pd.Timestamp(r.ex_date));div[k]=r.amount;due[k]=DATES.searchsorted(pd.Timestamp(r.payment))
    c=f.Close.to_numpy(); o=f.Open.to_numpy()
    tr=np.ones(N);tr[1:]=np.cumprod((c[1:]+div[1:])/c[:-1])
    ma20=pd.Series(tr).rolling(20,min_periods=20).mean().to_numpy()
    ma50=pd.Series(tr).rolling(50,min_periods=50).mean().to_numpy()
    momentum=pd.Series(tr).pct_change(5).to_numpy()
    DATA[s]=dict(close=c,open=o,div=div,due=due,tr=tr,ma20=ma20,ma50=ma50,mom5=momentum)

def configs():
    out=[]
    def add(name,family,**kw):
        out.append(dict(name=name,family=family,symbol='CHPY',filter='none',**kw))
    for b in [1,2,3,5,10]:
        for a,phase in [(0,'open'),(0,'close'),(1,'close'),(2,'close'),(3,'close'),(5,'close'),(10,'close')]:
            add(f'capture_b{b}_a{a}_{phase}','capture',kind='fixed',before=b,after=a,entry_phase='close',exit_phase=phase)
    for phase in ['open','close']:
        for a in [1,2,3,5]:
            add(f'rebound_{phase}_a{a}','rebound',kind='fixed',before=0,after=a,entry_phase=phase,exit_phase='close')
    for wd in range(5):
        for ed in range(wd,5):
            add(f'weekday_{wd}_{ed}','calendar',kind='calendar',weekday=wd,exit_weekday=ed,entry_phase='open',exit_phase='close')
    for wd in [3,4]:
        for ed in [7,8]:
            add(f'weekday_{wd}_{ed}','calendar',kind='calendar',weekday=wd,exit_weekday=ed,entry_phase='open',exit_phase='close')
    for target in [0,.005,.01]:
        for stop in [.03,.05]:
            for cap in [5,10,20]:
                add(f'recover_total_t{target}_s{stop}_h{cap}','recovery',kind='recovery',before=1,target=target,stop=stop,cap=cap,price_only=False,entry_phase='close')
    for stop in [.03,.05,0]:
        for cap in [5,10,20]:
            add(f'recover_price_s{stop}_h{cap}','recovery',kind='recovery',before=1,target=0,stop=stop,cap=cap,price_only=True,entry_phase='close')
    seeds=['capture_b1_a0_open','capture_b1_a1_close','capture_b3_a0_close','capture_b5_a5_close','rebound_open_a3','weekday_0_4']
    original={x['name']:x for x in out}
    for name in seeds:
        for filt in ['chpy20','chpy50','smh50','dip5','trend_dip']:
            x=original[name].copy();x.update(name=name+'_'+filt,family='filtered',filter=filt);out.append(x)
    for s in ['CHPY','SMH','SOXX']:
        out.append(dict(name=s+'_hold',family='benchmark',symbol=s,filter='none',kind='hold',reinvest=True))
    out.append(dict(name='CHPY_cash_dividends',family='benchmark',symbol='CHPY',filter='none',kind='hold',reinvest=False))
    return {x['name']:x for x in out}

CONFIG=configs()

def passes(cfg,i):
    j=i-1
    if j<0:return cfg['filter']=='none'
    f=cfg['filter'];d=DATA['CHPY'];m=DATA['SMH']
    if f=='none':return True
    if f=='chpy20':return d['tr'][j]>d['ma20'][j]
    if f=='chpy50':return d['tr'][j]>d['ma50'][j]
    if f=='smh50':return m['tr'][j]>m['ma50'][j]
    if f=='dip5':return d['mom5'][j]<0
    if f=='trend_dip':return d['tr'][j]>d['ma50'][j] and d['mom5'][j]<0
    raise ValueError(f)

def make_entries(cfg):
    entries={}
    if cfg['kind'] in ['fixed','recovery']:
        for k in EX:
            i=int(k-cfg['before'])
            if i<0:continue
            j=int(k+cfg['after']) if cfg['kind']=='fixed' else i+cfg['cap']
            entries[(i,cfg['entry_phase'])]=(j,cfg.get('exit_phase','open'),int(k))
    elif cfg['kind']=='calendar':
        for i,date in enumerate(DATES):
            if date.weekday()!=cfg['weekday']:continue
            due=date+pd.Timedelta(days=cfg['exit_weekday']-cfg['weekday'])
            j=int(DATES.searchsorted(due))
            entries[(i,'open')]=(j,'close',None)
    return entries

ENTRIES={name:make_entries(cfg) for name,cfg in CONFIG.items()}

def simulate(name,start=0,end=None,cost=.001,allocation=1.,cash0=10000.,whole=True,schedule=None,save=None,notional_cap=None):
    if end is None:end=N-1
    if isinstance(start,str):start=int(DATES.searchsorted(start))
    if isinstance(end,str):end=int(DATES.searchsorted(end,side='right')-1)
    cash=cash0;pending=[];pos=None;ledger=[];hist=[];accrued=0.;paid=0.;fees=0.;skips=0
    held_overnight=np.zeros(N);held_intraday=np.zeros(N)
    def equity(i):
        return cash+sum(x[1] for x in pending)+(pos['q']*DATA[pos['symbol']]['close'][i] if pos else 0.)
    def sell(i,phase,reason):
        nonlocal cash,pos,fees
        px=DATA[pos['symbol']][phase][i];gross=pos['q']*px;fee=gross*cost;fees+=fee
        pending.append((i+1,gross-fee,'sale'))
        ledger.append(dict(name=pos['cfg']['name'],entry=str(DATES[pos['entry']].date()),exit=str(DATES[i].date()),entry_i=pos['entry'],exit_i=i,entry_phase=pos['phase'],exit_phase=phase,
            shares=pos['q'],gross_buy=pos['gross_buy'],gross_sale=gross,dividends=pos['div'],fees=pos['fees']+fee,price_pnl=gross-pos['gross_buy'],
            net_pnl=gross-pos['gross_buy']+pos['div']-pos['fees']-fee,holding_sessions=i-pos['entry'],reason=reason,closed=True))
        pos=None
    def buy(cfg,i,phase,exit_info=None):
        nonlocal cash,pos,fees,skips
        px=DATA[cfg['symbol']][phase][i]
        target=min(equity(i)*allocation,notional_cap) if notional_cap else equity(i)*allocation
        budget=min(cash,target)
        # equity for position sizing before opening is cash + receivables, not today's unseen close.
        if budget<.95*target:skips+=1;return
        q=budget/(px*(1+cost));q=math.floor(q) if whole else q
        if q<=0:return
        gross=q*px;fee=gross*cost;cash-=gross+fee;fees+=fee
        j,p,k=exit_info if exit_info else (N+1,'close',None)
        pos=dict(q=q,cfg=cfg,symbol=cfg['symbol'],entry=i,phase=phase,gross_buy=gross,fees=fee,div=0.,exit_i=j,exit_phase=p,target_ex=k,next_exit=None)
    def add_hold_cash(i):
        nonlocal cash,fees
        px=DATA[pos['symbol']]['close'][i]
        q=cash/(px*(1+cost));q=math.floor(q) if whole else q
        if q<=0:return
        gross=q*px;fee=gross*cost;cash-=gross+fee;fees+=fee
        pos['q']+=q;pos['gross_buy']+=gross;pos['fees']+=fee
    for i in range(start,end+1):
        paid+=sum(x[1] for x in pending if x[0]<=i and x[2]=='div')
        cash+=sum(x[1] for x in pending if x[0]<=i);pending=[x for x in pending if x[0]>i]
        if pos:
            held_overnight[i]=pos['q']*DATA[pos['symbol']]['close'][i-1]/hist[-1]['equity'] if hist else 0
            d=DATA[pos['symbol']]
            if d['div'][i]>0:
                amt=pos['q']*d['div'][i];pos['div']+=amt;accrued+=amt
                due=int(d['due'][i])
                if due<=i:cash+=amt;paid+=amt
                else:pending.append((due,amt,'div'))
        current=CONFIG.get(schedule.get(i)) if schedule is not None and schedule.get(i) else (CONFIG.get(name) if schedule is None else None)
        for phase in ['open','close']:
            if pos:
                cfg=pos['cfg'];reason=None
                if pos['next_exit']==i and phase=='open':reason=pos.get('exit_reason','trigger')
                elif i==pos['exit_i'] and phase==pos['exit_phase']:reason='time_limit' if cfg['kind']=='recovery' else 'scheduled'
                if reason:sell(i,phase,reason)
            if pos is None and current and passes(current,i):
                if current['kind']=='hold':
                    if phase=='close':buy(current,i,phase)
                elif (i,phase) in ENTRIES[current['name']]:
                    buy(current,i,phase,ENTRIES[current['name']][(i,phase)])
            elif pos and pos['cfg']['kind']=='hold' and pos['cfg']['reinvest'] and phase=='close':
                add_hold_cash(i)
            if phase=='open' and pos:
                # Denominator uses last marked account equity; diagnostic reference only.
                held_intraday[i]=pos['q']*DATA[pos['symbol']]['open'][i]/(hist[-1]['equity'] if hist else cash0)
        if pos and pos['cfg']['kind']=='recovery' and i>pos['entry'] and pos['next_exit'] is None:
            cfg=pos['cfg'];value=pos['q']*DATA[pos['symbol']]['close'][i]*(1-cost)
            spent=pos['gross_buy']+pos['fees'];ret=(value+pos['div'])/spent-1
            target_ret=value/spent-1 if cfg['price_only'] else ret
            if cfg['stop'] and ret<=-cfg['stop']:
                pos['next_exit']=i+1;pos['exit_reason']='stop_signal'
            elif i>=pos['target_ex'] and target_ret>=cfg['target']:
                pos['next_exit']=i+1;pos['exit_reason']='target_signal'
        stock=pos['q']*DATA[pos['symbol']]['close'][i] if pos else 0.
        eq=cash+sum(x[1] for x in pending)+stock
        assert cash>=-1e-7 and eq>0 and stock<=eq+1e-6
        hist.append(dict(date=DATES[i],equity=eq,cash=cash,receivable=sum(x[1] for x in pending),invested=stock,dividends_accrued=accrued,dividends_paid=paid,fees=fees,overnight_weight=held_overnight[i],intraday_weight=held_intraday[i]))
    if pos:
        gross=pos['q']*DATA[pos['symbol']]['close'][end]
        ledger.append(dict(name=pos['cfg']['name'],entry=str(DATES[pos['entry']].date()),exit=str(DATES[end].date()),entry_i=pos['entry'],exit_i=end,entry_phase=pos['phase'],exit_phase='mark',shares=pos['q'],gross_buy=pos['gross_buy'],gross_sale=gross,
            dividends=pos['div'],fees=pos['fees'],price_pnl=gross-pos['gross_buy'],net_pnl=gross-pos['gross_buy']+pos['div']-pos['fees'],holding_sessions=end-pos['entry'],reason='open_at_cutoff',closed=False))
    h=pd.DataFrame(hist).set_index('date');l=pd.DataFrame(ledger)
    eq=h.equity.to_numpy();returns=eq/np.r_[cash0,eq[:-1]]-1
    h['return']=returns;h['drawdown']=eq/np.maximum.accumulate(np.r_[cash0,eq])[1:]-1
    # Sector reference uses actual overnight/intraday capital exposure. It is a diagnostic,
    # not an executable rebalanced portfolio, and deliberately has no added reference fees.
    m=DATA['SMH'];idx=np.arange(start,end+1)
    over=np.zeros(len(idx));valid=idx>0
    over[valid]=(m['open'][idx[valid]]+m['div'][idx[valid]])/m['close'][idx[valid]-1]-1
    intra=m['close'][idx]/m['open'][idx]-1
    h['sector_reference_return']=h.overnight_weight.to_numpy()*over+h.intraday_weight.to_numpy()*intra
    w=h.equity.resample('W-FRI').last();wr=w/np.r_[cash0,w.to_numpy()[:-1]]-1
    wp=w-np.r_[cash0,w.to_numpy()[:-1]]
    closed=l[l.closed] if len(l) else l
    err=float(l.net_pnl.sum())-(eq[-1]-cash0) if len(l) else eq[-1]-cash0
    assert abs(err)<1e-6,(name,err)
    result=dict(name=name,start=str(DATES[start].date()),end=str(DATES[end].date()),cost_bps=cost*10000,allocation=allocation,whole_shares=whole,notional_cap=notional_cap,
        ending=eq[-1],profit=eq[-1]-cash0,return_total=eq[-1]/cash0-1,max_drawdown=h.drawdown.min(),max_drawdown_dollars=float((eq-np.maximum.accumulate(np.r_[cash0,eq])[1:]).min()),
        distributions=accrued,cash_distributions_received=paid,price_pnl=float(l.price_pnl.sum()) if len(l) else 0.,fees=fees,
        closed_trades=len(closed),open_positions=int(pos is not None),trade_win_rate=float((closed.net_pnl>0).mean()) if len(closed) else np.nan,
        mean_trade_profit=float(closed.net_pnl.mean()) if len(closed) else np.nan,median_trade_profit=float(closed.net_pnl.median()) if len(closed) else np.nan,
        best_trade=float(closed.net_pnl.max()) if len(closed) else np.nan,worst_trade=float(closed.net_pnl.min()) if len(closed) else np.nan,
        avg_sessions=float(closed.holding_sessions.mean()) if len(closed) else np.nan,max_sessions=int(l.holding_sessions.max()) if len(l) else 0,
        weeks=len(w),winning_weeks=int((wp>1e-6).sum()),losing_weeks=int((wp< -1e-6).sum()),flat_weeks=int((wp.abs()<=1e-6).sum()),mean_weekly_profit=float(wp.mean()),median_weekly_profit=float(wp.median()),
        worst_week_dollars=float(wp.min()),best_week_dollars=float(wp.max()),worst_week_return=float(wr.min()),best_week_return=float(wr.max()),
        overnight_exposure=float(h.overnight_weight.mean()),close_exposure=float((h.invested/h.equity).mean()),cashflow_error=err,skipped_for_cash=skips)
    if save:
        (P/'daily').mkdir(exist_ok=True);(P/'ledgers').mkdir(exist_ok=True)
        h.to_csv(P/'daily'/f'{save}.csv',index_label='Date');l.to_csv(P/'ledgers'/f'{save}.csv',index=False)
    return result,h,l

if __name__=='__main__':
    print('Configurations',len(CONFIG),'prices',N,'payouts',len(EX))
    for name in ['CHPY_hold','capture_b1_a0_open','capture_b5_a5_close','recover_price_s0_h20']:
        r,_,_=simulate(name);print(json.dumps(r))
