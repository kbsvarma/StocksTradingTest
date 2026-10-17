"""Read-only market desk. All results come from the complete live ledger."""
import json
import sqlite3
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from lanes import CRYPTO_ASSETS, NEW_ASSETS, ACTIVE_ASSETS, record_asset, lane_for, Z2, Z3
from html import escape
import math

STATUS=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1/state/status.json')

DECISIONS={
    'price_gate':'Waiting for entry price', 'cushion_gate':'Cushion below lane threshold',
    'outside_window':'Outside entry window', 'eligible':'Qualified · execution checks pending',
    'book_outside_limit':'Qualified · book outside price limits','insufficient_depth':'Qualified · insufficient depth',
    'spread_gate':'Spread too wide', 'no_current_market':'Waiting for market',
    'decision_candle_unavailable':'Waiting for completed candle',
    'no_decision_quote':'Waiting for quote', 'already_entered':'Already attempted', 'already_paper_entered':'Order already attempted — see orders below',
    'zero_fill_locked':'Canceled · nothing filled', 'order_pending':'Order reconciliation pending',
    'order_rejected':'Order rejected',
    'position_filled':'Position filled', 'position_settled':'Position settled',
    'data_error':'Public data unavailable; retrying', 'incomplete_spot':'Waiting for complete spot data',
    'activation_blocked':'Market activation pending', 'unexpected_exchange_route':'Market route changed',
}

def formatted(value,pattern):
    try:return pattern.format(float(value))
    except (TypeError,ValueError):return '—'

def trade_outcome(row):
    if row.get('status') != 'settled':
        return '—'
    if row.get('result') in ('yes', 'no') and row.get('side') in ('yes', 'no'):
        return 'WIN' if row['result'] == row['side'] else 'LOSS'
    return 'Unknown'

def ledger_summary(rows):
    settled = [r for r in rows if r.get('status') == 'settled']
    active = {'intent', 'unknown', 'ack', 'open', 'accounting_pending'}
    return dict(net=sum((Decimal(str(r['pnl'])) for r in settled), Decimal('0')),
                settled=len(settled), attempts=sum(r.get('attempt_number',1) for r in rows),
                pending=sum(r.get('status') in active for r in rows),
                wins=sum(trade_outcome(r) == 'WIN' for r in settled),
                losses=sum(trade_outcome(r) == 'LOSS' for r in settled),
                unfilled=sum(r.get('attempt_number',1)-1+int(r.get('status') == 'unfilled') for r in rows))

def money(value):
    return '$' + format(value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP), '+.2f')

def read_ledger():
    conn = sqlite3.connect(STATUS.with_name('orders.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=2)
    try:
        return [json.loads(r[0]) for r in conn.execute('SELECT record FROM orders ORDER BY rowid')]
    finally:
        conn.close()

def read_attempts():
    conn=sqlite3.connect(STATUS.with_name('orders.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=2)
    try:
        exists=conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='order_attempts'").fetchone()
        if not exists:return []
        return [json.loads(r[0]) for r in conn.execute('SELECT record FROM order_attempts ORDER BY rowid DESC LIMIT 100')]
    finally:conn.close()

ET = ZoneInfo('America/New_York')
ASSET_NAMES = {'BTC':'Bitcoin','ETH':'Ethereum','SOL':'Solana','XRP':'XRP',
               'COPPER':'Copper','NATGAS':'Natural gas','EURUSD':'Euro / US dollar','WTI':'Crude oil'}
SYMBOLS = {'BTC':'B','ETH':'E','SOL':'S','XRP':'X','COPPER':'Cu','NATGAS':'Ng','EURUSD':'€','WTI':'W'}
GROUPS = {'All markets':ACTIVE_ASSETS,'Crypto':CRYPTO_ASSETS,
          'Natural gas':('NATGAS',),'EUR/USD':('EURUSD',),'WTI':('WTI',)}
STRATEGIES = {'All strategies':None,'Original cushion':Z2,'Strong cushion':Z3}
ACTIVE = {'intent','unknown','ack','open','accounting_pending'}

def h(value):
    return escape(str(value), quote=True)

def decimal(value):
    try:
        d=Decimal(str(value))
        return d if d.is_finite() else Decimal(0)
    except (ValueError, ArithmeticError):return Decimal(0)

def cash(value, signed=False, places=2):
    if value is None:return '—'
    d=decimal(value)
    return ('+' if signed and d>=0 else '−' if d<0 else '')+'$'+format(abs(d),f',.{places}f')

def tone(value):
    return 'positive' if decimal(value)>0 else 'negative' if decimal(value)<0 else ''

def moment(value, pattern='%b %d · %I:%M %p'):
    try:return datetime.fromtimestamp(float(value),ET).strftime(pattern)
    except (TypeError,ValueError,OverflowError):return '—'

def filtered(rows, assets, lane=None):
    return [r for r in rows if record_asset(r) in assets and (not lane or r.get('lane',Z2)==lane)]

def health(d, now):
    age=max(0,now-d.get('ts',0))
    if d.get('stopped'):return 'stopped','STOPPED','Trading process has stopped',age
    if age>45:return 'stale','STALE','Heartbeat overdue. Trading status cannot be confirmed.',age
    if d.get('halted') or d.get('error'):return 'blocked','ENTRY BLOCKED',d.get('halted') or d['error'],age
    if d.get('mode')=='LIVE' and d.get('armed'):return 'live','LIVE','Automatic trading enabled',age
    return 'observation',d.get('mode','UNCONFIRMED'),'Live activation is not confirmed',age

def risk_snapshot(d, rows, now):
    """Mirror the displayed reservations; never call the trading engine."""
    active={r['ticker']:r for r in rows if r.get('status') in ACTIVE}
    reserved={t:max(decimal(r.get('filled')),decimal(r.get('requested_count',d.get('contracts',5)))) for t,r in active.items()}
    account=d.get('account') or {}
    for t,count in (account.get('positions') or {}).items():
        reserved[t]=max(abs(decimal(count)),reserved.get(t,Decimal(0)))
    risk=sum((qty*(lane_for(active[t]).reserve if t in active else Decimal(1)) for t,qty in reserved.items()),Decimal(0))
    today=datetime.fromtimestamp(now,ET).date().isoformat()
    realized=sum((decimal(r.get('pnl')) for r in rows if r.get('settled_day')==today and r.get('pnl') is not None),Decimal(0))
    return dict(contracts=sum(reserved.values(),Decimal(0)),reserved=risk,today=realized,
                daily_headroom=max(Decimal(0),decimal(d.get('daily_loss_budget'))+realized-risk))

def performance_points(rows):
    settled=sorted((r for r in rows if r.get('status')=='settled'),key=lambda r:r.get('close_ts') or r.get('created_ts') or 0)
    total=Decimal(0);points=[]
    if settled:
        start=settled[0].get('created_ts') or settled[0].get('close_ts') or 0
        points.append(dict(date=datetime.fromtimestamp(start,ET).replace(tzinfo=None),net=0.0,market='Start',outcome=''))
    for r in settled:
        total+=Decimal(str(r['pnl']))
        points.append(dict(date=datetime.fromtimestamp(r.get('close_ts') or r.get('created_ts') or 0,ET).replace(tzinfo=None),
                           net=float(total),market=r['ticker'],outcome=trade_outcome(r)))
    return points

def metric(label,value,note,css=''):
    return f'<div class="metric-card"><div class="eyebrow">{h(label)}</div><div class="metric-value {css}">{h(value)}</div><div class="metric-note">{h(note)}</div></div>'

def render_performance(st,rows,totals):
    import altair as alt
    import pandas as pd
    with st.container(border=True,key='desk_performance'):
        st.html('<div class="section-row"><div><div class="section-title">Realized performance</div><div class="section-sub">Cumulative net P&amp;L · after fees · selected view</div></div><span class="eyebrow">LIFETIME</span></div>')
        points=performance_points(rows)
        if points:
            base=alt.Chart(pd.DataFrame(points)).encode(
                x=alt.X('date:T',title=None,axis=alt.Axis(format='%b %d',labelColor='#76847b',grid=False,tickCount=5,domain=False,ticks=False,labelPadding=10)),
                y=alt.Y('net:Q',title=None,stack=None,axis=alt.Axis(format='$,.0f',labelColor='#76847b',domain=False,ticks=False,gridColor='#edf1ec',tickCount=4,labelPadding=9)),
                tooltip=[alt.Tooltip('date:T',title='Market close (ET)',format='%b %d, %H:%M'),alt.Tooltip('net:Q',title='Net P&L',format='$,.4f'),alt.Tooltip('market:N',title='Market')])
            area=base.mark_area(color=alt.Gradient(gradient='linear',stops=[alt.GradientStop(color='#d2e8dc',offset=0),alt.GradientStop(color='#ffffff',offset=1)],x1=0,y1=0,x2=0,y2=1),interpolate='step-after')
            line=base.mark_line(color='#287b59',strokeWidth=2,interpolate='step-after')
            losses=base.transform_filter(alt.datum.outcome=='LOSS').mark_point(color='#b85d56',filled=True,size=25)
            chart=(area+line+losses).properties(height=205).configure_view(stroke=None).configure(background='white',font='sans-serif')
            st.altair_chart(chart,width='stretch',theme=None)
        else:
            st.html('<div class="empty-state" style="min-height:215px;display:grid;place-content:center"><strong>Performance starts with the first settlement</strong>No settled trades in this view yet.</div>')
        cards=[]
        for label,lane in [('Original cushion',Z2),('Strong cushion',Z3)]:
            s=ledger_summary([r for r in rows if r.get('lane',Z2)==lane])
            cards.append(f'<div><span class="lane-name">{label}</span><span class="lane-value {tone(s["net"])}">{h(cash(s["net"],True))}</span><div class="lane-detail">{s["wins"]} wins · {s["losses"]} losses · {s["settled"]} settled</div></div>')
        st.html('<div class="lane-grid">'+''.join(cards)+'</div>')
        st.caption('By market close time (ET). Red markers identify losing settlements. Historical position sizes vary.')

def render_capital(st,d,all_rows,now):
    a=d.get('account') or {};balances=a.get('balances') or {str(a.get('exchange_index')):a.get('balance')}
    risk=risk_snapshot(d,all_rows,now)
    combined=sum((decimal(balances[k]) for k in ('0','2')),Decimal(0)) if all(balances.get(k) is not None for k in ('0','2')) else None
    count_limit=decimal(d.get('max_contracts'));risk_limit=decimal(d.get('max_risk'))
    count_pct=min(100,100*float(risk['contracts']/count_limit)) if count_limit else 0
    risk_pct=min(100,100*float(risk['reserved']/risk_limit)) if risk_limit else 0
    with st.container(border=True,key='desk_capital'):
        st.html(f'''<div class="eyebrow">Account &amp; capacity · all markets</div><div class="cash-value">{h(cash(combined))}</div>
        <div class="section-sub">Available cash in your Predictions account</div>
        <div class="split-row"><span>Crypto &amp; commodities</span><strong>{h(cash(balances.get('2')))}</strong></div>
        <div class="split-row"><span>EUR/USD</span><strong>{h(cash(balances.get('0')))}</strong></div>
        <div class="divider"></div>
        <div class="split-row"><span>Open / reserved contracts</span><strong>{risk['contracts']:g} / {count_limit:g}</strong></div>
        <div class="risk-bar"><span style="width:{count_pct:.2f}%"></span></div>
        <div class="split-row"><span>Reserved exposure</span><strong>{h(cash(risk['reserved']))} / {h(cash(risk_limit))}</strong></div>
        <div class="risk-bar"><span style="width:{risk_pct:.2f}%"></span></div>
        <div class="split-row"><span>Realized today · ET</span><strong class="{tone(risk['today'])}">{h(cash(risk['today'],True))}</strong></div>
        <div class="split-row"><span>Daily risk available</span><strong>{h(cash(risk['daily_headroom']))}</strong></div>
        <div class="section-sub">{h(cash(d.get('daily_loss_budget')))} daily loss limit, including open risk.<br>{d.get('contracts',5)} contracts per entry · one position per asset.</div>''')
        if now-a.get('ts',0)>45:st.warning('Account snapshot is stale; balances and capacity need verification.')

def lane_note(s,rule):
    if s.get('z') is not None:
        required=s.get('required_z')
        if required is None and s.get('remaining_minutes',0)>0:required=rule.threshold(s['remaining_minutes'])
        return 'z '+formatted(s['z'],'{:.2f}')+' / '+formatted(required,'{:.2f}')
    return {'price_gate':'Price outside band','outside_window':'Outside time window','no_current_market':'Awaiting market',
            'already_entered':'Market owned','already_paper_entered':'Market owned','data_error':'Data retry'}.get(s.get('status'),'Awaiting data')

def render_markets(st,d,rows,assets,lane,now):
    scans=[r for r in d.get('scans',[]) if r.get('asset') in assets and (not lane or r.get('lane',Z2)==lane)]
    stamp=max((r.get('observed_ts',0) for r in scans),default=0)
    freshness='Scan '+moment(stamp,'%I:%M:%S %p ET') if stamp else 'Awaiting first scan'
    if stamp and now-stamp>90:freshness='SCANS STALE · '+freshness
    st.html(f'<div class="section-row"><div><div class="section-title">Live market board</div><div class="section-sub">Current signal conditions across your selected markets</div></div><div class="section-sub">{h(freshness)}</div></div>')
    cards=[]
    for asset in assets:
        es=[s for s in scans if s.get('asset')==asset]
        choice=next((s for s in es if s.get('status')=='eligible'),es[0] if es else {})
        stats=ledger_summary([r for r in rows if record_asset(r)==asset])
        status=DECISIONS.get(choice.get('status'),'Awaiting scan')
        if choice.get('status')=='eligible':
            if choice.get('execution_executable'):status='Executable · order processing'
            elif choice.get('execution_status'):status='Qualified · '+choice['execution_status']
        cls='good' if choice.get('execution_executable') else 'warn' if choice.get('status') in ('data_error','activation_blocked') else ''
        blocked=(d.get('assets',{}).get(asset) or {}).get('blocked')
        if blocked:status=blocked;cls='warn'
        if stamp and now-stamp>90:status='Scanner data is stale';cls='warn'
        price=formatted(float(choice['signal_ask'])*100,'{:.1f}¢') if choice.get('signal_ask') is not None else '—'
        price_kind='ask';price_side=choice.get('side') or ''
        position=next((r for r in rows if r.get('ticker')==choice.get('ticker') and r.get('status') in ACTIVE),None)
        if position:
            status='Holding '+formatted(position.get('filled'),'{:g}')+' contracts' if decimal(position.get('filled'))>0 else 'Order pending reconciliation'
            cls='good' if decimal(position.get('filled'))>0 else 'warn'
            price=formatted(float(position['entry'])*100,'{:.1f}¢') if position.get('entry') is not None else '—'
            price_kind='entry';price_side=position.get('side') or ''
        if stamp and now-stamp>90:status='Scanner data is stale';cls='warn'
        minutes=formatted(choice.get('remaining_minutes'),'{:g}m left')
        lanes=''.join(f'<div class="market-lane"><span>{"Original" if s.get("lane",Z2)==Z2 else "Strong cushion"}</span><span>{h(lane_note(s,lane_for(s)))}</span></div>' for s in es)
        cards.append(f'''<div class="market-card"><div class="market-top"><div class="asset-lockup"><div class="asset-icon">{h(SYMBOLS[asset])}</div><div><div class="asset-symbol">{h('EUR/USD' if asset=='EURUSD' else asset)}</div><div class="asset-name">{h(ASSET_NAMES[asset])}</div></div></div><span class="market-time">{h(minutes)}</span></div>
        <div class="market-status {cls}">{h(status)}</div><div class="market-price">{h(price)}<small>{h(price_side.upper())} {price_kind}</small></div>{lanes}
        <div class="market-footer"><span class="muted">{stats['settled']} settled</span><strong class="{tone(stats['net'])}">{h(cash(stats['net'],True))}</strong></div></div>''')
    st.html('<div class="market-grid">'+''.join(cards)+'</div>')
    st.caption('Ask is the completed-minute signal quote, not a live executable price. A dash means that input was not evaluated. Orders require all signal, price, depth and risk checks.')
    with st.expander('Detailed scanner & market identifiers'):
        st.dataframe([{'Asset':r.get('asset'),'Strategy':'Original' if r.get('lane',Z2)==Z2 else 'Strong cushion',
            'Ask (¢)':round(r['signal_ask']*100,2) if r.get('signal_ask') is not None else None,'z':r.get('z'),'Required z':r.get('required_z'),
            'Minutes left':r.get('remaining_minutes'),'Decision':DECISIONS.get(r.get('status'),r.get('status')),
            'Executable':bool(r.get('execution_executable')),'Buy limit (¢)':float(r['execution_cap'])*100 if r.get('execution_cap') else None,
            'Account checks (ms)':round(r['execution_account_ms'],1) if r.get('execution_account_ms') is not None else None,
            'Execution':r.get('execution_status') or '—','Market':r.get('ticker','—')} for r in scans],hide_index=True,width='stretch')

def render_activity(st,rows):
    import pandas as pd
    left,right=st.columns([3,1])
    with left:st.html('<div class="section-title">Orders &amp; settlements</div><div class="section-sub">Actual fills and fees · all history available through the filters</div>')
    with right:view=st.selectbox('Show',('All activity','Wins','Losses','Open / pending','Unfilled'),key='desk_activity_filter')
    shown=[r for r in rows if view=='All activity' or (view=='Wins' and trade_outcome(r)=='WIN') or (view=='Losses' and trade_outcome(r)=='LOSS') or (view=='Open / pending' and r.get('status') in ACTIVE) or (view=='Unfilled' and r.get('status')=='unfilled')]
    if not shown:
        st.html('<div class="empty-state"><strong>No '+('orders yet' if not rows else 'matching activity')+'</strong>'+('The first order will appear here when all entry conditions pass.' if not rows else 'Choose another activity filter to view trades.')+'</div>')
        return
    data=[]
    for r in sorted(shown,key=lambda x:x.get('created_ts',0),reverse=True):
        outcome=trade_outcome(r)
        data.append({'Entered (ET)':moment(r.get('created_ts')),'Asset':record_asset(r),
            'Strategy':'Original' if r.get('lane',Z2)==Z2 else 'Strong cushion','Side':r.get('side','').upper(),
            'Filled':formatted(r.get('filled'),'{:g}'),'Entry':formatted(r.get('entry'),'${:.3f}'),
            'Fees':cash(r.get('fees'),places=4),'Result':outcome.title() if outcome!='—' else r.get('status','').replace('_',' ').title(),
            'Net P&L':cash(r.get('pnl'),True,4),'Attempts':r.get('attempt_number',1),'Market':r['ticker']})
    frame=pd.DataFrame(data)
    def style(v):
        return 'color:#16744f;font-weight:600' if v=='Win' or str(v).startswith('+$') else 'color:#b54747;font-weight:600' if v=='Loss' or str(v).startswith('−$') else ''
    st.dataframe(frame.head(100).style.map(style,subset=['Result','Net P&L']),hide_index=True,width='stretch',height=min(460,42+35*len(data)),row_height=35)
    st.caption(f'Showing {min(100,len(data))} of {len(data)} matching markets. Summary and chart always use the complete selected ledger.')
    st.download_button('Export selected history',frame.to_csv(index=False),'kalshi-trades.csv','text/csv',key='desk_export')

def render_execution_funnel(st,diagnostics):
    st.html('<div class="section-row"><div><div class="section-title">Signal to fill</div><div class="section-sub">Last 24 hours · unique markets · selected view</div></div></div>')
    if diagnostics is None:st.warning('Execution diagnostics are temporarily unavailable.');return
    c=diagnostics['counts']
    st.html('<div class="metric-grid">'+
        metric('1 · Qualified',str(c['qualified']),'Signal rules passed; price may still be unavailable')+
        metric('2 · Executable',str(c['executable']),'Fresh book, full size, risk and price checks passed')+
        metric('3 · Submitted',str(c['submitted']),'Order request attempted; acceptance is reconciled')+
        metric('4 · Filled',str(c['filled']),'Exchange-confirmed contracts')+'</div>')
    st.caption(f"Unfilled outcomes: {c['book_blocked']} price/depth · {c['risk_blocked']} risk · {c['zero_fill']} canceled zero · {c['pending']} pending · {c['other']} other. Executable is a checked opportunity, not a guaranteed fill.")
    if c['other_lane']:st.caption(f"{c['other_lane']} qualifying markets filled through the other strategy; these are not missed bets.")
    if c['historical_executable_lower_bound']:
        st.caption('Before the stage-logging upgrade, executable counts are a confirmed minimum from actual submissions. Earlier unsubmitted executable checks cannot be reconstructed reliably.')

def render_operations(st,d,rows,assets,lane,diagnostics=None):
    st.html('<div class="section-title">Execution health</div><div class="section-sub">Separate signal eligibility from execution and risk constraints.</div>')
    try:
        if diagnostics is None:raise ValueError('Diagnostics unavailable')
        result=diagnostics['lanes']
        labels={'lane':'Strategy','qualified':'Qualified','executable':'Executable','submitted':'Submitted','filled':'Filled','other_lane':'Other lane filled','zero_fill':'Zero fills',
                'pending':'Pending','risk_blocked':'Risk blocked','book_blocked':'Price / depth blocked','other':'Other',
                'owned_market_skips':'Owned markets skipped','data_error_minutes':'Data error decisions','input_gap_minutes':'Input gap decisions'}
        st.dataframe([{labels.get(k,k):('Original' if v==Z2 else 'Strong cushion' if v==Z3 else v) for k,v in r.items() if k!='historical_executable_lower_bound'} for r in result],hide_index=True,width='stretch')
        st.caption('Last 24 hours · unique markets per strategy. Overlapping signals can appear in both strategy rows; the overview counts each market once. Owned skips do not prove another qualifying signal.')
        detail=diagnostics['markets']
        st.dataframe([{'Market':r['ticker'],'Asset':r['asset'],'Qualified':r['qualified'],'Executable':r['executable'],'Submitted':r['submitted'],'Filled':r['filled'],
            'Result / blocker':r['outcome'].replace('_',' '),'Last check':r['reason'],'Buy cap':r['limit']} for r in detail],hide_index=True,width='stretch')
    except (sqlite3.Error,OSError,ValueError,KeyError):st.warning('Execution diagnostics are temporarily unavailable.')
    with st.expander('Entry rules & shared limits'):
        rules=[]
        for a in assets:
            for name in ([lane] if lane else [Z2,Z3]):
                r=lane_for(dict(asset=a,lane=name))
                z=f'{r.z:g}' if not r.z_slope else f'max(0.5, {r.z:g} + 0.5 × log₂(t / 5))'
                rules.append({'Asset':a,'Strategy':'Original' if name==Z2 else 'Strong cushion','Minimum z':z,
                    'Signal band':f'{r.floor*100:g}–<{r.ceiling*100:g}¢','Buy cap':f'{r.cap*100:g}¢',
                    'Minutes remaining':f'{r.min_minutes:g}–{r.max_minutes:g}','Contracts':r.contracts})
        st.dataframe(rules,hide_index=True,width='stretch')
        st.write('One entry per market across both lanes. Up to three IOC submissions within 25 seconds, only after confirmed zero fills. Any fill ends retries. Hold to settlement.')
        premium=(d.get('lanes') or {}).get(Z3,{})
        st.caption(f"Shared: {d.get('max_contracts')} contracts / USD {d.get('max_risk')} exposure / USD {d.get('daily_loss_budget')} daily risk including open positions. Strong cushion: {premium.get('max_contracts')} contracts / USD {premium.get('max_risk')} exposure / USD {premium.get('daily_loss_budget')} daily risk, always subject to the shared limits.")
    with st.expander('Individual order attempts'):
        try:
            attempts=filtered(read_attempts(),assets,lane)
            st.dataframe([{'Market':r['ticker'],'Attempt':r.get('attempt_number',1),'State':r.get('status'),
                'Filled':r.get('filled','0'),'Limit':r.get('favorite_limit'),'Order ID':r.get('order_id','Pending')} for r in attempts],hide_index=True,width='stretch')
            st.caption('Filtered from the 100 most recent submission records. Market-level history remains available in Trade history.')
        except (sqlite3.Error,OSError,ValueError):st.warning('Attempt history is temporarily unavailable.')

def render(st):
    now=time.time()
    st.html('<style>'+Path(__file__).with_name('dashboard.css').read_text()+'</style>')
    st.html(f'<div class="desk-brand"><div class="brand-lockup"><div class="brand-mark">k.</div><div><div class="brand-name">Kalshi Desk</div><div class="brand-sub">Automated markets</div></div></div><div class="brand-date">{h(moment(now,"%A, %B %d"))}<br>{h(moment(now,"%I:%M %p ET"))}</div></div>')
    st.html('<div class="desk-title">Trading overview</div><div class="desk-sub">Performance, market signals and execution in one place.</div>')
    try:d=json.loads(STATUS.read_text())
    except (OSError,ValueError):st.error('Status unavailable. Trading state cannot be confirmed.');return
    state,label,detail,age=health(d,now)
    enabled=sum(bool(a.get('enabled')) for a in d.get('assets',{}).values())
    st.html(f'<div class="status-strip {state}"><div class="status-left"><span class="pill"><span class="status-dot"></span>{h(label)}</span><span>{h(detail)} · {enabled} markets enabled</span></div><span>Updated {age:.0f}s ago</span></div>')
    try:
        all_rows=read_ledger();ledger_summary(all_rows)
    except (OSError,sqlite3.Error,ValueError,KeyError,ArithmeticError):st.error('Ledger unavailable. Performance totals cannot be verified.');return
    left,right=st.columns([3,1])
    with left:group=st.radio('Markets',list(GROUPS),horizontal=True,key='desk_market_filter')
    with right:choice=st.selectbox('Strategy',list(STRATEGIES),key='desk_strategy_filter')
    assets=GROUPS[group];lane=STRATEGIES[choice];rows=filtered(all_rows,assets,lane);totals=ledger_summary(rows)
    try:
        from operations_view import read_operations
        diagnostics=read_operations(STATUS.with_name('orders.sqlite3'),now-86400,assets,lane)
    except (sqlite3.Error,OSError,ValueError,KeyError):diagnostics=None
    for a in assets:
        issue=(d.get('assets',{}).get(a) or {}).get('blocked')
        if issue:st.warning(a+': '+issue)
    if lane and (d.get('lanes',{}).get(lane) or {}).get('blocked'):st.warning(d['lanes'][lane]['blocked'])
    winrate=f'{100*totals["wins"]/totals["settled"]:.1f}%' if totals['settled'] else '—'
    st.html('<div class="metric-grid">'+metric('Net realized P&L',cash(totals['net'],True),'Lifetime · after fees · selected view',tone(totals['net']))+
        metric('Win rate',winrate,f'{totals["wins"]} wins / {totals["losses"]} losses')+
        metric('Settled markets',str(totals['settled']),f'{totals["attempts"]} order attempts · {totals["unfilled"]} unfilled')+
        metric('Open / pending',str(totals['pending']),'Positions held to settlement')+'</div>')
    render_execution_funnel(st,diagnostics)
    chart,capital=st.columns([2.1,1],gap='medium')
    with chart:render_performance(st,rows,totals)
    with capital:render_capital(st,d,all_rows,now)
    markets,activity,operations=st.tabs(['Markets','Trade history','Operations'])
    with markets:render_markets(st,d,rows,assets,lane,now)
    with activity:render_activity(st,rows)
    with operations:render_operations(st,d,rows,assets,lane,diagnostics)
    archived=[r for r in all_rows if record_asset(r)=='COPPER']
    if archived:
        with st.expander('Archived markets · historical results'):
            old=ledger_summary(archived)
            st.write(f"Copper retired · {old['settled']} settled · {old['wins']} wins / {old['losses']} losses · net {cash(old['net'],True)}")
            st.caption('Preserved in the ledger. Excluded from active-market performance and execution counts. Account cash and risk still cover the complete account.')
            st.dataframe([{'Market':r['ticker'],'Status':r['status'],'Outcome':trade_outcome(r),'Net':r.get('pnl')} for r in archived],hide_index=True,width='stretch')
    filled=[r for r in rows if decimal(r.get('filled'))>0]
    last=max(filled,key=lambda r:r.get('created_ts',0)) if filled else None
    last_text='Last fill '+moment(last['created_ts'])+' ET · '+record_asset(last) if last else 'No fills in this view yet'
    st.html(f'<div class="desk-footer"><span>Actual fills &amp; fees · full ledger · exact selected net {h(cash(totals["net"],True,4))}<br>Historical untagged trades belong to Original. Paper results are excluded.</span><span>{h(last_text)}<br>Refreshes every 10 seconds · signals checked each minute</span></div>')
