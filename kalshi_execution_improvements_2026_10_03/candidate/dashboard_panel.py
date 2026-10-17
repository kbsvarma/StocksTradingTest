"""Read-only current crypto lane. No trading imports or controls."""
import json
import sqlite3
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import time
from datetime import datetime
from zoneinfo import ZoneInfo

STATUS=Path('/home/varma/kalshi_revalidation_2026_09_12/live_v1/state/status.json')

DECISIONS={
    'price_gate':'Waiting for entry price', 'cushion_gate':'Cushion below lane threshold',
    'outside_window':'Outside entry window', 'eligible':'Signal eligible',
    'spread_gate':'Spread too wide', 'no_current_market':'Waiting for market',
    'decision_candle_unavailable':'Waiting for completed candle',
    'no_decision_quote':'Waiting for quote', 'already_entered':'Already attempted', 'already_paper_entered':'Order already attempted — see orders below',
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
                settled=len(settled), attempts=len(rows),
                pending=sum(r.get('status') in active for r in rows),
                wins=sum(trade_outcome(r) == 'WIN' for r in settled),
                losses=sum(trade_outcome(r) == 'LOSS' for r in settled),
                unfilled=sum(r.get('status') == 'unfilled' for r in rows))

def money(value):
    return '$' + format(value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP), '+.2f')

def read_ledger():
    conn = sqlite3.connect(STATUS.with_name('orders.sqlite3').as_uri() + '?mode=ro', uri=True, timeout=2)
    try:
        return [json.loads(r[0]) for r in conn.execute('SELECT record FROM orders ORDER BY rowid')]
    finally:
        conn.close()

def render(st):
    st.title('Crypto · 15-minute markets')
    try:d=json.loads(STATUS.read_text())
    except (OSError,ValueError):
        st.warning('Status is unavailable. Trading state cannot be confirmed.');return
    st.caption('BTC · ETH · SOL · XRP | Original z2: 5 contracts · Strong cushion z3: 5 contracts')
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
    if account.get('exchange_index')==2:
        st.caption('Crypto exchange cash: '+formatted(account.get('balance'),'USD {:.2f}'))
    try:
        rows = read_ledger()
        totals = ledger_summary(rows)
    except (OSError, sqlite3.Error, ValueError, KeyError, ArithmeticError):
        st.error('Ledger unavailable: summary cannot be verified.'); return
    st.caption(f"Shared limits: max {d.get('max_contracts',20)} contracts · ${d.get('max_risk','19.00')} reserved exposure · ${d.get('daily_loss_budget','10')} daily loss budget including open risk. One position per asset; original z2 gets first opportunity.")
    names={'All crypto':None,'Original z2 · 5 contracts':'cushion_z2','Strong cushion z3 · 5 contracts':'premium_z3'}
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
    st.caption(f"Lifetime settled results: {totals['wins']} wins · {totals['losses']} losses · {totals['unfilled']} unfilled attempts. Exact net after fees: ${totals['net']:+.4f}. Summary includes the complete ledger; table shows the latest 100 attempts.")
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
    scans=d.get('scans',[])
    if scans:
        st.dataframe([{'Strategy':r.get('lane','cushion_z2'),'Asset':r.get('asset'),'Side':(r.get('side') or '—').upper(),
                       'Ask':formatted(r.get('signal_ask'),'{:.1%}').replace('%','¢'),
                       'Cushion z':formatted(r.get('z'),'{:.2f}'),
                       'Minutes left':formatted(r.get('remaining_minutes'),'{:.1f}'),
                       'Status':DECISIONS.get(r.get('status'),str(r.get('status','Waiting')).replace('_',' ').capitalize()),
                       'Execution':r.get('execution_status') or '—',
                       'Market':r.get('ticker') or '—'} for r in scans],width='stretch',hide_index=True)
        scan_ts=max(r.get('observed_ts',0) for r in scans)
        st.caption('Last scan: '+datetime.fromtimestamp(scan_ts,ZoneInfo('America/New_York')).strftime('%I:%M:%S %p ET')+' · signals checked each minute')
    else:st.info('Waiting for the first market scan.')
    st.subheader('Crypto orders & settlements')
    if d.get('trades'):
        settled = [r for r in d['trades'] if r.get('status') == 'settled']
        if settled:
            last = max(settled, key=lambda r: r.get('close_ts') or r.get('created_ts') or 0)
            st.caption('Latest completed market: ' + last['ticker'] + ' · **' + trade_outcome(last) + '** · Net ' + formatted(last.get('pnl'), '${:+.4f}'))
        st.dataframe([{'Strategy':r.get('lane','cushion_z2'),'Market':r['ticker'],'State':r['status'].replace('_',' ').title(),'Outcome':trade_outcome(r),'Side':r['side'].upper(),
                       'Filled':formatted(r.get('filled'),'{:g}'),'Entry':formatted(r.get('entry'),'${:.3f}'),
                       'Fees':formatted(r.get('fees'),'${:.4f}'),
                       'Net P&L':formatted(r.get('pnl'),'${:+.4f}')} for r in reversed(d['trades'])],width='stretch',hide_index=True)
    else:st.info('No orders yet. The bot waits until all entry conditions qualify.')
    with st.expander('Entry rules'):
        st.write('Completed-minute favorite ask at least 85¢ and below 95¢ · cushion z ≥ 2 · 3–10 minutes remaining · spread ≤ 2¢. Maximum buy price 93.9¢, even when the signal quote is higher. One capped immediate-or-cancel attempt per market; filled contracts held to settlement.')
        st.write('Strong cushion: completed-minute favorite ask 93–<98¢ · z ≥ 3 · maximum buy 97.5¢ · 5 contracts. Same 3–10 minute window, spread ≤ 2¢, capped IOC and hold to settlement. The cheaper-value experimental rule is not enabled for live orders.')
    st.caption('Actual exchange fills and fees only. Historical untagged trades belong to original z2. Paper results are excluded.')
