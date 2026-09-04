"""Regressions for the Form 4 document parser.

The engine indexed 1,706 Form 4 filings and opened none of them, then asked
yfinance which way the insiders traded. These tests pin the transaction-level
facts that only the document contains, and the two literature-driven rules
built on them:

  Cohen, Malloy & Pomorski (2012) — routine vs opportunistic; routine trades
  carry essentially no information, so pooling dilutes the signal.
  Lakonishok & Lee (2001) — the BUY side is the informative one.
"""
import pandas as pd
import pytest

from advisor.research import form4


def _doc(txs, name="Doe Jane", officer=True, director=False,
         title="CFO", symbol="ACME", footnote=""):
    rows = "".join(f"""
      <nonDerivativeTransaction>
        <securityTitle><value>Common Stock</value></securityTitle>
        <transactionDate><value>{d}</value></transactionDate>
        <transactionCoding><transactionCode>{c}</transactionCode></transactionCoding>
        <transactionAmounts>
          <transactionShares><value>{s}</value></transactionShares>
          <transactionPricePerShare><value>{p}</value></transactionPricePerShare>
          <transactionAcquiredDisposedCode><value>{ad}</value></transactionAcquiredDisposedCode>
        </transactionAmounts>
        <postTransactionAmounts>
          <sharesOwnedFollowingTransaction><value>{after}</value></sharesOwnedFollowingTransaction>
        </postTransactionAmounts>
      </nonDerivativeTransaction>""" for c, d, s, p, ad, after in txs)
    return f"""<?xml version="1.0"?>
<ownershipDocument>
  <periodOfReport>2026-08-01</periodOfReport>
  <issuer><issuerCik>0000012345</issuerCik>
    <issuerTradingSymbol>{symbol}</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>{name}</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship>
      <isDirector>{int(director)}</isDirector>
      <isOfficer>{int(officer)}</isOfficer>
      <isTenPercentOwner>0</isTenPercentOwner>
      <officerTitle>{title}</officerTitle>
    </reportingOwnerRelationship>
  </reportingOwner>
  <nonDerivativeTable>{rows}</nonDerivativeTable>
  <footnotes><footnote id="F1">{footnote}</footnote></footnotes>
</ownershipDocument>""".encode()


# --- parsing ---------------------------------------------------------------

def test_extracts_the_transaction_the_index_never_had():
    rows = form4.parse_document(
        _doc([("P", "2026-07-28", 1000, 25.5, "A", 5000)]))
    assert len(rows) == 1
    r = rows[0]
    assert r["code"] == "P"
    assert r["shares"] == 1000 and r["price"] == 25.5
    assert r["value_usd"] == pytest.approx(25500.0)
    assert r["acquired_disposed"] == "A"
    assert r["shares_after"] == 5000
    assert r["tx_date"] == "2026-07-28"


def test_extracts_insider_role_and_title():
    r = form4.parse_document(
        _doc([("P", "2026-07-28", 10, 1.0, "A", 10)],
             officer=True, director=True, title="CEO"))[0]
    assert r["is_officer"] is True and r["is_director"] is True
    assert r["officer_title"] == "CEO"
    assert r["is_ten_pct"] is False


def test_flags_a_10b5_1_plan_from_the_footnote():
    plan = form4.parse_document(
        _doc([("S", "2026-07-28", 10, 1.0, "D", 10)],
             footnote="Sold pursuant to a Rule 10b5-1 trading plan."))[0]
    assert plan["plan_10b5_1"] is True
    bare = form4.parse_document(_doc([("S", "2026-07-28", 10, 1.0, "D", 10)]))[0]
    assert bare["plan_10b5_1"] is False


def test_separates_the_compensation_chain():
    """M exercise -> F tax withholding -> S sale is not three sell signals."""
    rows = form4.parse_document(_doc([
        ("M", "2026-07-27", 3634, 126.89, "A", 9000),
        ("F", "2026-07-27", 2131, 537.07, "D", 6869),
        ("S", "2026-07-27", 7888, 537.07, "D", 0)]))
    assert [r["code"] for r in rows] == ["M", "F", "S"]
    assert sum(r["code"] in form4.BUY_CODES for r in rows) == 0


def test_malformed_input_returns_empty_never_raises():
    assert form4.parse_document(b"not xml at all") == []
    assert form4.parse_document(b"<ownershipDocument><broken>") == []
    assert form4.parse_document(b"") == []


def test_missing_price_yields_no_value_not_a_crash():
    r = form4.parse_document(_doc([("A", "2026-07-28", 500, "", "A", 500)]))[0]
    assert r["shares"] == 500
    assert r["value_usd"] is None


# --- clusters --------------------------------------------------------------

@pytest.fixture
def parsed(tmp_path, monkeypatch):
    d = tmp_path / "form4_parsed"
    d.mkdir()
    monkeypatch.setattr(form4, "PARSED_DIR", d)
    monkeypatch.setattr(form4, "CLUSTERS", tmp_path / "insider_clusters.json")
    from datetime import datetime
    today = datetime.now(form4.ET_TZ).date().isoformat()

    def _write(rows):
        pd.DataFrame(rows).to_parquet(d / f"dt={today}.parquet", index=False)
    return _write


def _tx(ticker, insider, code="P", value=200_000.0, officer=True, plan=False,
        derivative=False, date="2026-08-15"):
    return {"ticker": ticker, "insider": insider, "code": code,
            "value_usd": value, "shares": 1000.0, "price": value / 1000.0,
            "is_officer": officer, "is_director": not officer,
            "is_ten_pct": False, "officer_title": "CFO", "derivative": derivative,
            "tx_date": date, "plan_10b5_1": plan, "acquired_disposed": "A",
            "shares_after": 5000.0, "path": f"p/{ticker}/{insider}",
            "filed": date, "issuer_cik": 1, "period": date}


def test_cluster_tier_needs_multiple_distinct_insiders(parsed):
    """One person buying twice is not a cluster — it is a single conviction
    buy, and the two must be labelled differently rather than pooled."""
    parsed([_tx("AAA", "One"), _tx("AAA", "One"),      # same person twice
            _tx("BBB", "One"), _tx("BBB", "Two")])
    tiers = {c["ticker"]: c["tier"] for c in form4.detect_clusters()["clusters"]}
    assert tiers == {"BBB": "cluster", "AAA": "conviction_single"}


def test_sales_are_excluded_entirely(parsed):
    """Lakonishok & Lee: the buy side is the informative one."""
    parsed([_tx("AAA", "One", code="S"), _tx("AAA", "Two", code="S")])
    assert form4.detect_clusters()["clusters"] == []


def test_grants_and_exercises_are_not_purchases(parsed):
    parsed([_tx("AAA", "One", code="A"), _tx("AAA", "Two", code="M"),
            _tx("AAA", "Three", code="F")])
    assert form4.detect_clusters()["clusters"] == []


def test_derivative_transactions_are_excluded(parsed):
    parsed([_tx("AAA", "One", derivative=True),
            _tx("AAA", "Two", derivative=True)])
    assert form4.detect_clusters()["clusters"] == []


def test_small_clusters_are_filtered_by_dollar_floor(parsed):
    parsed([_tx("AAA", "One", value=100.0), _tx("AAA", "Two", value=100.0)])
    assert form4.detect_clusters()["clusters"] == []


def test_cluster_reports_role_split_and_plan_share(parsed):
    parsed([_tx("AAA", "One", officer=True, plan=True),
            _tx("AAA", "Two", officer=False, plan=False)])
    c = form4.detect_clusters()["clusters"][0]
    assert c["n_insiders"] == 2
    assert c["n_officer_buys"] == 1 and c["n_director_buys"] == 1
    assert c["plan_10b5_1_share"] == pytest.approx(0.5)


def test_source_is_recorded_as_sec_primary(parsed):
    parsed([_tx("AAA", "One"), _tx("AAA", "Two")])
    res = form4.detect_clusters()
    assert "form4_parsed" in res["source"]
    assert "yfinance" not in res["source"]


# --- routine classification -----------------------------------------------

def test_routine_classifier_refuses_a_short_history(parsed):
    parsed([_tx("AAA", "One", date="2026-08-01"),
            _tx("AAA", "Two", date="2026-08-15")])
    r = form4.detect_clusters()["routine_classification"]
    assert r["usable"] is False
    assert r["reason"] == "insufficient_history"
    assert r["needs_years"] == form4.ROUTINE_MIN_YEARS


def test_routine_classifier_finds_a_calendar_pattern():
    """Same month, three consecutive years -> routine (Cohen-Malloy-Pomorski)."""
    # span must clear ROUTINE_MIN_YEARS end to end, or the classifier
    # correctly refuses before it ever looks at the calendar pattern
    rows = [_tx("AAA", "Clockwork", date=f"{y}-03-15") for y in (2022, 2023, 2024)]
    rows += [_tx("AAA", "Adhoc", date="2022-01-05"),
             _tx("AAA", "Adhoc", date="2023-07-22"),
             _tx("AAA", "Adhoc", date="2025-11-30")]
    r = form4.classify_routine(pd.DataFrame(rows))
    assert r["usable"] is True
    assert r["routine"] == ["Clockwork"]


# --- conviction tier and recency ------------------------------------------

def test_single_large_officer_buy_qualifies_as_conviction(parsed):
    """Clusters are rare (0 in a 30d universe sweep); a CEO buying $900k is
    still the informative case Lakonishok & Lee describe."""
    parsed([_tx("AAA", "Solo", value=900_000.0, officer=True)])
    c = form4.detect_clusters()["clusters"]
    assert len(c) == 1
    assert c[0]["tier"] == "conviction_single"
    assert c[0]["n_insiders"] == 1


def test_small_single_buy_does_not_qualify(parsed):
    parsed([_tx("AAA", "Solo", value=2_000.0, officer=True)])
    assert form4.detect_clusters()["clusters"] == []


def test_non_insider_filer_does_not_qualify(parsed):
    """A 10% holder that is neither officer nor director (e.g. an asset
    manager) is not the signal this generator is about."""
    row = _tx("AAA", "Some Asset Mgmt", value=900_000.0, officer=False)
    row["is_director"] = False
    parsed([row])
    assert form4.detect_clusters()["clusters"] == []


def test_clusters_outrank_single_conviction_buys(parsed):
    parsed([_tx("SOLO", "One", value=5_000_000.0, officer=True),
            _tx("CLUS", "One", value=80_000.0), _tx("CLUS", "Two", value=80_000.0)])
    res = form4.detect_clusters()["clusters"]
    assert [c["ticker"] for c in res] == ["CLUS", "SOLO"]
    assert res[0]["tier"] == "cluster"


def test_stale_transactions_are_excluded_by_transaction_date(parsed):
    """A 2019 purchase filed late is not a 2026 signal — this really happened
    (SYK rows dated 2019-2022 arrived in a 2026 partition)."""
    parsed([_tx("AAA", "Late", value=900_000.0, date="2019-06-26")])
    res = form4.detect_clusters()
    assert res["clusters"] == []
    assert res["n_stale_tx_excluded"] == 1


def test_empty_partition_is_reported_not_crashed(parsed):
    """A column-less frame must not raise AttributeError on df.code."""
    parsed([])
    res = form4.detect_clusters()
    assert res["clusters"] == []
    assert "no usable rows" in res["reason"]
