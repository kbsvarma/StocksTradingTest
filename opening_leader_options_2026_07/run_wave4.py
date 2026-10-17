#!/usr/bin/env python3
"""Wave 4: 09:35–10:00 option-demand formation, 10:00 reversal trade."""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from run_local_screen import (
    COMMISSION,
    OUT,
    TICKERS,
    Contract,
    build_signals,
    load_option_day,
    load_stock_days,
    option_dates,
)
from run_wave2 import (
    MAX_LEG_TIME_DIFFERENCE,
    after_quote,
    clock,
    close_quote,
    entry_quotes,
    prepare_frame,
    synchronized,
)


DTE_FAMILIES = {"FRONT": (0, 4), "NEXT": (5, 11)}
EXIT_TIMES = ("10:15", "10:25")
SCOPES = ("ALL", "SIGNAL")
EXPRESSIONS = ("SHORT_WINNER", "LONG_LOSER", "REVERSAL_PAIR")


def paired_straddle(
    calls: dict[str, Contract],
    puts: dict[str, Contract],
    day: date,
    spot: float,
    minimum_dte: int,
    maximum_dte: int,
) -> tuple[Contract, Contract] | None:
    call_lookup = {
        (contract.expiry, contract.strike): contract
        for contract in calls.values()
        if minimum_dte <= (contract.expiry - day).days <= maximum_dte
    }
    put_lookup = {
        (contract.expiry, contract.strike): contract
        for contract in puts.values()
        if minimum_dte <= (contract.expiry - day).days <= maximum_dte
    }
    keys = set(call_lookup) & set(put_lookup)
    if not keys:
        return None
    expiry = min(key[0] for key in keys)
    candidates = [
        (call_lookup[key], put_lookup[key])
        for key in keys
        if key[0] == expiry and synchronized((call_lookup[key], put_lookup[key]))
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda pair: (
            abs(pair[0].strike - spot),
            pair[0].spread + pair[1].spread,
            pair[0].strike,
        ),
    )


def side_result(candidate: dict, exit_time: str, side: str) -> dict | None:
    entry = candidate["entry"]
    exits = candidate["exits"].get(exit_time)
    if exits is None:
        return None
    call_entry, put_entry = entry
    call_exit, put_exit = exits
    commission = 4 * COMMISSION

    if side == "LONG":
        entry_value = call_entry[1] + put_entry[1]
        exit_value = call_exit[0] + put_exit[0]
        pnl = (exit_value - entry_value) * 100.0 - commission
        stressed_entry = (
            call_entry[1] + (call_entry[1] - call_entry[0]) / 2.0
            + put_entry[1] + (put_entry[1] - put_entry[0]) / 2.0
        )
        stressed_exit = (
            max(0.0, call_exit[0] - (call_exit[1] - call_exit[0]) / 2.0)
            + max(0.0, put_exit[0] - (put_exit[1] - put_exit[0]) / 2.0)
        )
        stress_pnl = (stressed_exit - stressed_entry) * 100.0 - commission
    else:
        entry_value = call_entry[0] + put_entry[0]
        exit_value = call_exit[1] + put_exit[1]
        pnl = (entry_value - exit_value) * 100.0 - commission
        stressed_entry = (
            call_entry[0] - (call_entry[1] - call_entry[0]) / 2.0
            + put_entry[0] - (put_entry[1] - put_entry[0]) / 2.0
        )
        stressed_exit = (
            call_exit[1] + (call_exit[1] - call_exit[0]) / 2.0
            + put_exit[1] + (put_exit[1] - put_exit[0]) / 2.0
        )
        stress_pnl = (stressed_entry - stressed_exit) * 100.0 - commission

    return {
        "pnl": pnl,
        "stress_pnl": stress_pnl,
        "entry_value": entry_value,
        "exit_value": exit_value,
        "entry_spread": (
            call_entry[1] - call_entry[0] + put_entry[1] - put_entry[0]
        ),
        "exit_spread": (
            call_exit[1] - call_exit[0] + put_exit[1] - put_exit[0]
        ),
        "actual_entry": max(call_entry[2], put_entry[2]),
        "actual_exit": max(call_exit[2], put_exit[2]),
    }


def candidate_for(
    ticker: str,
    day: date,
    values: dict,
    family: str,
    frame: pd.DataFrame,
) -> dict | None:
    spot_rows = values["post_signal_path"].between_time("09:35", "09:35")
    if spot_rows.empty:
        return None
    spot = float(spot_rows.iloc[0]["open"])
    calls = entry_quotes(frame, day, "09:35", "C", minutes=2)
    puts = entry_quotes(frame, day, "09:35", "P", minutes=2)
    pair = paired_straddle(
        calls, puts, day, spot, *DTE_FAMILIES[family]
    )
    if pair is None:
        return None
    call, put = pair
    formation_call = close_quote(frame, call.symbol, clock(day, "10:00"))
    formation_put = close_quote(frame, put.symbol, clock(day, "10:00"))
    entry_call = after_quote(call.symbol, clock(day, "10:00"))
    entry_put = after_quote(put.symbol, clock(day, "10:00"))
    if None in (formation_call, formation_put, entry_call, entry_put):
        return None
    if (
        abs(formation_call[2] - formation_put[2]) > MAX_LEG_TIME_DIFFERENCE
        or abs(entry_call[2] - entry_put[2]) > MAX_LEG_TIME_DIFFERENCE
    ):
        return None

    start_mid = (call.bid + call.ask + put.bid + put.ask) / 2.0
    end_mid = (
        formation_call[0]
        + formation_call[1]
        + formation_put[0]
        + formation_put[1]
    ) / 2.0
    if start_mid <= 0:
        return None

    exits: dict[str, tuple] = {}
    for exit_time in EXIT_TIMES:
        call_exit = close_quote(frame, call.symbol, clock(day, exit_time))
        put_exit = close_quote(frame, put.symbol, clock(day, exit_time))
        if (
            call_exit is not None
            and put_exit is not None
            and abs(call_exit[2] - put_exit[2]) <= MAX_LEG_TIME_DIFFERENCE
        ):
            exits[exit_time] = (call_exit, put_exit)
    if not exits:
        return None
    return {
        "ticker": ticker,
        "formation_return": end_mid / start_mid - 1.0,
        "call_symbol": call.symbol,
        "put_symbol": put.symbol,
        "expiry": call.expiry,
        "strike": call.strike,
        "entry": (entry_call, entry_put),
        "exits": exits,
    }


def summarize(trades: pd.DataFrame) -> pd.DataFrame:
    total_lookup = (
        trades.groupby(["scope", "family", "expression", "exit_time"])["pnl"]
        .sum()
        .to_dict()
    )
    rows = []
    for structure, group in trades.groupby("structure", sort=False):
        pnl = group["pnl"].astype(float)
        standard_error = pnl.std(ddof=1) / math.sqrt(len(pnl)) if len(pnl) > 1 else np.nan
        t_stat = pnl.mean() / standard_error if standard_error > 0 else np.nan
        positive = pnl[pnl > 0]
        concentration = positive.max() / positive.sum() if positive.sum() > 0 else np.nan
        cumulative = pnl.cumsum()
        drawdown = cumulative - cumulative.cummax().clip(lower=0)
        dates = pd.to_datetime(group["date"])
        early_total = group.loc[dates.dt.month <= 3, "pnl"].sum()
        late_total = group.loc[dates.dt.month >= 4, "pnl"].sum()
        scope = str(group.iloc[0]["scope"])
        family = str(group.iloc[0]["family"])
        expression = str(group.iloc[0]["expression"])
        exit_time = str(group.iloc[0]["exit_time"])
        neighbor_exit = "10:25" if exit_time == "10:15" else "10:15"
        neighbor_positive = (
            total_lookup.get((scope, family, expression, neighbor_exit), -np.inf)
            > 0
        )
        base = (
            len(group) >= 35
            and pnl.mean() > 0
            and pnl.median() > 0
            and t_stat >= 2.0
            and group["stress_pnl"].sum() > 0
            and concentration <= 0.35
            and early_total > 0
            and late_total > 0
        )
        rows.append(
            {
                "structure": structure,
                "scope": scope,
                "family": family,
                "expression": expression,
                "exit_time": exit_time,
                "n": len(group),
                "mean_pnl": pnl.mean(),
                "median_pnl": pnl.median(),
                "total_pnl": pnl.sum(),
                "t_stat": t_stat,
                "win_rate": (pnl > 0).mean(),
                "stress_total_pnl": group["stress_pnl"].sum(),
                "max_drawdown": drawdown.min(),
                "worst_trade": pnl.min(),
                "early_total": early_total,
                "late_total": late_total,
                "max_positive_contribution": concentration,
                "neighbor_exit_positive": bool(neighbor_positive),
                "passes_base_gates": bool(base),
                "research_lead": bool(base and neighbor_positive),
            }
        )
    return pd.DataFrame(rows).sort_values("total_pnl", ascending=False)


def write_report(summary: pd.DataFrame, trades: pd.DataFrame, skips: pd.DataFrame) -> None:
    lines = [
        "# Opening Leader Options — Wave 4 Option-Demand Reversal",
        "",
        "| Structure | n | Mean $ | Median $ | Total $ | t | Win | Stress $ | "
        "Early $ | Late $ | Lead |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    for row in summary.itertuples():
        lines.append(
            f"| {row.structure} | {row.n} | {row.mean_pnl:,.2f} | "
            f"{row.median_pnl:,.2f} | {row.total_pnl:,.2f} | "
            f"{row.t_stat:.2f} | {row.win_rate:.1%} | "
            f"{row.stress_total_pnl:,.2f} | {row.early_total:,.2f} | "
            f"{row.late_total:,.2f} | "
            f"{'YES' if row.research_lead else 'NO'} |"
        )
    leaders = summary.loc[summary["research_lead"], "structure"].tolist()
    lines += [
        "",
        "## Frozen-gate result",
        "",
        (
            "Research leads: " + ", ".join(leaders)
            if leaders
            else "No Wave 4 cell passed every frozen research-lead gate."
        ),
        "",
        f"Executions: {len(trades)}. Skips: {len(skips)}.",
    ]
    (OUT / "WAVE4_RESULTS.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    stock_days = {ticker: load_stock_days(ticker) for ticker in TICKERS}
    signal_frame = build_signals(stock_days)
    signal_dates = set(
        signal_frame.loc[signal_frame["reason"] == "signal", "date"].tolist()
    )
    common_dates = sorted(set.intersection(*(option_dates(t) for t in TICKERS)))
    trade_rows: list[dict] = []
    skip_rows: list[dict] = []

    for date_index, day in enumerate(common_dates, start=1):
        daily: dict[str, dict[str, dict]] = {family: {} for family in DTE_FAMILIES}
        for ticker in TICKERS:
            values = stock_days[ticker].get(day)
            if values is None:
                continue
            frame = load_option_day(ticker, day)
            prepare_frame(frame)
            for family in DTE_FAMILIES:
                candidate = candidate_for(ticker, day, values, family, frame)
                if candidate is not None:
                    daily[family][ticker] = candidate

        for family, candidates in daily.items():
            if len(candidates) < 5:
                skip_rows.append(
                    {
                        "date": day,
                        "family": family,
                        "reason": "fewer_than_five_straddles",
                        "available": len(candidates),
                    }
                )
                continue
            ranked = sorted(
                candidates.values(), key=lambda value: value["formation_return"]
            )
            loser = ranked[0]
            winner = ranked[-1]
            for scope in SCOPES:
                if scope == "SIGNAL" and day not in signal_dates:
                    continue
                for exit_time in EXIT_TIMES:
                    long_result = side_result(loser, exit_time, "LONG")
                    short_result = side_result(winner, exit_time, "SHORT")
                    if long_result is None or short_result is None:
                        continue
                    for expression, result in (
                        ("SHORT_WINNER", short_result),
                        ("LONG_LOSER", long_result),
                        (
                            "REVERSAL_PAIR",
                            {
                                "pnl": long_result["pnl"] + short_result["pnl"],
                                "stress_pnl": (
                                    long_result["stress_pnl"]
                                    + short_result["stress_pnl"]
                                ),
                                "entry_value": (
                                    long_result["entry_value"]
                                    - short_result["entry_value"]
                                ),
                                "exit_value": (
                                    long_result["exit_value"]
                                    - short_result["exit_value"]
                                ),
                                "entry_spread": (
                                    long_result["entry_spread"]
                                    + short_result["entry_spread"]
                                ),
                                "exit_spread": (
                                    long_result["exit_spread"]
                                    + short_result["exit_spread"]
                                ),
                                "actual_entry": max(
                                    long_result["actual_entry"],
                                    short_result["actual_entry"],
                                ),
                                "actual_exit": max(
                                    long_result["actual_exit"],
                                    short_result["actual_exit"],
                                ),
                            },
                        ),
                    ):
                        structure = (
                            f"{scope}__{family}__{expression}__X"
                            f"{exit_time.replace(':', '')}"
                        )
                        trade_rows.append(
                            {
                                "date": day,
                                "structure": structure,
                                "scope": scope,
                                "family": family,
                                "expression": expression,
                                "exit_time": exit_time,
                                "winner_ticker": winner["ticker"],
                                "loser_ticker": loser["ticker"],
                                "winner_formation_return": winner[
                                    "formation_return"
                                ],
                                "loser_formation_return": loser[
                                    "formation_return"
                                ],
                                **result,
                            }
                        )
        if date_index % 10 == 0:
            print(f"processed {date_index}/{len(common_dates)} dates", flush=True)

    trades = pd.DataFrame(trade_rows).sort_values(
        ["date", "structure"]
    ).reset_index(drop=True)
    skips = pd.DataFrame(skip_rows)
    if trades.empty:
        raise RuntimeError("Wave 4 produced no executable trades")
    summary = summarize(trades)
    trades.to_csv(OUT / "wave4_trades.csv", index=False)
    skips.to_csv(OUT / "wave4_skips.csv", index=False)
    summary.to_csv(OUT / "wave4_summary.csv", index=False)
    write_report(summary, trades, skips)
    print(summary.to_string(index=False))
    print(f"\ntrades={len(trades)} skips={len(skips)}")


if __name__ == "__main__":
    main()
