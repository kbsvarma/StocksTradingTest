"""Build the offline SPX/VIX provider + macro seed + cache index for cache-only reruns.

Sources (NO network):
  1. stop_175/daily_results.csv   — recorded 09:45 ET SPX/VIX (IBKR) 2013-04-01..2020-11-13
  2. ten_year/daily_results.csv   — recorded 09:45 ET SPX/VIX (IBKR) M/W/F 2013-04-01..2026-04-27
  3. ten_year/spx_vix_daily.csv   — daily OHLC; spx_close used as 15:45 EOD proxy, vix_open for derived days
  4. ten_year/raw_dbn_cache/*.meta.json — cached Databento queries; entry-snapshot symbol lists
     let us DERIVE the 09:45 SPX for days without records: we solve for the SPX value whose
     candidate-plan symbol set exactly reproduces the cached entry query (guaranteeing hash hits).

Outputs (in this dir):
  provider.csv          — date, spx_entry, vix_entry, spx_eod, source(recorded/derived), regime
  macro_fomc_dates.json — {date: ["FOMC"]} union of original runs' FOMC skips
  cache_meta_index.csv  — per-query index (schema/kind/date/path/symbols)
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

BT = Path(__file__).resolve().parent.parent          # backtests/
OUT = Path(__file__).resolve().parent
ET = ZoneInfo("America/New_York")
CACHE = BT / "ten_year" / "raw_dbn_cache"
OCC = re.compile(r"^(?P<root>[A-Z0-9]+)\s+(?P<yymmdd>\d{6})(?P<right>[CP])(?P<strike>\d{8})$")

WINDOW_START = date(2020, 1, 1)
WINDOW_END = date(2026, 4, 30)

# config_stop_*.yaml values (verified identical across the four configs except stop_multiplier)
OTM_LOW = 0.018     # -> regime low points [0.018, 0.019, 0.020]
OTM_HIGH = 0.025    # -> regime elevated points [0.025, 0.0275, 0.030]
SPREAD_WIDTH = 25
DTE_CANDIDATES = [0, 2]


def regime_points(name: str) -> list[float]:
    if name == "normal":
        low, high = OTM_LOW, round(OTM_LOW + 0.002, 4)
    else:
        low, high = OTM_HIGH, round(OTM_HIGH + 0.005, 4)
    return [low, round((low + high) / 2.0, 4), high]


def occ_parse(sym: str):
    m = OCC.match(sym.strip())
    if not m:
        return None, None, None
    return (
        datetime.strptime(m.group("yymmdd"), "%y%m%d").date(),
        m.group("right"),
        float(int(m.group("strike")) / 1000.0),
    )


def load_recorded() -> pd.DataFrame:
    frames = []
    for src, fp in [("stop_175", BT / "stop_175/daily_results.csv"), ("ten_year", BT / "ten_year/daily_results.csv")]:
        d = pd.read_csv(fp, usecols=["date", "spx_entry", "vix_entry"]).dropna(subset=["spx_entry", "vix_entry"])
        d["source"] = src
        frames.append(d)
    both = pd.concat(frames)
    # consistency check on overlap
    piv = both.pivot_table(index="date", columns="source", values=["spx_entry", "vix_entry"], aggfunc="first")
    ov = piv.dropna()
    if len(ov):
        spx_diff = (ov[("spx_entry", "stop_175")] - ov[("spx_entry", "ten_year")]).abs()
        vix_diff = (ov[("vix_entry", "stop_175")] - ov[("vix_entry", "ten_year")]).abs()
        print(f"[recorded] overlap days={len(ov)} max|spx diff|={spx_diff.max():.4f} max|vix diff|={vix_diff.max():.4f}")
        bad = ov[(spx_diff > 0.011) | (vix_diff > 0.011)]
        if len(bad):
            print(f"[recorded] WARNING {len(bad)} overlap days disagree > 0.01:")
            print(bad.head(10))
    # precedence: stop_175 first (all-weekday coverage), then ten_year fills the rest
    rec = both.drop_duplicates(subset="date", keep="first").set_index("date").sort_index()
    return rec


def index_cache() -> pd.DataFrame:
    rows = []
    for fp in CACHE.glob("*.meta.json"):
        try:
            q = json.load(open(fp))["query"]
        except Exception as exc:  # noqa: BLE001
            rows.append({"kind": "bad", "date": "", "path": fp.name, "symbols": str(exc)})
            continue
        schema = q.get("schema")
        dbn = fp.name[: -len(".meta.json")]
        if schema == "definition":
            rows.append({"kind": "definition", "date": str(q["start"])[:10], "path": dbn, "symbols": "SPXW.OPT"})
        elif schema == "cbbo-1m":
            st = datetime.fromisoformat(q["start"])
            en = datetime.fromisoformat(q["end"])
            d_et = st.astimezone(ET)
            kind = "entry" if (en - st).total_seconds() <= 120 else "pair"
            syms = q["symbols"]
            if isinstance(syms, str):
                syms = [syms]
            rows.append(
                {
                    "kind": kind,
                    "date": d_et.strftime("%Y-%m-%d"),
                    "path": dbn,
                    "symbols": "|".join(syms),
                    "et_time": d_et.strftime("%H:%M"),
                }
            )
        else:
            rows.append({"kind": schema or "unknown", "date": str(q.get("start"))[:10], "path": dbn, "symbols": ""})
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "cache_meta_index.csv", index=False)
    return df


def strikes_for_day(def_path: Path, day: date):
    """Replicates Backtester._definitions_snapshot -> {expiry: sorted strikes} + symbol map."""
    import databento as db

    store = db.DBNStore.from_file(def_path)
    df = store.to_df()
    if df.empty or "raw_symbol" not in df.columns:
        return {}
    parsed = df["raw_symbol"].astype(str).map(occ_parse)
    df = df.assign(
        expiry=[p[0] for p in parsed], right=[p[1] for p in parsed], strike=[p[2] for p in parsed]
    ).dropna(subset=["expiry", "right", "strike"])
    df = df[df["right"] == "P"]
    allowed = {day + timedelta(days=dte) for dte in DTE_CANDIDATES}
    df = df[df["expiry"].isin(allowed)]
    if df.empty:
        return {}
    snap = df.groupby("raw_symbol").last()[["expiry", "strike"]].reset_index()
    out = {}
    for exp, grp in snap.groupby("expiry"):
        sym_by_strike = {}
        for r in grp.itertuples():
            if r.strike not in sym_by_strike:
                sym_by_strike[r.strike] = r.raw_symbol
        out[exp] = sym_by_strike
    return out


def plan_symbols(day: date, spx: float, points: list[float], defs: dict) -> frozenset:
    """Replicates Backtester._build_candidate_plans symbol union."""
    syms = set()
    for dte in DTE_CANDIDATES:
        expiry = day + timedelta(days=dte)
        sym_by_strike = defs.get(expiry)
        if not sym_by_strike:
            continue
        strikes = sorted(sym_by_strike)
        strike_set = set(strikes)
        for otm in points:
            target = spx * (1.0 - otm)
            cands = [s for s in strikes if s <= target]
            if not cands:
                continue
            base_idx = strikes.index(max(cands))
            for offset in range(0, 10):
                idx = base_idx - offset
                if idx < 0:
                    break
                short_strike = strikes[idx]
                long_strike = round(short_strike - SPREAD_WIDTH, 3)
                if long_strike not in strike_set:
                    continue
                syms.add(sym_by_strike[short_strike])
                syms.add(sym_by_strike[long_strike])
    return frozenset(syms)


def derive_day(day: date, cached_sets: list[frozenset], defs: dict, spx_lo: float, spx_hi: float):
    """Find (regime, spx interval) whose plan symbol set equals a cached entry set."""
    all_strikes = sorted({s for m in defs.values() for s in m})
    if not all_strikes:
        return None
    for regime in ("normal", "elevated"):
        pts = regime_points(regime)
        # breakpoints where the plan set can change: spx = strike/(1-otm)
        bps = sorted(
            {s / (1.0 - otm) for s in all_strikes for otm in pts if spx_lo <= s / (1.0 - otm) <= spx_hi}
            | {spx_lo, spx_hi}
        )
        for a, b in zip(bps, bps[1:]):
            mid = (a + b) / 2.0
            got = plan_symbols(day, mid, pts, defs)
            if got and got in cached_sets:
                return regime, round(mid, 2), a, b
    return None


def main():
    rec = load_recorded()
    daily = pd.read_csv(BT / "ten_year/spx_vix_daily.csv").set_index("date")
    idx = index_cache()
    print(f"[cache] indexed {len(idx)} queries: {idx.kind.value_counts().to_dict()}")

    entry_by_date: dict[str, list] = {}
    def_by_date: dict[str, str] = {}
    for r in idx.itertuples():
        if r.kind == "entry":
            entry_by_date.setdefault(r.date, []).append(frozenset(r.symbols.split("|")))
        elif r.kind == "definition":
            def_by_date[r.date] = r.path

    # FOMC seed from original runs
    fomc = set()
    for fp in [BT / "stop_175/daily_results.csv", BT / "ten_year/daily_results.csv"]:
        d = pd.read_csv(fp, usecols=["date", "reason"])
        fomc |= set(d[d["reason"].astype(str).str.startswith("macro event filter")]["date"])
    (OUT / "macro_fomc_dates.json").write_text(
        json.dumps({d: ["FOMC"] for d in sorted(fomc)}, indent=1), encoding="utf-8"
    )
    print(f"[macro] {len(fomc)} FOMC-blocked dates seeded")

    rows = []
    days = pd.date_range(WINDOW_START, WINDOW_END, freq="D")
    n_derived = n_recorded = n_unmatched = 0
    for ts in days:
        d = ts.date()
        if d.weekday() >= 5:
            continue
        ds = d.isoformat()
        spx_eod = float(daily.loc[ds, "spx_close"]) if ds in daily.index else float("nan")
        if ds in rec.index:
            r = rec.loc[ds]
            rows.append(
                {"date": ds, "spx_entry": r.spx_entry, "vix_entry": r.vix_entry, "spx_eod": spx_eod,
                 "source": f"recorded:{r.source}", "regime": ""}
            )
            n_recorded += 1
            continue
        # derivation only possible with cached defs + entry query + daily anchor
        if ds not in entry_by_date or ds not in def_by_date or ds not in daily.index:
            continue
        defs = strikes_for_day(CACHE / def_by_date[ds], d)
        if not defs:
            continue
        lo = min(daily.loc[ds, "spx_open"], daily.loc[ds, "spx_low"]) * 0.995
        hi = max(daily.loc[ds, "spx_open"], daily.loc[ds, "spx_high"]) * 1.005
        got = derive_day(d, entry_by_date[ds], defs, lo, hi)
        if got is None:
            n_unmatched += 1
            continue
        regime, spx_mid, a, b = got
        vox = float(daily.loc[ds, "vix_open"])
        vix = min(vox, 19.5) if regime == "normal" else min(max(vox, 20.5), 29.5)
        rows.append(
            {"date": ds, "spx_entry": spx_mid, "vix_entry": round(vix, 2), "spx_eod": spx_eod,
             "source": "derived", "regime": regime}
        )
        n_derived += 1

    prov = pd.DataFrame(rows)
    prov.to_csv(OUT / "provider.csv", index=False)
    print(f"[provider] window weekdays={sum(1 for t in days if t.weekday()<5)} "
          f"recorded={n_recorded} derived={n_derived} unmatched-entry-sets={n_unmatched} "
          f"total={len(prov)}")
    yr = prov.assign(year=prov.date.str[:4]).groupby(["year", "source"]).size().unstack(fill_value=0)
    print(yr)


if __name__ == "__main__":
    sys.exit(main())
