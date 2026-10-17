"""
Live market scanner. Public data only — no credentials needed.

Pulls open markets, drops auto-generated provisional combo markets, and ranks the
genuinely-quoted ones by a market-making attractiveness score:

    - wide spread        -> more edge to capture as a maker
    - real liquidity/vol  -> the LIP pool follows activity; dead markets pay nothing
    - price near 50c      -> deeper books, but also where pros compete hardest

Run during US market hours for meaningful output (overnight everything is dead
except crypto/weather).

    python kalshi_mm/scanner.py --top 40

NOTE: which markets have an ACTIVE liquidity-incentive reward period is NOT in the
public payload (only `liquidity_dollars` is). That marker appears to require the
authenticated account view. `investigate_reward_markers()` is a stub to chase that
down once we are signing requests.
"""

from __future__ import annotations

import argparse
import time
from collections import Counter

from kalshi_client import KalshiClient


def f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def yb(m):  return f(m.get("yes_bid_dollars"))
def ya(m):  return f(m.get("yes_ask_dollars"))
def liq(m): return f(m.get("liquidity_dollars"))
def vol24(m): return f(m.get("volume_24h_fp"))
def voltot(m): return f(m.get("volume_fp"))


def pull_open_markets(client: KalshiClient, max_pages: int = 20,
                      pace_sec: float = 0.4) -> list[dict]:
    """Page through open markets. `pace_sec` spaces requests to stay under the
    public rate limit during repeated rescans in a long-running loop."""
    markets, cursor, seen = [], None, set()
    for i in range(max_pages):
        params = {"limit": 1000, "status": "open"}
        if cursor:
            params["cursor"] = cursor
        try:
            d = client.get_markets(**params)
        except Exception:
            time.sleep(1.0)  # backoff on a transient/429 and stop this rescan
            break
        batch = d.get("markets", [])
        for m in batch:
            t = m["ticker"]
            if t not in seen:
                seen.add(t)
                markets.append(m)
        cursor = d.get("cursor")
        if not cursor or not batch:
            break
        if pace_sec:
            time.sleep(pace_sec)
    return markets


def is_two_sided(m) -> bool:
    return yb(m) > 0 and ya(m) > yb(m) and ya(m) < 1.0


def score(m) -> float:
    """Higher = more attractive to quote. Rough heuristic, tune later."""
    spread = ya(m) - yb(m)
    # reward spread and activity; penalize totally dead books
    activity = max(vol24(m), 1.0)
    return spread * 100.0 * (1.0 + (activity ** 0.5) / 10.0) * (1.0 + liq(m) / 1000.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=40)
    args = ap.parse_args()

    client = KalshiClient(dry_run=True)  # public reads only
    print("exchange:", client.exchange_status())

    markets = pull_open_markets(client)
    print(f"pulled {len(markets)} unique open markets")

    real = [m for m in markets if is_two_sided(m)]
    print(f"two-sided quoted: {len(real)}")
    real.sort(key=score, reverse=True)

    print(f"\nTOP {args.top} CANDIDATES (by MM-attractiveness score):")
    hdr = f"{'ticker':46} {'score':>6} {'spr¢':>4} {'liq$':>8} {'vol24':>8} {'yb¢':>4} {'ya¢':>4}"
    print(hdr)
    print("-" * len(hdr))
    for m in real[: args.top]:
        print(
            f"{m['ticker'][:46]:46} {score(m):>6.1f} "
            f"{int((ya(m)-yb(m))*100):>4} {int(liq(m)):>8} {int(vol24(m)):>8} "
            f"{int(yb(m)*100):>4} {int(ya(m)*100):>4}"
        )

    print("\nCATEGORIES among two-sided markets:")
    def pre(t): return t.split("-")[0]
    for p, n in Counter(pre(m["ticker"]) for m in real).most_common(20):
        tl = sum(liq(m) for m in real if pre(m["ticker"]) == p)
        print(f"  {p:30} n={n:>4}  totalLiq=${int(tl):>9}")


def investigate_reward_markers(client: KalshiClient):
    """
    TODO: with auth, probe for the LIP/VIP reward-period markers. Candidate
    sources to try once signing: the market/event objects under an authed call,
    a dedicated /incentives or /rewards endpoint, or the milestone/structured-target
    fields. Until confirmed, target selection leans on liquidity + spread.
    """
    raise NotImplementedError


if __name__ == "__main__":
    main()
