"""Verify the ATM bot can read/write to its data directories without collision with legacy."""
import json
import tempfile
from pathlib import Path
import yaml


def test_state_file_path_is_atm_specific():
    """state_file path must include atm_bot, not vG_spx_w50_vix25."""
    cfg = yaml.safe_load((Path(__file__).parent.parent / "config_atm.yaml").read_text())
    assert "atm_bot" in cfg["state_file"]
    assert "vG_spx_w50_vix25" not in cfg["state_file"]


def test_trade_csv_path_is_atm_specific():
    cfg = yaml.safe_load((Path(__file__).parent.parent / "config_atm.yaml").read_text())
    assert "atm_bot" in cfg["trade_csv"]
    assert "trades.csv" in cfg["trade_csv"]


def test_data_dir_can_be_created():
    """The data_dir from config should be creatable (parent dirs exist)."""
    cfg = yaml.safe_load((Path(__file__).parent.parent / "config_atm.yaml").read_text())
    # Resolve relative to repo root (parent of webull_bot_atm)
    repo_root = Path(__file__).parent.parent.parent
    data_dir = repo_root / cfg["data_dir"]
    # We don't actually create it (might already exist or be in a test env)
    # but the parent must exist
    assert data_dir.parent.parent.exists(), f"Parent dir {data_dir.parent.parent} must exist"


def test_logger_writes_trades_with_profile_column():
    """When logger.append_trade is called, the row must include Profile column."""
    from webull_bot_atm.logger import BotLogger
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        logger = BotLogger(logs_dir=str(td_path), trade_csv=str(td_path / "trades.csv"))
        logger.append_trade(
            date="2026-05-22", symbol="SPXW", expiry="2026-05-22",
            short_strike=7400, long_strike=7355, entry_credit=9.5,
            entry_spx=7473.0, entry_vix=17.0, exit_price=0.05,
            pnl_pts=9.45, pnl_usd=945, exit_reason="EXPIRED",
        )
        # Read back
        import csv
        with (td_path / "trades.csv").open() as fh:
            rows = list(csv.DictReader(fh))
        assert len(rows) == 1
        assert "Profile" in rows[0], f"Profile column missing. Cols: {list(rows[0].keys())}"
        assert rows[0]["Profile"] == "PROFILE_NEW_ATM"


def test_atm_state_default_paths_in_data_dir():
    """Verify state_file path is co-located with heartbeat / trades in same data dir."""
    cfg = yaml.safe_load((Path(__file__).parent.parent / "config_atm.yaml").read_text())
    state_parent = Path(cfg["state_file"]).parent
    trade_parent = Path(cfg["trade_csv"]).parent
    assert state_parent == trade_parent, \
        f"state_file ({state_parent}) and trade_csv ({trade_parent}) must share dir for dashboard discovery"
