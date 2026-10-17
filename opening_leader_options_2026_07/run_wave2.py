#!/usr/bin/env python3
"""Wave 2: options-native timing and payoff screen on executable OPRA CBBOs."""

from __future__ import annotations

import math
from collections.abc import Iterable
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


ENTRY_TIMES = ("09:36", "09:40", "09:45")
EXIT_TIMES = ("10:00", "10:15", "10:25")
CALL_SPECS = {
    "FRONT_ATM": (0, 4, 1.00),
    "NEXT_ATM": (5, 11, 1.00),
    "NEXT_ITM2": (5, 11, 0.98),
    "NEXT_OTM1": (5, 11, 1.01),
}
MAX_LEG_TIME_DIFFERENCE = pd.Timedelta(seconds=75)
_VALID_ALL = pd.DataFrame()
_CHAINS: dict[str, pd.DataFrame] = {}


def clock(day: date, hhmm: str) -> pd.Timestamp:
    hour, minute = (int(value) for value in hhmm.split(":"))
    return pd.Timestamp(day, tz=ET) + pd.Timedelta(hours=hour, minutes=minute)


def valid_rows(frame: pd.DataFrame, right: str | None = None) -> pd.DataFrame:
    rows = frame[
        (frame["bid_px_00"] >= 0)
        & (frame["ask_px_00"] >= frame["bid_px_00"])
        & (frame["bid_sz_00"] > 0)
        & (frame["ask_sz_00"] > 0)
    ].copy()
    if right is not None:
        rows = rows[rows["right"] == right]
    return rows


def prepare_frame(frame: pd.DataFrame) -> None:
    """Build the per-session quote index once; the raw OPRA files are large."""
    global _VALID_ALL, _CHAINS
    _VALID_ALL = valid_rows(frame)
    _CHAINS = {
        str(symbol): chain.sort_values("event_et")
        for symbol, chain in _VALID_ALL.groupby("symbol", sort=False)
    }


def entry_quotes(
    frame: pd.DataFrame,
    day: date,
    entry_time: str,
    right: str,
    *,
    minutes: int = 3,
    max_proportional_spread: float = 0.20,
) -> dict[str, Contract]:
    start = clock(day, entry_time)
    end = start + pd.Timedelta(minutes=minutes)
    window = _VALID_ALL[_VALID_ALL["right"] == right]
    window = window[
        (window["event_et"] >= start)
        & (window["event_et"] < end)
        & (window["bid_px_00"] > 0)
    ].copy()
    mid = (window["bid_px_00"] + window["ask_px_00"]) / 2.0
    window = window[
        (window["ask_px_00"] - window["bid_px_00"])
        <= max_proportional_spread * mid
    ]
    first = window.sort_values("event_et").drop_duplicates("symbol", keep="first")
    return {
        str(row.symbol): Contract(
            symbol=str(row.symbol),
            expiry=row.expiry,
            strike=float(row.strike),
            bid=float(row.bid_px_00),
            ask=float(row.ask_px_00),
            bid_size=int(row.bid_sz_00),
            ask_size=int(row.ask_sz_00),
            ts_event=row.event_et,
        )
        for row in first.itertuples()
    }


def stock_spot(values: dict, entry_time: str) -> float | None:
    row = values["path"].between_time(entry_time, entry_time)
    if row.empty:
        return None
    return float(row.iloc[0]["open"])


def close_quote(
    frame: pd.DataFrame, symbol: str, at: pd.Timestamp
) -> tuple[float, float, pd.Timestamp] | None:
    chain = _CHAINS.get(symbol)
    if chain is None:
        return None
    chain = chain[
        (chain["event_et"] <= at)
        & (chain["event_et"] >= at - pd.Timedelta(minutes=3))
    ]
    if chain.empty:
        return None
    row = chain.iloc[-1]
    return float(row["bid_px_00"]), float(row["ask_px_00"]), row["event_et"]


def after_quote(
    symbol: str,
    at: pd.Timestamp,
    *,
    minutes: int = 2,
    max_proportional_spread: float = 0.20,
) -> tuple[float, float, pd.Timestamp] | None:
    """First executable quote after a causal decision timestamp."""
    chain = _CHAINS.get(symbol)
    if chain is None:
        return None
    rows = chain[
        (chain["event_et"] >= at)
        & (chain["event_et"] < at + pd.Timedelta(minutes=minutes))
        & (chain["bid_px_00"] > 0)
    ].copy()
    if rows.empty:
        return None
    midpoint = (rows["bid_px_00"] + rows["ask_px_00"]) / 2.0
    rows = rows[
        (rows["ask_px_00"] - rows["bid_px_00"])
        <= max_proportional_spread * midpoint
    ]
    if rows.empty:
        return None
    row = rows.iloc[0]
    return float(row["bid_px_00"]), float(row["ask_px_00"]), row["event_et"]


def synchronized(contracts: Iterable[Contract]) -> bool:
    stamps = [contract.ts_event for contract in contracts]
    return max(stamps) - min(stamps) <= MAX_LEG_TIME_DIFFERENCE


def call_result(
    family: str,
    contract: Contract,
    frame: pd.DataFrame,
    exit_at: pd.Timestamp,
    exit_rule: str = "TIME",
) -> dict | None:
    exit_quote = close_quote(frame, contract.symbol, exit_at)
    if exit_quote is None:
        return None
    exit_bid, exit_ask, actual_exit = exit_quote
    pnl = (exit_bid - contract.ask) * 100.0 - 2 * COMMISSION
    stress_pnl = (
        exit_bid
        - (exit_ask - exit_bid) / 2.0
        - contract.ask
        - contract.spread / 2.0
    ) * 100.0 - 2 * COMMISSION
    capital = contract.ask * 100.0 + COMMISSION
    return {
        "family": family,
        "long_symbol": contract.symbol,
        "short_symbol": None,
        "expiry": contract.expiry,
        "long_strike": contract.strike,
        "short_strike": np.nan,
        "entry_value": contract.ask,
        "exit_value": exit_bid,
        "entry_spread": contract.spread,
        "exit_spread": exit_ask - exit_bid,
        "pnl": pnl,
        "stress_pnl": stress_pnl,
        "roi": pnl / capital,
        "stress_roi": stress_pnl / capital,
        "actual_entry": contract.ts_event,
        "actual_exit": actual_exit,
        "exit_rule": exit_rule,
    }


def path_call_result(
    family: str,
    contract: Contract,
    frame: pd.DataFrame,
    exit_at: pd.Timestamp,
    profit_multiple: float | None,
) -> dict | None:
    chain = _CHAINS.get(contract.symbol)
    if chain is None:
        return None
    chain = chain[
        (chain["event_et"] > contract.ts_event)
        & (chain["event_et"] <= exit_at)
    ]
    if chain.empty:
        return None

    stop_level = 0.50 * contract.ask
    target_level = (
        None if profit_multiple is None else profit_multiple * contract.ask
    )
    selected = None
    exit_rule = "TIME"
    for row in chain.itertuples():
        if float(row.bid_px_00) <= stop_level:
            selected = row
            exit_rule = "SL50"
            break
        if target_level is not None and float(row.bid_px_00) >= target_level:
            selected = row
            exit_rule = f"PT{int((profit_multiple - 1.0) * 100)}"
            break

    if selected is None:
        eligible = chain[chain["event_et"] >= exit_at - pd.Timedelta(minutes=3)]
        if eligible.empty:
            return None
        selected = next(eligible.iloc[::-1].itertuples())

    exit_bid = float(selected.bid_px_00)
    exit_ask = float(selected.ask_px_00)
    actual_exit = selected.event_et
    pnl = (exit_bid - contract.ask) * 100.0 - 2 * COMMISSION
    stress_pnl = (
        exit_bid
        - (exit_ask - exit_bid) / 2.0
        - contract.ask
        - contract.spread / 2.0
    ) * 100.0 - 2 * COMMISSION
    capital = contract.ask * 100.0 + COMMISSION
    return {
        "family": family,
        "long_symbol": contract.symbol,
        "short_symbol": None,
        "expiry": contract.expiry,
        "long_strike": contract.strike,
        "short_strike": np.nan,
        "entry_value": contract.ask,
        "exit_value": exit_bid,
        "entry_spread": contract.spread,
        "exit_spread": exit_ask - exit_bid,
        "pnl": pnl,
        "stress_pnl": stress_pnl,
        "roi": pnl / capital,
        "stress_roi": stress_pnl / capital,
        "actual_entry": contract.ts_event,
        "actual_exit": actual_exit,
        "exit_rule": exit_rule,
    }


def debit_spread_result(
    long_contract: Contract,
    short_contract: Contract,
    frame: pd.DataFrame,
    exit_at: pd.Timestamp,
) -> dict | None:
    if not synchronized((long_contract, short_contract)):
        return None
    long_exit = close_quote(frame, long_contract.symbol, exit_at)
    short_exit = close_quote(frame, short_contract.symbol, exit_at)
    if long_exit is None or short_exit is None:
        return None
    if abs(long_exit[2] - short_exit[2]) > MAX_LEG_TIME_DIFFERENCE:
        return None

    entry_debit = long_contract.ask - short_contract.bid
    exit_credit = long_exit[0] - short_exit[1]
    if entry_debit <= 0:
        return None
    pnl = (exit_credit - entry_debit) * 100.0 - 4 * COMMISSION
    stressed_entry = (
        long_contract.ask
        + long_contract.spread / 2.0
        - short_contract.bid
        + short_contract.spread / 2.0
    )
    stressed_exit = (
        max(0.0, long_exit[0] - (long_exit[1] - long_exit[0]) / 2.0)
        - short_exit[1]
        - (short_exit[1] - short_exit[0]) / 2.0
    )
    stress_pnl = (stressed_exit - stressed_entry) * 100.0 - 4 * COMMISSION
    capital = entry_debit * 100.0 + 2 * COMMISSION
    return {
        "family": "NEXT_VERTICAL2",
        "long_symbol": long_contract.symbol,
        "short_symbol": short_contract.symbol,
        "expiry": long_contract.expiry,
        "long_strike": long_contract.strike,
        "short_strike": short_contract.strike,
        "entry_value": entry_debit,
        "exit_value": exit_credit,
        "entry_spread": long_contract.spread + short_contract.spread,
        "exit_spread": (
            long_exit[1] - long_exit[0] + short_exit[1] - short_exit[0]
        ),
        "pnl": pnl,
        "stress_pnl": stress_pnl,
        "roi": pnl / capital,
        "stress_roi": stress_pnl / capital,
        "actual_entry": max(long_contract.ts_event, short_contract.ts_event),
        "actual_exit": max(long_exit[2], short_exit[2]),
        "exit_rule": "TIME",
    }


def put_credit_result(
    family: str,
    short_contract: Contract,
    long_contract: Contract,
    frame: pd.DataFrame,
    exit_at: pd.Timestamp,
) -> dict | None:
    if not synchronized((short_contract, long_contract)):
        return None
    short_exit = close_quote(frame, short_contract.symbol, exit_at)
    long_exit = close_quote(frame, long_contract.symbol, exit_at)
    if short_exit is None or long_exit is None:
        return None
    if abs(short_exit[2] - long_exit[2]) > MAX_LEG_TIME_DIFFERENCE:
        return None

    width = short_contract.strike - long_contract.strike
    entry_credit = short_contract.bid - long_contract.ask
    exit_debit = short_exit[1] - long_exit[0]
    if width <= 0 or entry_credit <= 0 or entry_credit >= width:
        return None
    pnl = (entry_credit - exit_debit) * 100.0 - 4 * COMMISSION
    stressed_entry = (
        short_contract.bid
        - short_contract.spread / 2.0
        - long_contract.ask
        - long_contract.spread / 2.0
    )
    stressed_exit = (
        short_exit[1]
        + (short_exit[1] - short_exit[0]) / 2.0
        - max(0.0, long_exit[0] - (long_exit[1] - long_exit[0]) / 2.0)
    )
    stress_pnl = (stressed_entry - stressed_exit) * 100.0 - 4 * COMMISSION
    capital = (width - entry_credit) * 100.0 + 2 * COMMISSION
    return {
        "family": family,
        "long_symbol": long_contract.symbol,
        "short_symbol": short_contract.symbol,
        "expiry": short_contract.expiry,
        "long_strike": long_contract.strike,
        "short_strike": short_contract.strike,
        "entry_value": entry_credit,
        "exit_value": exit_debit,
        "entry_spread": short_contract.spread + long_contract.spread,
        "exit_spread": (
            short_exit[1] - short_exit[0] + long_exit[1] - long_exit[0]
        ),
        "pnl": pnl,
        "stress_pnl": stress_pnl,
        "roi": pnl / capital,
        "stress_roi": stress_pnl / capital,
        "actual_entry": max(short_contract.ts_event, long_contract.ts_event),
        "actual_exit": max(short_exit[2], long_exit[2]),
        "exit_rule": "TIME",
    }


def choose_same_expiry(
    quotes: dict[str, Contract],
    expiry: date,
    target: float,
    *,
    below: float | None = None,
    above: float | None = None,
) -> Contract | None:
    eligible = [
        contract
        for contract in quotes.values()
        if contract.expiry == expiry
        and (below is None or contract.strike < below)
        and (above is None or contract.strike > above)
    ]
    if not eligible:
        return None
    return min(
        eligible, key=lambda contract: (abs(contract.strike - target), contract.strike)
    )


def add_trade(
    rows: list[dict],
    signal,
    values: dict,
    structure: str,
    entry_time: str,
    exit_time: str,
    result: dict,
) -> None:
    entry_spot = stock_spot(values, entry_time)
    exit_spot_rows = values["path"].between_time(exit_time, exit_time)
    exit_spot = (
        float(exit_spot_rows.iloc[0]["close"]) if not exit_spot_rows.empty else np.nan
    )
    rows.append(
        {
            "date": signal.date,
            "ticker": signal.ticker,
            "structure": structure,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "gap": signal.gap,
            "r5": signal.r5,
            "strength": signal.strength,
            "spot_entry": entry_spot,
            "spot_exit": exit_spot,
            "underlying_return": (
                exit_spot / entry_spot - 1.0
                if entry_spot and np.isfinite(exit_spot)
                else np.nan
            ),
            **result,
        }
    )


def summarize(trades: pd.DataFrame) -> pd.DataFrame:
    timing_lookup = (
        trades.groupby(["family", "entry_time", "exit_time"])["pnl"].sum().to_dict()
    )
    entry_position = {value: index for index, value in enumerate(ENTRY_TIMES)}
    exit_position = {value: index for index, value in enumerate(EXIT_TIMES)}
    rows = []
    for structure, group in trades.groupby("structure", sort=False):
        pnl = group["pnl"].astype(float)
        cumulative = pnl.cumsum()
        drawdown = cumulative - cumulative.cummax().clip(lower=0)
        standard_error = pnl.std(ddof=1) / math.sqrt(len(pnl)) if len(pnl) > 1 else np.nan
        t_stat = pnl.mean() / standard_error if standard_error > 0 else np.nan
        positive = pnl[pnl > 0]
        concentration = positive.max() / positive.sum() if positive.sum() > 0 else np.nan
        excluded_totals = [
            group.loc[group["ticker"] != ticker, "pnl"].sum() for ticker in TICKERS
        ]
        positive_exclusions = sum(value > 0 for value in excluded_totals)

        family = str(group.iloc[0]["family"])
        entry_time = str(group.iloc[0]["entry_time"])
        exit_time = str(group.iloc[0]["exit_time"])
        adjacent_positive = 0
        if group["exit_rule"].eq("TIME").all():
            entry_index = entry_position.get(entry_time)
            exit_index = exit_position.get(exit_time)
            neighbors: set[tuple[str, str]] = set()
            if entry_index is not None:
                for offset in (-1, 1):
                    candidate = entry_index + offset
                    if 0 <= candidate < len(ENTRY_TIMES):
                        neighbors.add((ENTRY_TIMES[candidate], exit_time))
            if exit_index is not None:
                for offset in (-1, 1):
                    candidate = exit_index + offset
                    if 0 <= candidate < len(EXIT_TIMES):
                        neighbors.add((entry_time, EXIT_TIMES[candidate]))
            adjacent_positive = sum(
                timing_lookup.get((family, neighbor_entry, neighbor_exit), -np.inf)
                > 0
                for neighbor_entry, neighbor_exit in neighbors
            )

        base_gates = (
            len(group) >= 35
            and pnl.mean() > 0
            and t_stat >= 2.0
            and pnl.median() > 0
            and group["stress_pnl"].sum() > 0
            and concentration <= 0.35
        )
        research_lead = (
            base_gates and adjacent_positive >= 2 and positive_exclusions >= 6
        )
        rows.append(
            {
                "structure": structure,
                "family": family,
                "entry_time": entry_time,
                "exit_time": exit_time,
                "n": len(group),
                "mean_pnl": pnl.mean(),
                "median_pnl": pnl.median(),
                "total_pnl": pnl.sum(),
                "t_stat": t_stat,
                "win_rate": (pnl > 0).mean(),
                "mean_roi_pct": group["roi"].mean() * 100.0,
                "stress_total_pnl": group["stress_pnl"].sum(),
                "max_drawdown": drawdown.min(),
                "worst_trade": pnl.min(),
                "max_positive_contribution": concentration,
                "adjacent_positive_cells": adjacent_positive,
                "positive_leave_one_symbol_out": positive_exclusions,
                "passes_base_gates": bool(base_gates),
                "research_lead": bool(research_lead),
            }
        )
    return pd.DataFrame(rows).sort_values("total_pnl", ascending=False)


def write_report(
    signals: pd.DataFrame,
    trades: pd.DataFrame,
    summary: pd.DataFrame,
    skips: pd.DataFrame,
) -> None:
    leaders = summary[summary["research_lead"]]
    lines = [
        "# Opening Leader Options — Wave 2",
        "",
        f"Signal days: {int((signals['reason'] == 'signal').sum())}; "
        f"flat days: {int((signals['reason'] != 'signal').sum())}.",
        "",
        "The table includes real OPRA consolidated bid/ask crossings, commissions, "
        "and the registered extra-half-spread stress. The stock opening-low stop "
        "is absent from the primary matrix.",
        "",
        "| Structure | n | Mean $ | Median $ | Total $ | t | Win | "
        "Stress $ | Max DD $ | Adj + | LOO + | Lead |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    for row in summary.itertuples():
        lines.append(
            f"| {row.structure} | {row.n} | {row.mean_pnl:,.2f} | "
            f"{row.median_pnl:,.2f} | {row.total_pnl:,.2f} | "
            f"{row.t_stat:.2f} | {row.win_rate:.1%} | "
            f"{row.stress_total_pnl:,.2f} | {row.max_drawdown:,.2f} | "
            f"{row.adjacent_positive_cells} | "
            f"{row.positive_leave_one_symbol_out} | "
            f"{'YES' if row.research_lead else 'NO'} |"
        )
    lines += [
        "",
        "## Frozen-gate result",
        "",
        (
            "Research leads: " + ", ".join(leaders["structure"].tolist())
            if not leaders.empty
            else "No Wave 2 cell passed every frozen research-lead gate."
        ),
        "",
        "Every execution is in `wave2_trades.csv`; every skipped cell and reason is "
        "in `wave2_skips.csv`. This remains a one-regime mechanism screen, not a "
        "multi-era validation.",
    ]
    (OUT / "WAVE2_RESULTS.md").write_text("\n".join(lines) + "\n")


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
        ticker = signal.ticker
        day = signal.date
        values = stock_days[ticker][day]
        frame = load_option_day(ticker, day)
        prepare_frame(frame)

        for entry_time in ENTRY_TIMES:
            spot = stock_spot(values, entry_time)
            if spot is None:
                continue
            call_quotes = entry_quotes(frame, day, entry_time, "C")
            put_quotes = entry_quotes(frame, day, entry_time, "P")

            selected_calls: dict[str, Contract | None] = {}
            for family, (minimum_dte, maximum_dte, moneyness) in CALL_SPECS.items():
                selected_calls[family] = choose_contract(
                    call_quotes,
                    day,
                    minimum_dte,
                    maximum_dte,
                    spot * moneyness,
                )

            for exit_time in EXIT_TIMES:
                for family, contract in selected_calls.items():
                    structure = f"{family}__E{entry_time.replace(':', '')}__X{exit_time.replace(':', '')}"
                    if contract is None:
                        skip(signal, structure, "no_contract")
                        continue
                    result = call_result(
                        family, contract, frame, clock(day, exit_time)
                    )
                    if result is None:
                        skip(signal, structure, "no_exit_quote")
                        continue
                    add_trade(
                        trade_rows,
                        signal,
                        values,
                        structure,
                        entry_time,
                        exit_time,
                        result,
                    )

                long_contract = selected_calls["NEXT_ATM"]
                structure = (
                    f"NEXT_VERTICAL2__E{entry_time.replace(':', '')}"
                    f"__X{exit_time.replace(':', '')}"
                )
                if long_contract is None:
                    skip(signal, structure, "no_long_contract")
                else:
                    short_contract = choose_same_expiry(
                        call_quotes,
                        long_contract.expiry,
                        spot * 1.02,
                        above=long_contract.strike,
                    )
                    if short_contract is None:
                        skip(signal, structure, "no_short_contract")
                    else:
                        result = debit_spread_result(
                            long_contract,
                            short_contract,
                            frame,
                            clock(day, exit_time),
                        )
                        if result is None:
                            skip(signal, structure, "unsynchronized_or_invalid")
                        else:
                            add_trade(
                                trade_rows,
                                signal,
                                values,
                                structure,
                                entry_time,
                                exit_time,
                                result,
                            )

            for family, minimum_dte, maximum_dte in (
                ("FRONT_PUT_CREDIT", 0, 4),
                ("NEXT_PUT_CREDIT", 5, 11),
            ):
                structure = f"{family}__E{entry_time.replace(':', '')}__X1025"
                short_contract = choose_contract(
                    put_quotes, day, minimum_dte, maximum_dte, spot * 0.99
                )
                if short_contract is None:
                    skip(signal, structure, "no_short_contract")
                    continue
                long_contract = choose_same_expiry(
                    put_quotes,
                    short_contract.expiry,
                    spot * 0.97,
                    below=short_contract.strike,
                )
                if long_contract is None:
                    skip(signal, structure, "no_long_contract")
                    continue
                result = put_credit_result(
                    family,
                    short_contract,
                    long_contract,
                    frame,
                    clock(day, "10:25"),
                )
                if result is None:
                    skip(signal, structure, "unsynchronized_or_invalid")
                    continue
                add_trade(
                    trade_rows,
                    signal,
                    values,
                    structure,
                    entry_time,
                    "10:25",
                    result,
                )

        for family in ("FRONT_ATM", "NEXT_ATM"):
            contract = choose_contract(
                entry_quotes(frame, day, "09:36", "C"),
                day,
                CALL_SPECS[family][0],
                CALL_SPECS[family][1],
                values["spot_entry"],
            )
            for rule, profit_multiple in (
                ("SL50", None),
                ("PT50_SL50", 1.50),
                ("PT100_SL50", 2.00),
            ):
                structure = f"{family}__E0936__X1025__{rule}"
                if contract is None:
                    skip(signal, structure, "no_contract")
                    continue
                result = path_call_result(
                    f"{family}_{rule}",
                    contract,
                    frame,
                    clock(day, "10:25"),
                    profit_multiple,
                )
                if result is None:
                    skip(signal, structure, "no_exit_quote")
                    continue
                add_trade(
                    trade_rows,
                    signal,
                    values,
                    structure,
                    "09:36",
                    "10:25",
                    result,
                )

    trades = pd.DataFrame(trade_rows).sort_values(
        ["date", "structure"]
    ).reset_index(drop=True)
    skips = pd.DataFrame(skip_rows)
    if trades.empty:
        raise RuntimeError("Wave 2 produced no executable trades")
    summary = summarize(trades)
    symbol_attribution = (
        trades.groupby(["structure", "ticker"])
        .agg(
            n=("pnl", "size"),
            total_pnl=("pnl", "sum"),
            mean_pnl=("pnl", "mean"),
        )
        .reset_index()
    )
    trades.to_csv(OUT / "wave2_trades.csv", index=False)
    skips.to_csv(OUT / "wave2_skips.csv", index=False)
    summary.to_csv(OUT / "wave2_summary.csv", index=False)
    symbol_attribution.to_csv(OUT / "wave2_symbol_attribution.csv", index=False)
    write_report(signals, trades, summary, skips)
    print(summary.to_string(index=False))
    print(f"\ntrades={len(trades)} skips={len(skips)}")


if __name__ == "__main__":
    main()
