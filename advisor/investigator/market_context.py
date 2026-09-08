"""Readable dated market observations, independent of what the writer omits."""
from advisor.intelligence.contract import number


def markdown(observations,as_of):
    lines=[]
    cutoff=str(as_of)[:10]
    for item in observations:
        value=item.get('value',{});clock=item.get('period_end') or item.get('observed_at') or 'unknown'
        urls=item.get('source_urls',[])
        source=f" [Source]({urls[0]})" if urls else ''
        suffix=f" · dated {str(clock)[:10]}{source}"
        if 'calendar' in value:
            dates=value['calendar'].get('Earnings Date',[])
            if isinstance(dates,str):dates=[dates]
            future=[str(d)[:10] for d in dates if str(d)[:10]>=cutoff]
            if future:
                lines.append('- **Next earnings:** '+', '.join(future)+' · provider estimate, not issuer-confirmed'+suffix)
        if number(value.get('shortPercentOfFloat')):
            text=f"- **Short positioning:** {value['shortPercentOfFloat']*100:.2f}% of float"
            if number(value.get('shortRatio')):text+=f"; {value['shortRatio']:.2f} days to cover"
            lines.append(text+' · settlement snapshot, not live borrow availability'+suffix)
        if value.get('dataset')=='eps_revisions':
            row=next((r for r in value.get('rows',[]) if r.get('period')=='0q'),{})
            up=row.get('upLast30days');down=row.get('downLast30days')
            if number(up) and number(down):
                lines.append(f'- **Estimate revisions:** {up:g} upward / {down:g} downward in 30 days · provider current-quarter bucket; not an estimate-surprise calculation'+suffix)
        if value.get('dataset')=='earnings_estimate':
            row=next((r for r in value.get('rows',[]) if r.get('period')=='0q'),{})
            if number(row.get('avg')):
                lines.append(f"- **Consensus EPS:** {row['avg']:.2f} {row.get('currency','')} · provider current-quarter bucket; accounting basis and fiscal target require confirmation"+suffix)
        if item.get('unit')=='calculated technical measures':
            fields=[]
            for key,label,fmt in [('rsi14','RSI 14','.1f'),('distance_ma200_pct','distance from 200-day average','+.2f'),('return_21d_pct','21-session return','+.2f'),('relative_21d_pp','relative return vs '+str(value.get('benchmark','benchmark')),'+.2f')]:
                if number(value.get(key)):
                    unit=' pp' if key=='relative_21d_pp' else '%' if key.endswith('_pct') else ''
                    fields.append(label+' '+format(value[key],fmt)+unit)
            if fields:lines.append('- **Price structure:** '+'; '.join(fields)+' · context, not a valuation target'+suffix)
    return '\n'.join(['### Dated market context','These observations are included even when the narrative omits them.','',*lines]) if lines else ''
