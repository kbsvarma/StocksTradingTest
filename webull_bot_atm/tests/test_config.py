"""Smoke tests for config_atm.yaml — verify all expected keys exist with correct values."""
from pathlib import Path
import yaml


def _cfg():
    p = Path(__file__).parent.parent / "config_atm.yaml"
    assert p.exists(), f"config_atm.yaml not found at {p}"
    return yaml.safe_load(p.read_text())


def test_strategy_params_match_validated_winner():
    """ATM config must match the W45 × 30% × 09:45 × noThu × SL 3.0× validated winner."""
    cfg = _cfg()
    assert cfg["spread_width"] == 45, "validated winner uses 45pt width"
    assert cfg["target_credit"] == 13.50, "30% of 45 = $13.50 target"
    assert cfg["min_credit"] == 6.75, "15% of 45 = $6.75 min"
    assert cfg["entry_start"] == "09:45", "validated winner enters at 09:45"
    assert cfg["stop_multiplier"] == 3.0, "validated winner uses SL 3.0×"
    assert cfg["disable_profit_target"] is True, "no PT — hold to SL or close"


def test_run_days_skip_thursday():
    """noThu filter: weekday 3 = Thursday, must NOT be in run_days."""
    cfg = _cfg()
    assert 3 not in cfg["run_days"], "Thursday (weekday 3) must be excluded"
    assert set(cfg["run_days"]) == {0, 1, 2, 4}, "should be Mon, Tue, Wed, Fri"


def test_vix_gates_are_forward_looking():
    """ATM gates must use forward-looking VIX features (no hindsight)."""
    cfg = _cfg()
    assert "vix_open_max" in cfg, "must use vix_open (today's 9:30 VIX) gate"
    assert "vix_overnight_gap_max" in cfg, "must use overnight_gap (open - prev close) gate"
    assert cfg["vix_open_max"] == 25.0
    assert cfg["vix_overnight_gap_max"] == 1.0


def test_direction_filter_disabled():
    """Validated winner does NOT use SPX direction filter."""
    cfg = _cfg()
    assert cfg["direction_filter_enabled"] is False


def test_paper_safety_enabled():
    """ATM bot must default to paper-only unless explicitly overridden."""
    cfg = _cfg()
    assert cfg["require_live_override"] is True, "ATM bot defaults to paper-only"


def test_state_paths_separate_from_legacy():
    """All file paths must be in atm_bot subdirectory to avoid collision with legacy."""
    cfg = _cfg()
    for key in ("data_dir", "logs_dir", "state_file", "trade_csv"):
        path = cfg[key]
        assert "atm_bot" in path, f"{key}={path} must contain 'atm_bot' for isolation"


def test_profile_name_set():
    """Telegram tagging requires profile_name to be set."""
    cfg = _cfg()
    assert cfg.get("profile_name") == "PROFILE_NEW_ATM"


def test_timezone_is_eastern():
    cfg = _cfg()
    assert cfg["timezone"] == "America/New_York"


def test_no_market_fallback():
    """Combo MARKET orders are unsafe on SPX — must be disabled."""
    cfg = _cfg()
    assert cfg["entry_market_fallback"] is False, "MARKET fallback disabled per 2026-05-20 audit"


def test_otm_seed_close_to_atm():
    """ATM strategy uses very small otm_pct seed (just slightly OTM)."""
    cfg = _cfg()
    assert cfg["otm_pct"] <= 0.005, "ATM strategy targets near-the-money strikes"
