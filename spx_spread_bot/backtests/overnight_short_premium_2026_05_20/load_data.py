"""
Load SPXW options data with BOTH puts and calls for overnight backtest.
DTE <= 3 only (we only need contracts that survive overnight).

Caches to /tmp/spxw_overnight_both_sides.pkl
"""
from __future__ import annotations
import re, pickle, warnings
from datetime import date, datetime, timedelta
from pathlib import Path
import databento as db
import pandas as pd

warnings.filterwarnings("ignore")

BASE = Path(__file__).resolve().parent.parent.parent / "data" / "databento"
SPXW_DIRS = [
    BASE / "spxw_opt_2014_2015",
    BASE / "spxw_opt_2016_2018",
    BASE / "spxw_opt_2019",
    BASE / "spxw_opt_2020_2022",
    BASE / "spxw_opt",
    BASE / "spxw_opt_2025_2026",
]
CACHE = Path("/tmp/spxw_overnight_both_sides.pkl")
MAX_DTE_TO_LOAD = 4

OCC_RE = re.compile(
    r"^(?P<root>[A-Z0-9]+)\s+(?P<yymmdd>\d{6})(?P<right>[CP])(?P<strike>\d{8})$"
)


def parse_symbol(sym: str):
    m = OCC_RE.match(sym.strip())
    if not m:
        return None
    try:
        exp = datetime.strptime(m.group("yymmdd"), "%y%m%d").date()
        strike = int(m.group("strike")) / 1000.0
        return m.group("root"), exp, m.group("right"), strike
    except Exception:
        return None


def load_all(force=False) -> dict:
    """raw[file_date][right][expiry_date] = DataFrame(strike, open, high, low, close)"""
    if CACHE.exists() and not force:
        with CACHE.open("rb") as f:
            raw = pickle.load(f)
        print(f"cache: {len(raw)} trading days")
        return raw

    print("Loading SPXW data (DTE <= %d, both P+C)..." % MAX_DTE_TO_LOAD)
    raw: dict = {}
    for data_dir in SPXW_DIRS:
        if not data_dir.exists():
            continue
        files = sorted(data_dir.glob("opra-pillar-*.ohlcv-1d.dbn.zst"))
        print(f"  {data_dir.name:30}  {len(files):4} files")
        for i, f in enumerate(files):
            try:
                day_str = f.stem.split(".")[0].split("-")[2]
                file_date = datetime.strptime(day_str, "%Y%m%d").date()
            except Exception:
                continue
            if file_date in raw:
                continue
            try:
                store = db.DBNStore.from_file(str(f))
                df_raw = store.to_df()
            except Exception:
                continue
            buckets = {"P": {}, "C": {}}
            for sym, grp in df_raw.groupby("symbol"):
                parsed = parse_symbol(str(sym).strip())
                if not parsed:
                    continue
                root, exp, right, strike = parsed
                if root not in ("SPXW", "SPX"):
                    continue
                dte = (exp - file_date).days
                if dte < 0 or dte > MAX_DTE_TO_LOAD:
                    continue
                r = grp.iloc[0]
                buckets[right].setdefault(exp, []).append({
                    "strike": strike,
                    "open":   float(r["open"]),
                    "high":   float(r["high"]),
                    "low":    float(r["low"]),
                    "close":  float(r["close"]),
                })
            day_obj = {}
            for right, by_exp in buckets.items():
                day_obj[right] = {exp: pd.DataFrame(rows) for exp, rows in by_exp.items()}
            if day_obj["P"] or day_obj["C"]:
                raw[file_date] = day_obj
        print(f"    cum days={len(raw)}")

    print(f"loaded {len(raw)} trading days")
    with CACHE.open("wb") as f:
        pickle.dump(raw, f)
    return raw


if __name__ == "__main__":
    raw = load_all()
    if raw:
        d = sorted(raw.keys())
        print(f"range: {d[0]} -> {d[-1]}")
