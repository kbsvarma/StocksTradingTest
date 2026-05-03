# config.py — All runtime parameters. Nothing is hardcoded anywhere else.

# ── Deployment mode ────────────────────────────────────────────────────────────
PAPER_TRADING: bool = True   # False = live. Changes port automatically.
USE_IB_GATEWAY: bool = True  # True = IB Gateway; False = TWS

# ── Instrument ─────────────────────────────────────────────────────────────────
SYMBOL: str = "MES"
CURRENCY: str = "USD"
CONTRACTS: int = 4            # 4 contracts @ $5/pt → $20/pt; Kelly-optimal for $50K account
TICK_SIZE: float = 0.25       # MES minimum price increment
MULTIPLIER: int = 5           # dollars per point per contract

# ── Timezone ───────────────────────────────────────────────────────────────────
TIMEZONE: str = "America/New_York"

# ── Session schedule (HH:MM, all ET) ──────────────────────────────────────────
RANGE_START_TIME: str = "09:30"
RANGE_END_TIME: str = "10:00"         # 30-min range (9:30-9:59) — validated OOS 2023-2026
ENTRY_CUTOFF_TIME: str = "15:15"      # no new entries after 15:15 to avoid EOD noise
HARD_CLOSE_TIME: str = "15:15"        # close any open position by 15:15 (15 min buffer before close)
SESSION_END_TIME: str = "16:00"

# ── Strategy parameters ────────────────────────────────────────────────────────
# Exit priority: VWAP_STOP > HARD_CLOSE > TARGET (bracket order).
# WARNING_SIGNAL_EXIT is DISABLED — replay shows 294/310 exits via warning,
# producing Sharpe -1.25. Removing it lifts OOS Sharpe to +0.78.
#
# TARGET is a real exit at 3× range width — matches the historically validated
# research configuration (strategy_lab_v2). The 15× "safety net" design broke
# research/live parity and is replaced here.
SAFETY_TARGET_MULT: float = 3.0       # target = entry ± 3 × range_width (validated R:R = 3:1)

# Direction mode: 'LONG_ONLY' | 'SHORT_ONLY' | 'BOTH'
# LONG_ONLY: structural 8-10pp long edge confirmed over 12 years (3,030 days).
# Short side: avg -$4.09/trade historically across all configurations.
DIRECTION_MODE: str = "LONG_ONLY"

# Trend filter: combined with DIRECTION_MODE.
# When DIRECTION_MODE='LONG_ONLY' and TREND_FILTER_ENABLED=True:
#   → trade LONG only on days where prev_close > prev_prev_close (aligned momentum)
#   → skip days where prior session closed down (counter-trend long avoided)
# OOS 2023-2026: Sharpe 0.79 with filter vs 0.74 without. Improves avg/trade 46%.
TREND_FILTER_ENABLED: bool = True

# ATR-based range width filter.
# True range (max of H-L, |H-prev_close|, |L-prev_close|) used in computation.
# ATR_MAX_WIDTH_MULT reduced from 0.9 to 0.7: at 0.9× MES ATR (~50pts) a 4-contract
# stop could reach $900, well above the $600 daily loss limit. At 0.7× the P95
# stop-out is ~$560, consistent with the risk budget.
ATR_PERIOD: int = 20                   # lookback for daily true-range ATR computation
ATR_MIN_WIDTH_MULT: float = 0.3        # skip if range < 0.3 × 20-day ATR
ATR_MAX_WIDTH_MULT: float = 0.7        # skip if range > 0.7 × 20-day ATR (was 0.9)

# Fixed fallback limits (used when ATR data unavailable).
MIN_OPENING_RANGE_POINTS: float = 5.0  # range too narrow → skip day
MAX_OPENING_RANGE_POINTS: float = 25.0 # range too wide  → skip day (tightened with ATR_MAX)

# Breakout confirmation buffer: bar close must exceed range edge by at least
# BREAKOUT_BUFFER_TICKS ticks to avoid one-tick false breaks in noisy mid-morning action.
BREAKOUT_BUFFER_TICKS: int = 1         # 1 tick = 0.25 pts for MES

# VWAP trailing stop: exit when close crosses session VWAP against position.
# Primary dynamic exit. Validated on SPY; deployed here as primary exit alongside
# the 3× bracket target.
VWAP_TRAILING_STOP: bool = True        # enable VWAP-based dynamic exit
VWAP_MIN_BARS: int = 1                 # minimum bars in trade before VWAP stop activates

# WARNING_SIGNAL_EXIT: DISABLED.
# Replay 2019-2026: 294/310 exits were WARNING_EXIT → Sharpe -1.25.
# Removing it: Sharpe +0.78. The threshold fires too aggressively on valid breakouts.
WARNING_SIGNAL_EXIT: bool = False

# ── Pre-trade filters ──────────────────────────────────────────────────────────
VIX_THRESHOLD: float = 25.0     # skip day if VIX >= this
GAP_THRESHOLD_PCT: float = 0.01  # skip day if overnight gap >= 1%

# ── Risk ───────────────────────────────────────────────────────────────────────
# Raised from -$400 to -$600 to be consistent with actual stop geometry.
# At ATR_MAX_WIDTH_MULT=0.7 and 4 contracts, P95 stop-out ≈ $560.
# -$600 allows one full stop-out at max allowed range width before halting.
DAILY_LOSS_LIMIT: float = -600.0  # net dollars; halt if breached after a trade

# ── Behavior flags ─────────────────────────────────────────────────────────────
ALLOW_TRADING_WITHOUT_VIX: bool = False       # if True, trade even when VIX pull fails
ALLOW_TRADING_WITHOUT_PREV_CLOSE: bool = True # if True, skip gap filter when prev_close
                                               # unavailable (e.g. HMDS error 162 on paper)
HALT_ON_ABNORMAL_SESSION: bool = True         # halt on half-days / holiday uncertainty

# ── Timeouts ───────────────────────────────────────────────────────────────────
FILL_TIMEOUT_SECS: int = 60     # seconds to wait for entry fill before halting
PROTECTIVE_ORDER_TIMEOUT: int = 5  # seconds after fill to confirm stop+target live
RECONNECT_INTERVAL: int = 30    # seconds between reconnect attempts
RECONNECT_MAX: int = 10          # max reconnect attempts before giving up

# ── IBKR connection ────────────────────────────────────────────────────────────
TWS_HOST: str = "127.0.0.1"
TWS_PAPER_PORT: int = 7497
TWS_LIVE_PORT: int = 7496
GW_PAPER_PORT: int = 4002
GW_LIVE_PORT: int = 4001

# ── Market data ────────────────────────────────────────────────────────────────
# 1 = live streaming (mirrors live conditions), 3 = delayed (paper-safe fallback)
MARKET_DATA_TYPE: int = 1
BAR_SIZE: str = "1 min"

# ── Cost model ─────────────────────────────────────────────────────────────────
# Note: live slippage (~0.25 tick on MES market orders) is not modelled here.
# Paper fills are at mid/last with no slippage; expect live avg/trade to be
# ~$2.50 lower per trade than paper reporting implies.
COMMISSION_PER_CONTRACT: float = 2.25  # one-way, dollars (adjust to your broker)

# ── Logging ────────────────────────────────────────────────────────────────────
LOG_DIR: str = "logs"
