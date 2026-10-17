"""Read-only current crypto lane. No trading imports or controls."""
import json
import sqlite3
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from lanes import CRYPTO_ASSETS, NEW_ASSETS, record_asset, lane_for, Z2, Z3

STATUS=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1/state/status.json')

DECISIONS={
    'price_gate':'Waiting for entry price', 'cushion_gate':'Cushion below lane threshold',
    'outside_window':'Outside entry window', 'eligible':'Signal eligible',
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

def render(st):
    st.title('Kalshi · 15-minute markets')
    try:d=json.loads(STATUS.read_text())
    except (OSError,ValueError):
        st.warning('Status is unavailable. Trading state cannot be confirmed.');return
    st.caption('Crypto · Copper · Natural gas · EUR/USD | Five contracts per market')
    age=max(0,time.time()-d.get('ts',0))
    mode=d.get('mode','UNKNOWN')
    issue=d.get('halted') or d.get('error')
    if d.get('stopped') or age>45:
        st.error(f'STOPPED / STALE · last heartbeat {age:.0f}s ago')
    elif issue:
        st.warning(f'ENTRY BLOCKED · {issue}')
    elif mode=='LIVE' and d.get('armed'):
        st.success(f'LIVE · Automatic trading enabled · updated {age:.0f}s ago')
    else:
        st.info(f'{mode} · Real orders disabled or activation unconfirmed · updated {age:.0f}s ago')
    account=d.get('account') or {}
    balances=account.get('balances') or {str(account.get('exchange_index')):account.get('balance')}
    st.caption('Crypto / commodities cash: '+formatted(balances.get('2'),'USD {:.2f}')+' · EUR/USD route cash: '+formatted(balances.get('0'),'USD {:.2f}'))
    try:
        rows = read_ledger()
        totals = ledger_summary(rows)
    except (OSError, sqlite3.Error, ValueError, KeyError, ArithmeticError):
        st.error('Ledger unavailable: summary cannot be verified.'); return
    st.caption(f"Shared limits: max {d.get('max_contracts',20)} contracts · ${d.get('max_risk','19.00')} reserved exposure · ${d.get('daily_loss_budget','10')} daily loss budget including open risk. One position per asset; original z2 gets first opportunity.")
    groups={'Crypto':CRYPTO_ASSETS,'Copper':('COPPER',),'Natural gas':('NATGAS',),'EUR/USD':('EURUSD',),'All markets':CRYPTO_ASSETS+NEW_ASSETS}
    group=st.radio('Markets',list(groups),horizontal=True,key='market_group_selector')
    selected_assets=groups[group]
    rows=[r for r in rows if record_asset(r) in selected_assets]
    totals=ledger_summary(rows)
    d['scans']=[r for r in d.get('scans',[]) if r.get('asset') in selected_assets]
    for a in selected_assets:
        info=(d.get('assets') or {}).get(a,{})
        if info.get('blocked'):st.warning(a+': '+info['blocked'])
    names={'All strategies':None,'Original lane · 5 contracts':'cushion_z2','Premium lane · 5 contracts':'premium_z3'}
    choice=st.radio('Strategy',list(names),horizontal=True,key='crypto_lane_selector')
    selected=names[choice]
    if selected:
        rows=[r for r in rows if r.get('lane','cushion_z2')==selected]
        totals=ledger_summary(rows)
        d['scans']=[r for r in d.get('scans',[]) if r.get('lane','cushion_z2')==selected]
        lane=(d.get('lanes') or {}).get(selected,{})
        if lane.get('blocked'):st.warning(lane['blocked'])
        if selected=='premium_z3':
            st.caption('Strong cushion: 5 contracts · lane max 20 contracts / $20 reserved / $25 daily budget. Shared $19 exposure and $10 daily loss limits still take precedence.')
    # Totals and displayed trades come from the same complete ledger snapshot.
    d['trades'] = rows[-100:]
    cols=st.columns(6)
    cols[0].metric('Net P&L · selected view',money(totals['net']))
    cols[1].metric('Wins',totals['wins'])
    cols[2].metric('Losses',totals['losses'])
    cols[3].metric('Settled total',totals['settled'])
    cols[4].metric('Open / pending',totals['pending'])
    cols[5].metric('Order attempts',totals['attempts'])
    st.caption(f"Lifetime settled results: {totals['wins']} wins · {totals['losses']} losses · {totals['unfilled']} unfilled attempts. Exact net after fees: ${totals['net']:+.4f}. Summary includes the complete ledger; table shows the latest 100 markets.")
    if not selected:
        st.dataframe([dict(Strategy=label,**{k:(str(v) if isinstance(v,Decimal) else v) for k,v in ledger_summary([r for r in rows if r.get('lane','cushion_z2')==name]).items()}) for label,name in names.items() if name],width='stretch',hide_index=True)
    losses = [r for r in rows if trade_outcome(r) == 'LOSS']
    if losses:
        st.subheader('Losing trades · all history')
        st.dataframe([{'Strategy':r.get('lane','cushion_z2'), 'Market':r['ticker'], 'Outcome':'LOSS', 'Side':r['side'].upper(),
                       'Contracts':formatted(r.get('filled'),'{:g}'),
                       'Net loss after fees':formatted(r.get('pnl'),'${:+.4f}')}
                      for r in reversed(losses)], width='stretch', hide_index=True)
    st.subheader('Market scanner')
    feed=d.get('book_feed') or {}
    if feed:
        st.caption('Book feed: '+('connected · REST validation before every order' if feed.get('connected') else 'REST polling fallback · '+feed.get('error','Connecting')))
    with st.expander('Why trades were skipped · last 24 hours'):
        try:
            from operations_view import read_funnel
            funnel=read_funnel(STATUS.with_name('orders.sqlite3'),time.time()-86400,selected_assets)
            if selected:funnel=[r for r in funnel if r['lane']==selected]
            st.dataframe(funnel,hide_index=True,width='stretch')
            st.caption('Unique markets, not repeated checks. Owned-market skips were not evaluated again and do not prove an overlapping signal. Filled results come from the order ledger.')
        except (sqlite3.Error,OSError,ValueError,KeyError):st.info('Execution diagnostics temporarily unavailable.')
        risk=d.get('last_risk_check') or {}
        if risk:
            st.caption('Latest risk check: '+risk.get('ticker','')+' · realized today $'+risk.get('realized_today','?')+' · existing reserved $'+risk.get('existing_reserved','?')+' · proposed reservation $'+risk.get('new_reserved','?')+' · daily headroom after entry $'+risk.get('daily_headroom','?'))
    scans=d.get('scans',[])
    if scans:
        st.dataframe([{'Strategy':r.get('lane','cushion_z2'),'Asset':r.get('asset'),'Side':(r.get('side') or '—').upper(),
                       'Ask':formatted(r.get('signal_ask'),'{:.1%}').replace('%','¢'),
                       'Cushion z':formatted(r.get('z'),'{:.2f}'),
                       'Required z':formatted(r.get('required_z'),'{:.2f}'),
                       'Minutes left':formatted(r.get('remaining_minutes'),'{:.1f}'),
                       'Status':DECISIONS.get(r.get('status'),str(r.get('status','Waiting')).replace('_',' ').capitalize()),
                       'Execution':r.get('execution_status') or '—',
                       'Market':r.get('ticker') or '—'} for r in scans],width='stretch',hide_index=True)
        scan_ts=max(r.get('observed_ts',0) for r in scans)
        st.caption('Last scan: '+datetime.fromtimestamp(scan_ts,ZoneInfo('America/New_York')).strftime('%I:%M:%S %p ET')+' · signals checked each minute')
    else:st.info('Waiting for the first market scan.')
    st.subheader(group+' orders & settlements')
    if d.get('trades'):
        settled = [r for r in d['trades'] if r.get('status') == 'settled']
        if settled:
            last = max(settled, key=lambda r: r.get('close_ts') or r.get('created_ts') or 0)
            st.caption('Latest completed market: ' + last['ticker'] + ' · **' + trade_outcome(last) + '** · Net ' + formatted(last.get('pnl'), '${:+.4f}'))
        st.dataframe([{'Strategy':r.get('lane','cushion_z2'),'Market':r['ticker'],'State':r['status'].replace('_',' ').title(),'Outcome':trade_outcome(r),'Side':r['side'].upper(),
                       'Order attempts':r.get('attempt_number',1),
                       'Filled':formatted(r.get('filled'),'{:g}'),'Entry':formatted(r.get('entry'),'${:.3f}'),
                       'Fees':formatted(r.get('fees'),'${:.4f}'),
                       'Net P&L':formatted(r.get('pnl'),'${:+.4f}')} for r in reversed(d['trades'])],width='stretch',hide_index=True)
    else:st.info('No orders yet. The bot waits until all entry conditions qualify.')
    with st.expander('Individual order attempts'):
        try:
            attempts=read_attempts()
            attempts=[r for r in attempts if record_asset(r) in selected_assets]
            if selected:attempts=[r for r in attempts if r.get('lane','cushion_z2')==selected]
            st.dataframe([{'Market':r['ticker'],'Attempt':r.get('attempt_number',1),'State':r['status'],
                           'Filled':r.get('filled','0'),'Limit':r.get('favorite_limit'),
                           'Order ID':r.get('order_id','Pending reconciliation')} for r in attempts],hide_index=True,width='stretch')
        except (sqlite3.Error,OSError,ValueError):st.info('Attempt history temporarily unavailable.')
    with st.expander('Entry rules'):
        for a in selected_assets:
            if a not in NEW_ASSETS:continue
            first=lane_for(dict(asset=a,lane=Z2));second=lane_for(dict(asset=a,lane=Z3))
            adaptive=' + 0.5 × log₂(minutes / 5), minimum 0.5' if first.z_slope else ''
            st.write(f'{a}: original z ≥ {first.z}{adaptive}; premium z ≥ {second.z}{adaptive}. {first.min_minutes:g}–{first.max_minutes:g} minutes remaining. Signal bands {first.floor*100:g}–<95¢ / 93–<98¢; maximum buys 93.9¢ / 97.5¢. Five contracts; same shared limits.')
        if any(a in CRYPTO_ASSETS for a in selected_assets):st.write('Crypto: completed-minute favorite ask at least 85¢ and below 95¢ · cushion z ≥ 2 · 3–10 minutes remaining · spread ≤ 2¢. Maximum buy price 93.9¢, even when the signal quote is higher. Up to three capped IOC submissions on the same signal within 25 seconds, only after confirmed zero-fill cancellation. Any fill ends retries; contracts held to settlement.')
        if any(a in CRYPTO_ASSETS for a in selected_assets):st.write('Crypto premium: completed-minute favorite ask 93–<98¢ · z ≥ 3 · maximum buy 97.5¢ · 5 contracts. Same 3–10 minute window, spread ≤ 2¢, capped IOC and hold to settlement. The cheaper-value experimental rule is not enabled for live orders.')
    st.caption('Actual exchange fills and fees only. Historical untagged trades belong to original z2. Paper results are excluded.')
