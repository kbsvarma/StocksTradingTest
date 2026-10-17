"""Reproducible calculations and competing hypotheses; no opaque buy-score sum."""
from __future__ import annotations
import math
import re
from datetime import datetime
from difflib import SequenceMatcher
import pandas as pd
from advisor.intelligence.contract import number, digest
from .catalog import DIMENSIONS, QUESTIONS, SOURCES


def current(rows,kind):return [r for r in rows if r['kind']==kind and r['temporal']['state']=='current']
def usable(rows,kind):return [r for r in rows if r['kind']==kind and r['temporal']['state']!='excluded']


def technicals(row, benchmark=None):
    bars=pd.DataFrame(row['payload']['bars'])
    fields={str(c).lower():c for c in bars.columns}
    close=pd.to_numeric(bars[fields['close']],errors='coerce')
    valid=close.notna() & (close>0);bars=bars[valid].copy();close=close[valid].reset_index(drop=True);bars=bars.reset_index(drop=True)
    if len(close)<21: return {'insufficient_sessions':len(close)}
    returns=close.pct_change();p=float(close.iloc[-1]);change=close.diff()
    gain=change.clip(lower=0).ewm(alpha=1/14,adjust=False,min_periods=14).mean().iloc[-1]
    loss=(-change.clip(upper=0)).ewm(alpha=1/14,adjust=False,min_periods=14).mean().iloc[-1]
    rsi=100-100/(1+gain/loss) if loss>0 else 100 if gain>0 else 50
    out={'close':p,'sessions':len(close),'rsi14':float(rsi),'return_21d_pct':float((p/close.iloc[-22]-1)*100) if len(close)>21 else None,
         'rv20_pct':float(returns.tail(20).std()*math.sqrt(252)*100),
         'drawdown_252_pct':float((p/close.tail(252).max()-1)*100),'max_up_day_21_pct':float(returns.tail(21).max()*100),
         'max_down_day_21_pct':float(returns.tail(21).min()*100)}
    for n in (20,50,200):
        if len(close)>=n:
            out[f'ma{n}']=float(close.tail(n).mean());out[f'distance_ma{n}_pct']=(p/out[f'ma{n}']-1)*100
    if {'high','low'}<=set(fields):
        h=pd.to_numeric(bars[fields['high']],errors='coerce');l=pd.to_numeric(bars[fields['low']],errors='coerce')
        tr=pd.concat([h-l,(h-close.shift()).abs(),(l-close.shift()).abs()],axis=1).max(axis=1)
        out['atr14']=float(tr.tail(14).mean());out['prior_20_high']=float(h.iloc[-21:-1].max());out['prior_20_low']=float(l.iloc[-21:-1].min())
    if 'volume' in fields:
        v=pd.to_numeric(bars[fields['volume']],errors='coerce')
        avg=v.iloc[-21:-1].mean()
        if avg>0:out['volume_vs_prior20']=float(v.iloc[-1]/avg)
        out['median_dollar_volume20']=float((v*close).tail(20).median())
    if benchmark:
        b=pd.DataFrame(benchmark['payload']['bars'])
        def dates(f):
            col=next((c for c in f if c.lower() in {'date','datetime','index'}),None)
            return pd.to_datetime(f[col],utc=True).dt.strftime('%Y-%m-%d') if col else None
        d1,d2=dates(bars),dates(b)
        if d1 is not None and d2 is not None:
            bc=next(c for c in b if c.lower()=='close')
            aligned=pd.concat([pd.Series(close.values,index=d1,name='stock'),pd.Series(b[bc].values,index=d2,name='benchmark')],axis=1).dropna()
            if len(aligned)>21 and aligned.index[-1]==d1.iloc[-1]==d2.iloc[-1]:
                last=aligned.iloc[-1]/aligned.iloc[-22]-1
                out['relative_21d_pp']=float((last['stock']-last['benchmark'])*100)
                out['benchmark']=benchmark['ticker']
    return {k:round(v,4) if isinstance(v,float) and math.isfinite(v) else v for k,v in out.items()}


def fundamental_metrics(rows):
    facts=usable(rows,'fundamental');out={};citations={}
    def latest(metric,dclass='quarter'):
        vals=[r for r in facts if r['payload']['metric']==metric and r['payload']['duration_class']==dclass and r['temporal']['state']=='current']
        return max(vals,key=lambda r:(r['period_end'],r.get('published_at') or '')) if vals else None
    def matching(metric,anchor):
        vals=[r for r in facts if r['payload']['metric']==metric and r['period_end']==anchor['period_end'] and r.get('period_start')==anchor.get('period_start') and r['payload']['unit']==anchor['payload']['unit']]
        return max(vals,key=lambda r:r.get('published_at') or '') if vals else None
    def prior_comparable(row):
        end=datetime.fromisoformat(row['period_end'])
        duration=(end-datetime.fromisoformat(row.get('period_start') or row['period_end'])).days
        older=[]
        for r in facts:
            if any(r['payload'][k]!=row['payload'][k] for k in ('metric','unit','duration_class')):continue
            e=datetime.fromisoformat(r['period_end']);d=(e-datetime.fromisoformat(r.get('period_start') or r['period_end'])).days
            if 350<=(end-e).days<=380 and abs(duration-d)<=12:older.append(r)
        return max(older,key=lambda r:(r['period_end'],r.get('published_at') or '')) if older else None
    for metric in ('revenue','net_income','gross_profit','op_income','eps_diluted','inventory','receivables','shares_diluted','cash','lt_debt'):
        dclass='instant' if metric in {'inventory','receivables','cash','lt_debt'} else 'quarter'
        row=latest(metric,dclass)
        if not row:continue
        value=row['payload']['value'];out[metric]=value;out[metric+'_period']=row['period_end'];citations[metric]=[row['id']]
        prior=prior_comparable(row)
        if prior:
            v=prior['payload']['value']
            if number(value) and number(v) and v>0:
                out[metric+'_yoy_pct']=(value/v-1)*100;citations[metric+'_yoy_pct']=[row['id'],prior['id']]
    rev=latest('revenue')
    if rev and rev['payload']['value']>0:
        for metric in ('gross_profit','op_income','net_income'):
            r=matching(metric,rev)
            if r:
                out[metric+'_margin_pct']=r['payload']['value']/rev['payload']['value']*100;citations[metric+'_margin_pct']=[rev['id'],r['id']]
                prior_ids=citations.get('revenue_yoy_pct',[])
                prior_rev=next((x for x in facts if len(prior_ids)==2 and x['id']==prior_ids[1]),None)
                prior_metric=matching(metric,prior_rev) if prior_rev else None
                if prior_metric and prior_rev['payload']['value']>0:
                    prior_margin=prior_metric['payload']['value']/prior_rev['payload']['value']*100
                    out[metric+'_prior_margin_pct']=prior_margin
                    out[metric+'_margin_change_pp']=out[metric+'_margin_pct']-prior_margin
                    out[metric+'_amount_change']=r['payload']['value']-prior_metric['payload']['value']
                    out[metric+'_amount_unit']=r['payload']['unit']
                    citations[metric+'_margin_change_pp']=[rev['id'],r['id'],prior_rev['id'],prior_metric['id']]
    if all(k in out for k in ('gross_profit_margin_change_pp','op_income_margin_change_pp','net_income_margin_change_pp')):
        out['operating_cost_share_pct']=out['gross_profit_margin_pct']-out['op_income_margin_pct']
        out['prior_operating_cost_share_pct']=out['gross_profit_prior_margin_pct']-out['op_income_prior_margin_pct']
        out['operating_cost_leverage_pp']=out['prior_operating_cost_share_pct']-out['operating_cost_share_pct']
    if all(k in out for k in ('op_income_margin_change_pp','net_income_margin_change_pp')):
        out['below_operating_margin_change_pp']=out['net_income_margin_change_pp']-out['op_income_margin_change_pp']
        out['below_operating_amount_change']=out['net_income_amount_change']-out['op_income_amount_change']
        citations['earnings_bridge']=list(dict.fromkeys(ident for metric in ('gross_profit','op_income','net_income') for ident in citations.get(metric+'_margin_change_pp',[])))
    # Cash-flow statements are often YTD. Pair exact windows and label them as such.
    cashflows=[r for r in facts if r['payload']['metric']=='cfo' and r['temporal']['state']=='current']
    if cashflows:
        cfo=max(cashflows,key=lambda r:(r['period_end'],r.get('period_start') or ''))
        capex=matching('capex',cfo);income=matching('net_income',cfo);sbc=matching('sbc',cfo)
        out['cashflow_window']={'start':cfo.get('period_start'),'end':cfo['period_end'],'duration_class':cfo['payload']['duration_class']}
        out['cashflow_cfo']=cfo['payload']['value'];out['cashflow_unit']=cfo['payload']['unit']
        if income:out['cashflow_net_income']=income['payload']['value']
        if capex:out['cashflow_capex']=capex['payload']['value']
        if capex:
            out['free_cash_flow']=cfo['payload']['value']-capex['payload']['value'];citations['free_cash_flow']=[cfo['id'],capex['id']]
        if income and income['payload']['value']>0:
            out['cash_conversion']=cfo['payload']['value']/income['payload']['value'];citations['cash_conversion']=[cfo['id'],income['id']]
        if sbc and capex:
            out['fcf_less_sbc']=out['free_cash_flow']-sbc['payload']['value'];citations['fcf_less_sbc']=[cfo['id'],capex['id'],sbc['id']]
        prior=prior_comparable(cfo)
        if prior:
            out['prior_cashflow_window']={'start':prior.get('period_start'),'end':prior['period_end'],'duration_class':prior['payload']['duration_class']}
            prior_income=matching('net_income',prior);prior_capex=matching('capex',prior)
            if prior['payload']['value']>0:
                out['cashflow_cfo_yoy_pct']=(cfo['payload']['value']/prior['payload']['value']-1)*100
                citations['cashflow_cfo_yoy_pct']=[cfo['id'],prior['id']]
            if capex and prior_capex and prior_capex['payload']['value']>0:
                out['cashflow_capex_yoy_pct']=(capex['payload']['value']/prior_capex['payload']['value']-1)*100
                citations['cashflow_capex_yoy_pct']=[capex['id'],prior_capex['id']]
            if income and prior_income and min(income['payload']['value'],prior_income['payload']['value'])>0:
                out['prior_cash_conversion']=prior['payload']['value']/prior_income['payload']['value']
                out['cash_conversion_change_pp']=100*(out['cash_conversion']-out['prior_cash_conversion'])
                citations['cash_conversion_change_pp']=citations['cash_conversion']+[prior['id'],prior_income['id']]
            if capex and prior_capex:
                prior_fcf=prior['payload']['value']-prior_capex['payload']['value']
                out['prior_free_cash_flow']=prior_fcf
                if prior_fcf>0:
                    out['free_cash_flow_yoy_pct']=(out['free_cash_flow']/prior_fcf-1)*100
                    citations['free_cash_flow_yoy_pct']=citations['free_cash_flow']+[prior['id'],prior_capex['id']]
    return out,citations


def deduplicate_news(rows):
    groups=[]
    for r in sorted(usable(rows,'news'),key=lambda x:x.get('published_at') or '',reverse=True):
        text=re.sub(r'[^a-z0-9 ]',' ',r.get('title','').lower());words=set(text.split())
        url=r['payload'].get('article_url') or r['url']
        match=None
        for g in groups:
            other=g['normalized'];overlap=len(words&set(other.split()))/max(1,len(words|set(other.split())))
            if url==g['url'] or overlap>.72 or SequenceMatcher(None,text,other).ratio()>.88:match=g;break
        if match:match['source_ids'].append(r['id'])
        else:groups.append({'headline':r['title'],'normalized':text,'url':url,'source_ids':[r['id']],
                           'published_at':r['published_at'],'state':r['temporal']['state'],'independence':r['independence'],
                           'note':'Discovery lead; sentiment and materiality require article evidence'})
    return [{k:v for k,v in g.items() if k!='normalized'} for g in groups]


def estimate_metrics(rows):
    out={};ids=[]
    for r in current(rows,'estimate'):
        dataset=r['payload'].get('dataset')
        for v in r['payload'].get('rows',[]):
            period=str(v.get('period',v.get('index','')))
            if period not in {'0q','+1q','0y','+1y'}:continue
            if dataset=='eps_trend':
                new,old=v.get('current'),v.get('30daysAgo')
                if number(new) and number(old) and old>0:
                    out[period+'_eps_revision_30d_pct']=(new/old-1)*100;ids.append(r['id'])
            if dataset=='eps_revisions':
                up,down=v.get('upLast30days'),v.get('downLast30days')
                if number(up) and number(down) and up+down>0:
                    out[period+'_revision_breadth']=(up-down)/(up+down);ids.append(r['id'])
    return out,list(dict.fromkeys(ids))


def option_metrics(rows,price):
    out=[]
    for r in usable(rows,'option'):
        p=r['payload'];calls=p.get('calls') or [];puts=p.get('puts') or []
        if not calls or not puts or not number(price) or price<=0:continue
        c=min(calls,key=lambda c:abs((c.get('strike') or 0)-price));put=min(puts,key=lambda c:abs((c.get('strike') or 0)-price))
        values={'expiry':p.get('expiry'),'source_id':r['id'],'state':r['temporal']['state'],'basis':'indicative snapshot; quote and OI clocks unverified'}
        cv=sum(v.get('volume') or 0 for v in calls);pv=sum(v.get('volume') or 0 for v in puts)
        if cv>0:values['put_call_volume_ratio']=pv/cv
        oi=sum(v.get('openInterest') or 0 for v in calls+puts)
        values['total_open_interest']=oi
        iv=[v.get('impliedVolatility') for v in (c,put) if number(v.get('impliedVolatility')) and v['impliedVolatility']>0]
        if iv:values['atm_iv_pct']=sum(iv)/len(iv)*100
        # Only a bounded positive two-sided market; zero bid or crossed quotes invalidate the move.
        valid=all(number(v.get('bid')) and number(v.get('ask')) and 0<v['bid']<=v['ask'] and (v['ask']-v['bid'])/((v['ask']+v['bid'])/2)<=.35 for v in (c,put))
        if valid and c.get('strike')==put.get('strike'):
            values['indicative_straddle_pct']=sum((v['bid']+v['ask'])/2 for v in (c,put))/price*100
        out.append(values)
    return out


def reverse_dcf(market_cap,fcf,*,discount=.10,terminal_growth=.03,years=5):
    """Required FCF growth, not a target price. Explicit assumptions, annual FCF only."""
    if not all(number(x) for x in (market_cap,fcf,discount,terminal_growth)) or market_cap<=0 or fcf<=0 or not 0<=terminal_growth<discount<1 or not 1<=years<=15:raise ValueError('Positive equity value/annual FCF and discount > terminal growth required')
    def pv(g):
        return sum(fcf*(1+g)**t/(1+discount)**t for t in range(1,years+1))+fcf*(1+g)**years*(1+terminal_growth)/(discount-terminal_growth)/(1+discount)**years
    lo,hi=-.90,3.0
    if not pv(lo)<=market_cap<=pv(hi):return {'required_growth_pct':None,'reason':'outside modeled growth bounds'}
    for _ in range(100):
        mid=(lo+hi)/2
        if pv(mid)>market_cap:hi=mid
        else:lo=mid
    return {'required_growth_pct':(lo+hi)/2*100,'discount_rate':discount,'terminal_growth':terminal_growth,'years':years,
            'basis':'Equity FCF approximation; ignores financing changes. Sensitivity, not intrinsic value certification.'}


def analyze(rows,ticker):
    own=[r for r in rows if r['ticker']==ticker];techrows=current(own,'technical')
    bench=next((r for r in current(rows,'technical') if r['ticker']=='SPY'),None)
    tech=technicals(techrows[-1],bench) if techrows else {}
    fund,fcites=fundamental_metrics(own);est,eids=estimate_metrics(own)
    profiles=[r['payload'] for r in current(own,'profile')]
    financial_firm=any(str(p.get('sic',''))[:2] in {'60','63'} or str(p.get('sic',''))=='6211' or
        re.search(r'bank|capital markets|insurance(?![\s-]*brokers)',str(p.get('industry','')),re.I) for p in profiles)
    applicability={}
    if financial_firm:
        for metric in ('cash_conversion','free_cash_flow','fcf_less_sbc','prior_cash_conversion','cash_conversion_change_pp','prior_free_cash_flow','free_cash_flow_yoy_pct'):
            fund.pop(metric,None);fcites.pop(metric,None)
        applicability['cash_flow_valuation']='Not applicable to this financial institution: assess regulatory capital, credit losses, funding costs, normalized earnings and book value.'
    findings=[]
    def finding(key,dimension,direction,title,detail,ids,materiality=2,conditions=None):
        evidence=[r for r in rows if r['id'] in ids]
        if not evidence:return
        findings.append({'id':key,'dimension':dimension,'direction':direction,'title':title,'detail':detail,
            'evidence_ids':ids,'materiality':materiality,'conditions':conditions or [],'basis':'computed',
            'independent_origins':len({r['independence'] for r in evidence}),
            'as_of':max((r.get('observed_at') or r.get('published_at') or '') for r in evidence)})
    rg=fund.get('revenue_yoy_pct');eg=fund.get('net_income_yoy_pct')
    if number(rg):finding('revenue_growth','fundamentals','bullish' if rg>10 else 'bearish' if rg<0 else 'neutral','Latest comparable-quarter revenue',f'Revenue changed {rg:.1f}% year over year for quarter ending {fund["revenue_period"]}. Growth alone does not establish an expectations beat.',fcites['revenue_yoy_pct'],3)
    if number(eg):finding('profit_growth','fundamentals','bullish' if eg>10 else 'bearish' if eg<0 else 'neutral','Profit growth versus revenue',f'Net income changed {eg:.1f}% year over year. Check one-offs and adjusted/GAAP reconciliation.',fcites['net_income_yoy_pct'],2)
    if 'operating_cost_leverage_pp' in fund:
        finding('earnings_bridge','accounting','neutral','Where the profit-margin change comes from',
            f'For the comparable quarter ending {fund["revenue_period"]}, gross margin changed {fund["gross_profit_margin_change_pp"]:+.1f}pp. '
            f'The implied operating-expense share excluding cost of revenue, (gross profit minus operating income) / revenue, moved from {fund["prior_operating_cost_share_pct"]:.1f}% to {fund["operating_cost_share_pct"]:.1f}%, contributing {fund["operating_cost_leverage_pp"]:+.1f}pp to operating margin. '
            f'Together these explain the {fund["op_income_margin_change_pp"]:+.1f}pp operating-margin change. Net margin changed {fund["net_income_margin_change_pp"]:+.1f}pp; the residual {fund["below_operating_margin_change_pp"]:+.1f}pp arose below operating profit. '
            'Reconcile interest, taxes and other non-operating items before calling that residual operating efficiency. This expense share excludes cost of revenue, already captured in gross margin; it does not identify a specific expense category or prove sustainability.',
            fcites['earnings_bridge'],3 if abs(fund['below_operating_margin_change_pp'])>=2 else 2)
    elif 'below_operating_margin_change_pp' in fund:
        finding('earnings_bridge','accounting','neutral','Separate operating and below-operating profit changes',
            f'For the comparable quarter ending {fund["revenue_period"]}, operating margin changed {fund["op_income_margin_change_pp"]:+.1f}pp and net margin {fund["net_income_margin_change_pp"]:+.1f}pp. '
            f'The below-operating residual is {fund["below_operating_margin_change_pp"]:+.1f}pp. Reconcile taxes, interest and investment gains before extrapolating headline earnings. '
            'A comparable gross-profit input is unavailable, so this does not decompose operating performance into gross margin and operating expenses.',
            fcites['earnings_bridge'],3 if abs(fund['below_operating_margin_change_pp'])>=2 else 2)
    operating_growth=fund.get('op_income_yoy_pct')
    if number(eg) and number(operating_growth) and eg-operating_growth>50 and fund.get('net_income_period')==fund.get('op_income_period'):
        finding('earnings_normalization','accounting','neutral','Headline profit growth needs normalization',
            f'Net income grew {eg:.1f}% versus operating income {operating_growth:.1f}% in comparable quarters. '
            'The difference is not proof of recurring operating improvement. Reconcile investment gains, interest, taxes and other non-operating items before extrapolating earnings or interpreting cash conversion.',
            fcites['net_income_yoy_pct']+fcites['op_income_yoy_pct'],3)
    for m in ('inventory','receivables'):
        g=fund.get(m+'_yoy_pct')
        if number(g) and number(rg) and g-rg>20 and fund.get(m+'_period')==fund.get('revenue_period'):
            scale=fund[m]/fund['revenue']*100 if fund.get('revenue',0)>0 else None
            small=scale is not None and scale<5
            detail=f'{m.title()} growth {g:.1f}% exceeds revenue growth {rg:.1f}% by {g-rg:.1f}pp.'
            if scale is not None:detail+=f' The balance equals {scale:.1f}% of quarterly revenue (a scale comparison, not a cash-flow ratio).'
            detail+=(' Small company-level exposure; verify concentrated segment risk before escalating.' if small else '')+' Test acquisition, seasonality and customer terms before concluding deterioration.'
            finding(m+'_divergence','accounting','neutral' if small else 'bearish',m.title()+' growth outruns sales',detail,fcites[m+'_yoy_pct']+fcites['revenue_yoy_pct'],1 if small else 3)
    cc=fund.get('cash_conversion')
    if number(cc):
        detail=f'Operating cash flow {fund["cashflow_cfo"]:,.0f} {fund["cashflow_unit"]} divided by net income {fund["cashflow_net_income"]:,.0f} {fund["cashflow_unit"]} was {cc:.2f} times over {fund["cashflow_window"]["start"]} to {fund["cashflow_window"]["end"]}. The numerator and denominator use the same reporting window.'
        ids=fcites['cash_conversion']
        if 'prior_cash_conversion' in fund:
            detail+=f' The comparable prior-year window was {fund["prior_cash_conversion"]:.2f} times; change {fund["cash_conversion_change_pp"]:+.1f} percentage points. This ratio does not isolate working capital from tax or non-cash profit effects.'
            ids=fcites['cash_conversion_change_pp']
        else:detail+=' A comparable prior-period ratio is unavailable; this level alone does not establish deterioration.'
        finding('cash_conversion','accounting','bearish' if cc<.75 else 'neutral','Cash generation relative to reported earnings',detail,ids,3 if cc<.75 else 2)
    if 'free_cash_flow_yoy_pct' in fund:
        detail=f'Operating cash flow less capital expenditure changed {fund["free_cash_flow_yoy_pct"]:+.1f}% year over year for the {fund["cashflow_window"]["duration_class"]} window ending {fund["cashflow_window"]["end"]}.'
        if 'cashflow_cfo_yoy_pct' in fund:detail+=f' Operating cash flow changed {fund["cashflow_cfo_yoy_pct"]:+.1f}%.'
        if 'cashflow_capex_yoy_pct' in fund:detail+=f' Capital expenditure changed {fund["cashflow_capex_yoy_pct"]:+.1f}%.'
        detail+=' Capital expenditure reduces FCF, not operating cash flow. These historical growth rates are not forward forecasts.'
        finding('cash_flow_growth','fundamentals','neutral','Comparable-window free cash flow',detail,fcites['free_cash_flow_yoy_pct'],2)
    if 'op_income_margin_change_pp' in fund:
        change=fund['op_income_margin_change_pp']
        finding('operating_margin','fundamentals','bullish' if change>0 else 'bearish' if change<0 else 'neutral','Comparable-quarter operating margin',
            f'Operating margin was {fund["op_income_margin_pct"]:.1f}% versus {fund["op_income_prior_margin_pct"]:.1f}% a year earlier, a {change:+.1f} percentage-point change for the quarter ending {fund["op_income_period"]}. Identify the actual cost, mix and pricing drivers before attributing the change.',fcites['op_income_margin_change_pp'],2)
    dilution=fund.get('shares_diluted_yoy_pct')
    if number(dilution) and dilution>3:finding('dilution','financing','bearish','Per-share dilution headwind',f'Weighted diluted shares rose {dilution:.1f}% in comparable quarters; reconcile stock splits and deal issuance.',fcites['shares_diluted_yoy_pct'],2)
    if techrows:
        ids=[techrows[-1]['id']]
        if 'distance_ma200_pct' in tech:
            d=tech['distance_ma200_pct'];finding('long_trend','technicals','bullish' if d>0 else 'bearish','Long-term trend context',f'Price is {d:.1f}% from its 200-session average. This measures trend, not valuation.',ids,1)
        rsi=tech.get('rsi14')
        if number(rsi) and (rsi<30 or rsi>70):finding('rsi_extreme','technicals','neutral','Oversold' if rsi<30 else 'Overbought',f'RSI14 is {rsi:.1f}. Require stabilization and fundamentals for mean reversion; strong trends can remain extreme.',ids,2,['No directional call from RSI alone'])
        rs=tech.get('relative_21d_pp')
        if number(rs):finding('relative_strength','technicals','bullish' if rs>3 else 'bearish' if rs< -3 else 'neutral','Market-relative performance',f'{rs:+.1f}pp versus SPY over the same 21-session window.',ids+([bench['id']] if bench else []),1)
        if tech.get('rv20_pct',0)>80 or tech.get('max_up_day_21_pct',0)>20:
            finding('lottery_risk','options','bearish','Speculative / gap-risk signature',f'Realized volatility {tech.get("rv20_pct",0):.1f}%; largest recent up day {tech.get("max_up_day_21_pct",0):.1f}%. Investigate binary catalysts and OTM speculation; this does not prove irrationality.',ids,3)
        if tech.get('median_dollar_volume20',float('inf'))<2_000_000:finding('illiquidity','microstructure','bearish','Execution capacity is limited','Median daily dollar volume is below $2m; gaps and spreads can dominate a nominal payoff.',ids,3)
    shorts=current(own,'short_interest')
    if shorts:
        r=shorts[-1];p=r['payload'];pct=p.get('shortPercentOfFloat');days=p.get('shortRatio')
        if number(pct) and pct>.10:finding('short_crowding','positioning','neutral','Large reported short position',f'{pct*100:.1f}% of float; days to cover {days}; settlement {r["period_end"]}. Borrow pressure and a positive catalyst are still required for a squeeze thesis.',[r['id']],3,['Verify borrow fee/utilization','Identify a positive catalyst','Require price/volume confirmation'])
    revisions=[v for k,v in est.items() if k.endswith('eps_revision_30d_pct')]
    if revisions:
        up=sum(v>0 for v in revisions);down=sum(v<0 for v in revisions)
        magnitude=max(abs(v) for v in revisions)
        materiality=1 if magnitude<1 else 2 if magnitude<3 else 3
        direction='neutral' if materiality==1 else 'bullish' if up>down else 'bearish' if down>up else 'neutral'
        detail='; '.join(f'{dict(zip(("0q","+1q","0y","+1y"),("Current quarter","Next quarter","Current fiscal year","Next fiscal year")))[k.split("_")[0]]}: EPS estimate {v:+.1f}% over 30 days' for k,v in est.items() if k.endswith('eps_revision_30d_pct'))+'. These are current provider fiscal-period labels, not pre-event consensus.'
        if materiality==1:detail+=' All changes are below 1%; the attention heuristic treats these as context, not a changed earnings thesis.'
        finding('estimate_revision','expectations',direction,'Forward earnings expectations are changing',detail,eids,materiality)
    if number(rg) and rg>10 and revisions and all(v<0 for v in revisions) and min(revisions)<=-1:finding('growth_revision_conflict','expectations','bearish','Strong reported growth conflicts with falling expectations','Backward-looking growth is positive while every observed forward EPS revision is negative. Investigate margins, outlook and the comparison base before buying the headline.',fcites['revenue_yoy_pct']+eids,3)
    for r in current(own,'filing'):
        form=r['payload']['form']
        if form in {'S-3','S-3ASR','424B5','NT 10-Q','NT 10-K','SC 13D','SC 13D/A'}:
            finding('filing_'+r['id'],'financing' if form.startswith(('S-3','424','NT')) else 'ownership','neutral','Investigate '+form,f'Filed {r["published_at"]}; read terms and intent. Filing existence alone establishes no completed issuance or investment thesis.',[r['id']],2)
    # Rank research attention by consequence, not by counting correlated indicators as independent votes.
    findings.sort(key=lambda f:(f['materiality'],f['independent_origins']),reverse=True)
    from .temporal import dated_call
    for row in current(own,'document'):
        if not dated_call(row) or row.get('authority') not in {'primary','issuer_statement'}:continue
        text=row['payload'].get('text','')
        match=re.search(r'\bshift(?:ing)?\s+from\s+finance(?:\s+leases)?\s+to\s+operating\s+leases\b',text,re.I)
        if match:
            findings.insert(0,{'id':'lease_classification_change','dimension':'accounting','direction':'neutral',
                'title':'Reported capex needs a consistent lease basis',
                'detail':'The dated issuer call describes a shift from finance to operating leases. A change in reported capex classification alone does not establish lower economic investment. Reconcile lease commitments and operating cash payments before treating lower capex as a cash-return catalyst.',
                'evidence_ids':[row['id']],'materiality':3,'conditions':[],
                'basis':'source_disclosure','independent_origins':1,'as_of':row.get('event_at')})
            break
    hypotheses=[]
    for name,(dimension,question) in QUESTIONS.items():
        related=[f for f in findings if f['dimension']==dimension]
        if name=='oversold_recovery':related=[f for f in findings if f['id'] in {'rsi_extreme','revenue_growth','estimate_revision','cash_conversion'}]
        if name=='short_squeeze':related=[f for f in findings if f['id'] in {'short_crowding','relative_strength','estimate_revision'}]
        if name=='lottery_speculation':related=[f for f in findings if f['id']=='lottery_risk']
        hypotheses.append({'name':name,'question':question,'support':[f['id'] for f in related if f['direction']=='bullish'],
            'challenge':[f['id'] for f in related if f['direction']=='bearish'],'context':[f['id'] for f in related if f['direction']=='neutral'],
            'attention_priority':max([f['materiality'] for f in related] or [0]),'status':'investigate' if related else 'evidence_gap'})
    hypotheses.sort(key=lambda h:h['attention_priority'],reverse=True)
    covered={f['dimension'] for f in findings}
    kinds={'fundamental':'fundamentals','technical':'technicals','short_interest':'positioning','borrow':'positioning','macro':'macro'}
    for r in own:
        if r['temporal']['state']!='current':continue
        if r['kind'] in kinds:covered.add(kinds[r['kind']])
        if r['kind']=='document' and r['payload'].get('dimension') in DIMENSIONS:covered.add(r['payload']['dimension'])
        if r['kind']=='news' and r['payload'].get('body_verified'):covered.add('news')
    coverage=[{'dimension':k,'question':v,'status':'current_inputs' if k in covered else 'needs_investigation',
               'sources':[s.id for s in SOURCES if s.dimension==k]} for k,v in DIMENSIONS.items()]
    from .valuation import valuation
    valuation_result=valuation(rows,ticker)
    if financial_firm:
        for key in ('ttm_fcf_proxy','ttm_fcf_less_sbc','fcf_yield_pct','implied_growth_sensitivity'):valuation_result.pop(key,None)
        valuation_result['cash_flow_model']='not_applicable_financial_institution'
        valuation_result['interpretation']=applicability['cash_flow_valuation']
    result={'technical_evidence':[techrows[-1]['id']] if techrows else [],'technical_as_of':techrows[-1].get('observed_at') if techrows else None,
            'metric_applicability':applicability,'valuation':valuation_result,'technicals':tech,'fundamentals':fund,'fundamental_evidence':fcites,'estimates':est,
            'options':option_metrics(own,tech.get('close')),'news_clusters':deduplicate_news(own),
            'findings':findings,'hypotheses':hypotheses,'coverage':coverage,
            'ranking_basis':'Research attention: materiality first, independently sourced support second. Not return probabilities or an additive buy score.'}
    from .reconciliation import reconcile
    from .non_gaap import reconciliations
    result['non_gaap_reconciliations']=reconciliations(rows,ticker,fund,fcites)
    result['reconciled_case']=reconcile(result)
    return result
