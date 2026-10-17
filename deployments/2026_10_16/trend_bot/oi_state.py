"""OI-state provider — forward trial #2 (docs/PREREG_OI_FORWARD.md).
Frozen definition: state = sign of 5-trading-day pct change of front-month
open interest (prior day, roll jumps >10% masked). Fetched once daily from
GLBX statistics (~pennies), cached in state/oi_history.parquet.
Never raises; returns {} on any failure (trial logs None until data)."""
import os
from pathlib import Path
import pandas as pd

SYMS = {"NQ": "NQ.v.0", "ES": "ES.v.0", "YM": "YM.v.0", "GC": "GC.v.0"}
CACHE = Path(__file__).parent / "state/oi_history.parquet"


def _fetch(days=15):
    import databento as db
    key = os.environ.get("DATABENTO_API_KEY", "")
    if not key:
        return None
    c = db.Historical(key)
    end = pd.Timestamp.utcnow().normalize()
    d = None
    for back in (0, 1, 2):        # historical API lags the live edge ~1 session
        try:
            e = end - pd.Timedelta(days=back)
            d = c.timeseries.get_range(dataset="GLBX.MDP3",
                symbols=list(SYMS.values()), stype_in="continuous",
                schema="statistics",
                start=(e - pd.Timedelta(days=days)).date().isoformat(),
                end=e.date().isoformat())
            break
        except Exception:
            continue
    if d is None:
        return None
    df = d.to_df()
    df = df[df["stat_type"] == 9]
    df["day"] = pd.DatetimeIndex(df["ts_event"]).tz_convert("America/New_York").normalize().tz_localize(None)
    return df.groupby(["symbol", "day"])["quantity"].last().rename("oi").reset_index()


def get_states() -> dict:
    """{sym: {'oi_chg5': float, 'oi_state': 'falling'|'rising'}} — cached daily."""
    try:
        today = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
        hist = pd.read_parquet(CACHE) if CACHE.exists() else pd.DataFrame(columns=["symbol", "day", "oi"])
        if not len(hist) or hist["day"].max() < today - pd.Timedelta(days=1):
            new = _fetch()
            if new is not None:
                hist = (pd.concat([hist, new]).drop_duplicates(["symbol", "day"], keep="last")
                        .sort_values(["symbol", "day"]))
                hist.to_parquet(CACHE)
        out = {}
        for sym, full in SYMS.items():
            o = hist[hist["symbol"] == full].set_index("day")["oi"].sort_index()
            ch = o.pct_change()
            ch[ch.abs() > 0.10] = None            # roll mask (frozen)
            o5 = (1 + ch).rolling(5).apply(lambda x: x.prod()) - 1
            v = o5.dropna()
            if len(v):
                c5 = float(v.iloc[-1])
                out[sym] = {"oi_chg5": round(c5, 5),
                            "oi_state": "falling" if c5 < 0 else "rising"}
        return out
    except Exception:
        return {}
