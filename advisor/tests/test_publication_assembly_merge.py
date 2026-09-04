"""Regression: a red-team amendment must PATCH a view, not replace blocks.

2026-09-04, a real publication failure. The red-team amended DELL's sizing to
cut quantity 20 -> 15 and sent only what it changed:

    {"quantity": 15, "capital_usd": 7710, "max_loss_usd": 484.3}

`view.update(amended)` is SHALLOW, so `view["sizing"]` was replaced wholesale
and portfolio_capital_after_usd / instrument_type / slippage_bps / method were
destroyed. The production contract then rejected the assembled brief and the
day published nothing — after $4.19 of model time, over a merge bug rather
than anything wrong with the idea.

Only reachable on the `amend` path, which is rare. That is why it survived.
"""
import copy

import pytest

from advisor.publication_assembly import _deep_update


FULL_SIZING = {
    "capital_usd": 10280, "max_loss_usd": 645.7,
    "portfolio_capital_after_usd": 10280, "quantity": 20,
    "instrument_type": "equity", "slippage_bps": 25,
    "method": "vol-targeted shares",
}
REQUIRED = ("capital_usd", "max_loss_usd", "portfolio_capital_after_usd",
            "quantity", "instrument_type", "slippage_bps", "method")


def test_partial_sizing_amendment_preserves_the_contract():
    """The exact 2026-09-04 failure."""
    view = {"yf_ticker": "DELL", "sizing": copy.deepcopy(FULL_SIZING)}
    _deep_update(view, {"sizing": {"quantity": 15, "capital_usd": 7710,
                                   "max_loss_usd": 484.3}})
    assert all(k in view["sizing"] for k in REQUIRED), view["sizing"]
    assert view["sizing"]["quantity"] == 15            # the amendment applied
    assert view["sizing"]["capital_usd"] == 7710
    assert view["sizing"]["slippage_bps"] == 25        # untouched key survived
    assert view["sizing"]["method"] == "vol-targeted shares"


def test_scalar_fields_are_replaced():
    view = {"direction": "long", "p_win": 0.6}
    _deep_update(view, {"p_win": 0.7})
    assert view == {"direction": "long", "p_win": 0.7}


def test_new_keys_are_added():
    view = {"a": 1}
    _deep_update(view, {"b": 2})
    assert view == {"a": 1, "b": 2}


def test_nested_blocks_merge_independently():
    view = {"sizing": {"quantity": 20, "method": "m"},
            "probability_basis": {"type": "analyst_judgment", "sample_n": 0}}
    _deep_update(view, {"probability_basis": {"sample_n": 41}})
    assert view["sizing"] == {"quantity": 20, "method": "m"}
    assert view["probability_basis"] == {"type": "analyst_judgment",
                                         "sample_n": 41}


def test_merge_recurses_more_than_one_level():
    view = {"a": {"b": {"c": 1, "d": 2}}}
    _deep_update(view, {"a": {"b": {"c": 9}}})
    assert view["a"]["b"] == {"c": 9, "d": 2}


def test_patch_does_not_alias_the_source():
    """A later mutation of the verdict must not reach into the view."""
    patch = {"sizing": {"quantity": 15}}
    view = {"sizing": copy.deepcopy(FULL_SIZING)}
    _deep_update(view, patch)
    patch["sizing"]["quantity"] = 999
    assert view["sizing"]["quantity"] == 15


def test_a_dict_replacing_a_scalar_still_works():
    view = {"sizing": None}
    _deep_update(view, {"sizing": {"quantity": 5}})
    assert view["sizing"] == {"quantity": 5}


# --- derived exposure -------------------------------------------------------

def test_capital_amendment_is_detected():
    from advisor.publication_assembly import _capital_amended
    originals = [{"decision_key": "k1", "sizing": {"capital_usd": 10280}}]
    unchanged = [{"decision_key": "k1", "sizing": {"capital_usd": 10280}}]
    amended = [{"decision_key": "k1", "sizing": {"capital_usd": 7710}}]
    assert _capital_amended(originals, unchanged) is False
    assert _capital_amended(originals, amended) is True


def test_derived_exposure_is_restated_across_every_view(monkeypatch):
    """portfolio_capital_after_usd is existing + deployed. It is DERIVED, so
    after code changes capital_usd it must be recomputed, not inherited from
    the draft — the second half of the 2026-09-04 publication failure."""
    import advisor.brief_check as bc
    from advisor.publication_assembly import _recompute_after_capital

    monkeypatch.setattr(bc, "_existing_open_capital", lambda keys: 1000.0)
    views = [
        {"decision_key": "k1", "sizing": {"capital_usd": 7710,
                                          "portfolio_capital_after_usd": 10280}},
        {"decision_key": "k2", "sizing": {"capital_usd": 500,
                                          "portfolio_capital_after_usd": 10280}},
    ]
    _recompute_after_capital(views)
    for v in views:                       # identical across views, as required
        assert v["sizing"]["portfolio_capital_after_usd"] == 9210.0


def test_recompute_never_blocks_publication(monkeypatch):
    import advisor.brief_check as bc
    from advisor.publication_assembly import _recompute_after_capital

    def boom(keys):
        raise RuntimeError("journal unreadable")
    monkeypatch.setattr(bc, "_existing_open_capital", boom)
    views = [{"decision_key": "k", "sizing": {"capital_usd": 1,
                                              "portfolio_capital_after_usd": 42}}]
    _recompute_after_capital(views)       # must not raise
    assert views[0]["sizing"]["portfolio_capital_after_usd"] == 42
