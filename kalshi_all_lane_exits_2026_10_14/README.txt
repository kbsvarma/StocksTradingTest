All-lane exit and entry research, October14,2026

Start with REPORT.txt. RESEARCH_SCOPE.txt is the pre-calculation scope;
SCOPE_COMPLETION.txt distinguishes completed tests from unavailable execution
history. all_22_loss_audit.json retains every actual loss and its counterfactuals.

Research only: no order-writing client or activation workflow is used. Existing
services, strategy, price limits,5contract size and risk limits are unchanged.
Copper and USD/CAD remain paused.

Reproduce using Python with numpy, from this directory:
  python -m unittest -q test_core
  python actual_minute.py
  python actual_fast.py
  python entry_grid.py
  python portfolio.py
  python fast_entry_screen.py
  python depth_grid.py
  python audit.py
  python diagnostics.py
  python make_report.py

inputs.pkl.gz and seconds.pkl.gz contain frozen assembled inputs. Source receipts
and hashes are retained. depth_grid.py additionally reads the preserved prior
kalshi_fast_z_exit_2026_10_14 experiment. Recollection scripts are separate and
not needed to recompute the frozen results. Server runtime:
/home/varma/kalshi/venv/bin/python
Server experiment:
/home/varma/kalshi_all_lane_exits_2026_10_14

Execution evidence tiers:
1. Actual ledger costs,fees,settlements for459trades.
2. Minute bid/ask execution proxies for all-entry grids.
3. Fast historical source triggers with minute OHLC stress prices, not fills.
4. Actual timestamped continuous depth/first-arrival cohort:20trades/2losses.
Do not combine these tiers into one claim of executable all-history1sprofit.
