"""Matching-period crypto interaction; never add standalone profits together."""
import json
import pickle
import replay as r
from validate import actual_engine

def main():
    end=r.ts('2026-10-03T00:00:00Z')
    crypto=pickle.loads((r.ROOT/'kalshi_btc_eth_quant_2026_10_02/features.pickle').read_bytes())
    crypto=[dict(m,multiplier=1) for m in crypto if r.START<=m['close']<end]
    commodities=pickle.loads((r.P/'features.pickle').read_bytes())
    commodities=[m for m in commodities if r.START<=m['close']<end and m['asset'] in ('NATGAS','COPPER')]
    index={m['ticker']:m for m in crypto+commodities}
    results={}
    for mode,stress in [('opening',.01),('opening',.02),('cap_cost',.01),('delay60',.01)]:
        cr,_=r.opportunities(crypto,mode)
        cm,_=r.opportunities(commodities,mode)
        base,_=r.portfolio(cr,stress)
        baseline=r.metrics(base)
        if mode=='opening' and stress==.01:
            assert baseline['trades']==334 and abs(baseline['net']-69.5823)<.00001,baseline
        names={x['ticker'] for x in base}
        scenario={'crypto_alone':baseline}
        for label,assets in [('NATGAS',('NATGAS',)),('COPPER',('COPPER',)),('NATGAS_COPPER',('NATGAS','COPPER'))]:
            opportunities=cr+[x for x in cm if x['asset'] in assets]
            selected,blocks=r.portfolio(opportunities,stress)
            metric=r.metrics(selected)
            samecrypto=[x for x in selected if x['asset'] not in assets]
            extra=[x for x in selected if x['asset'] in assets]
            one=dict(joint=metric,commodity_attribution=r.metrics(extra),
                incremental_trades=metric['trades']-baseline['trades'],
                incremental_net=round(metric['net']-baseline['net'],5),
                displaced_crypto=len(names-{x['ticker'] for x in samecrypto}),blocked=blocks)
            if mode=='opening' and stress==.01 and label=='NATGAS':
                reproduced,_=actual_engine(opportunities,index,stress)
                identity=lambda rows:[(x['ticker'],x['lane'],x['t']) for x in rows]
                assert identity(reproduced)==identity(selected)
                assert abs(r.metrics(reproduced)['net']-metric['net'])<1e-7
                one['production_risk_parity']=True
            scenario[label]=one
        results[f'{mode}/{stress}']=scenario
        print(mode,stress,'BASE',baseline['trades'],baseline['net'],
              [(name,v['joint']['trades'],v['incremental_net'],v['joint']['settled_drawdown'],v['displaced_crypto'])
               for name,v in scenario.items() if name!='crypto_alone'],flush=True)
    r.save('crypto_interaction.json',dict(start_utc='2026-09-11T00:00:00Z',
        end_exclusive_utc='2026-10-03T00:00:00Z',same_period_inputs=True,
        selection_note='NATGAS and COPPER grouping chosen after the asset-level replay; exploratory, not independent confirmation.',
        scenarios=results))

if __name__=='__main__':main()
