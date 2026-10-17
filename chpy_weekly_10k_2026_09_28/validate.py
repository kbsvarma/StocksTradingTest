from engine import *

def main():
    # Independent economic checks with one actual ex-date and synthetic constant prices.
    k=int(EX[0]); original={s:{x:v.copy() if isinstance(v,np.ndarray) else v for x,v in d.items()} for s,d in DATA.items()}
    try:
        d=DATA['CHPY'];d['close'][:]=100.;d['open'][:]=100.;d['div'][:]=0.;d['div'][k]=1.;d['close'][k:]=99.;d['open'][k:]=99.;d['due'][k]=k+1
        r,h,l=simulate('capture_b1_a0_open',start=k-1,end=k)
        assert abs(r['profit']-(-19.701))<1e-8,r
        assert r['distributions']==99 and h.receivable.iloc[-1]>99
        r2,_,_=simulate('capture_b1_a0_open',start=k-1,end=k+1)
        assert abs(r2['profit']-r['profit'])<1e-8
        assert r2['cash_distributions_received']==99
        r3,_,_=simulate('rebound_open_a1',start=k,end=k+1)
        assert r3['distributions']==0
    finally:
        DATA.clear();DATA.update(original)
    # Independently sum each real overnight trade directly from prices and issuer cash.
    r,h,l=simulate('capture_b1_a0_open',start='2026-01-02')
    for row in l.itertuples():
        i,j=row.entry_i,row.exit_i;d=DATA['CHPY'];q=row.shares
        expected=q*(d['open'][j]*(1-.001)-d['close'][i]*(1+.001)+d['div'][i+1:j+1].sum())
        assert abs(expected-row.net_pnl)<1e-7
        assert row.shares==int(row.shares)
    # Fractional, zero-cost simple hold should be close to ex-date reinvestment TR,
    # with remaining discrepancy explained by actual later payment-date reinvestment.
    hold,_,_=simulate('CHPY_hold',cost=0,whole=False)
    theoretical=10000*DATA['CHPY']['tr'][-1]
    (P/'validation.json').write_text(json.dumps(dict(flat_ex_price_drop_net_profit=-19.701,buy_on_ex_date_dividends=0,actual_overnight_trades_checked=len(l),fractional_hold_payment_reinvestment=hold['ending'],fractional_hold_exdate_reinvestment=theoretical,payment_timing_difference=hold['ending']-theoretical),indent=2))
    names=['capture_b1_a0_open','capture_b3_a0_close_chpy20','capture_b5_a5_close','recover_price_s0_h20','weekday_0_3']
    rows=[]
    for name in names:
        a,_,_=simulate(name,start='2026-01-02',notional_cap=10000,save=name+'_fixed10k_test2026');a['variant']='maximum_10000_per_trade';rows.append(a)
        if CONFIG[name]['kind']!='recovery':
            for s in ['SMH','SOXX']:
                new=name+'_'+s+'_same_signals';cfg=CONFIG[name].copy();cfg.update(name=new,symbol=s)
                CONFIG[new]=cfg;ENTRIES[new]=ENTRIES[name]
                a,_,_=simulate(new,start='2026-01-02',save=new+'_test2026');a['variant']='sector_same_CHYP_signals';rows.append(a)
    pd.DataFrame(rows).to_csv(P/'additional_checks.csv',index=False)
    # Last twelve actual weekly trades, including price P&L and dividends separately.
    for name in ['capture_b1_a0_open','capture_b3_a0_close_chpy20']:
        _,_,l=simulate(name,start='2026-01-02');l.tail(12).to_csv(P/(name+'_last12trades.csv'),index=False)
    print('Economic checks passed; payout entitlement, exact ex-price adjustment, payment timing and real trade reconstruction verified.')
    print(pd.DataFrame(rows)[['name','variant','profit','max_drawdown','closed_trades']].to_string(index=False))

if __name__=='__main__':main()
