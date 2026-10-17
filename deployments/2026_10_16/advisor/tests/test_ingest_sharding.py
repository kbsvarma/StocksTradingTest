from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from advisor.research.ingest import runner, snapshots


def test_rotating_info_set_prioritizes_active_and_respects_budget():
    universe = [f"T{i:04d}" for i in range(1000)]
    active = ["T0999", "T0500", "NOT_IN_UNIVERSE"]
    first = runner._rotating_info_set(universe, active, day_ordinal=10, budget=100)
    second = runner._rotating_info_set(universe, active, day_ordinal=11, budget=100)
    assert len(first) == len(set(first)) == 100
    assert {"T0999", "T0500"}.issubset(first)
    assert first != second


def test_recent_partitions_build_latest_per_ticker_coverage(tmp_path):
    today = datetime.now(snapshots.ET).date()
    yesterday = today - timedelta(days=1)
    pd.DataFrame([
        {"ticker": "AAA", "snapshot_ts": f"{yesterday}T06:00:00-04:00", "marketCap": 1},
        {"ticker": "BBB", "snapshot_ts": f"{yesterday}T06:00:00-04:00", "marketCap": 2},
    ]).to_parquet(tmp_path / f"dt={yesterday}.parquet", index=False)
    pd.DataFrame([
        {"ticker": "AAA", "snapshot_ts": f"{today}T06:00:00-04:00", "marketCap": 3},
    ]).to_parquet(tmp_path / f"dt={today}.parquet", index=False)
    result = snapshots.build_coverage(["AAA", "BBB", "CCC"], tmp_path)
    panel = pd.read_parquet(tmp_path / "latest_coverage.parquet").set_index("ticker")
    assert result["coverage_rows"] == 2
    assert result["coverage_universe"] == 3
    assert panel.loc["AAA", "marketCap"] == 3


def test_info_quality_uses_rolling_coverage_not_single_fetch():
    result = runner._apply_quality(
        "info", {"errors": 20, "coverage_rows": 900,
                 "coverage_universe": 1000}, requested=100)
    assert result["fetch_success_ratio"] == 0.8
    assert result["success_ratio"] == 0.9
    assert result["ok"] is True


def test_same_day_snapshot_retry_preserves_prior_successes(tmp_path, monkeypatch):
    day = datetime.now(snapshots.ET).date().isoformat()
    prior = pd.DataFrame([{"ticker": "AAA", "snapshot_ts": f"{day}T06:00:00-04:00",
                           "marketCap": 1}])
    prior.to_parquet(tmp_path / f"dt={day}.parquet", index=False)
    # **kwargs: this test is about merge preservation, not the pool's call
    # signature — it must not break when build() tunes pacing/retries.
    monkeypatch.setattr("advisor.research.ingest._pool.run_pool",
                        lambda fn, tickers, **kw: (
                            [{"ticker": "BBB", "marketCap": 2}], 0, []))
    result = snapshots.build(["BBB"], tmp_path)
    stored = pd.read_parquet(tmp_path / f"dt={day}.parquet")
    assert result["rows"] == 2
    assert set(stored.ticker) == {"AAA", "BBB"}
