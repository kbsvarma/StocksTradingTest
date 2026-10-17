"""Frozen old-versus-new Copper comparison; cached inputs only, no network/orders."""
import datetime as dt
import gzip
import hashlib
import json
import pickle
import sys
from decimal import Decimal as D
from pathlib import Path

OUT = Path(__file__).resolve().parent
P = OUT.parent
sys.path.insert(0, str(P))
import study as s

inputs = json.loads((OUT / "period_inputs.json").read_text())
markets = pickle.loads((P / "features.pickle").read_bytes())
pred = pickle.loads((P / "predictions.pickle").read_bytes())
actual = json.loads((P / "actual_metal_orders.json").read_text())

# Reuse the audited book overlay without rewriting the original study artifacts.
ns = {}
old_save = s.save
try:
    s.save = lambda *args: None
    exec(compile((P / "book_overlay.py").read_text().split("frozen=json.loads")[0],
                 str(P / "book_overlay.py"), "exec"), ns)
finally:
    s.save = old_save

old_observed, evidence = ns["supplemented"](s.CONFIG[1], "observed")
old_allcap, _ = ns["supplemented"](s.CONFIG[1], "all_signal_cap")
new_base, _ = s.opportunities(markets, s.CONFIG[40], pred)
with gzip.open(P / "value_qualifiers.json.gz", "rt") as f:
    new_allcap = json.load(f)
new_known = {(r["ticker"], r["t"]): r for r in new_base if r["asset"] == "COPPER"}
lookup = {m["ticker"]: m for m in markets}
for r in new_allcap:
    known = ns["recorded_quote"](r["ticker"], r["t"], r["side"], .5, r["cap"])
    if known:
        px, stamp, source = known
        entry = min(r["cap"], max(r["ask"], px))
        paid = s.fee(entry, 5, lookup[r["ticker"]]["multiplier"])
        new_known[(r["ticker"], r["t"])] = dict(
            r, entered=stamp, entry=entry, fee=paid, bookask=px, source=source,
            net_before_friction=5 * (r["won"] - entry) - paid)

pools = {
    "old": {"observed_overlay": old_observed, "all_signals_at_cap": old_allcap},
    "new": {"observed_overlay": list(new_known.values()), "all_signals_at_cap": new_allcap},
}
periods = {
    "common_since_model_ready": (s.ts("2026-09-14T00:00:00Z"), s.END),
    "later_oct3_onward": (s.ts("2026-10-03T00:00:00Z"), s.END),
    "actual_copper_operating_window": (inputs["activated_ts"], inputs["halt_request_ts"]),
    "entire_input_period_including_new_model_warmup": (s.START, s.END),
}
results, ledgers = {}, {}
checks = 0
for period, (lo, hi) in periods.items():
    results[period], ledgers[period] = {}, {}
    for design, modes in pools.items():
        results[period][design], ledgers[period][design] = {}, {}
        for mode, pool in modes.items():
            results[period][design][mode], ledgers[period][design][mode] = {}, {}
            for cents in (1, 2, 3):
                rows = [r for r in pool if r["asset"] == "COPPER" and lo <= r["entered"] < hi]
                ledger, diag = s.portfolio(rows, cents / 100)
                metrics = s.metrics(ledger)
                assert abs(metrics["net"] - (diag["final_cash"] - 100)) < 1e-7
                assert len(ledger) == len({r["ticker"] for r in ledger})
                for r in ledger:
                    assert lo <= r["entered"] < hi
                    assert r["quantity"] == 5 and r["entry"] <= r["cap"] + 1e-8
                    assert r["won"] == int(lookup[r["ticker"]]["result"] == int(r["side"] == 1))
                    expected = D(5) * (D(r["won"]) - D(str(r["entry"]))) - D(str(r["fee"])) - D(cents) / 20
                    assert abs(float(expected) - r["net"]) < 1e-9
                    checks += 1
                results[period][design][mode][f"cost{cents}"] = dict(metrics, **diag)
                ledgers[period][design][mode][f"cost{cents}"] = ledger

losses = []
for r in actual:
    if r["result"] == r["side"]:
        continue
    m = lookup[r["ticker"]]
    f = next(x for x in m["features"] if x["rem"] == 5)
    p = pred.get((m["ticker"], f["t"], "empirical"))
    options = []
    for side, ask, probability in ((1, f["yask"], p), (-1, 1-f["ybid"], 1-p)):
        options.append(dict(side="yes" if side == 1 else "no", ask=ask,
                            probability=probability,
                            edge=probability-ask-s.fee(ask,5,m["multiplier"])/5-.01))
    losses.append(dict(ticker=r["ticker"],actual_side=r["side"],actual_net=r["pnl"],
                       new_five_minute_options=options,
                       new_qualified=[x for x in new_allcap if x["ticker"] == r["ticker"]]))

actual_metrics = dict(trades=len(actual), wins=sum(r["side"] == r["result"] for r in actual),
                      net=str(sum((D(r["pnl"]) for r in actual), D(0))))
out = dict(rules={"old":s.CONFIG[1],"new":s.CONFIG[40]}, periods=periods,
           period_inputs=inputs, results=results, actual=actual_metrics,
           old_losses_under_new_rule=losses, accounting_checks=checks,
           note="Both are standalone simulations. Quote proxy plus partial recorded books is not complete execution evidence. Actual live results have their real charged fees, no hypothetical friction. All-signal-cap is an assumed-fill sensitivity. Current standardized budgets do not reconstruct other live lanes' historical contention.")
(OUT / "results.json").write_text(json.dumps(out, indent=2) + "\n")
with gzip.open(OUT / "ledgers.json.gz", "wt") as f:
    json.dump(ledgers, f)
for name in periods:
    print(name, flush=True)
    for design in pools:
        print(design, {mode:{cost:{k:r[k] for k in ("trades","wins","losses","net","drawdown")}
                            for cost,r in val.items()}
                       for mode,val in results[name][design].items()}, flush=True)
print("old_losses", losses, flush=True)
print("checks",checks,flush=True)
