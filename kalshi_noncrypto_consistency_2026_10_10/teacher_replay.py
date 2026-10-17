"""Replay observable Polymarket specialist trades on matching Kalshi markets.

This is a research-only, causal execution study. A signal is the first public
BUY by a teacher for an outcome. The simulated Kalshi fill uses the first
available one-minute candle at least 60 seconds later and holds to settlement.
"""

from __future__ import annotations

import calendar
import json
import math
import os
import re
import statistics
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError
from collections import defaultdict
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DATA = HERE / "data"
SPORTS_DATA = REPO / "kalshi_sports_2026_08" / "data"
USER_AGENT = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 Chrome/138.0 Safari/537.36"
    )
}
LOOKBACK_DAYS = 7
HOLDOUT_DAYS = 2
MAX_WALLETS = int(os.environ.get("TEACHER_MAX_WALLETS", "25"))
LEADER_PERIOD = os.environ.get("TEACHER_LEADER_PERIOD", "ALL")
MAX_TRADES_PER_WALLET = 5_000
MIN_TRAIN_SIGNALS = 5
MIN_DIRECTIONAL_NOTIONAL = 100.0
MIN_DIRECTIONAL_RATIO = 4.0
SERIES = (
    "KXMLBGAME",
    "KXWNBAGAME",
    "KXATPMATCH",
    "KXWTAMATCH",
    "KXUFCFIGHT",
)

ALIASES = {
    "Arizona Diamondbacks": "Arizona",
    "Athletics": "Athletics",
    "Atlanta Braves": "Atlanta",
    "Baltimore Orioles": "Baltimore",
    "Boston Red Sox": "Boston",
    "Chicago Cubs": "Chicago C",
    "Chicago White Sox": "Chicago W",
    "Cincinnati Reds": "Cincinnati",
    "Cleveland Guardians": "Cleveland",
    "Colorado Rockies": "Colorado",
    "Detroit Tigers": "Detroit",
    "Houston Astros": "Houston",
    "Kansas City Royals": "Kansas City",
    "Los Angeles Angels": "Los Angeles A",
    "Los Angeles Dodgers": "Los Angeles D",
    "Miami Marlins": "Miami",
    "Milwaukee Brewers": "Milwaukee",
    "Minnesota Twins": "Minnesota",
    "New York Mets": "New York M",
    "New York Yankees": "New York Y",
    "Philadelphia Phillies": "Philadelphia",
    "Pittsburgh Pirates": "Pittsburgh",
    "San Diego Padres": "San Diego",
    "San Francisco Giants": "San Francisco",
    "Seattle Mariners": "Seattle",
    "St. Louis Cardinals": "St Louis",
    "Tampa Bay Rays": "Tampa Bay",
    "Texas Rangers": "Texas",
    "Toronto Blue Jays": "Toronto",
    "Washington Nationals": "Washington",
    "Atlanta Dream": "Atlanta",
    "Chicago Sky": "Chicago",
    "Connecticut Sun": "Connecticut",
    "Dallas Wings": "Dallas",
    "Golden State Valkyries": "Golden State",
    "Indiana Fever": "Indiana",
    "Las Vegas Aces": "Las Vegas",
    "Los Angeles Sparks": "Los Angeles",
    "Minnesota Lynx": "Minnesota",
    "New York Liberty": "New York",
    "Phoenix Mercury": "Phoenix",
    "Seattle Storm": "Seattle",
    "Toronto Tempo": "Toronto",
    "Washington Mystics": "Washington",
}


def get_json(url: str, params: dict[str, object] | None = None) -> object:
    query = "?" + urllib.parse.urlencode(params) if params else ""
    request = urllib.request.Request(url + query, headers=USER_AGENT)
    for attempt in range(6):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code != 429 or attempt == 5:
                raise
            retry_after = error.headers.get("Retry-After")
            delay = float(retry_after) if retry_after else 2.0**attempt
            time.sleep(min(delay, 30.0))
    raise RuntimeError("unreachable")


def parse_utc(value: str) -> int:
    return calendar.timegm(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ"))


def normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", value.lower()).strip()


def participant_score(polymarket_outcome: str, kalshi_participant: str) -> float:
    left = normalized(ALIASES.get(polymarket_outcome, polymarket_outcome))
    right = normalized(kalshi_participant)
    if left == right:
        return 1.0
    left_words, right_words = set(left.split()), set(right.split())
    overlap = len(left_words & right_words) / max(1, len(left_words | right_words))
    # Token overlap is sufficient for full names; aliases handle ambiguous cities.
    return overlap


def direct_winner_market(title: str) -> bool:
    lowered = title.lower()
    rejected = (
        "o/u",
        "spread:",
        "game handicap",
        "1st half",
        "maps",
        "games of the future",
        "end in a draw",
    )
    return " vs" in lowered and not any(term in lowered for term in rejected)


def matchup_participants(title: str) -> tuple[str, str] | None:
    participants = re.split(r"\s+vs\.?\s+", title, maxsplit=1, flags=re.IGNORECASE)
    if len(participants) != 2:
        return None
    right = re.sub(r"\s+(?:match\s+)?winner\??$", "", participants[1], flags=re.I)
    return participants[0].strip(), right.strip()


def load_kalshi_rows() -> dict[str, dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    for series in SERIES:
        path = SPORTS_DATA / f"games_{series}.jsonl"
        for line in path.open():
            row = json.loads(line)
            rows[row["ticker"]] = row
    return rows


def fetch_kalshi_metadata(rows: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    metadata: dict[str, dict[str, object]] = {}
    minimum_close = int(time.time()) - 55 * 86_400
    for series in SERIES:
        cursor = ""
        while True:
            params: dict[str, object] = {
                "series_ticker": series,
                "status": "settled",
                "limit": 1_000,
                "min_close_ts": minimum_close,
            }
            if cursor:
                params["cursor"] = cursor
            response = get_json(
                "https://external-api.kalshi.com/trade-api/v2/markets", params
            )
            assert isinstance(response, dict)
            for market in response.get("markets", []):
                if market["ticker"] in rows:
                    metadata[market["ticker"]] = market
            cursor = response.get("cursor") or ""
            if not cursor:
                break
    return metadata


def fetch_leaders() -> list[dict[str, object]]:
    response = get_json(
        "https://data-api.polymarket.com/v1/leaderboard",
        {
            "category": "SPORTS",
            # All-time leaders reduce leakage from selecting teachers on the same
            # short interval used for the replay holdout.
            "timePeriod": LEADER_PERIOD,
            "orderBy": "PNL",
            "limit": MAX_WALLETS,
        },
    )
    assert isinstance(response, list)
    return response


def fetch_wallet_trades(wallet: str, start: int) -> tuple[list[dict[str, object]], bool]:
    trades: list[dict[str, object]] = []
    for offset in range(0, MAX_TRADES_PER_WALLET, 1_000):
        response = get_json(
            "https://data-api.polymarket.com/trades",
            {
                "user": wallet,
                "start": start,
                "side": "BUY",
                "takerOnly": "true",
                "limit": 1_000,
                "offset": offset,
            },
        )
        assert isinstance(response, list)
        trades.extend(response)
        if len(response) < 1_000:
            return trades, False
    return trades, True


def kalshi_fee(price: float) -> float:
    return 0.07 * price * (1.0 - price)


def next_minute_ask(row: dict[str, object], timestamp: int) -> float | None:
    target = timestamp + 60
    for candle in row["candles"]:
        ask = (candle.get("yes_ask") or {}).get("open_dollars")
        candle_open_ts = candle.get("end_period_ts", 0) - 60
        if candle_open_ts >= target and ask is not None:
            price = float(ask)
            return price if 0.0 < price < 1.0 else None
    return None


def build_signals(
    rows: dict[str, dict[str, object]],
    metadata: dict[str, dict[str, object]],
    leaders: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[str]]:
    candidates = [
        (ticker, market, parse_utc(market["close_time"]))
        for ticker, market in metadata.items()
    ]
    start = int(time.time()) - LOOKBACK_DAYS * 86_400
    signals: list[dict[str, object]] = []
    warnings: list[str] = []

    for leader in leaders:
        wallet = str(leader["proxyWallet"])
        trades, truncated = fetch_wallet_trades(wallet, start)
        if truncated:
            warnings.append(f"{leader.get('userName')}: trade history truncated at 5,000 rows")
        for trade in sorted(trades, key=lambda item: int(item["timestamp"])):
            title = str(trade.get("title") or "")
            outcome = str(trade.get("outcome") or "")
            participants = matchup_participants(title)
            if not direct_winner_market(title) or participants is None:
                continue

            first_score = participant_score(outcome, participants[0])
            second_score = participant_score(outcome, participants[1])
            opponent = participants[1] if first_score >= second_score else participants[0]

            possible: list[tuple[float, int, str, dict[str, object], int]] = []
            for ticker, market, close_ts in candidates:
                distance = close_ts - int(trade["timestamp"])
                if not 0 < distance <= 36 * 3_600:
                    continue
                outcome_score = participant_score(
                    outcome, market.get("yes_sub_title") or ""
                )
                kalshi_participants = matchup_participants(str(market.get("title") or ""))
                if kalshi_participants is None:
                    continue
                yes_name = str(market.get("yes_sub_title") or "")
                left_yes_score = participant_score(yes_name, kalshi_participants[0])
                right_yes_score = participant_score(yes_name, kalshi_participants[1])
                kalshi_opponent = (
                    kalshi_participants[1]
                    if left_yes_score >= right_yes_score
                    else kalshi_participants[0]
                )
                opponent_score = participant_score(
                    opponent, kalshi_opponent
                )
                if min(outcome_score, opponent_score) >= 0.82:
                    possible.append(
                        (
                            (outcome_score + opponent_score) / 2,
                            -distance,
                            ticker,
                            market,
                            close_ts,
                        )
                    )
            if not possible:
                continue

            _, _, ticker, market, close_ts = max(possible, key=lambda item: item[:2])
            entry_price = next_minute_ask(rows[ticker], int(trade["timestamp"]))
            if entry_price is None:
                continue

            won = rows[ticker]["result"] == "yes"
            fee = kalshi_fee(entry_price)
            pnl = (1.0 - entry_price if won else -entry_price) - fee
            signals.append(
                {
                    "timestamp": int(trade["timestamp"]),
                    "wallet": wallet,
                    "teacher": leader.get("userName") or wallet,
                    "teacher_rank": int(leader["rank"]),
                    "ticker": ticker,
                    "event_ticker": ticker.rsplit("-", 1)[0],
                    "series": rows[ticker]["series"],
                    "polymarket_title": title,
                    "outcome": outcome,
                    "polymarket_price": float(trade["price"]),
                    "polymarket_size": float(trade.get("size") or 0),
                    "polymarket_notional": float(trade["price"])
                    * float(trade.get("size") or 0),
                    "kalshi_entry_price": entry_price,
                    "price_gap": float(trade["price"]) - entry_price,
                    "time_to_close_hours": (close_ts - int(trade["timestamp"])) / 3_600,
                    "won": won,
                    "pnl": pnl,
                }
            )
    return sorted(signals, key=lambda item: int(item["timestamp"])), warnings


def first_by(records: list[dict[str, object]], key: str) -> list[dict[str, object]]:
    selected: dict[str, dict[str, object]] = {}
    for record in records:
        selected.setdefault(str(record[key]), record)
    return list(selected.values())


def remove_two_sided_wallet_events(
    records: list[dict[str, object]],
) -> tuple[list[dict[str, object]], int]:
    sides: dict[tuple[str, str], set[str]] = defaultdict(set)
    for record in records:
        sides[(str(record["wallet"]), str(record["event_ticker"]))].add(
            str(record["ticker"])
        )
    ambiguous = {key for key, tickers in sides.items() if len(tickers) > 1}
    return [
        record
        for record in records
        if (str(record["wallet"]), str(record["event_ticker"])) not in ambiguous
    ], len(ambiguous)


def conviction_signals(records: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for record in records:
        grouped[(str(record["wallet"]), str(record["event_ticker"]))].append(record)

    selected: list[dict[str, object]] = []
    for event_records in grouped.values():
        notionals: dict[str, float] = defaultdict(float)
        for record in sorted(event_records, key=lambda item: int(item["timestamp"])):
            ticker = str(record["ticker"])
            notionals[ticker] += float(record["polymarket_notional"])
            leader, leader_notional = max(notionals.items(), key=lambda item: item[1])
            opposing_notional = sum(notionals.values()) - leader_notional
            ratio = (
                leader_notional / opposing_notional
                if opposing_notional > 0
                else math.inf
            )
            if (
                ticker == leader
                and leader_notional >= MIN_DIRECTIONAL_NOTIONAL
                and ratio >= MIN_DIRECTIONAL_RATIO
            ):
                signal = dict(record)
                signal["cumulative_directional_notional"] = leader_notional
                signal["directional_ratio"] = ratio
                selected.append(signal)
                break
    return selected


def consensus_signals(records: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        grouped[str(record["ticker"])].append(record)
    selected_by_side: list[dict[str, object]] = []
    for ticker_records in grouped.values():
        distinct: list[dict[str, object]] = []
        wallets: set[str] = set()
        for record in sorted(ticker_records, key=lambda item: int(item["timestamp"])):
            wallet = str(record["wallet"])
            if wallet not in wallets:
                wallets.add(wallet)
                distinct.append(record)
        if len(distinct) >= 2:
            selected_by_side.append(distinct[1])

    # A game where separate teacher groups agree on opposite outcomes is not
    # consensus. Keep one entry only when all qualifying teachers point one way.
    by_event: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in selected_by_side:
        by_event[str(record["event_ticker"])].append(record)
    return [
        min(event_records, key=lambda item: int(item["timestamp"]))
        for event_records in by_event.values()
        if len({str(record["ticker"]) for record in event_records}) == 1
    ]


def stats(records: list[dict[str, object]]) -> dict[str, float | int | None]:
    if not records:
        return {"n": 0, "win_rate": None, "mean_pnl_cents": None, "t_stat": None}
    pnls = [float(record["pnl"]) for record in records]
    mean = statistics.mean(pnls)
    standard_error = statistics.stdev(pnls) / math.sqrt(len(pnls)) if len(pnls) > 1 else 0.0
    return {
        "n": len(records),
        "win_rate": sum(bool(record["won"]) for record in records) / len(records),
        "mean_pnl_cents": 100.0 * mean,
        "t_stat": mean / standard_error if standard_error else None,
        "mean_price_gap_cents": 100.0
        * statistics.mean(float(record["price_gap"]) for record in records),
        "median_entry_price": statistics.median(
            float(record["kalshi_entry_price"]) for record in records
        ),
    }


def train_holdout(records: list[dict[str, object]]) -> tuple[list[str], list[dict[str, object]], int]:
    cutoff = max(int(record["timestamp"]) for record in records) - HOLDOUT_DAYS * 86_400
    train = [record for record in records if int(record["timestamp"]) < cutoff]
    holdout = [record for record in records if int(record["timestamp"]) >= cutoff]
    by_teacher: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in train:
        by_teacher[str(record["wallet"])].append(record)
    qualified = [
        wallet
        for wallet, wallet_records in by_teacher.items()
        if len(wallet_records) >= MIN_TRAIN_SIGNALS
        and statistics.mean(float(record["pnl"]) for record in wallet_records) > 0
    ]
    qualified_holdout = [record for record in holdout if record["wallet"] in qualified]
    return qualified, first_by(qualified_holdout, "event_ticker"), cutoff


def line(label: str, values: dict[str, float | int | None]) -> str:
    if values["n"] == 0:
        return f"| {label} | 0 | - | - | - |"
    t_stat = f"{values['t_stat']:+.2f}" if values["t_stat"] is not None else "-"
    return (
        f"| {label} | {values['n']} | {100 * values['win_rate']:.1f}% | "
        f"{values['mean_pnl_cents']:+.2f}c | "
        f"{t_stat} |"
    )


def write_report(
    signals: list[dict[str, object]],
    leaders: list[dict[str, object]],
    warnings: list[str],
) -> None:
    directional_signals, ambiguous_count = remove_two_sided_wallet_events(signals)
    conviction = conviction_signals(signals)
    conviction_events = first_by(conviction, "event_ticker")
    market_dedup = first_by(directional_signals, "ticker")
    event_dedup = first_by(directional_signals, "event_ticker")
    consensus = consensus_signals(directional_signals)
    consensus_midprice_large = [
        record
        for record in consensus
        if 0.40 <= float(record["kalshi_entry_price"]) <= 0.60
        and float(record["polymarket_notional"]) >= 500.0
    ]
    qualified, holdout, cutoff = train_holdout(directional_signals)
    conviction_teachers, conviction_holdout, _ = train_holdout(conviction)
    result = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "leaderboard": leaders,
        "warnings": warnings,
        "signal_count": len(signals),
        "directional_signal_count": len(directional_signals),
        "two_sided_wallet_events_removed": ambiguous_count,
        "leader_period": LEADER_PERIOD,
        "conviction": stats(conviction_events),
        "conviction_qualified_teachers": conviction_teachers,
        "conviction_holdout": stats(conviction_holdout),
        "market_dedup": stats(market_dedup),
        "event_dedup": stats(event_dedup),
        "consensus": stats(consensus),
        "consensus_midprice_large": stats(consensus_midprice_large),
        "consensus_midprice_large_train": stats(
            [
                record
                for record in consensus_midprice_large
                if int(record["timestamp"]) < cutoff
            ]
        ),
        "consensus_midprice_large_holdout": stats(
            [
                record
                for record in consensus_midprice_large
                if int(record["timestamp"]) >= cutoff
            ]
        ),
        "qualified_teachers": qualified,
        "holdout_cutoff_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cutoff)),
        "holdout": stats(holdout),
    }
    DATA.mkdir(exist_ok=True)
    suffix = LEADER_PERIOD.lower()
    serialized_signals = json.dumps(signals, indent=2) + "\n"
    serialized_result = json.dumps(result, indent=2) + "\n"
    (DATA / "signals.json").write_text(serialized_signals)
    (DATA / f"signals_{suffix}.json").write_text(serialized_signals)
    (DATA / "result.json").write_text(serialized_result)
    (DATA / f"result_{suffix}.json").write_text(serialized_result)

    names = {str(item["proxyWallet"]): str(item.get("userName") or item["proxyWallet"]) for item in leaders}
    qualified_names = ", ".join(names.get(wallet, wallet) for wallet in qualified) or "none"
    report = [
        "# Cross-Venue Teacher Replay",
        "",
        f"Generated: {result['generated_utc']}",
        "",
        f"Polymarket's current top-{MAX_WALLETS} {LEADER_PERIOD.lower()} sports traders are "
        "treated as candidate teachers. "
        "Each first public BUY is mapped to the same Kalshi winner contract. Entry is the first "
        "available Kalshi ask at least 60 seconds later; one contract is held to settlement.",
        "",
        "| Replay | Independent n | Win rate | Net/contract | t-stat |",
        "|---|---:|---:|---:|---:|",
        line("First signal per contract", result["market_dedup"]),
        line("First signal per game/event", result["event_dedup"]),
        line("Two-teacher agreement", result["consensus"]),
        line("Two teachers, 40-60c, second buy >= $500", result["consensus_midprice_large"]),
        line("That lane, earlier slice", result["consensus_midprice_large_train"]),
        line("That lane, unseen two-day slice", result["consensus_midprice_large_holdout"]),
        line("Earlier-trained teachers, unseen holdout", result["holdout"]),
        line("$100 and 4:1 conviction signal", result["conviction"]),
        line("Conviction teachers, unseen holdout", result["conviction_holdout"]),
        "",
        f"Holdout cutoff: {result['holdout_cutoff_utc']}",
        f"Teachers qualifying on the earlier window: {qualified_names}",
        f"Two-sided wallet/game pairs removed as ambiguous: {ambiguous_count}",
        "Conviction rule: at least $100 cumulative BUY notional on one outcome and "
        "at least a 4:1 imbalance versus the opposing outcome.",
        "",
        "## Interpretation",
        "",
        "The full replay is exploratory because the current leaderboard was used to discover "
        "wallets. The holdout row is the least biased internal check: teachers must first show "
        "positive earlier-window Kalshi-copy P&L before their later games are evaluated.",
        "",
        "A positive result does not yet prove that the wallet's decision rule can be predicted "
        "before the wallet trades. It establishes whether their public action leaves enough "
        "cross-venue edge for Kalshi after a realistic delay.",
    ]
    if warnings:
        report.extend(("", "## Data Warnings", "", *[f"- {warning}" for warning in warnings]))
    serialized_report = "\n".join(report) + "\n"
    (HERE / "REPORT.md").write_text(serialized_report)
    (HERE / f"REPORT_{suffix}.md").write_text(serialized_report)


def main() -> None:
    rows = load_kalshi_rows()
    metadata = fetch_kalshi_metadata(rows)
    leaders = fetch_leaders()
    signals, warnings = build_signals(rows, metadata, leaders)
    if not signals:
        raise RuntimeError("No causal cross-venue signals mapped")
    write_report(signals, leaders, warnings)
    print((HERE / "REPORT.md").read_text())


if __name__ == "__main__":
    main()
