# MES ORB Bot

Opening Range Breakout bot for Micro E-mini S&P 500 (MES) futures.
Connects to Interactive Brokers via `ib_insync`. Paper trading by default.

---

## Prerequisites

- Python 3.11+
- Interactive Brokers account with TWS or IB Gateway running
- Paper trading account enabled in TWS/Gateway

Install dependencies:

```bash
pip install ib_insync pandas numpy
```

---

## TWS / IB Gateway Setup

1. Open TWS or IB Gateway and log into your **paper trading** account
2. Go to **File → Global Configuration → API → Settings**
3. Enable **Enable ActiveX and Socket Clients**
4. Set **Socket port** to `7497` (TWS paper) or `4002` (IB Gateway paper)
5. Enable **Allow connections from localhost only**
6. Restart TWS/Gateway after changing settings

---

## Configuration

All parameters are in `config.py`. Key settings:

| Parameter | Default | Description |
|---|---|---|
| `PAPER_TRADING` | `True` | Set `False` for live — requires typing `LIVE` to confirm |
| `USE_IB_GATEWAY` | `False` | `True` if using IB Gateway instead of TWS |
| `CONTRACTS` | `1` | Number of MES contracts |
| `VIX_THRESHOLD` | `25.0` | Skip day if VIX >= this |
| `GAP_THRESHOLD_PCT` | `0.01` | Skip day if overnight gap >= 1% |
| `TARGET_MULTIPLIER` | `3.0` | Target = entry ± 3.0 × range width |
| `DAILY_LOSS_LIMIT` | `-100.0` | Halt after net loss exceeds this |
| `MARKET_DATA_TYPE` | `3` | `3`=delayed (paper), `1`=live (requires subscription) |
| `COMMISSION_PER_CONTRACT` | `2.25` | One-way cost estimate, adjust to your broker |

---

## Running in Paper Mode

```bash
cd mes_orb_bot
python main.py
```

The bot will:
1. Connect to TWS paper account on port 7497
2. Resolve the front-month MES contract
3. Check for any existing positions (halts if found and you don't confirm)
4. Pull previous session close and subscribe to 1-minute bars
5. Wait for 09:29 ET to check VIX
6. Build opening range 09:30–09:59
7. Lock range at 10:00 and evaluate breakout signals
8. Submit bracket orders on signal, manage stop/target, hard close at 15:30

---

## Switching to Live

1. Change `PAPER_TRADING = False` in `config.py`
2. Change `MARKET_DATA_TYPE = 1` if you have live data subscriptions
3. Log into your **live** account in TWS
4. Run `python main.py` — you must type `LIVE` at the confirmation prompt

---

## Verify Before First Paper Run

- [ ] TWS is running and logged into paper account
- [ ] API settings enabled in TWS with socket port 7497
- [ ] `PAPER_TRADING = True` in config.py
- [ ] `MARKET_DATA_TYPE = 3` in config.py
- [ ] Run replay test to confirm strategy logic (see below)
- [ ] No existing MES positions in the paper account
- [ ] Logs directory exists or will be created automatically

---

## Replay Test (No Broker Required)

Validate the full state machine and strategy logic without connecting to IBKR:

```bash
# Run all 8 scenarios
python replay_test.py

# Run a single scenario
python replay_test.py long_win

# List available scenarios
python replay_test.py --list
```

Available scenarios:

| Scenario | Description |
|---|---|
| `long_win` | Clean long breakout → target hit |
| `short_win` | Clean short breakout → target hit |
| `long_loss` | Long entry → stop hit |
| `no_trade` | Range holds all day → no trade |
| `vix_halt` | VIX=30 → halted before open |
| `gap_halt` | 1.2% overnight gap → halted at open |
| `narrow_range` | Range too narrow → halted after lock |
| `hard_close` | Trade still open at 15:30 → hard close |

All 8 scenarios must pass before connecting to IBKR.

---

## Logs

Logs are written to `logs/YYYYMMDD.log`. Console shows INFO+, file captures DEBUG (every bar, every signal evaluation, every latency measurement). Use the file logs for post-session analysis and fine-tuning.

Key log events to watch:

```
[STATE_TRANSITION]     State changes
[RANGE_LOCKED]         Opening range confirmed
[VIX_FILTER]           VIX check result
[GAP_FILTER]           Gap check result
[WIDTH_FILTER]         Range width check result
[SIGNAL_DETECTED]      Breakout signal with bar age
[ENTRY_CONFIRMED]      Fill with submit→fill latency
[TRADE_CLOSED]         P&L breakdown (gross, commission, net)
[TRADE_CONTEXT]        Full entry context for analysis
[DAILY_SUMMARY]        End-of-session summary
[BAR_RECEIVED]         Every bar with delivery latency (file only)
[SIGNAL_EVAL]          Every bar evaluated for signal (file only)
[POSITION_MONITOR]     Unrealised P&L each bar in trade (file only)
```

---

## File Structure

```
mes_orb_bot/
├── main.py          Entry point
├── bot.py           State machine and orchestration
├── strategy.py      ORB range and signal logic
├── broker.py        IBKR wrapper (connection, orders, data)
├── risk.py          Filters and daily P&L tracking
├── config.py        All parameters
├── logger.py        Console + file logging
├── replay_test.py   Broker-free scenario testing
└── logs/            Daily log files (auto-created)
```

---

## Deployment Ladder

Follow this sequence. Do not skip steps.

| Level | Condition | Action |
|---|---|---|
| L0 | Day 1 | Run `replay_test.py`, verify all pass |
| L1 | Day 2+ | Paper trading, 1 contract, watch every trade |
| L2 | After 50 paper trades | Review logs, confirm costs match model |
| L3 | Paper Sharpe > 0 over 50+ trades | Consider live with 1 contract |
| L4 | Live performance validated | Scale to 2 contracts if edge confirmed |
