from pathlib import Path


def test_terminal_toast_icons_are_valid_for_streamlit_runtime():
    """Regression: an invalid success icon raised after queuing a real run."""
    import ast
    from streamlit.string_util import validate_icon_or_emoji

    source = Path(__file__).resolve().parents[1] / "terminal.py"
    tree = ast.parse(source.read_text())
    icons = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "toast":
            for kw in node.keywords:
                if kw.arg == "icon" and isinstance(kw.value, ast.Constant):
                    icons.append(kw.value.value)
    assert icons
    for icon in icons:
        assert validate_icon_or_emoji(icon) == icon


def test_terminal_reads_only_fields_the_cluster_producer_emits():
    """Regression 2026-09-04: `KeyError: 'n_sells'` took down the IDEA tab.

    The insider signal moved from yfinance text-matching to parsed Form 4
    documents. The new producer excludes sales entirely (Lakonishok & Lee
    2001: the buy side is the informative one), so it emits no sell count —
    but the terminal still subscripted `c['n_sells']`, and a producer schema
    change crashed the whole tab.

    This pins the contract in both directions: every field the renderer
    subscripts must be one the producer actually emits.
    """
    import ast

    from advisor.research import form4

    source = Path(__file__).resolve().parents[1] / "terminal.py"
    tree = ast.parse(source.read_text())

    # Scope to the cluster loop itself — `c` is a common loop name and a
    # file-wide scan picks up unrelated dicts.
    loop = None
    for node in ast.walk(tree):
        if isinstance(node, ast.For) and isinstance(node.target, ast.Name) \
                and node.target.id == "c" \
                and "clusters" in ast.dump(node.iter):
            loop = node
            break
    assert loop is not None, "insider-cluster render loop not found"

    hard, soft = set(), set()
    for node in ast.walk(loop):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
                and node.value.id == "c" and isinstance(node.slice, ast.Constant) \
                and isinstance(node.slice.value, str):
            hard.add(node.slice.value)
        # c.get("field", ...) — counted too, so the check is not vacuous once
        # the renderer has been made defensive
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "get" \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "c" and node.args \
                and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            soft.add(node.args[0].value)
    assert soft, "renderer reads no cluster fields — test would be vacuous"

    produced = {
        "ticker", "tier", "n_buys", "n_insiders", "net_value_usd",
        "opportunistic_value_usd", "n_officer_buys", "n_director_buys",
        "plan_10b5_1_share", "top_title", "buyers_seen",
    }
    # EVERY field the renderer touches must be one the producer emits —
    # whether it is subscripted (crashes) or .get() (silently blank).
    unknown = (hard | soft) - produced
    assert not unknown, (
        f"terminal reads cluster fields the producer does not emit: "
        f"{sorted(unknown)}")
    assert not hard, (
        f"cluster fields must be read with .get() so a producer schema change "
        f"degrades the panel instead of taking the tab down; hard: {sorted(hard)}")

    # and the producer really does emit them — caught by construction, not by
    # a list that can drift out of date
    assert form4.BUY_CODES == {"P"}


def test_cluster_record_matches_the_documented_contract(tmp_path, monkeypatch):
    """Drive the real producer and check the renderer's fields exist."""
    import pandas as pd
    from datetime import datetime

    from advisor.research import form4

    d = tmp_path / "form4_parsed"
    d.mkdir()
    monkeypatch.setattr(form4, "PARSED_DIR", d)
    monkeypatch.setattr(form4, "CLUSTERS", tmp_path / "clusters.json")
    today = datetime.now(form4.ET_TZ).date().isoformat()
    pd.DataFrame([{
        "ticker": "AAA", "insider": "One", "code": "P", "value_usd": 500_000.0,
        "shares": 1000.0, "price": 500.0, "is_officer": True,
        "is_director": False, "is_ten_pct": False, "officer_title": "CEO",
        "derivative": False, "tx_date": today, "plan_10b5_1": False,
        "acquired_disposed": "A", "shares_after": 1.0, "path": "p",
        "filed": today, "issuer_cik": 1, "period": today}]).to_parquet(
            d / f"dt={today}.parquet", index=False)

    c = form4.detect_clusters()["clusters"][0]
    for field in ("ticker", "tier", "n_buys", "n_insiders", "net_value_usd",
                  "opportunistic_value_usd", "n_officer_buys",
                  "plan_10b5_1_share", "top_title", "buyers_seen"):
        assert field in c, f"producer stopped emitting {field}"
    assert "n_sells" not in c        # sales are excluded by design


def test_nan_officer_title_does_not_render_as_the_string_nan():
    """pandas NaN is TRUTHY, so `c.get("top_title") or fallback` never fired
    and the panel printed a literal "nan" for buyers with no officer title."""
    import ast

    source = Path(__file__).resolve().parents[1] / "terminal.py"
    tree = ast.parse(source.read_text())
    loop = next(n for n in ast.walk(tree)
                if isinstance(n, ast.For) and isinstance(n.target, ast.Name)
                and n.target.id == "c" and "clusters" in ast.dump(n.iter))
    src = ast.unparse(loop)
    # a bare `c.get("top_title") or ...` is the bug; there must be an explicit
    # NaN guard between the read and the fallback
    assert 'title != title' in src or 'isna' in src, (
        "top_title must be NaN-guarded before the `or` fallback")
