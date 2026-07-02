"""validate2 — the honest factor-validation harness (replaces the 11-period study).

What it fixes vs validate.py (quant-panel findings, INTELLIGENCE_PLAN §2):
  HISTORY      ~10y daily panel (~115 non-overlapping 21d periods) instead of 11
  COSTS        per-side cost by ADV decile (5bps mega → 25bps small) + 5bps
               impact; gross AND net long-short reported, with turnover
  STATISTICS   circular block bootstrap CIs on mean IC; Benjamini-Hochberg
               FDR (10%) across the FULL factor×regime test grid; deflated
               Sharpe (Bailey/López de Prado) on the walk-forward portfolio
  OOS          weights frozen on data ≤ FREEZE_END (2023-12-31); in-sample
               and out-of-sample ICs reported SEPARATELY; config hash logged
  HONESTY      survivorship is REPORTED, not hidden: current-constituent
               universe, yfinance cannot price delisted names — treat all
               results as upper bounds

CLI:
  python -m advisor.research.validate2 --build-panel     # 10y pull (~10 min)
  python -m advisor.research.validate2 --run [--fwd 21]
Writes advisor/data/research/validation2_latest.json
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from advisor.research.datastore import RESEARCH_DIR

ET = ZoneInfo("America/New_York")
P10 = RESEARCH_DIR / "panel_10y"
OUT = RESEARCH_DIR / "validation2_latest.json"

FREEZE_END = "2023-12-31"      # OOS protocol: any post-hoc tweak restarts this
FWD = 21
WARMUP = 265                   # rows needed before first as-of (252d factors)
FDR_Q = 0.10
BENCH = ["SPY", "^VIX", "^VIX3M", "HYG"]


# ── 10y panel ────────────────────────────────────────────────────────────────

def build_panel() -> dict:
    import yfinance as yf
    from advisor.research.universe import load as load_universe
    t0 = time.time()
    u = load_universe()
    tickers = sorted(u["stocks"].keys()) + list(u["sector_etf"].values()) + BENCH
    tickers = list(dict.fromkeys(tickers))
    closes, volumes = [], []
    BATCH = 200
    fails = 0
    for i in range(0, len(tickers), BATCH):
        chunk = tickers[i:i + BATCH]
        try:
            df = yf.download(chunk, period="10y", interval="1d",
                             auto_adjust=True, progress=False,
                             group_by="column", threads=True)
            closes.append(df["Close"] if "Close" in df else df)
            volumes.append(df["Volume"])
            print(f"[panel10] batch {i // BATCH + 1}/"
                  f"{(len(tickers) - 1) // BATCH + 1} ok", flush=True)
        except Exception as exc:
            fails += 1
            print(f"[panel10] batch {i // BATCH + 1} FAILED: {exc}", flush=True)
    import pandas as pd
    close = pd.concat(closes, axis=1)
    volume = pd.concat(volumes, axis=1)
    close = close.loc[:, ~close.columns.duplicated()]
    volume = volume.loc[:, ~volume.columns.duplicated()]
    keep = close.columns[close.notna().sum() >= 120]
    close, volume = close[keep], volume[[c for c in keep if c in volume.columns]]
    P10.mkdir(parents=True, exist_ok=True)
    close.to_parquet(P10 / "close.parquet")
    volume.to_parquet(P10 / "volume.parquet")
    meta = {"built": datetime.now(ET).isoformat(), "rows": len(close),
            "cols": close.shape[1], "failed_batches": fails,
            "note": "current-constituent universe — SURVIVORSHIP-BIASED; "
                    "results are upper bounds"}
    (P10 / "meta.json").write_text(json.dumps(meta, indent=2))
    print(f"[panel10] {close.shape} in {time.time() - t0:.0f}s")
    return meta


# ── validation core ──────────────────────────────────────────────────────────

def _cost_bps(dollar_vol) -> "object":
    """Per-side cost by ADV decile: 25bps (smallest) → 5bps (largest), +5bps
    impact. Computed from CURRENT ADV — understates historical small-cap
    costs (labeled limitation)."""
    import numpy as np
    ranks = dollar_vol.rank(pct=True)
    return (25 - ranks * 20) + 5     # 30bps → 10bps per side incl. impact


def _block_bootstrap_ci(vals, n_draws: int = 1000, block: int = 3,
                        seed: int = 7) -> tuple:
    import numpy as np
    rng = np.random.default_rng(seed)
    arr = np.asarray(vals)
    n = len(arr)
    if n < 6:
        return (None, None)
    means = []
    n_blocks = max(1, n // block)
    for _ in range(n_draws):
        starts = rng.integers(0, n, size=n_blocks)
        sample = np.concatenate([arr[(s + np.arange(block)) % n] for s in starts])[:n]
        means.append(sample.mean())
    return (round(float(np.percentile(means, 2.5)), 4),
            round(float(np.percentile(means, 97.5)), 4))


def _bh_fdr(pvals: dict, q: float = FDR_Q) -> dict:
    """Benjamini-Hochberg: returns {test_name: survives_bool}."""
    items = sorted((p, k) for k, p in pvals.items() if p is not None)
    m = len(items)
    survives = {k: False for k in pvals}
    max_i = 0
    for i, (p, _k) in enumerate(items, start=1):
        if p <= q * i / m:
            max_i = i
    for i, (_p, k) in enumerate(items, start=1):
        survives[k] = i <= max_i
    return survives


def _deflated_sharpe(returns, n_trials: int) -> dict:
    """Bailey & López de Prado deflated Sharpe probability."""
    import numpy as np
    from scipy.stats import norm, skew, kurtosis
    r = np.asarray(returns)
    T = len(r)
    if T < 10 or r.std(ddof=1) == 0:
        return {"dsr": None}
    sr = r.mean() / r.std(ddof=1)
    sk, ku = float(skew(r)), float(kurtosis(r, fisher=False))
    # expected max SR of n_trials under the null
    emc = 0.5772156649
    sr0 = (1 - emc) * norm.ppf(1 - 1 / n_trials) \
        + emc * norm.ppf(1 - 1 / (n_trials * np.e))
    sr0 *= 1 / (T - 1) ** 0.5 * (T - 1) ** 0.5 / T ** 0.5   # scale to per-period
    denom = (1 - sk * sr + (ku - 1) / 4 * sr ** 2)
    if denom <= 0:
        return {"dsr": None, "sr_per_period": round(float(sr), 3)}
    dsr = norm.cdf(((sr - sr0) * (T - 1) ** 0.5) / denom ** 0.5)
    return {"sr_per_period": round(float(sr), 3),
            "sr_annualized": round(float(sr * (252 / FWD) ** 0.5), 2),
            "n_trials_assumed": n_trials,
            "dsr": round(float(dsr), 3),
            "note": "DSR = P(true SR > 0 | multiple testing); <0.95 = not proven"}


def run(fwd: int = FWD) -> dict:
    import numpy as np
    import pandas as pd
    from scipy.stats import spearmanr, ttest_1samp
    from advisor.research.factors import WEIGHTS, detect_regime, raw_factors
    from advisor.research.universe import load as load_universe

    if not (P10 / "close.parquet").exists():
        return {"error": "panel_10y missing — run --build-panel first"}
    close = pd.read_parquet(P10 / "close.parquet")
    volume = pd.read_parquet(P10 / "volume.parquet")
    u = load_universe()
    rets_full = close.pct_change()

    dates = list(close.index)
    period_ics: list[dict] = []
    ls_returns: dict[str, list] = {}
    prev_top: dict[str, set] = {}
    turnover: dict[str, list] = {}
    wf_returns, wf_spy = [], []

    t0 = time.time()
    for k in range(WARMUP, len(dates) - fwd, fwd):
        c = close.iloc[:k + 1]
        v = volume.iloc[:k + 1]
        r = rets_full.iloc[:k + 1]
        try:
            f, z, liquid, dollar_vol, px, _sm, _sd = raw_factors(c, v, r, u)
        except Exception:
            continue
        regime = detect_regime(c)
        fwd_ret = (close.iloc[k + fwd] / close.iloc[k] - 1).reindex(z.index).dropna()
        oos = str(dates[k].date()) > FREEZE_END
        row = {"as_of": str(dates[k].date()), "regime": regime["name"],
               "oos": oos, "n": len(fwd_ret), "ic": {}}
        cost = _cost_bps(dollar_vol.reindex(z.index)) / 1e4

        for col in z.columns:
            x = z[col].reindex(fwd_ret.index)
            ok = x.notna()
            if ok.sum() < 100:
                continue
            ic, _ = spearmanr(x[ok], fwd_ret[ok])
            row["ic"][col] = round(float(ic), 4)
            # decile long-short, net of costs
            xs = x[ok]
            top = set(xs.nlargest(max(20, len(xs) // 10)).index)
            bot = set(xs.nsmallest(max(20, len(xs) // 10)).index)
            gross = fwd_ret[list(top)].mean() - fwd_ret[list(bot)].mean()
            held = prev_top.get(col, set())
            churn = 1.0 if not held else len(top - held) / max(len(top), 1)
            prev_top[col] = top
            side_cost = float(cost.reindex(list(top | bot)).mean() or 0.002)
            net = gross - churn * side_cost * 2
            ls_returns.setdefault(col, []).append(
                {"as_of": row["as_of"], "oos": oos,
                 "gross": round(float(gross), 5), "net": round(float(net), 5)})
            turnover.setdefault(col, []).append(churn)

        # walk-forward top-20 composite (regime weights), net of costs
        w = WEIGHTS[regime["name"]]
        comp = sum(z[c2].fillna(0) * wt for c2, wt in w.items())
        comp = comp.reindex(fwd_ret.index).dropna()
        if len(comp) > 100:
            top20 = comp.nlargest(20).index
            side_cost = float(cost.reindex(top20).mean() or 0.002)
            held = prev_top.get("__wf__", set())
            churn = 1.0 if not held else len(set(top20) - held) / 20
            prev_top["__wf__"] = set(top20)
            wf_returns.append({"as_of": row["as_of"], "oos": oos,
                               "net": round(float(fwd_ret[top20].mean()
                                                  - churn * side_cost), 5)})
            if "SPY" in close.columns:
                wf_spy.append(float(close["SPY"].iloc[k + fwd]
                                    / close["SPY"].iloc[k] - 1))
        period_ics.append(row)

    # aggregate: per factor × (all, is, oos, regime) with BH-FDR
    factors = sorted({c for r_ in period_ics for c in r_["ic"]})
    tests, pvals = {}, {}
    for fac in factors:
        for scope, flt in (("all", lambda r_: True),
                           ("insample", lambda r_: not r_["oos"]),
                           ("oos", lambda r_: r_["oos"]),
                           ("risk_on", lambda r_: r_["regime"] == "risk_on"),
                           ("neutral", lambda r_: r_["regime"] == "neutral"),
                           ("stress", lambda r_: r_["regime"] == "stress")):
            vals = [r_["ic"][fac] for r_ in period_ics
                    if fac in r_["ic"] and flt(r_)]
            if len(vals) < 6:
                continue
            arr = np.array(vals)
            t, p = ttest_1samp(arr, 0)
            lo, hi = _block_bootstrap_ci(arr)
            nets = [x["net"] for x in ls_returns.get(fac, [])
                    if flt({"oos": x["oos"], "regime": "?", "ic": {}})] \
                if scope in ("all", "insample", "oos") else []
            tests[f"{fac}|{scope}"] = {
                "n": len(vals), "mean_ic": round(float(arr.mean()), 4),
                "t": round(float(t), 2), "p": round(float(p), 4),
                "ci95": [lo, hi],
                "net_ls_per_period_pct": round(float(np.mean(nets)) * 100, 3)
                if nets else None,
                "avg_turnover": round(float(np.mean(turnover.get(fac, [0]))), 2),
            }
            pvals[f"{fac}|{scope}"] = float(p)
    fdr = _bh_fdr(pvals)
    for k2 in tests:
        tests[k2]["fdr10_survives"] = bool(fdr.get(k2, False))

    wf_net = [x["net"] for x in wf_returns]
    wf_oos = [x["net"] for x in wf_returns if x["oos"]]
    n_trials = len(pvals)          # honest lower bound on the trial space
    cfg = {"fwd": fwd, "freeze_end": FREEZE_END, "fdr_q": FDR_Q,
           "warmup": WARMUP, "weights": WEIGHTS}
    result = {
        "as_of": datetime.now(ET).isoformat(),
        "config_hash": hashlib.sha256(
            json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:12],
        "config": cfg,
        "panel": json.loads((P10 / "meta.json").read_text()),
        "n_periods": len(period_ics),
        "n_tests_in_grid": len(pvals),
        "tests": tests,
        "walk_forward_top20": {
            "n_periods": len(wf_net),
            "net_total_return_pct": round((np.prod([1 + x for x in wf_net]) - 1)
                                          * 100, 1) if wf_net else None,
            "spy_total_pct": round((np.prod([1 + x for x in wf_spy]) - 1) * 100, 1)
            if wf_spy else None,
            "oos_net_mean_per_period_pct": round(float(np.mean(wf_oos)) * 100, 3)
            if wf_oos else None,
            "deflated_sharpe": _deflated_sharpe(wf_net, max(n_trials, 10)),
        },
        "caveats": [
            "SURVIVORSHIP: current-constituent universe; yfinance cannot price "
            "delisted names — every number here is an upper bound",
            "costs from CURRENT ADV deciles — understates historical small-cap costs",
            f"OOS freeze at {FREEZE_END}: any parameter change after reading OOS "
            "results restarts the freeze (log this run's config_hash)",
            "t-stats on 21d non-overlapping periods; regime subsets are small",
        ],
        "runtime_s": round(time.time() - t0, 1),
    }
    OUT.write_text(json.dumps(result, indent=2))
    return result


def main() -> int:
    args = sys.argv[1:]
    if "--build-panel" in args:
        build_panel()
        return 0
    if "--run" in args:
        fwd = int(args[args.index("--fwd") + 1]) if "--fwd" in args else FWD
        res = run(fwd)
        if "error" in res:
            print(res["error"])
            return 1
        print(f"validate2: {res['n_periods']} periods, "
              f"{res['n_tests_in_grid']} tests, {res['runtime_s']}s")
        survivors = [k for k, t in res["tests"].items()
                     if t["fdr10_survives"] and k.endswith("|all")]
        print(f"FDR-10% survivors (all-sample): {survivors}")
        wf = res["walk_forward_top20"]
        print(f"walk-forward net {wf['net_total_return_pct']}% vs SPY "
              f"{wf['spy_total_pct']}% · DSR {wf['deflated_sharpe'].get('dsr')}")
        print(f"→ {OUT}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
