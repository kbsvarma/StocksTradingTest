#!/usr/bin/env python3
"""Wave 3: post-signal confirmation and execution-aware option selection."""

from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd

from run_local_screen import (
    COMMISSION,
    ET,
    OUT,
    TICKERS,
    Contract,
    build_signals,
    choose_contract,
    load_option_day,
    load_stock_days,
)
from run_wave2 import (
    MAX_LEG_TIME_DIFFERENCE,
    add_trade,
    call_result,
    choose_same_expiry,
    clock,
    close_quote,
    entry_quotes,
    prepare_frame,
    synchronized,
)


TRIGGERS = ("HIGH_CLOSE", "BUFFER_CLOSE", "PERSIST_0940")
EXIT_TIMES = ("10:00", "10:25")
PAYOFFS = ("FRONT_LIQUID_ATM", "NEXT_LIQUID_ATM", "NEXT_RISK_REVERSAL")


def minute_label(timestamp: pd.Timestamp) -> str:
    return timestamp.strftime("%H:%M")


def confirmation_time(values: dict, trigger: str) -> str | None:
    path = values["post_signal_path"].between_time("09:35", "09:44")
    if path.empty:
        return None
    opening_range = values["opening_high"] - values["opening_low"]

    if trigger in {"HIGH_CLOSE", "BUFFER_CLOSE"}:
        threshold = values["opening_high"]
        if trigger == "BUFFER_CLOSE":
            threshold += 0.10 * opening_range
        crossed = path[path["close"] > threshold]
        if crossed.empty:
            return None
        # A bar stamped 09:35 is complete at 09:36.
        return minute_label(crossed.index[0] + pd.Timedelta(minutes=1))

    block = values["post_signal_path"].between_time("09:35", "09:39")
    if len(block) < 5:
        return None
    midpoint = (values["opening_high"] + values["opening_low"]) / 2.0
    if (
        float(block.iloc[-1]["close"]) > values["close_0934"]
        and float(block["low"].min()) > midpoint
    ):
        return "09:40"
    return None


def liquid_atm(
    quotes: dict[str, Contract],
    day: date,
    spot: float,
    minimum_dte: int,
    maximum_dte: int,
) -> Contract | None:
    eligible = [
        contract
        for contract in quotes.values()
        if minimum_dte <= (contract.expiry - day).days <= maximum_dte
    ]
    if not eligible:
        return None
    expiry = min(contract.expiry for contract in eligible)
    band = [
        contract
        for contract in eligible
        if contract.expiry == expiry
        and abs(contract.strike / spot - 1.0) <= 0.01
    ]
    if not band:
        return None
    return min(
        band,
        key=lambda contract: (
            contract.spread / ((contract.ask + contract.bid) / 2.0),
            abs(contract.strike - spot),
            contract.strike,
        ),
    )


def risk_reversal_result(
    call: Contract,
    put: Contract,
    frame: pd.DataFrame,
    exit_at: pd.Timestamp,
) -> dict | None:
    if not synchronized((call, put)):
        return None
    call_exit = close_quote(frame, call.symbol, exit_at)
    put_exit = close_quote(frame, put.symbol, exit_at)
    if call_exit is None or put_exit is None:
        return None
    if abs(call_exit[2] - put_exit[2]) > MAX_LEG_TIME_DIFFERENCE:
        return None

    entry_value = call.ask - put.bid
    exit_value = call_exit[0] - put_exit[1]
    pnl = (exit_value - entry_value) * 100.0 - 4 * COMMISSION
    stressed_entry = (
        call.ask
        + call.spread / 2.0
        - put.bid
        + put.spread / 2.0
    )
    stressed_exit = (
        max(0.0, call_exit[0] - (call_exit[1] - call_exit[0]) / 2.0)
        - put_exit[1]
        - (put_exit[1] - put_exit[0]) / 2.0
    )
    stress_pnl = (stressed_exit - stressed_entry) * 100.0 - 4 * COMMISSION
    return {
        "family": "NEXT_RISK_REVERSAL",
        "long_symbol": call.symbol,
        "short_symbol": put.symbol,
        "expiry": call.expiry,
        "long_strike": call.strike,
        "short_strike": put.strike,
        "entry_value": entry_value,
        "exit_value": exit_value,
        "entry_spread": call.spread + put.spread,
        "exit_spread": (
            call_exit[1] - call_exit[0] + put_exit[1] - put_exit[0]
        ),
        "pnl": pnl,
        "stress_pnl": stress_pnl,
        "roi": np.nan,
        "stress_roi": np.nan,
        "actual_entry": max(call.ts_event, put.ts_event),
        "actual_exit": max(call_exit[2], put_exit[2]),
        "exit_rule": "TIME",
    }


def summarize(trades: pd.DataFrame) -> pd.DataFrame:
    totals = trades.groupby(["trigger", "family", "exit_time"])["pnl"].sum().to_dict()
    rows = []
    for structure, group in trades.groupby("structure", sort=False):
        pnl = group["pnl"].astype(float)
        standard_error = pnl.std(ddof=1) / math.sqrt(len(pnl)) if len(pnl) > 1 else np.nan
        t_stat = pnl.mean() / standard_error if standard_error > 0 else np.nan
        positive = pnl[pnl > 0]
        concentration = positive.max() / positive.sum() if positive.sum() > 0 else np.nan
        cumulative = pnl.cumsum()
        drawdown = cumulative - cumulative.cummax().clip(lower=0)
        excluded = [
            group.loc[group["ticker"] != ticker, "pnl"].sum() for ticker in TICKERS
        ]
        positive_exclusions = sum(value > 0 for value in excluded)

        trigger = str(group.iloc[0]["trigger"])
        family = str(group.iloc[0]["family"])
        exit_time = str(group.iloc[0]["exit_time"])
        neighbors = 0
        for other_trigger in TRIGGERS:
            if other_trigger != trigger:
                neighbors += totals.get((other_trigger, family, exit_time), -np.inf) > 0
        other_exit = "10:25" if exit_time == "10:00" else "10:00"
        neighbors += totals.get((trigger, family, other_exit), -np.inf) > 0

        base = (
            len(group) >= 25
            and pnl.mean() > 0
            and pnl.median() > 0
            and t_stat >= 2.0
            and group["stress_pnl"].sum() > 0
            and concentration <= 0.35
            and positive_exclusions >= 6
        )
        rows.append(
            {
                "structure": structure,
                "trigger": trigger,
                "family": family,
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
                "max_positive_contribution": concentration,
                "positive_leave_one_symbol_out": positive_exclusions,
                "positive_neighbor_cells": neighbors,
                "passes_base_gates": bool(base),
                "research_lead": bool(base and neighbors >= 1),
            }
        )
    return pd.DataFrame(rows).sort_values("total_pnl", ascending=False)


def write_report(summary: pd.DataFrame, trades: pd.DataFrame, skips: pd.DataFrame) -> None:
    lines = [
        "# Opening Leader Options — Wave 3",
        "",
        "Post-signal confirmation, liquidity-aware contract selection and bullish "
        "risk reversals were executed at displayed OPRA prices.",
        "",
        "| Structure | n | Mean $ | Median $ | Total $ | t | Win | Stress $ | "
        "Max DD $ | LOO + | Neighbor + | Lead |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    for row in summary.itertuples():
        lines.append(
            f"| {row.structure} | {row.n} | {row.mean_pnl:,.2f} | "
            f"{row.median_pnl:,.2f} | {row.total_pnl:,.2f} | "
            f"{row.t_stat:.2f} | {row.win_rate:.1%} | "
            f"{row.stress_total_pnl:,.2f} | {row.max_drawdown:,.2f} | "
            f"{row.positive_leave_one_symbol_out} | "
            f"{row.positive_neighbor_cells} | "
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
            else "No Wave 3 cell passed every frozen research-lead gate."
        ),
        "",
        f"Executions: {len(trades)}. Skipped cells: {len(skips)}.",
    ]
    (OUT / "WAVE3_RESULTS.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    stock_days = {ticker: load_stock_days(ticker) for ticker in TICKERS}
    signals = build_signals(stock_days)
    trade_rows: list[dict] = []
    skip_rows: list[dict] = []

    def skip(signal, structure: str, reason: str) -> None:
        skip_rows.append(
            {
                "date": signal.date,
                "ticker": signal.ticker,
                "structure": structure,
                "reason": reason,
            }
        )

    for signal in signals.itertuples():
        if signal.reason != "signal":
            continue
        day: date = signal.date
        values = stock_days[signal.ticker][day]
        frame = load_option_day(signal.ticker, day)
        prepare_frame(frame)

        for trigger in TRIGGERS:
            entry_time = confirmation_time(values, trigger)
            if entry_time is None or entry_time > "09:45":
                for payoff in PAYOFFS:
                    for exit_time in EXIT_TIMES:
                        skip(
                            signal,
                            f"{trigger}__{payoff}__X{exit_time.replace(':', '')}",
                            "no_confirmation",
                        )
                continue

            spot_rows = values["post_signal_path"].between_time(
                entry_time, entry_time
            )
            if spot_rows.empty:
                continue
            spot = float(spot_rows.iloc[0]["open"])
            calls = entry_quotes(
                frame, day, entry_time, "C", minutes=2
            )
            puts = entry_quotes(
                frame, day, entry_time, "P", minutes=2
            )
            front = liquid_atm(calls, day, spot, 0, 4)
            next_call = liquid_atm(calls, day, spot, 5, 11)
            rr_call = choose_contract(calls, day, 5, 11, spot * 1.01)
            rr_put = (
                choose_same_expiry(
                    puts, rr_call.expiry, spot * 0.99
                )
                if rr_call is not None
                else None
            )

            for exit_time in EXIT_TIMES:
                for family, contract in (
                    ("FRONT_LIQUID_ATM", front),
                    ("NEXT_LIQUID_ATM", next_call),
                ):
                    structure = (
                        f"{trigger}__{family}__X{exit_time.replace(':', '')}"
                    )
                    if contract is None:
                        skip(signal, structure, "no_contract")
                        continue
                    result = call_result(
                        family, contract, frame, clock(day, exit_time)
                    )
                    if result is None:
                        skip(signal, structure, "no_exit_quote")
                        continue
                    result["trigger"] = trigger
                    add_trade(
                        trade_rows,
                        signal,
                        values,
                        structure,
                        entry_time,
                        exit_time,
                        result,
                    )

                structure = (
                    f"{trigger}__NEXT_RISK_REVERSAL__X"
                    f"{exit_time.replace(':', '')}"
                )
                if rr_call is None or rr_put is None:
                    skip(signal, structure, "no_contract")
                    continue
                result = risk_reversal_result(
                    rr_call, rr_put, frame, clock(day, exit_time)
                )
                if result is None:
                    skip(signal, structure, "unsynchronized_or_invalid")
                    continue
                result["trigger"] = trigger
                add_trade(
                    trade_rows,
                    signal,
                    values,
                    structure,
                    entry_time,
                    exit_time,
                    result,
                )

        trigger = "HIGH_CLOSE"
        entry_time = confirmation_time(values, trigger)
        if entry_time is not None and entry_time <= "09:45":
            spot_rows = values["post_signal_path"].between_time(
                entry_time, entry_time
            )
            if not spot_rows.empty:
                spot = float(spot_rows.iloc[0]["open"])
                for spread_limit in (0.05, 0.10):
                    calls = entry_quotes(
                        frame,
                        day,
                        entry_time,
                        "C",
                        minutes=2,
                        max_proportional_spread=spread_limit,
                    )
                    contract = liquid_atm(calls, day, spot, 0, 4)
                    for exit_time in EXIT_TIMES:
                        family = f"FRONT_LIQUID_ATM_SPREAD{int(spread_limit * 100)}"
                        structure = (
                            f"HIGH_CLOSE__{family}__X"
                            f"{exit_time.replace(':', '')}"
                        )
                        if contract is None:
                            skip(signal, structure, "no_contract")
                            continue
                        result = call_result(
                            family, contract, frame, clock(day, exit_time)
                        )
                        if result is None:
                            skip(signal, structure, "no_exit_quote")
                            continue
                        result["trigger"] = trigger
                        add_trade(
                            trade_rows,
                            signal,
                            values,
                            structure,
                            entry_time,
                            exit_time,
                            result,
                        )

    trades = pd.DataFrame(trade_rows).sort_values(
        ["date", "structure"]
    ).reset_index(drop=True)
    skips = pd.DataFrame(skip_rows)
    if trades.empty:
        raise RuntimeError("Wave 3 produced no executable trades")
    summary = summarize(trades)
    attribution = (
        trades.groupby(["structure", "ticker"])
        .agg(n=("pnl", "size"), total_pnl=("pnl", "sum"))
        .reset_index()
    )
    trades.to_csv(OUT / "wave3_trades.csv", index=False)
    skips.to_csv(OUT / "wave3_skips.csv", index=False)
    summary.to_csv(OUT / "wave3_summary.csv", index=False)
    attribution.to_csv(OUT / "wave3_symbol_attribution.csv", index=False)
    write_report(summary, trades, skips)
    print(summary.to_string(index=False))
    print(f"\ntrades={len(trades)} skips={len(skips)}")


if __name__ == "__main__":
    main()
