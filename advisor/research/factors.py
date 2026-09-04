"""Cross-sectional factor engine — literature-grounded, sector-neutral, regime-aware.

Computes for every liquid name in the cached panel:

  mom_12_1   12-month momentum excluding last month   (Jegadeesh-Titman 1993)
  rev_1m     1-month short-term reversal              (Jegadeesh 1990, Lehmann 1990)
  resid_mom  6-month residual momentum vs sector ETF  (Blitz-Huij-Martens 2011)
  prox_52w   proximity to 52-week high                (George-Hwang 2004)
  lowvol     negative 60d realized vol                (Ang et al 2006 low-vol anomaly)
  turn_anom  20d/120d volume ratio (abnormal attention/turnover)
  shock      recent outsized move flag (|1d| > 2.5σ in last 10d) — exploratory
             candidate flag only; it is not a validated standalone signal

Method: winsorized (±3) z-scores computed WITHIN GICS sector (sector-neutral),
combined with REGIME-CONDITIONED weights — momentum is de-weighted and
reversal/low-vol up-weighted when the vol/credit regime turns, per the
momentum-crash literature (Daniel-Moskowitz 2016). Liquidity floor:
median 21d dollar volume > $10M and price > $5.

Output: ranked LONG and SHORT sheets with full factor attribution per name.

CLI: python -m advisor.research.factors [--json PATH] [--top 20]
"""
from __future__ import annotations

import json
import os
import sys
import warnings
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore")
ET = ZoneInfo("America/New_York")
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Exploratory discovery weights informed by a small 2026-06-12 IC study
# (advisor/research/validate.py, 11 non-overlapping periods, mostly risk-on).
# They are not production weights; validation_status() enforces the promotion gate.
#   positive in sample: mom_12_1 (t 1.88), resid_mom (t 1.84), prox_52w (t 1.84)
#   wrong-way IN RISK_ON sample: rev_1m (t -1.74), lowvol (t -1.85) → zeroed
#     in risk_on ONLY; kept in neutral/stress where the literature prior
#     (reversal/low-vol shine in turmoil) is untested by this bull-period sample.
#   dead: turn_anom (IC -0.008) → zeroed everywhere; kept computed for display.
WEIGHTS = {
    "risk_on": {"mom_12_1": .35, "resid_mom": .30, "prox_52w": .25,
                "rev_1m": .00, "lowvol": .00, "turn_anom": .00},
    "neutral": {"mom_12_1": .25, "resid_mom": .20, "prox_52w": .15,
                "rev_1m": .15, "lowvol": .10, "turn_anom": .00},
    "stress":  {"mom_12_1": .08, "resid_mom": .07, "prox_52w": .05,
                "rev_1m": .30, "lowvol": .30, "turn_anom": .00},
}


def validation_status() -> dict:
    """Machine-readable promotion gate for the factor ranker."""
    data = REPO_ROOT / "advisor" / "data" / "research"
    reasons, survivors = [], []
    dsr, validation_as_of, live_n, live_independent = None, None, 0, 0
    config_hash, approved_hash, oos_n = None, None, 0
    try:
        v = json.loads((data / "validation2_latest.json").read_text())
        validation_as_of = v.get("as_of")
        wf = v.get("walk_forward_top20") or {}
        dsr = (wf.get("deflated_sharpe_oos") or {}).get("dsr")
        oos_n = int(wf.get("oos_n_periods") or 0)
        config_hash = v.get("config_hash")
        survivors = [k for k, row in (v.get("tests") or {}).items()
                     if k.endswith("|oos") and row.get("fdr10_survives")]
    except Exception:
        reasons.append("validation2 artifact unavailable")
    try:
        live = json.loads((data / "ic_live.json").read_text())
        live_n = int(live.get("n_matured") or 0)
        live_independent = max(
            (int(row.get("n_independent") or 0)
             for row in (live.get("factors") or {}).values()), default=0)
    except Exception:
        reasons.append("live IC artifact unavailable")
    try:
        approval = json.loads((data / "model_approval.json").read_text())
        approved_hash = approval.get("config_hash") if approval.get("approved") is True else None
    except Exception:
        reasons.append("no explicit model approval artifact")
    if dsr is None or dsr < 0.95:
        reasons.append(f"out-of-sample deflated Sharpe confidence {dsr} < 0.95")
    if oos_n < 24:
        reasons.append(f"only {oos_n} non-overlapping OOS periods; require 24")
    if not survivors:
        reasons.append("no out-of-sample factor test survives 10% FDR")
    if live_independent < 12:
        reasons.append(f"only {live_independent} independent live-IC windows; require 12")
    if not config_hash or approved_hash != config_hash:
        reasons.append("model approval missing or does not match validation config hash")
    eligible = not reasons
    return {"status": "production_eligible" if eligible else "research_only",
            "role": "ranking" if eligible else "discovery_only",
            "reasons": reasons, "deflated_sharpe_confidence": dsr,
            "fdr10_survivors": survivors, "live_ic_matured": live_n,
            "live_ic_independent": live_independent,
            "oos_n_periods": oos_n, "config_hash": config_hash,
            "approved_config_hash": approved_hash,
            "validation_as_of": validation_as_of}


# ── live-IC feedback ────────────────────────────────────────────────────────
# 2026-09-03: the daily-picks lane lost 8.7pp to SPY over Jul-Aug because the
# composite is ~90% momentum in risk_on while momentum's realized IC was
# deeply negative (mom_12_1 -0.216, resid_mom -0.257, prox_52w -0.189). The
# market-regime detector never noticed: VIX stayed ~15 and SPY held its
# 200dma, so it kept calling risk_on. Low VIX does not mean momentum works.
# ic_monitor had measured the crash correctly and nothing consumed it.
#
# Three deliberate constraints, because this is the exact place overfitting
# gets institutionalised:
#   DE-WEIGHT ONLY  a negative IC shrinks a factor toward the floor; it never
#                   inverts it (shorting your own signal on one regime's data)
#                   and never amplifies a positive one.
#   SHRINKAGE       the adjustment scales with independent-window count, so
#                   two overlapping observations barely move it.
#   FLOOR           no factor is driven to zero — that is a bigger claim than
#                   the evidence supports.
IC_FEEDBACK_FLOOR = 0.25      # smallest surviving fraction of a factor's weight
IC_FEEDBACK_SCALE = 0.10      # |IC| at which the penalty saturates
IC_FEEDBACK_K = 6.0           # shrinkage half-weight in independent windows


def ic_weight_multipliers() -> dict:
    """Per-factor weight multipliers in [FLOOR, 1.0] from realized live IC."""
    if os.environ.get("ADVISOR_IC_FEEDBACK", "1").strip() in ("0", "false", "no"):
        return {"enabled": False, "reason": "disabled by ADVISOR_IC_FEEDBACK",
                "multipliers": {}}
    path = (REPO_ROOT / "advisor" / "data" / "research" / "ic_live.json")
    try:
        live = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"enabled": False, "reason": "no ic_live.json", "multipliers": {}}
    mults, detail = {}, {}
    for name, row in (live.get("factors") or {}).items():
        ic = row.get("mean_ic_independent")
        if ic is None:
            ic = row.get("mean_ic")
        n_ind = int(row.get("n_independent") or 0)
        if ic is None or n_ind < 1:
            continue
        if ic >= 0:
            mults[name] = 1.0                       # never amplify
            detail[name] = {"ic": ic, "n_independent": n_ind, "mult": 1.0}
            continue
        confidence = n_ind / (n_ind + IC_FEEDBACK_K)
        severity = min(1.0, abs(ic) / IC_FEEDBACK_SCALE)
        mult = round(max(IC_FEEDBACK_FLOOR,
                         1.0 - confidence * severity * (1.0 - IC_FEEDBACK_FLOOR)), 4)
        mults[name] = mult
        detail[name] = {"ic": round(ic, 4), "n_independent": n_ind, "mult": mult}
    return {"enabled": True, "multipliers": mults, "detail": detail,
            "params": {"floor": IC_FEEDBACK_FLOOR, "scale": IC_FEEDBACK_SCALE,
                       "shrinkage_k": IC_FEEDBACK_K},
            "policy": ("de-weight only; never inverts or amplifies a factor; "
                       "shrinkage scales with independent-window count"),
            "ic_as_of": live.get("as_of")}


REDUNDANCY_FLOOR = 0.35      # a duplicated factor is shrunk, never removed


def redundancy_multipliers(z, active: list) -> dict:
    """De-weight factors whose information the others already carry.

    The composite sums correlated z-scores. `mom_12_1`, `prox_52w` and
    `resid_mom` are three ways of measuring trend, so a six-factor composite
    can carry far less independent information than its factor count suggests
    — and nobody could say how much, because the correlation was never
    reported.

      Green, Hand & Zhang (2017) (RFS) — of ~94 documented return
      characteristics only about a dozen carry independent information once
      tested jointly.
      Novy-Marx (2016) — naive combination of multiple signals inflates
      apparent significance; the correct null is far more demanding.

    Method: for each active factor, R^2 from regressing it on the others,
    which the correlation-matrix inverse gives directly (VIF_i = (C^-1)_ii =
    1/(1-R^2_i)). The multiplier is sqrt(1 - R^2_i) = 1/sqrt(VIF_i) — the
    share of the factor that is its OWN, in standard-deviation units.

    Symmetric (Lowdin) orthogonalization was the alternative. It was rejected
    because it rewrites what each factor MEANS, which would silently break
    every `attribution` figure on the factor sheet. This changes only how much
    independent information a factor is credited with, and leaves its z-score
    interpretable.

    De-weight only, floored, and inert when the matrix is singular.
    """
    import numpy as np
    import pandas as pd

    cols = [c for c in active if c in z.columns]
    if len(cols) < 2:
        return {"enabled": False, "reason": "fewer than 2 active factors",
                "multipliers": {}}
    sub = z[cols].dropna()
    if len(sub) < 50:
        return {"enabled": False, "reason": f"only {len(sub)} complete rows",
                "multipliers": {}}
    corr = sub.corr()
    try:
        inv = np.linalg.inv(corr.values)
    except np.linalg.LinAlgError:
        return {"enabled": False, "reason": "singular correlation matrix",
                "multipliers": {}}
    mults, detail = {}, {}
    for i, c in enumerate(cols):
        vif = float(inv[i, i])
        if not np.isfinite(vif) or vif < 1.0:
            mults[c] = 1.0
            detail[c] = {"vif": None, "r2": None, "mult": 1.0}
            continue
        r2 = 1.0 - 1.0 / vif
        mults[c] = round(max(REDUNDANCY_FLOOR, (1.0 - r2) ** 0.5), 4)
        detail[c] = {"vif": round(vif, 3), "r2": round(r2, 4),
                     "mult": mults[c]}
    # participation ratio of the eigenvalues: how many INDEPENDENT factors
    # this correlated set is actually worth
    eig = np.linalg.eigvalsh(corr.values)
    eig = eig[eig > 0]
    eff = float((eig.sum() ** 2) / (eig ** 2).sum()) if len(eig) else None
    return {"enabled": True, "multipliers": mults, "detail": detail,
            "n_active": len(cols),
            "effective_factor_count": round(eff, 2) if eff else None,
            "correlations": {c: {d: round(float(corr.at[c, d]), 3)
                                 for d in cols if d != c} for c in cols},
            "policy": ("de-weight by sqrt(1 - R^2) against the other active "
                       f"factors (floor {REDUNDANCY_FLOOR}); z-scores keep "
                       "their meaning so attribution stays readable")}


def detect_regime(close) -> dict:
    """Regime from SPY trend, VIX level + term structure, credit, breadth.

    VIX cutoffs are DATA-DRIVEN (loop-pass-5): stress = VIX above its own
    trailing 90th percentile, risk_on requires below the 70th — adapts to the
    vol level of the era instead of hand-set 20/26 constants. Term-structure
    backwardation (>1.0) and credit (-2% HYG/21d) remain absolute: those are
    structural stress markers, not level-relative ones.
    """
    import pandas as _pd
    def _bench(col, min_len=1):
        # Benchmark series from the panel if present + long enough, else from
        # yfinance. A panel that drops SPY/^VIX must NOT crash the whole research
        # build (KeyError 'SPY' was silently killing daily briefs — 2026-07-01 fix).
        try:
            if col in getattr(close, "columns", []):
                s = close[col].dropna()
                if len(s) >= min_len:
                    return s
        except Exception:
            pass
        try:
            import yfinance as _yf
            return _yf.Ticker(col).history(period="2y")["Close"].dropna()
        except Exception:
            return _pd.Series(dtype=float)
    spy = _bench("SPY", min_len=200)
    vix_hist = _bench("^VIX")
    if len(spy) < 200 or len(vix_hist) < 1:
        # Benchmarks unavailable — default neutral instead of crashing the build.
        return {"name": "neutral", "vix": None, "vix_term": None,
                "vix_p70": None, "vix_p90": None, "spy_above_200dma": None,
                "hyg_21d_pct": None, "weights": WEIGHTS["neutral"]}
    vix = float(vix_hist.iloc[-1])
    vix_p70 = float(vix_hist.quantile(0.70))
    vix_p90 = float(vix_hist.quantile(0.90))
    try:
        term = vix / float(_bench("^VIX3M").iloc[-1])
    except Exception:
        term = 0.9
    trend_up = float(spy.iloc[-1]) > float(spy.rolling(200).mean().iloc[-1])
    try:
        hyg = _bench("HYG")
        credit_21d = (float(hyg.iloc[-1]) / float(hyg.iloc[-22]) - 1) * 100
    except Exception:
        credit_21d = 0.0
    if vix > vix_p90 or term > 1.0 or credit_21d < -2.0:
        name = "stress"
    elif not trend_up:
        name = "neutral"
    elif vix < vix_p70 and term < 0.95 and trend_up:
        name = "risk_on"
    else:
        name = "neutral"
    return {"name": name, "vix": round(vix, 1), "vix_term": round(term, 2),
            "vix_p70": round(vix_p70, 1), "vix_p90": round(vix_p90, 1),
            "spy_above_200dma": trend_up, "hyg_21d_pct": round(credit_21d, 1),
            "weights": WEIGHTS[name]}


def raw_factors(close, volume, rets, u: dict):
    """Compute raw factors + sector-neutral z's from PRE-SLICED panels.

    Panels must already end at the as-of date (signal time = that date's
    close; consumed next morning — the documented convention, no lookahead).
    Shared by live compute() and the IC validation harness so there is one
    source of truth for the math. Returns (f, z, liquid, dollar_vol, px,
    shock_mask, shock_dir).
    """
    import numpy as np
    import pandas as pd

    sectors = u["stocks"]
    stock_cols = [c for c in close.columns if c in sectors]
    c, r, v = close[stock_cols], rets[stock_cols], volume[stock_cols]

    dollar_vol = (c * v).rolling(21, min_periods=10).median().iloc[-1]
    px = c.iloc[-1]
    liquid = dollar_vol[(dollar_vol > 1e7) & (px > 5)].index
    c, r, v = c[liquid], r[liquid], v[liquid]

    f = pd.DataFrame(index=liquid)
    f["mom_12_1"] = c.iloc[-21] / c.iloc[-252] - 1
    f["rev_1m"] = -(c.iloc[-1] / c.iloc[-21] - 1)
    f["prox_52w"] = c.iloc[-1] / c.rolling(252, min_periods=120).max().iloc[-1]
    f["lowvol"] = -r.rolling(60, min_periods=40).std().iloc[-1]
    f["turn_anom"] = (v.rolling(20, min_periods=10).mean().iloc[-1]
                      / v.rolling(120, min_periods=60).mean().iloc[-1])

    f["resid_mom"] = np.nan
    win = r.tail(126)
    for sec, etf in u["sector_etf"].items():
        names = [t for t in liquid if sectors.get(t) == sec]
        if not names or etf not in rets.columns:
            continue
        re = rets[etf].tail(126)
        sub = win[names]
        beta = sub.apply(lambda s: s.cov(re) / re.var() if re.var() else 0.0)
        resid = sub.sub(np.outer(re, beta), axis=0)
        f.loc[names, "resid_mom"] = (1 + resid.iloc[:-21]).prod() - 1

    sigma = r.rolling(20).std()
    recent = r.tail(10)
    shock_mask = (recent.abs() > 2.5 * sigma.tail(10)).any()
    shock_dir = np.sign(recent.where(recent.abs() > 2.5 * sigma.tail(10)).sum())

    sec_series = pd.Series({t: sectors.get(t, "?") for t in f.index})
    z = pd.DataFrame(index=f.index)
    for col in f.columns:
        def _winsorized_z(s):
            valid = s.dropna()
            if len(valid) < 2:
                return s * np.nan
            lo, hi = valid.quantile([0.01, 0.99])
            clipped = s.clip(lo, hi)
            sd = clipped.std()
            if pd.isna(sd) or sd == 0:
                return clipped * 0.0
            return ((clipped - clipped.mean()) / sd).clip(-3, 3)
        z[col] = f[col].groupby(sec_series).transform(_winsorized_z)
    return f, z, liquid, dollar_vol, px, shock_mask, shock_dir


def compute(top: int = 20, score_snapshot_dir: Path | None = None) -> dict:
    import pandas as pd
    from advisor.research.datastore import current_meta, load_panel, panel_age_hours
    from advisor.research.universe import load as load_universe

    age = panel_age_hours()
    u = load_universe()
    panel_meta = current_meta()
    if not (panel_meta.get("quality") or {}).get("ok"):
        # Legacy caches predate the atomic quality gate. Audit them at use time
        # and refuse to rank a partial universe rather than grandfathering it.
        from advisor.research.data_quality import assess
        audit = assess({"Close": load_panel("close"), "High": load_panel("high"),
                        "Low": load_panel("low"), "Volume": load_panel("volume")},
                       requested=int(panel_meta.get("n_tickers_requested") or 1))
        if not audit["ok"]:
            raise RuntimeError("current price panel is not production-safe: " +
                               " | ".join(audit["errors"]))
        panel_meta = {**panel_meta, "quality": audit}
    # ARTIFACT <-> CODE BINDING. A panel built by superseded code is not
    # obviously wrong, which is exactly why it went unnoticed for a day: the
    # metadata simply lacked fields the newer code writes. Surface the
    # mismatch on the sheet rather than letting it be invisible.
    from advisor.research.datastore import code_version
    _now, _built = code_version(), (panel_meta.get("code_version") or {})
    code_drift = None
    if _built.get("git_sha") and _now.get("git_sha") \
            and _built["git_sha"] != _now["git_sha"]:
        code_drift = (f"panel built by {_built['git_sha'][:8]}, "
                      f"current code is {_now['git_sha'][:8]}")
    elif not _built.get("git_sha"):
        code_drift = "panel predates code-version stamping"
    quarantined = set((panel_meta.get("quality") or {}).get("quarantine") or {})
    if quarantined:
        u = dict(u)
        u["stocks"] = {t: sector for t, sector in u["stocks"].items()
                       if t not in quarantined}
    sectors = u["stocks"]
    close, volume = load_panel("close"), load_panel("volume")
    rets = close.pct_change()

    f, z, liquid, dollar_vol, px, shock_mask, shock_dir = raw_factors(
        close, volume, rets, u)
    stock_cols = [c for c in close.columns if c in sectors]

    regime = detect_regime(close)
    base_w = regime["weights"]
    ic_fb = ic_weight_multipliers()
    mults = ic_fb.get("multipliers") or {}
    # Second, faster feedback loop. IC feedback needs 21 trading days to
    # mature an observation; realized factor VOLATILITY is measurable today.
    # Barroso & Santa-Clara (2015) / Daniel & Moskowitz (2016): scaling a
    # factor by its own volatility, and shrinking harder when it is both in
    # drawdown and unusually volatile, is what removes the crash tail.
    # `detect_regime` cannot see this — it reads market regime (VIX, credit,
    # 200dma), which stayed benign right through the Jul-Aug factor unwind.
    try:
        from advisor.research.factor_returns import vol_weight_multipliers
        vol_fb = vol_weight_multipliers()
    except Exception as exc:                      # never block the sheet
        vol_fb = {"enabled": False, "reason": f"{type(exc).__name__}: {exc}",
                  "multipliers": {}}
    vmults = vol_fb.get("multipliers") or {}
    # Third loop: independence. The first two ask whether a factor WORKS (IC)
    # and how violent it is (volatility); this asks whether the other factors
    # already say the same thing.
    try:
        red_fb = redundancy_multipliers(z, [k for k, v in base_w.items() if v])
    except Exception as exc:
        red_fb = {"enabled": False, "reason": f"{type(exc).__name__}: {exc}",
                  "multipliers": {}}
    rmults = red_fb.get("multipliers") or {}
    # Deliberately NOT renormalized: shrinking a broken factor must not hand
    # its weight to the survivors (that would amplify them on the same thin
    # evidence). The composite simply gets smaller, and ranking is unaffected
    # because only relative order matters downstream.
    w = {k: round(wt * mults.get(k, 1.0) * vmults.get(k, 1.0)
                  * rmults.get(k, 1.0), 4)
         for k, wt in base_w.items()}
    regime = {**regime, "base_weights": base_w, "weights": w,
              "ic_feedback": ic_fb, "vol_feedback": vol_fb,
              "redundancy": red_fb}
    composite = sum(z[k].fillna(0) * wt for k, wt in w.items())

    # Cross-sectional percentile of the composite. This is the price side's
    # entry in the common currency every generator now speaks (see
    # research/generators.py): a percentile taken against the FULL liquid
    # cross-section, not against the 20 names that happen to be on the sheet.
    comp_pct = composite.dropna().rank(pct=True)
    rank_basis = f"composite percentile within {len(comp_pct)} liquid names"

    def sheet(idx, side: str = "long") -> list[dict]:
        out = []
        for t in idx:
            p = comp_pct.get(t)
            rank_pct = None
            if p is not None and pd.notna(p):
                # oriented BY SIDE: a bottom-of-book name is a strong short,
                # so its short-side percentile is high.
                rank_pct = round(float(p) if side == "long" else 1.0 - float(p), 6)
            out.append({
                "ticker": t, "sector": sectors.get(t, "?"),
                "px": round(float(px[t]), 2),
                "score": round(float(composite[t]), 2),
                "rank_pct": rank_pct,
                "rank_basis": rank_basis,
                "attribution": {k: round(float(z.loc[t, k]), 2) if pd.notna(z.loc[t, k]) else None
                                for k in w},
                "raw": {"mom_12_1_pct": round(float(f.loc[t, "mom_12_1"]) * 100, 1),
                        "ret_1m_pct": round(-float(f.loc[t, "rev_1m"]) * 100, 1),
                        "pct_of_52w_high": round(float(f.loc[t, "prox_52w"]) * 100, 1),
                        "rv60_ann_pct": round(-float(f.loc[t, "lowvol"]) * (252 ** .5) * 100, 0),
                        "dollar_vol_21d_m": round(float(dollar_vol[t]) / 1e6, 1)},
                "shock": bool(shock_mask.get(t, False)),
                "shock_dir": int(shock_dir.get(t, 0) or 0),
            })
        return out

    ranked = composite.dropna().sort_values(ascending=False)
    shocks = [t for t in liquid if shock_mask.get(t, False)]

    if score_snapshot_dir is not None:
        # full score-vector snapshot → maturing live-IC series (each row is
        # scoreable against realized 21d returns once it ages; the honest
        # replacement for the frozen 11-period backtest)
        try:
            snap = z.copy()
            snap["composite"] = composite
            snap["px"] = px[z.index]
            snap["sector"] = [sectors.get(t, "?") for t in z.index]
            snap["regime"] = regime["name"]
            snap["shock"] = [bool(shock_mask.get(t, False)) for t in z.index]
            d = Path(score_snapshot_dir)
            d.mkdir(parents=True, exist_ok=True)
            snap.to_parquet(d / f"{datetime.now(ET).date().isoformat()}.parquet")
        except Exception as exc:
            print(f"[factors] score snapshot failed (non-fatal): {exc}")
    return {
        "as_of": datetime.now(ET).isoformat(),
        "panel_build_id": panel_meta.get("build_id", "legacy"),
        "data_quality": panel_meta.get("quality", {}),
        "quarantined_tickers": sorted(quarantined),
        "model_validation": validation_status(),
        "panel_age_hours": round(age, 1),
        "code_version": code_version(),
        "panel_code_drift": code_drift,
        "n_universe": len(stock_cols), "n_liquid": len(liquid),
        "regime": regime,
        "longs": sheet(ranked.head(top).index, "long"),
        "shorts": sheet(ranked.tail(max(top // 2, 5)).index[::-1], "short"),
        "shock_candidates": sheet([t for t in ranked.index if t in shocks][:15], "long"),
        "method": ("sector-neutral winsorized z-scores; regime-conditioned weights "
                   f"({regime['name']}); liquidity floor $10M ADV, px>$5"),
    }


def render(s: dict) -> str:
    L = [f"FACTOR SHEET  {s['as_of']}   regime={s['regime']['name'].upper()} "
         f"(VIX {s['regime']['vix']}, term {s['regime']['vix_term']}, "
         f"HYG21d {s['regime']['hyg_21d_pct']}%)",
         f"universe {s['n_universe']} → liquid {s['n_liquid']}   "
         f"panel age {s['panel_age_hours']}h",
         f"method: {s['method']}",
         f"MODEL STATUS: {s.get('model_validation', {}).get('status', 'unknown').upper()} "
         f"— {s.get('model_validation', {}).get('role', 'unknown')}", ""]
    for label, rowsk in (("LONG CANDIDATES", "longs"), ("SHORT CANDIDATES", "shorts"),
                         ("SHOCK CANDIDATES — 21d evidence says REVERSION, not drift", "shock_candidates")):
        L.append(f"== {label} ==")
        L.append(f"{'tkr':<7}{'px':>9}{'score':>7}  {'sector':<24}"
                 f"{'mom12':>7}{'r1m':>7}{'%52wH':>7}{'rv60':>6}{'$vol(M)':>9}  attribution(z)")
        for x in s[rowsk]:
            a = x["attribution"]; rw = x["raw"]
            att = " ".join(f"{k.split('_')[0]}:{v:+.1f}" for k, v in a.items() if v is not None)
            L.append(f"{x['ticker']:<7}{x['px']:>9}{x['score']:>7.2f}  {x['sector']:<24}"
                     f"{rw['mom_12_1_pct']:>7}{rw['ret_1m_pct']:>7}{rw['pct_of_52w_high']:>7}"
                     f"{rw['rv60_ann_pct']:>6.0f}{rw['dollar_vol_21d_m']:>9}  {att}"
                     + ("  ⚡shock" if x["shock"] else ""))
        L.append("")
    return "\n".join(L)


def main() -> int:
    top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 20
    s = compute(top=top)
    print(render(s))
    if "--json" in sys.argv:
        p = Path(sys.argv[sys.argv.index("--json") + 1])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(s, indent=2))
        print(f"[factors] json → {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
