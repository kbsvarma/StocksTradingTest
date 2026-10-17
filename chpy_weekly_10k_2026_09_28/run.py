from engine import *
import hashlib

def eligible_best(rows,min_trades):
    ok=[x for x in rows if x['closed_trades']>=min_trades and x['max_drawdown']>=-.15]
    return max(ok,key=lambda x:(x['ending'],x['name']))['name'] if ok else None

def run():
    (P/'configurations.json').write_text(json.dumps(CONFIG,indent=2))
    rows=[];daily={};train=[];test=[]
    for name,cfg in CONFIG.items():
        for period,start,end in [('full',0,N-1),('train2025',0,'2025-12-31'),('test2026','2026-01-02',N-1),('recent3m','2026-06-25',N-1)]:
            r,h,l=simulate(name,start=start,end=end,save=f'{name}_{period}')
            r.update(period=period,family=cfg['family']);rows.append(r)
            if period=='train2025':train.append(r)
            if period=='test2026':test.append(r);daily[name]=h
        if len(rows)%80==0:print('Paths',len(rows),flush=True)
    allresults=pd.DataFrame(rows);allresults.to_csv(P/'results.csv',index=False)
    winners=[]
    for family in sorted(set(x['family'] for x in CONFIG.values())-{'benchmark'}):
        selected=eligible_best([r for r in train if r['family']==family],12)
        tr=next((r for r in train if r['name']==selected),None)
        te=next((r for r in test if r['name']==selected),None)
        winners.append(dict(family=family,selected=selected,train_profit=tr['profit'] if tr else 0.,train_drawdown=tr['max_drawdown'] if tr else 0.,test_profit=te['profit'] if te else 0.,test_ending=te['ending'] if te else 10000.,test_drawdown=te['max_drawdown'] if te else 0.,test_closed_trades=te['closed_trades'] if te else 0))
    pd.DataFrame(winners).to_csv(P/'frozen_selection.csv',index=False)
    # Only prior observations select each month's strategy. Outstanding positions keep
    # the entry-time rules; there is one persistent $10,000 account across all months.
    schedule={};selection=[]
    for month in pd.period_range('2026-01','2026-09',freq='M'):
        first=int(DATES.searchsorted(month.start_time));endtrain=first-1;starttrain=max(0,first-126)
        trials=[simulate(name,start=starttrain,end=endtrain)[0] for name,cfg in CONFIG.items() if cfg['family']!='benchmark']
        winner=eligible_best(trials,8)
        wr=next((x for x in trials if x['name']==winner),None)
        selection.append(dict(month=str(month),training_start=str(DATES[starttrain].date()),training_end=str(DATES[endtrain].date()),selected=winner,train_profit=wr['profit'] if wr else 0.,train_drawdown=wr['max_drawdown'] if wr else 0.))
        for i in range(first,min(N,int(DATES.searchsorted((month+1).start_time)))):schedule[i]=winner
    walk,h,l=simulate('monthly_adaptive',start='2026-01-02',schedule=schedule,save='monthly_adaptive_test2026')
    pd.DataFrame(selection).to_csv(P/'monthly_selection.csv',index=False)
    pd.DataFrame([walk]).to_csv(P/'walkforward.csv',index=False)
    focus=set(x['selected'] for x in winners if x['selected'])|{'capture_b1_a0_open','capture_b1_a0_close','capture_b1_a1_close','capture_b5_a5_close','CHPY_hold','CHPY_cash_dividends','recover_price_s0_h20','recover_total_t0.005_s0.03_h5','weekday_0_4','rebound_open_a3'}
    # Add the hindsight leaders explicitly as diagnostics, never label these frozen picks.
    tt=allresults[(allresults.period=='test2026')&(allresults.family!='benchmark')]
    focus.update(tt.nlargest(3,'profit').name)
    sens=[]
    for name in sorted(focus):
        for period,start in [('full',0),('test2026','2026-01-02')]:
            for cost in [0,.0005,.002,.003]:
                r,_,_=simulate(name,start=start,cost=cost);r.update(period=period);sens.append(r)
        if CONFIG[name]['kind']!='hold':
            for allocation in [.5]:
                r,_,_=simulate(name,start='2026-01-02',allocation=allocation,save=name+'_half_test2026');r.update(period='test2026');sens.append(r)
        r,_,_=simulate(name,start='2026-01-02',whole=False);r.update(period='test2026');sens.append(r)
    pd.DataFrame(sens).to_csv(P/'sensitivity.csv',index=False)
    # Each possible weekly start, held for 13 calendar weeks. Avoid counting an
    # unfinished last window. These overlap and are not independent trials.
    rolling=[]
    first_per_week=pd.Series(np.arange(N),index=DATES).groupby(DATES.to_period('W-FRI')).first()
    for name in sorted(focus):
        for a in first_per_week:
            enddate=DATES[a]+pd.Timedelta(weeks=13)
            if enddate>DATES[-1]:continue
            b=int(DATES.searchsorted(enddate,side='right')-1)
            r,_,_=simulate(name,start=int(a),end=b)
            rolling.append(dict(name=name,start=r['start'],end=r['end'],profit=r['profit'],return_total=r['return_total'],drawdown=r['max_drawdown']))
    roll=pd.DataFrame(rolling);roll.to_csv(P/'rolling_13weeks.csv',index=False)
    summary=roll.groupby('name').agg(windows=('profit','size'),profitable_fraction=('profit',lambda x:(x>0).mean()),median_profit=('profit','median'),worst_profit=('profit','min'),best_profit=('profit','max'),worst_drawdown=('drawdown','min'))
    summary.to_csv(P/'rolling_summary.csv')
    # Four-week circular block bootstrap. Shifted blocks preserve some serial
    # dependence but short history cannot represent a full sector bear market.
    rng=np.random.default_rng(20260928);stats=[];B=4000
    for name,h in daily.items():
        if CONFIG[name]['family']=='benchmark':continue
        w=(1+h['return']).resample('W-FRI').prod()-1
        ref=(1+h.sector_reference_return).resample('W-FRI').prod()-1
        excess=(w-ref).to_numpy(); n=len(w); blocks=int(np.ceil(n/4))
        starts=rng.integers(0,n,size=(B,blocks));ix=((starts[:,:,None]+np.arange(4))%n).reshape(B,-1)[:,:n]
        means=w.to_numpy()[ix].mean(axis=1);emean=excess[ix].mean(axis=1)
        centered=emean-excess.mean()
        p=(1+np.sum(centered>=excess.mean()))/(B+1)
        stats.append(dict(name=name,weekly_mean=w.mean(),weekly_mean_low=np.quantile(means,.025),weekly_mean_high=np.quantile(means,.975),sector_excess_mean=excess.mean(),excess_low=np.quantile(emean,.025),excess_high=np.quantile(emean,.975),p_value=p))
    st=pd.DataFrame(stats).sort_values('p_value');m=len(st)
    st['q_value']=np.minimum.accumulate((st.p_value.to_numpy()*m/np.arange(1,m+1))[::-1])[::-1].clip(0,1)
    st.to_csv(P/'bootstrap_diagnostics.csv',index=False)
    # Decompose each immediate overnight capture into payout, price change and costs.
    events=[]
    d=DATA['CHPY'];m=DATA['SMH']
    for k in EX:
        if k<1:continue
        px=d['close'][k-1];sale=d['open'][k];div=d['div'][k]
        events.append(dict(ex_date=str(DATES[k].date()),previous_close=px,ex_open=sale,dividend=div,dividend_yield=div/px,price_gap=sale/px-1,gross_total=(sale+div)/px-1,net_total=(sale*(1-.001)+div)/(px*(1+.001))-1,smh_overnight=(m['open'][k]+m['div'][k])/m['close'][k-1]-1))
    pd.DataFrame(events).to_csv(P/'overnight_event_decomposition.csv',index=False)
    # Save concise assertions and data identities for reproducing the research.
    audit=dict(configurations=len(CONFIG),active_strategies=sum(c['family']!='benchmark' for c in CONFIG.values()),base_paths=len(rows),sessions=N,payouts=len(EX),baseline_cost_bps=10,initial_capital=10000,whole_shares=True,
        maximum_absolute_cashflow_error=float(allresults.cashflow_error.abs().max()),bootstrap_resamples=B,bootstrap_block_weeks=4,
        source_hashes={str(f.relative_to(SOURCE)):hashlib.sha256(f.read_bytes()).hexdigest() for f in [SOURCE/'data/CHPY.csv',SOURCE/'data/SMH.csv',SOURCE/'data/SOXX.csv',SOURCE/'issuer_distributions.csv']})
    (P/'audit.json').write_text(json.dumps(audit,indent=2))
    print('Frozen selections');print(pd.DataFrame(winners).to_string(index=False))
    print('Adaptive',json.dumps(walk))
    print('2026 leaders, hindsight');print(tt.nlargest(12,'profit')[['name','profit','max_drawdown','closed_trades','winning_weeks','losing_weeks']].to_string(index=False))
    print('2026 anchors');print(allresults[(allresults.period=='test2026')&allresults.name.isin(focus)][['name','profit','max_drawdown','distributions','price_pnl','fees','closed_trades','worst_week_dollars']].to_string(index=False))
    print('q<.05',int((st.q_value<.05).sum()),'of',len(st),flush=True)

if __name__=='__main__':run()
