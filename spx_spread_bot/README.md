# SPX Multi-Strategy Spread Bot (Production-Oriented, Paper-First)

This is a production-style SPX strategy bot for IBKR paper trading.

- Strategy instrument: `SPX` index options (PM-settled weekly chain via `SPXW` trading class)
- Strategy set (parallel-enabled): `BULL_PUT_SPREAD`, `PUT_BWB`, `IRON_CONDOR`, `IRON_FLY`
- Deployment target: AWS Lightsail + `systemd`
- Execution flow: signal is published to UI/log first, then order is auto-placed
- Exit lifecycle: handled by bot (profit target, stop, Friday close, EOD rules)
- Live mode: disabled by default; designed to switch later by config

## Module Layout

- `market_data.py`: SPX/VIX quotes, option chain, tick streaming, macro calendar ingestion
- `signal_engine.py`: filters + strike selection + contract sizing
- `execution.py`: combo order entry, retries, protective orders, force close
- `monitor.py`: 1-second monitoring loop for stops/targets/EOD
- `trade_logger.py`: JSONL events + tick logs + CSV trade journal + daily summaries
- `state_store.py`: restart-safe runtime state persistence
- `main.py`: orchestration, scheduler, reconnect, reconciliation
- `dashboard.py`: Streamlit operator UI

## Local Run

```bash
cd spx_spread_bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py config.yaml
```

Dashboard:

```bash
streamlit run dashboard.py --server.address 127.0.0.1 --server.port 8503
```

Parallel XSP paper canary run (separate state/logs):

```bash
python main.py config_xsp.yaml
```

## Historical Backtest (Databento + IBKR)

The project includes a replay backtester that uses:

- Databento OPRA (`SPXW.OPT`) minute NBBO for option legs.
- IBKR historical minute bars for SPX and VIX checkpoints used by the strategy filters.

Set your Databento key in environment (do not commit keys to files):

```bash
export DATABENTO_API_KEY="db-...redacted..."
```

Run a short validation slice:

```bash
python backtest_databento.py \
  --config config.yaml \
  --start-date 2026-01-01 \
  --end-date 2026-02-15 \
  --output-dir backtests/validation
```

Run a full 10-year window:

```bash
python backtest_databento.py \
  --config config.yaml \
  --start-date 2016-05-01 \
  --end-date 2026-04-30 \
  --output-dir backtests/ten_year \
  --resume
```

Outputs:

- `backtests/.../daily_results.csv`
- `backtests/.../trade_results.csv`
- `backtests/.../summary.json`
- `backtests/.../cost_preview.json`
- `backtests/.../backtest.log`

## Paper Trading TWS / Gateway Setup

1. Download and install TWS or IB Gateway from IBKR.
2. Log in with paper credentials.
3. In TWS: `File -> Global Configuration -> API -> Settings`.
4. Enable ActiveX and Socket Clients.
5. Set paper port to `7497` (or set matching `config.yaml` port).
6. Disable Read-Only API.
7. Add trusted IP (`127.0.0.1` for local; server IP for cloud).
8. Ensure options permissions include SPX index options.
9. Ensure market data entitlements for SPX/SPXW and VIX are available to paper login.
10. Start bot: `python main.py config.yaml`.
11. If entitlements are still pending in paper, set `market_data_type: 3` in `config.yaml` for delayed data.

## Lightsail Deployment

Use deployment scripts under:

- `deploy/lightsail/install_spx_bot.sh`
- `deploy/lightsail/enter_credentials_linux.sh`
- `deploy/lightsail/README.md`

Installed services:

- `spx-ibgateway.service`
- `spx-spread-bot.service`
- `spx-spread-dashboard.service`

## Runtime Artifacts

- `data/runtime_state.json`
- `data/status.json`
- `data/trades.csv`
- `logs/signal_events.jsonl`
- `logs/order_events.jsonl`
- `logs/tick_events.jsonl`
- `logs/daily_summary.log`
- `logs/system.log`

## Macro Calendar Source

The bot blocks trading on FOMC/CPI/NFP through a hybrid approach:

1. `data/manual_macro_dates.csv` (always available, no API key)
2. Federal Reserve FOMC schedule parsing
3. Optional FinancialModelingPrep economic calendar API (`macro_api_key` in `config.yaml`)

If external sources fail, behavior follows `macro_fail_open` in config.

## Live Safety Notes

- `paper_trading: true` and `live_mode_enabled: false` by default.
- Flip to live only after explicit approval and cutover checklist.
- Bot enforces protection placement after fill; if protection fails, it attempts immediate flatten.
- For parallel SPX + XSP operation, use different `client_id` values and isolated
  `data/*` + `logs/*` paths per config.
