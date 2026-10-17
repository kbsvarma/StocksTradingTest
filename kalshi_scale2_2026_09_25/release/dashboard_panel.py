"""Read-only current crypto lane. No trading imports or controls."""
import json
from pathlib import Path
import time
from datetime import datetime
from zoneinfo import ZoneInfo

STATUS=Path('/home/varmak/kalshi_revalidation_2026_09_12/live_v1/state/status.json')

DECISIONS={
    'price_gate':'Waiting for entry price', 'cushion_gate':'Cushion below 2.0',
    'outside_window':'Outside entry window', 'eligible':'Signal eligible',
    'spread_gate':'Spread too wide', 'no_current_market':'Waiting for market',
    'decision_candle_unavailable':'Waiting for completed candle',
    'no_decision_quote':'Waiting for quote', 'already_entered':'Already attempted', 'already_paper_entered':'Order already attempted — see orders below',
}

def formatted(value,pattern):
    try:return pattern.format(float(value))
    except (TypeError,ValueError):return '—'

def render(st):
    st.title('Crypto · 15-minute markets')
    try:d=json.loads(STATUS.read_text())
    except (OSError,ValueError):
        st.warning('Status is unavailable. Trading state cannot be confirmed.');return
    st.caption(f"BTC · ETH · SOL · XRP | {d.get('contracts',1)} contracts per market · z ≥ 2")
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
    cols=st.columns(4)
    cols[0].metric('Crypto net P&L',f"${d.get('realized_pnl',0):+.2f}")
    cols[1].metric('Open / pending',len(d.get('active',[])))
    cols[2].metric('Settled trades',d.get('settled',0))
    cols[3].metric('Order attempts',d.get('attempts',0))
    st.caption(f"{d.get('contracts',1)} contracts per market · max {d.get('max_contracts',4)} open contracts · USD {d.get('max_risk','3.80')} exposure cap · USD {d.get('daily_loss_budget','5')} daily loss budget including open risk")
    st.subheader('Market scanner')
    scans=d.get('scans',[])
    if scans:
        st.dataframe([{'Asset':r.get('asset'),'Side':(r.get('side') or '—').upper(),
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
        st.dataframe([{'Market':r['ticker'],'State':r['status'].replace('_',' ').title(),'Side':r['side'].upper(),
                       'Filled':formatted(r.get('filled'),'{:g}'),'Entry':formatted(r.get('entry'),'${:.3f}'),
                       'Fees':formatted(r.get('fees'),'${:.4f}'),
                       'Net P&L':formatted(r.get('pnl'),'${:+.4f}')} for r in reversed(d['trades'])],width='stretch',hide_index=True)
    else:st.info('No orders yet. The bot waits until all entry conditions qualify.')
    with st.expander('Entry rules'):
        st.write('Favorite ask at least 85¢ and below 93¢ · cushion z ≥ 2 · 3–10 minutes remaining · spread ≤ 2¢. One capped immediate-or-cancel attempt per market; filled contracts held to settlement.')
    st.caption('P&L covers this crypto strategy only. Legacy history has been archived.')
