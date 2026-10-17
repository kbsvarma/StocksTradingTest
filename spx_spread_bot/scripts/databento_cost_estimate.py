"""
databento_cost_estimate.py — Estimate costs for all candidate datasets.
Run this BEFORE submitting any jobs. Read-only, no charges incurred.

Usage: python databento_cost_estimate.py --api-key db-XXXX
"""

import argparse
from datetime import date

import databento as db

# ── candidate datasets to price ───────────────────────────────────────────────
CANDIDATES = [
    # ── SPXW options (what we care about most) ────────────────────────────────
    dict(
        label      = "SPXW daily 2025-01 to 2026-05  (extend existing)",
        dataset    = "OPRA.PILLAR",
        schema     = "ohlcv-1d",
        symbols    = ["SPXW.OPT"],
        stype_in   = "parent",
        start      = "2025-01-01",
        end        = "2026-05-08",
    ),
    dict(
        label      = "SPXW daily 2021-2022  (add bear market / COVID recovery)",
        dataset    = "OPRA.PILLAR",
        schema     = "ohlcv-1d",
        symbols    = ["SPXW.OPT"],
        stype_in   = "parent",
        start      = "2021-01-01",
        end        = "2022-12-31",
    ),
    dict(
        label      = "SPXW daily 2020-2022  (full COVID + bear market)",
        dataset    = "OPRA.PILLAR",
        schema     = "ohlcv-1d",
        symbols    = ["SPXW.OPT"],
        stype_in   = "parent",
        start      = "2020-01-01",
        end        = "2022-12-31",
    ),
    dict(
        label      = "SPXW daily ALL: 2020-2026  (full history)",
        dataset    = "OPRA.PILLAR",
        schema     = "ohlcv-1d",
        symbols    = ["SPXW.OPT"],
        stype_in   = "parent",
        start      = "2020-01-01",
        end        = "2026-05-08",
    ),
    # ── SPXW 5-minute (partial intraday) ─────────────────────────────────────
    dict(
        label      = "SPXW 5-min 2023-2024  (partial intraday stop sim)",
        dataset    = "OPRA.PILLAR",
        schema     = "ohlcv-1m",    # 1-min
        symbols    = ["SPXW.OPT"],
        stype_in   = "parent",
        start      = "2023-01-01",
        end        = "2024-12-31",
    ),
    # ── SPX options (regular, larger strikes) ─────────────────────────────────
    dict(
        label      = "SPX daily 2025-01 to 2026-05",
        dataset    = "OPRA.PILLAR",
        schema     = "ohlcv-1d",
        symbols    = ["SPX.OPT"],
        stype_in   = "parent",
        start      = "2025-01-01",
        end        = "2026-05-08",
    ),
    # ── ES/NQ futures (for momentum / spread strategies) ─────────────────────
    dict(
        label      = "ES 1-min 5yr 2020-2024  (ES scalping research)",
        dataset    = "GLBX.MDP3",
        schema     = "ohlcv-1m",
        symbols    = ["ES.c.0"],
        stype_in   = "continuous",
        start      = "2020-01-01",
        end        = "2024-12-31",
    ),
    dict(
        label      = "NQ 1-min 5yr 2020-2024  (NQ scalping research)",
        dataset    = "GLBX.MDP3",
        schema     = "ohlcv-1m",
        symbols    = ["NQ.c.0"],
        stype_in   = "continuous",
        start      = "2020-01-01",
        end        = "2024-12-31",
    ),
    dict(
        label      = "MES 1-min 2025-2026  (extend existing MES)",
        dataset    = "GLBX.MDP3",
        schema     = "ohlcv-1m",
        symbols    = ["MES.c.0"],
        stype_in   = "continuous",
        start      = "2025-01-01",
        end        = "2026-05-08",
    ),
    # ── VIX futures (regime filter research) ─────────────────────────────────
    dict(
        label      = "VX (VIX futures) daily 5yr 2020-2024",
        dataset    = "GLBX.MDP3",
        schema     = "ohlcv-1d",
        symbols    = ["VX.c.0"],
        stype_in   = "continuous",
        start      = "2020-01-01",
        end        = "2024-12-31",
    ),
    # ── CL (crude oil) for diversification ───────────────────────────────────
    dict(
        label      = "MCL (micro crude) 1-min 5yr 2020-2024",
        dataset    = "GLBX.MDP3",
        schema     = "ohlcv-1m",
        symbols    = ["MCL.c.0"],
        stype_in   = "continuous",
        start      = "2020-01-01",
        end        = "2024-12-31",
    ),
]


def estimate(client: db.Historical, item: dict) -> float | None:
    try:
        cost = client.metadata.get_cost(
            dataset   = item["dataset"],
            schema    = item["schema"],
            symbols   = item["symbols"],
            stype_in  = item.get("stype_in", "raw_symbol"),
            start     = item["start"],
            end       = item["end"],
            mode      = "historical-streaming",
        )
        return cost
    except Exception as e:
        return f"ERROR: {e}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", required=True)
    args = parser.parse_args()

    client = db.Historical(args.api_key)

    # Check account balance first
    try:
        usage = client.metadata.get_dataset_range(dataset="OPRA.PILLAR")
    except Exception:
        pass

    print(f"\n{'='*78}")
    print("DATABENTO COST ESTIMATES — Read-only, no charges")
    print(f"{'='*78}\n")
    print(f"  {'Cost':>10}  Dataset")
    print(f"  {'─'*10}  {'─'*60}")

    total_if_all = 0.0
    results = []

    for item in CANDIDATES:
        cost = estimate(client, item)
        if isinstance(cost, float):
            total_if_all += cost
            results.append((cost, item["label"]))
            print(f"  ${cost:>9.2f}  {item['label']}")
        else:
            results.append((None, item["label"]))
            print(f"  {'N/A':>10}  {item['label']}  ← {cost}")

    print(f"\n  {'─'*72}")
    print(f"  Total if everything: ${total_if_all:.2f}")
    print(f"  Budget:              $500.00")
    print(f"  Remaining:           ${500 - total_if_all:.2f}")

    # Priority ranking
    affordable = [(c, l) for c, l in results if c is not None and c <= 500]
    affordable.sort(key=lambda x: x[0])

    print(f"\n{'='*78}")
    print("AFFORDABLE ITEMS (ranked cheapest first)")
    print(f"{'='*78}")
    running = 0.0
    for cost, label in affordable:
        running += cost
        marker = " ✓" if running <= 500 else " ✗ (over budget)"
        print(f"  ${cost:>8.2f}  (running: ${running:>7.2f})  {label}{marker}")

    print(f"\n{'='*78}")
    print("RECOMMENDATION — Highest research value within $500")
    print(f"{'='*78}")


if __name__ == "__main__":
    main()
