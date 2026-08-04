"""AUDITABLE event-deduped replay of the deployed sports_bot rule.

Written 2026-08-03 after the Codex audit (mirror-market double count).
Reproduces the corrected numbers in REPORT.md §Corrected. Run:
    python3 replay_dedup.py

Deployed rule (sports_bot.py): at a 1-min close, favorite MID in
[0.97,0.99), spread <= 2c, prior H-1 closes same side with fav mid >=
0.97 (NO upper bound on prior closes, NO spread gate on prior closes),
entry taker at ask (fill requires ask <= 0.99), one entry per EVENT
(first trigger across the two mirror markets), hold to settlement,
fee = 0.07*p*(1-p).

Variants ("strict_band", "strict_spread", "ask995") quantify how
sensitive the count is to rule details other auditors may have assumed.
"""
import json, glob, os

HERE = os.path.dirname(os.path.abspath(__file__))


def fee(p):
    return 0.07 * p * (1 - p)


def fav_of(b, a):
    m = (b + a) / 2
    return ("yes", b, a, m) if m > 0.5 else ("no", 1 - a, 1 - b, 1 - m)


def load_events(path):
    events = {}
    for line in open(path):
        r = json.loads(line)
        events.setdefault(r["ticker"].rsplit("-", 1)[0], []).append(r)
    return events


def seq_of(r):
    seq = []
    for c in r["candles"]:
        try:
            b = float(c["yes_bid"]["close_dollars"])
            a = float(c["yes_ask"]["close_dollars"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= b <= a <= 1:
            seq.append((c["end_period_ts"],) + fav_of(b, a))
    return seq


def replay(path, hold, lo=0.97, hi=0.99, spr=0.02, max_entry=0.99,
           strict_band=False, strict_spread=False):
    trades = []
    for ev, mkts in load_events(path).items():
        best = None
        for r in mkts:
            seq = seq_of(r)
            if len(seq) < hold:
                continue
            for i in range(hold - 1, len(seq)):
                ts, cs, cb, ca, cm = seq[i]
                if not (lo <= cm < hi and ca - cb <= spr and ca <= max_entry):
                    continue
                window = seq[i - hold + 1:i]
                if not all(s[1] == cs and s[4] >= lo for s in window):
                    continue
                if strict_band and not all(s[4] < hi for s in window):
                    continue
                if strict_spread and not all(s[3] - s[2] <= spr
                                             for s in window):
                    continue
                won = (cs == "yes") == (r["result"] == "yes")
                if best is None or ts < best[0]:
                    best = (ts, ca, won)
                break
        if best:
            trades.append(best)
    trades.sort()
    n = len(trades)
    if not n:
        return None
    W = sum(1 for t in trades if t[2])
    net = sum(((1 - e) - fee(e) if w else -e - fee(e))
              for _, e, w in trades) / n * 100
    cut = trades[int(n * 2 / 3)][0]
    oos = [t for t in trades if t[0] >= cut]
    onet = (sum(((1 - e) - fee(e) if w else -e - fee(e))
                for _, e, w in oos) / len(oos) * 100) if oos else float("nan")
    return n, W, n - W, net, onet


if __name__ == "__main__":
    FAMS = [("KXWTAMATCH", 10), ("KXWNBAGAME", 5), ("KXWCGAME", 5),
            ("KXATPMATCH", 10),
            ("KXITFMATCH", 10), ("KXITFWMATCH", 10),
            ("KXATPCHALLENGERMATCH", 10), ("KXWTACHALLENGERMATCH", 10)]
    print(f"{'family':>22} {'variant':>14} {'n':>4} {'W-L':>8} "
          f"{'net c/ct':>9} {'OOS c/ct':>9}")
    for fam, hold in FAMS:
        path = os.path.join(HERE, "data", f"games_{fam}.jsonl")
        if not os.path.exists(path):
            continue
        for label, kw in [
                ("deployed", {}),
                ("strict_band", {"strict_band": True}),
                ("strict_both", {"strict_band": True, "strict_spread": True}),
                ("ask<=0.995", {"max_entry": 0.995})]:
            r = replay(path, hold, **kw)
            if r:
                n, W, L, net, onet = r
                print(f"{fam:>22} {label:>14} {n:>4} {W:>4}-{L:<3} "
                      f"{net:>+8.2f} {onet:>+8.2f}")
