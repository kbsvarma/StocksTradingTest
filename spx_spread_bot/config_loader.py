from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import yaml


def _parse_hhmm(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def _to_path(value: str | Path) -> Path:
    return value if isinstance(value, Path) else Path(value)


@dataclass(slots=True)
class BotConfig:
    capital: float = 60_000.0
    max_contracts: int = 12
    spread_width: int = 25
    min_credit: float = 1.50
    target_credit: float = 2.00
    stop_multiplier: float = 2.0
    profit_target_pct: float = 0.50
    otm_pct_low_vix: float = 0.018
    otm_pct_high_vix: float = 0.025
    vix_min: float = 12.0
    vix_max: float = 30.0
    entry_start: str = "09:45"
    entry_end: str = "10:15"
    eod_check: str = "15:45"
    friday_forced_close: str = "15:30"
    expiry_safety_distance_points: float = 50.0
    paper_trading: bool = True
    live_mode_enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 1
    market_data_type: int = 1
    timezone: str = "America/New_York"

    underlying_symbol: str = "SPX"
    underlying_exchange: str = "CBOE"
    option_exchange: str = "SMART"
    combo_exchange: str = "SMART"
    preferred_trading_class: str = "SPXW"
    currency: str = "USD"

    max_margin_usage_pct: float = 0.50
    min_available_margin: float = 5_000.0
    max_retries: int = 3
    retry_price_step: float = 0.05
    retry_wait_seconds: int = 120
    fill_timeout_seconds: int = 300
    protection_deadline_seconds: int = 10

    monitor_interval_seconds: int = 1
    tick_log_interval_seconds: float = 0.25
    macro_refresh_hour_et: int = 6

    run_days: list[int] = field(default_factory=lambda: [0, 2, 4])
    dte_candidates: list[int] = field(default_factory=lambda: [0, 2])
    trade_once_per_day: bool = True
    enabled_strategies: list[str] = field(
        default_factory=lambda: ["BULL_PUT_SPREAD", "PUT_BWB", "IRON_CONDOR", "IRON_FLY"]
    )
    strategy_priority: list[str] = field(
        default_factory=lambda: ["BULL_PUT_SPREAD", "PUT_BWB", "IRON_CONDOR", "IRON_FLY"]
    )

    bwb_narrow_wing_width: int = 25
    bwb_wide_wing_width: int = 50
    condor_wing_width: int = 25
    iron_fly_wing_width: int = 25

    auto_place_on_signal: bool = True
    one_click_confirm: bool = False

    macro_provider: str = "hybrid"
    macro_fail_open: bool = True
    macro_api_key: str = ""
    manual_macro_dates_csv: str = "data/manual_macro_dates.csv"

    data_dir: str = "data"
    logs_dir: str = "logs"
    state_file: str = "data/runtime_state.json"
    signal_events_file: str = "logs/signal_events.jsonl"
    tick_events_file: str = "logs/tick_events.jsonl"
    order_events_file: str = "logs/order_events.jsonl"
    daily_summary_file: str = "logs/daily_summary.log"
    trade_csv_file: str = "data/trades.csv"
    status_file: str = "data/status.json"

    def entry_start_time(self) -> time:
        return _parse_hhmm(self.entry_start)

    def entry_end_time(self) -> time:
        return _parse_hhmm(self.entry_end)

    def eod_check_time(self) -> time:
        return _parse_hhmm(self.eod_check)

    def friday_forced_close_time(self) -> time:
        return _parse_hhmm(self.friday_forced_close)

    def max_margin_dollars(self) -> float:
        return self.capital * self.max_margin_usage_pct

    def is_trade_day(self, current_date: date) -> bool:
        return current_date.weekday() in self.run_days

    @property
    def data_path(self) -> Path:
        return _to_path(self.data_dir)

    @property
    def logs_path(self) -> Path:
        return _to_path(self.logs_dir)


class ConfigError(RuntimeError):
    pass


def load_config(path: str | Path) -> BotConfig:
    file_path = _to_path(path)
    if not file_path.exists():
        raise ConfigError(f"config file not found: {file_path}")

    raw: dict[str, Any] = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
    cfg = BotConfig()

    for key, value in raw.items():
        if not hasattr(cfg, key):
            continue
        setattr(cfg, key, value)

    if not cfg.paper_trading and not cfg.live_mode_enabled:
        raise ConfigError(
            "paper_trading=false but live_mode_enabled=false. "
            "Refusing to run in ambiguous mode."
        )

    cfg.data_path.mkdir(parents=True, exist_ok=True)
    cfg.logs_path.mkdir(parents=True, exist_ok=True)
    _to_path(cfg.state_file).parent.mkdir(parents=True, exist_ok=True)

    return cfg
