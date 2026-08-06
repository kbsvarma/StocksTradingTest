#!/usr/bin/env python3
"""STAGE 1 of PREREG_LIVESCORE.md — pure-tennis screen.

For each pre-registered score state: when a player first reaches it, how
often does she go on to WIN THE MATCH? No Kalshi prices involved, so all
865 tapes are usable (timestamps only matter for stage 2).

A state passes the screen only if its win rate clears the break-even of
the price Kalshi charges for that zone. Break-even ~= price + fee.
"""
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PTS = {"0": 0, "15": 1, "30": 2, "40": 3, "AD": 4, "A": 4}


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def game_point(pts, i, tb):
    """Is player i one point from winning the current game/tiebreak?"""
    j = 1 - i
    a, b = pts[i], pts[j]
    if tb:
        try:
            a, b = int(a), int(b)
        except (TypeError, ValueError):
            return False
        return a >= 6 and a - b >= 1
    if a not in PTS or b not in PTS:
        return False
    if PTS[a] == 4:                       # advantage
        return True
    return PTS[a] == 3 and PTS[b] < 3     # 40 vs 0/15/30


def set_point(games, cur, i, pts, tb):
    """One point from taking the current set."""
    j = 1 - i
    gi, gj = games[i][cur], games[j][cur]
    if not game_point(pts, i, tb):
        return False
    if tb:
        return True
    return gi >= 5 and gi - gj >= 1       # 5-4/6-5 -> set; 5-5 -> only 6-5


def states_for(row, fmt_sets_to_win=2):
    """Return {state: player_index} for states holding on this row."""
    out = {}
    sets, games, pts = row.get("sets"), row.get("games"), row.get("points")
    if not sets or not games or not pts or len(games) != 2:
        return out
    cur = len(games[0]) - 1
    if cur < 0 or len(games[1]) <= cur:
        return out
    tb = bool(row.get("is_tiebreak"))
    srv = row.get("server")
    for i in (0, 1):
        j = 1 - i
        gi, gj = games[i][cur], games[j][cur]
        lead = gi - gj
        one_set_from_match = sets[i] == fmt_sets_to_win - 1
        serving_for = (one_set_from_match and gi >= 5 and lead >= 1
                       and srv == i + 1)
        mp = one_set_from_match and set_point(games, cur, i, pts, tb)
        # --- Hypothesis A (point-level) ---
        if mp:
            out["P1_match_point"] = i
            if srv == j + 1:
                out["P1b_match_point_on_return"] = i
            if sets[i] + sets[j] == 2:      # deciding set
                out["P1c_match_point_decider"] = i
        if serving_for and not mp:
            out["P2_serving_for_match"] = i
        # --- Hypothesis B (game/set level) ---
        if sets[i] >= 1:
            if lead >= 2:
                out["B1_set_and_break"] = i
            if lead >= 4:
                out["B2_set_and_double_break"] = i
            if serving_for:
                out["B3_set_and_serving_for_match"] = i
            if abs(lead) <= 1:
                out["B4_set_and_on_serve_CONTROL"] = i
        if sets[i] + sets[j] == 2 and lead >= 2:
            out["P3_break_in_decider"] = i
    return out


if __name__ == "__main__":
    rows = [json.loads(l)
            for l in open(os.path.join(HERE, "data", "tape_wta.jsonl"))]
    usable = [r for r in rows
              if (r.get("match") or {}).get("winner") and (r.get("tape") or [])]
    tally = {}
    for r in usable:
        winner = int(r["match"]["winner"]) - 1        # 0 or 1
        seen = set()
        for row in r["tape"]:
            for st, i in states_for(row).items():
                if (st, i) in seen:
                    continue
                seen.add((st, i))
                d = tally.setdefault(st, [0, 0])
                d[1] += 1
                if i == winner:
                    d[0] += 1
    print(f"STAGE 1 SCREEN — {len(usable)} WTA matches with tape + winner\n")
    print(f"{'state':<34} {'n':>5} {'won':>5} {'win rate':>9} "
          f"{'95% CI':>16}   verdict vs break-even")
    BE = {"P1_match_point": 98.1, "P1b_match_point_on_return": 98.1,
          "P1c_match_point_decider": 98.1, "P2_serving_for_match": 98.1,
          "P3_break_in_decider": 92.5, "B1_set_and_break": 92.5,
          "B2_set_and_double_break": 92.5,
          "B3_set_and_serving_for_match": 98.1,
          "B4_set_and_on_serve_CONTROL": 88.0}
    for st in sorted(tally, key=lambda s: -tally[s][1]):
        k, n = tally[st]
        wr = k / n * 100
        lo, hi = wilson(k, n)
        be = BE.get(st, 95.0)
        if lo * 100 > be:
            v = f"PASS (LB {lo*100:.1f} > {be})"
        elif hi * 100 < be:
            v = f"FAIL (UB {hi*100:.1f} < {be})"
        else:
            v = f"inconclusive (vs {be})"
        print(f"{st:<34} {n:>5} {k:>5} {wr:>8.2f}% "
              f"[{lo*100:>5.1f},{hi*100:>5.1f}]   {v}")
