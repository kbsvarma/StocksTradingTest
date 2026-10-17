"""Read-only, standard-library reproduction of the ledger diagnosis."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def summary(rows):
    count = sum(r['count'] for r in rows)
    pnl = sum(r['pnl'] for r in rows)
    return dict(trades=len(rows), contracts=count, ledger_pnl=round(pnl, 4),
                cents_per_contract=round(100 * pnl / count, 4),
                equal_size_cents_per_trade=round(
                    100 * sum(r['pnl'] / r['count'] for r in rows) / len(rows), 4))


rows = [json.loads(line) for line in
        (ROOT / 'server_snapshot/cushion_trades.jsonl').read_text().splitlines()]
rows = [r for r in rows if r.get('result') in ('win', 'loss', 'stopped')]
out = {'cushion': summary(rows), 'by_day': {}}
for day in sorted({r['settled_day'] for r in rows}):
    out['by_day'][day] = summary([r for r in rows if r['settled_day'] == day])
delta = 0
for r in rows:
    if r['result'] == 'stopped':
        p = r['exit_px']
        delta += ((r['would_have'] == 'win') - p + 0.07*p*(1-p)) * r['exit_ct']
out['hold_counterfactual_pnl'] = round(sum(r['pnl'] for r in rows) + delta, 4)
out['stop_cost_vs_hold'] = round(delta, 4)
old = [json.loads(line) for line in (ROOT.parent /
       'kalshi_unbroken_bot/analysis/crypto_era_ledger_final.jsonl').read_text().splitlines()]
old = [r for r in old if r.get('result') in ('win', 'loss')
       and r.get('series') in ('KXBTC15M', 'KXETH15M', 'KXSOL15M', 'KXXRP15M')]
out['old_crypto_only'] = summary(old)
out['old_labeled_r1'] = summary([r for r in old if r.get('lane') == 'r1'])
print(json.dumps(out, indent=2))
