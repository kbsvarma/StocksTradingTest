"""Reprice all opportunities with one shared crypto/commodity risk budget."""
import study as s
import importlib.util
_spec=importlib.util.spec_from_file_location('tuning_validation',s.P/'validate.py')
_module=importlib.util.module_from_spec(_spec);_spec.loader.exec_module(_module)
compare=_module.compare

def main():
    choices=s.json.loads((s.P/'choices_frozen.json').read_text())['choices']
    end=s.r.SPLIT
    crypto=s.pickle.loads((s.P.parent/'kalshi_btc_eth_quant_2026_10_02/features.pickle').read_bytes())
    crypto=[dict(m,multiplier=1) for m in crypto if s.r.START<=m['close']<end]
    other=s.pickle.loads((s.P/'features.pickle').read_bytes())
    other=[m for m in other if m['asset']!='EURUSD' and s.r.START<=m['close']<end]
    combinations=[('NATGAS',),('COPPER',),('WTI',),('GOLD',),('NATGAS','COPPER'),('NATGAS','COPPER','WTI')]
    result={};ledgers={}
    for scenario,mode,stress in [('opening1','opening',.01),('opening2','opening',.02),('cap1','cap_cost',.01)]:
        cr,_=s.r.opportunities(crypto,mode)
        baseline,_=s.r.portfolio(cr,stress);bm=s.compact(baseline)
        assert scenario!='opening1' or bm['trades']==334 and abs(bm['net']-69.5823)<1e-7
        cm=s.pool(other,mode);one={'crypto_alone':bm}
        for assets in combinations:
            record={}
            for label in ('unchanged','tuned'):
                rows=list(cr)
                for a in assets:
                    i=0 if label=='unchanged' else choices[a]['robust']
                    rows+=s.select([x for x in cm if x['asset']==a],s.CONFIG[i])
                trades,blocks=s.r.portfolio(rows,stress);metric=s.compact(trades)
                cc=[x for x in trades if x['asset'] in ('BTC','ETH','SOL','XRP')]
                record[label]=dict(metrics=metric,net_delta_vs_crypto=round(metric['net']-bm['net'],5),
                    trade_delta_vs_crypto=metric['trades']-bm['trades'],
                    crypto_markets_displaced=len({x['ticker'] for x in baseline}-{x['ticker'] for x in cc}),
                    blocked=blocks,asset_attribution={a:s.compact([x for x in trades if x['asset']==a]) for a in assets})
                ledgers[f'{scenario}/{"_".join(assets)}/{label}']=trades
                if scenario=='opening1' and assets==('NATGAS','COPPER','WTI') and label=='tuned':
                    record[label]['production_risk_parity']=compare(rows,stress)
            record['tuning_incremental_net']=round(record['tuned']['metrics']['net']-record['unchanged']['metrics']['net'],5)
            record['tuning_incremental_trades']=record['tuned']['metrics']['trades']-record['unchanged']['metrics']['trades']
            one['_'.join(assets)]=record
        result[scenario]=one
        print('INTERACTION',scenario,[(k,v['tuning_incremental_trades'],v['tuning_incremental_net']) for k,v in one.items() if k!='crypto_alone'],flush=True)
    s.save('crypto_interaction.json',dict(start='2026-09-11T00:00:00Z',end_exclusive='2026-10-03T00:00:00Z',
        route2_only=True,fx_excluded=True,shared_cash=50,shared_risk='20 contracts / $19 / $10 daily including open risk',
        tie_priority='standard lane before premium; crypto assets before commodities inside each lane',
        note='Early matched period overlaps selection. Combinations are exploratory, not independent holdouts.',results=result))
    with s.gzip.open(s.P/'interaction_ledgers.json.gz','wt') as f:s.json.dump(ledgers,f,separators=(',',':'))

if __name__=='__main__':main()
