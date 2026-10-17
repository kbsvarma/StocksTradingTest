"""
Unit tests for the quote/inventory/risk brain. No network.

    python kalshi_mm/test_quoter.py
"""

from inventory import Portfolio, RiskLimits
from quoter import OrderIntent, QuoteConfig, compute_quotes, diff_orders


def fresh(**limit_kw) -> Portfolio:
    return Portfolio(limits=RiskLimits(**limit_kw))


def test_basic_two_sided():
    pf = fresh()
    cfg = QuoteConfig(quote_size=1, edge_cents=2, min_spread_cents=3)
    q = compute_quotes("MKT", yes_bid=40, yes_ask=50, fair_cents=45, portfolio=pf, cfg=cfg)
    assert len(q) == 2, q
    bid = [o for o in q if o.side == "yes"][0]
    ask = [o for o in q if o.side == "no"][0]
    # fair 45, edge 2 -> bid 43, ask 47 -> buy NO @ 53
    assert bid.price_cents == 43, bid
    assert ask.price_cents == 53, ask
    assert bid.action == "buy" and ask.action == "buy"


def test_tight_spread_no_quote():
    pf = fresh()
    cfg = QuoteConfig(min_spread_cents=3)
    q = compute_quotes("MKT", yes_bid=49, yes_ask=50, fair_cents=49.5, portfolio=pf, cfg=cfg)
    assert q == [], "should not quote when spread < min_spread_cents"


def test_no_book_no_quote():
    pf = fresh()
    q = compute_quotes("MKT", None, None, None, pf, QuoteConfig())
    assert q == []


def test_inventory_skew_shifts_down_when_long():
    pf = fresh()
    pf.state("MKT").net = 3  # long 3 yes
    cfg = QuoteConfig(edge_cents=2, inventory_skew_cents=1, min_spread_cents=3)
    q = compute_quotes("MKT", 40, 50, 45, pf, cfg)
    bid = [o for o in q if o.side == "yes"][0]
    # fair_adj = 45 - 3 = 42 -> bid 40, ask 44 -> NO @ 56
    assert bid.price_cents == 40, bid


def test_position_cap_blocks_bid():
    pf = fresh(max_position_per_market=3)
    pf.state("MKT").net = 3  # already at long cap
    q = compute_quotes("MKT", 40, 50, 45, pf, QuoteConfig(quote_size=1, min_spread_cents=3))
    # can't add long -> no yes bid; should only offer the NO (short) side
    sides = sorted(o.side for o in q)
    assert "yes" not in sides, q
    assert "no" in sides, q


def test_position_cap_blocks_both_at_full_long_and_exposure():
    pf = fresh(max_position_per_market=3, max_total_exposure_usd=0.0)
    pf.state("MKT").net = 0
    q = compute_quotes("MKT", 40, 50, 45, pf, QuoteConfig())
    assert q == [], "zero exposure budget should block both sides"


def test_halt_blocks_everything():
    pf = fresh(max_daily_loss_usd=10)
    pf.markets.setdefault("MKT", pf.state("MKT")).realized_pnl_cents = -1500  # -$15
    q = compute_quotes("MKT", 40, 50, 45, pf, QuoteConfig())
    assert pf.check_halt() is True
    assert q == []


def test_apply_fill_updates_net():
    pf = fresh()
    pf.apply_fill("MKT", "yes", 2, 43)   # buy 2 yes
    assert pf.state("MKT").net == 2
    pf.apply_fill("MKT", "no", 5, 53)    # buy 5 no -> net 2-5 = -3
    assert pf.state("MKT").net == -3


def test_diff_orders_keeps_identical():
    desired = [OrderIntent("M", "yes", "buy", 43, 1), OrderIntent("M", "no", "buy", 53, 1)]
    resting = [OrderIntent("M", "yes", "buy", 43, 1)]  # bid already there
    to_place, to_cancel = diff_orders(desired, resting)
    assert [o.side for o in to_place] == ["no"], to_place
    assert to_cancel == [], to_cancel


def test_diff_orders_cancels_stale():
    desired = [OrderIntent("M", "yes", "buy", 44, 1)]
    resting = [OrderIntent("M", "yes", "buy", 43, 1)]  # wrong price -> replace
    to_place, to_cancel = diff_orders(desired, resting)
    assert len(to_place) == 1 and to_place[0].price_cents == 44
    assert len(to_cancel) == 1 and to_cancel[0].price_cents == 43


def test_never_crosses_the_market():
    # fair pinned near the ask; bid must stay below best ask, ask above best bid
    pf = fresh()
    q = compute_quotes("MKT", 40, 50, 49, pf, QuoteConfig(edge_cents=0, min_spread_cents=3))
    for o in q:
        if o.side == "yes":
            assert o.price_cents <= 49  # < yes_ask
        else:
            # NO price = 100 - yes_ask_quote; yes_ask_quote >= yes_bid+1 = 41
            assert o.price_cents <= 59


def run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} passed")


if __name__ == "__main__":
    run()
