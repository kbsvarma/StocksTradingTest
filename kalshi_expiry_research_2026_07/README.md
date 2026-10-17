# Kalshi recurring-expiry research

This directory is an isolated research harness for recurring Kalshi markets.
It does not import, configure, or modify the live trading bot.

Research gates:

1. Quotes and outcomes must come from saved public Kalshi API data.
2. Entries must be causal and include executable ask plus taker fees.
3. The independent unit is the settlement event, not each correlated strike.
4. Candidate discovery and chronological validation are reported separately.
5. Multiple-testing correction is applied across every searched cell.
6. A strategy is not promoted from research without realistic liquidity,
   drawdown, frequency, and prospective validation.

Run the inventory:

```bash
python3 inventory.py
```

Run the full research suite:

```bash
python3 pull_history.py KXCS2MAP KXRAINNYC KXINXDUD KXNASDAQDUD --period 1
python3 study_closers.py
python3 study_ladders.py
python3 study_cs2_routes.py
python3 study_structural.py
python3 study_index_settlement.py
python3 study_wti_settlement.py
python3 study_rain_lock.py
python3 scan_current_arbitrage.py
```

`REPORT.md` contains the current decision summary. Raw trade and audit records
are under `results/`; public API pulls are under `data/`.
