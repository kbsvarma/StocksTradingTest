#!/usr/bin/env python3
"""Executable-quote screen of opening-leader calls on the local OPRA sample."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).resolve().parent
STOCK_DIR = ROOT / "stock_trend_2026_07" / "data"
OPTION_DIR = ROOT / "spx_spread_bot" / "backtests" / "multi_ic_2026" / "data"
TICKERS = ("AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA")
ET = "America/New_York"
COMMISSION = 0.65
OPTION_RE = re.compile(r"^\s*([A-Z0-9.\-]+)\s+(\d{6})([CP])(\d{8})\s*$")


@dataclass(frozen=True)
class Contract:
    symbol: str
    expiry: date
    strike: float
    bid: float
    ask: float
    bid_size: int
    ask_size: int
    ts_event: pd.Timestamp

    @property
    def spread(self) -> float:
        return self.ask - self.bid


def _et_index(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out.index = (
        out.index.tz_localize("UTC")
        if out.index.tz is None
        else out.index.tz_convert("UTC")
    )
    out.index = out.index.tz_convert(ET)
    return out


def load_stock_days(ticker: str) -> dict[date, dict]:
    raw = _et_index(pd.read_parquet(STOCK_DIR / f"{ticker}_1m.parquet"))
    raw = raw.loc["2026-01-20":"2026-05-22"].between_time("09:30", "15:59")
    days: dict[date, dict] = {}
    prior_close = None
    for day, group in raw.groupby(raw.index.date):
        group = group.sort_index()
        by_time = {ts.strftime("%H:%M"): row for ts, row in group.iterrows()}
        needed = ("09:30", "09:31", "09:32", "09:33", "09:34", "09:36", "10:25")
        if prior_close is not None and all(t in by_time for t in needed):
            opening = group.between_time("09:30", "09:34")
            path = group.between_time("09:36", "10:25")
            post_signal_path = group.between_time("09:35", "10:25")
            open_0930 = float(by_time["09:30"]["open"])
            close_0934 = float(by_time["09:34"]["close"])
            days[day] = {
                "prior_close": float(prior_close),
                "open_0930": open_0930,
                "close_0934": close_0934,
                "gap": open_0930 / float(prior_close) - 1.0,
                "r5": close_0934 / open_0930 - 1.0,
                "spot_entry": float(by_time["09:36"]["open"]),
                "opening_low": float(opening["low"].min()),
                "opening_high": float(opening["high"].max()),
                "path": path,
                "post_signal_path": post_signal_path,
                "time_exit_spot": float(by_time["10:25"]["close"]),
            }
        if len(group):
            prior_close = float(group.iloc[-1]["close"])
    return days


def option_dates(ticker: str) -> set[date]:
    folder = OPTION_DIR / ticker.lower()
    return {
        pd.Timestamp(path.stem.rsplit("_", 1)[-1]).date()
        for path in folder.glob(f"{ticker.lower()}_cbbo_*.parquet")
    }


def build_signals(stock_days: dict[str, dict[date, dict]]) -> pd.DataFrame:
    common_dates = sorted(set.intersection(*(option_dates(t) for t in TICKERS)))
    rows = []
    for day in common_dates:
        available = {
            ticker: stock_days[ticker][day]
            for ticker in TICKERS
            if day in stock_days[ticker]
        }
        if len(available) < 5:
            continue
        median_r5 = float(np.median([values["r5"] for values in available.values()]))
        eligible = [
            (ticker, values)
            for ticker, values in available.items()
            if values["gap"] > 0 and values["r5"] > 0
        ]
        if not eligible:
            rows.append({"date": day, "ticker": None, "reason": "no_eligible"})
            continue
        ticker, values = max(
            eligible, key=lambda item: item[1]["r5"] - median_r5
        )
        rows.append(
            {
                "date": day,
                "ticker": ticker,
                "reason": "signal",
                "gap": values["gap"],
                "r5": values["r5"],
                "median_r5": median_r5,
                "strength": values["r5"] - median_r5,
                "spot_entry": values["spot_entry"],
                "opening_low": values["opening_low"],
            }
        )
    return pd.DataFrame(rows)


def parse_option_symbol(symbol: str) -> tuple[date, str, float] | None:
    match = OPTION_RE.match(str(symbol))
    if not match:
        return None
    expiry = pd.to_datetime(match.group(2), format="%y%m%d").date()
    return expiry, match.group(3), int(match.group(4)) / 1000.0


def load_option_day(ticker: str, day: date) -> pd.DataFrame:
    path = (
        OPTION_DIR
        / ticker.lower()
        / f"{ticker.lower()}_cbbo_{pd.Timestamp(day):%Y%m%d}.parquet"
    )
    frame = pd.read_parquet(path)
    event = pd.to_datetime(frame["ts_event"], utc=True).dt.tz_convert(ET)
    frame = frame.assign(event_et=event)
    # Parse the OCC fields vectorially. Calling pd.to_datetime once per quote
    # made cross-sectional option studies unnecessarily quadratic in practice.
    parsed = frame["symbol"].astype(str).str.extract(OPTION_RE)
    good = parsed[1].notna()
    frame = frame.loc[good].copy()
    parsed = parsed.loc[good]
    frame["expiry"] = pd.to_datetime(
        parsed[1], format="%y%m%d", errors="raise"
    ).dt.date.to_numpy()
    frame["right"] = parsed[2].to_numpy()
    frame["strike"] = parsed[3].astype(int).to_numpy() / 1000.0
    return frame.sort_values("event_et")


def valid_entry_quotes(frame: pd.DataFrame, day: date) -> dict[str, Contract]:
    start = pd.Timestamp(day, tz=ET) + pd.Timedelta(hours=9, minutes=36)
    end = pd.Timestamp(day, tz=ET) + pd.Timedelta(hours=9, minutes=39)
    window = frame[
        (frame["event_et"] >= start)
        & (frame["event_et"] < end)
        & (frame["right"] == "C")
    ].copy()
    window = window[
        (window["bid_px_00"] > 0)
        & (window["ask_px_00"] >= window["bid_px_00"])
        & (window["bid_sz_00"] > 0)
        & (window["ask_sz_00"] > 0)
    ]
    mid = (window["bid_px_00"] + window["ask_px_00"]) / 2.0
    window = window[(window["ask_px_00"] - window["bid_px_00"]) <= 0.20 * mid]
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


def choose_contract(
    quotes: dict[str, Contract],
    day: date,
    min_dte: int,
    max_dte: int,
    strike_target: float,
) -> Contract | None:
    eligible = [
        contract
        for contract in quotes.values()
        if min_dte <= (contract.expiry - day).days <= max_dte
    ]
    if not eligible:
        return None
    expiry = min(contract.expiry for contract in eligible)
    same_expiry = [contract for contract in eligible if contract.expiry == expiry]
    return min(
        same_expiry,
        key=lambda contract: (abs(contract.strike - strike_target), contract.strike),
    )


def exit_quote(
    frame: pd.DataFrame,
    symbol: str,
    when: pd.Timestamp,
    stopped: bool,
) -> tuple[float, float, pd.Timestamp] | None:
    chain = frame[
        (frame["symbol"] == symbol)
        & (frame["bid_px_00"] >= 0)
        & (frame["ask_px_00"] >= frame["bid_px_00"])
        & (frame["bid_sz_00"] > 0)
        & (frame["ask_sz_00"] > 0)
    ].sort_values("event_et")
    if stopped:
        rows = chain[
            (chain["event_et"] >= when)
            & (chain["event_et"] <= when + pd.Timedelta(minutes=3))
        ]
        if rows.empty:
            return None
        row = rows.iloc[0]
    else:
        rows = chain[
            (chain["event_et"] <= when)
            & (chain["event_et"] >= when - pd.Timedelta(minutes=3))
        ]
        if rows.empty:
            return None
        row = rows.iloc[-1]
    return float(row["bid_px_00"]), float(row["ask_px_00"]), row["event_et"]


def stop_event(values: dict) -> tuple[bool, pd.Timestamp | None, float]:
    path = values["path"]
    hit = path[path["low"] <= values["opening_low"]]
    if hit.empty:
        return False, None, values["time_exit_spot"]
    ts = hit.index[0]
    row = hit.iloc[0]
    underlying_exit = min(float(row["open"]), values["opening_low"])
    return True, ts + pd.Timedelta(minutes=1), underlying_exit


def long_call_trade(
    structure: str,
    contract: Contract,
    frame: pd.DataFrame,
    exit_when: pd.Timestamp,
    stopped: bool,
    values: dict,
) -> dict | None:
    quote = exit_quote(frame, contract.symbol, exit_when, stopped)
    if quote is None:
        return None
    exit_bid, exit_ask, actual_exit = quote
    pnl = (exit_bid - contract.ask) * 100.0 - 2 * COMMISSION
    entry_half_spread = contract.spread / 2.0
    exit_half_spread = (exit_ask - exit_bid) / 2.0
    stress_exit = max(0.0, exit_bid - exit_half_spread)
    stress_entry = contract.ask + entry_half_spread
    stress_pnl = (stress_exit - stress_entry) * 100.0 - 2 * COMMISSION
    capital = contract.ask * 100.0 + COMMISSION
    return {
        "structure": structure,
        "long_symbol": contract.symbol,
        "short_symbol": None,
        "expiry": contract.expiry,
        "long_strike": contract.strike,
        "short_strike": np.nan,
        "entry_debit": contract.ask,
        "exit_credit": exit_bid,
        "entry_spread": contract.spread,
        "exit_spread": exit_ask - exit_bid,
        "pnl": pnl,
        "stress_pnl": stress_pnl,
        "roi": pnl / capital,
        "stress_roi": stress_pnl / capital,
        "actual_exit": actual_exit,
        "underlying_return": (
            values["underlying_exit"] / values["spot_entry"] - 1.0
        ),
    }


def vertical_trade(
    long_contract: Contract,
    short_contract: Contract,
    frame: pd.DataFrame,
    exit_when: pd.Timestamp,
    stopped: bool,
    values: dict,
) -> dict | None:
    long_exit = exit_quote(frame, long_contract.symbol, exit_when, stopped)
    short_exit = exit_quote(frame, short_contract.symbol, exit_when, stopped)
    if long_exit is None or short_exit is None:
        return None
    long_bid, long_ask, long_time = long_exit
    short_bid, short_ask, short_time = short_exit
    entry_debit = long_contract.ask - short_contract.bid
    exit_credit = long_bid - short_ask
    if entry_debit <= 0:
        return None
    pnl = (exit_credit - entry_debit) * 100.0 - 4 * COMMISSION
    entry_stress = (
        long_contract.ask
        + long_contract.spread / 2.0
        - (short_contract.bid - short_contract.spread / 2.0)
    )
    exit_stress = (
        max(0.0, long_bid - (long_ask - long_bid) / 2.0)
        - (short_ask + (short_ask - short_bid) / 2.0)
    )
    stress_pnl = (exit_stress - entry_stress) * 100.0 - 4 * COMMISSION
    capital = entry_debit * 100.0 + 2 * COMMISSION
    return {
        "structure": "NEXT_VERTICAL2",
        "long_symbol": long_contract.symbol,
        "short_symbol": short_contract.symbol,
        "expiry": long_contract.expiry,
        "long_strike": long_contract.strike,
        "short_strike": short_contract.strike,
        "entry_debit": entry_debit,
        "exit_credit": exit_credit,
        "entry_spread": long_contract.spread + short_contract.spread,
        "exit_spread": (long_ask - long_bid) + (short_ask - short_bid),
        "pnl": pnl,
        "stress_pnl": stress_pnl,
        "roi": pnl / capital,
        "stress_roi": stress_pnl / capital,
        "actual_exit": max(long_time, short_time),
        "underlying_return": (
            values["underlying_exit"] / values["spot_entry"] - 1.0
        ),
    }


def summarize(trades: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for structure, group in trades.groupby("structure", sort=False):
        pnl = group["pnl"].astype(float)
        cumulative = pnl.cumsum()
        drawdown = cumulative - cumulative.cummax().clip(lower=0)
        t_stat = (
            pnl.mean() / (pnl.std(ddof=1) / math.sqrt(len(pnl)))
            if len(pnl) > 1 and pnl.std(ddof=1) > 0
            else np.nan
        )
        positive = pnl[pnl > 0]
        concentration = (
            positive.max() / positive.sum() if positive.sum() > 0 else np.nan
        )
        passed = (
            pnl.mean() > 0
            and t_stat >= 2.0
            and pnl.median() > 0
            and group["stress_pnl"].sum() > 0
            and concentration <= 0.35
        )
        rows.append(
            {
                "structure": structure,
                "n": len(group),
                "mean_pnl": pnl.mean(),
                "median_pnl": pnl.median(),
                "total_pnl": pnl.sum(),
                "t_stat": t_stat,
                "win_rate": (pnl > 0).mean(),
                "mean_roi_pct": group["roi"].mean() * 100,
                "median_roi_pct": group["roi"].median() * 100,
                "stress_total_pnl": group["stress_pnl"].sum(),
                "max_drawdown": drawdown.min(),
                "worst_trade": pnl.min(),
                "stop_rate": group["stopped"].mean(),
                "max_positive_contribution": concentration,
                "passes_local_gates": bool(passed),
            }
        )
    return pd.DataFrame(rows).sort_values("total_pnl", ascending=False)


def write_report(
    signals: pd.DataFrame,
    trades: pd.DataFrame,
    summary: pd.DataFrame,
    skips: pd.DataFrame,
) -> None:
    lines = [
        "# Opening Leader Options — Local OPRA Screen",
        "",
        f"Signal days: {int((signals['reason'] == 'signal').sum())}; "
        f"flat days: {int((signals['reason'] != 'signal').sum())}.",
        "",
        "All entries cross the OPRA ask and exits cross the OPRA bid. "
        "The stressed column pays an additional half-spread on every leg at "
        "both entry and exit.",
        "",
        "| Structure | n | Mean $ | Median $ | Total $ | t | Win | "
        "Mean ROI | Stress total $ | Max DD $ | Pass |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    for row in summary.itertuples():
        lines.append(
            f"| {row.structure} | {row.n} | {row.mean_pnl:,.2f} | "
            f"{row.median_pnl:,.2f} | {row.total_pnl:,.2f} | "
            f"{row.t_stat:.2f} | {row.win_rate:.1%} | "
            f"{row.mean_roi_pct:.2f}% | {row.stress_total_pnl:,.2f} | "
            f"{row.max_drawdown:,.2f} | "
            f"{'YES' if row.passes_local_gates else 'NO'} |"
        )
    lines += [
        "",
        "## Attribution",
        "",
        "P&L by selected underlying and structure is in `symbol_attribution.csv`; "
        "all executions are in `trades.csv`; skip accounting is in `skips.csv`.",
        "",
        "## Scope",
        "",
        "This is the frozen seven-name, February–May 2026 mechanism screen. "
        "Any local survivor still requires candidate-day OPRA history from the "
        "separate point-in-time top-150 stock study before it can be considered "
        "a multi-era result.",
    ]
    (OUT / "RESULTS.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    stock_days = {ticker: load_stock_days(ticker) for ticker in TICKERS}
    signals = build_signals(stock_days)
    signals.to_csv(OUT / "signals.csv", index=False)

    trade_rows = []
    skip_rows = []
    specifications = {
        "FRONT_ATM": (0, 4, 1.00),
        "NEXT_ATM": (5, 11, 1.00),
        "NEXT_ITM2": (5, 11, 0.98),
        "NEXT_OTM1": (5, 11, 1.01),
        "MONTH_ATM": (20, 35, 1.00),
    }

    for signal in signals.itertuples():
        if signal.reason != "signal":
            continue
        ticker = signal.ticker
        day = signal.date
        values = stock_days[ticker][day]
        stopped, stop_time, underlying_exit = stop_event(values)
        exit_when = (
            stop_time
            if stopped
            else pd.Timestamp(day, tz=ET) + pd.Timedelta(hours=10, minutes=25)
        )
        enriched = dict(values)
        enriched["underlying_exit"] = underlying_exit
        frame = load_option_day(ticker, day)
        quotes = valid_entry_quotes(frame, day)
        if not quotes:
            skip_rows.append(
                {"date": day, "ticker": ticker, "structure": "ALL", "reason": "no_entry_quotes"}
            )
            continue

        for structure, (min_dte, max_dte, moneyness) in specifications.items():
            contract = choose_contract(
                quotes, day, min_dte, max_dte, values["spot_entry"] * moneyness
            )
            if contract is None:
                skip_rows.append(
                    {"date": day, "ticker": ticker, "structure": structure, "reason": "no_contract"}
                )
                continue
            result = long_call_trade(
                structure, contract, frame, exit_when, stopped, enriched
            )
            if result is None:
                skip_rows.append(
                    {"date": day, "ticker": ticker, "structure": structure, "reason": "no_exit_quote"}
                )
                continue
            trade_rows.append(
                {
                    "date": day,
                    "ticker": ticker,
                    "gap": signal.gap,
                    "r5": signal.r5,
                    "strength": signal.strength,
                    "spot_entry": values["spot_entry"],
                    "opening_low": values["opening_low"],
                    "stopped": stopped,
                    **result,
                }
            )

        long_contract = choose_contract(
            quotes, day, 5, 11, values["spot_entry"]
        )
        if long_contract is None:
            skip_rows.append(
                {"date": day, "ticker": ticker, "structure": "NEXT_VERTICAL2", "reason": "no_long"}
            )
            continue
        same_expiry = {
            symbol: contract
            for symbol, contract in quotes.items()
            if contract.expiry == long_contract.expiry
            and contract.strike > long_contract.strike
        }
        short_contract = choose_contract(
            same_expiry,
            day,
            (long_contract.expiry - day).days,
            (long_contract.expiry - day).days,
            values["spot_entry"] * 1.02,
        )
        if short_contract is None:
            skip_rows.append(
                {"date": day, "ticker": ticker, "structure": "NEXT_VERTICAL2", "reason": "no_short"}
            )
            continue
        result = vertical_trade(
            long_contract, short_contract, frame, exit_when, stopped, enriched
        )
        if result is None:
            skip_rows.append(
                {"date": day, "ticker": ticker, "structure": "NEXT_VERTICAL2", "reason": "no_exit_or_debit"}
            )
            continue
        trade_rows.append(
            {
                "date": day,
                "ticker": ticker,
                "gap": signal.gap,
                "r5": signal.r5,
                "strength": signal.strength,
                "spot_entry": values["spot_entry"],
                "opening_low": values["opening_low"],
                "stopped": stopped,
                **result,
            }
        )

    trades = pd.DataFrame(trade_rows)
    skips = pd.DataFrame(skip_rows)
    if trades.empty:
        raise RuntimeError("No option trades could be simulated")
    trades = trades.sort_values(["date", "structure"]).reset_index(drop=True)
    summary = summarize(trades)
    symbol_attribution = (
        trades.groupby(["structure", "ticker"])
        .agg(n=("pnl", "size"), total_pnl=("pnl", "sum"), mean_pnl=("pnl", "mean"))
        .reset_index()
    )
    trades.to_csv(OUT / "trades.csv", index=False)
    skips.to_csv(OUT / "skips.csv", index=False)
    summary.to_csv(OUT / "summary.csv", index=False)
    symbol_attribution.to_csv(OUT / "symbol_attribution.csv", index=False)
    write_report(signals, trades, summary, skips)
    print(summary.to_string(index=False))
    print(f"\nsignals={len(signals)} trades={len(trades)} skips={len(skips)}")


if __name__ == "__main__":
    main()
