"""Regressions for the Schedule 13D/G parser.

Two format traps this pins, both of which silently return nothing:
  1. The EDGAR daily-index form type is `SCHEDULE 13D`, WITH A SPACE. Splitting
     the row and taking field 0 yields "SCHEDULE" — 98 filings in one day were
     invisible that way.
  2. EDGAR indexes each filing under BOTH filer and subject, so accessions
     repeat and the CIK on a row is not reliably the issuer.

And the distinction that IS the signal: 13D = intent to influence
(Brav, Jiang, Partnoy & Thomas 2008), 13G = passive.
"""
import json

import pandas as pd
import pytest

from advisor.research import schedule13 as s13


def _header(subject="Definitive Healthcare Corp.", cik="0001861795",
            filer="ADVENT INTERNATIONAL, L.P.", form="SCHEDULE 13D", pcts=()):
    body = "".join(f"<percentOfClass>{p}</percentOfClass>" for p in pcts)
    return f"""<SEC-DOCUMENT>x.txt : 20260902
<SEC-HEADER>x.hdr.sgml : 20260902
CONFORMED SUBMISSION TYPE:	{form}
FILED AS OF DATE:		20260902

SUBJECT COMPANY:\t

\tCOMPANY DATA:\t
\t\tCOMPANY CONFORMED NAME:\t\t\t{subject}
\t\tCENTRAL INDEX KEY:\t\t\t{cik}

FILED BY:\t\t

\tCOMPANY DATA:\t
\t\tCOMPANY CONFORMED NAME:\t\t\t{filer}
\t\tCENTRAL INDEX KEY:\t\t\t0001034196
</SEC-HEADER>
<TYPE>{form}
{body}
""".encode("latin-1")


# --- document parsing ------------------------------------------------------

def test_reads_subject_not_filer():
    """The subject is the tradable name; the filer is who bought it."""
    d = s13.parse_document(_header(pcts=["58.54"]))
    assert d["subject_cik"] == 1861795
    assert d["subject_name"] == "Definitive Healthcare Corp."
    assert d["filer_name"] == "ADVENT INTERNATIONAL, L.P."


def test_reads_the_structured_stake():
    d = s13.parse_document(_header(pcts=["58.54", "15.88", "5.09"]))
    assert d["pct_of_class"] == 58.54          # the group's largest
    assert d["n_reporting_persons"] == 3


def test_distinguishes_13d_from_13g():
    assert s13.parse_document(_header(form="SCHEDULE 13D"))["is_13d"] is True
    assert s13.parse_document(_header(form="SCHEDULE 13G"))["is_13d"] is False


def test_flags_amendments():
    assert s13.parse_document(_header(form="SCHEDULE 13D/A"))["is_amendment"]
    assert not s13.parse_document(_header(form="SCHEDULE 13D"))["is_amendment"]


def test_missing_stake_is_none_not_zero():
    assert s13.parse_document(_header(pcts=()))["pct_of_class"] is None


def test_nonsense_percentages_are_rejected():
    d = s13.parse_document(_header(pcts=["0", "812.5", "7.25"]))
    assert d["pct_of_class"] == 7.25


def test_unparseable_document_returns_none():
    assert s13.parse_document(b"not a filing") is None
    assert s13.parse_document(b"") is None


# --- index sweep -----------------------------------------------------------

IDX = """Form Type   Company Name                            CIK
      Date Filed  File Name
-----------------------------------------------------------------------
SCHEDULE 13D     ADVENT INTERNATIONAL, L.P.        1034196     20260902    edgar/data/1/a.txt
SCHEDULE 13D     Definitive Healthcare Corp.       1861795     20260902    edgar/data/1/a.txt
SCHEDULE 13G     BlueArc Capital Management, LLC   2097963     20260902    edgar/data/2/b.txt
4                SOME INSIDER                       999999     20260902    edgar/data/3/c.txt
"""


def test_sweep_matches_the_spaced_form_type(monkeypatch):
    """`SCHEDULE 13D`.split()[0] == 'SCHEDULE' — the bug this guards."""
    monkeypatch.setattr("advisor.research.edgar._get",
                        lambda url, timeout=60: IDX.encode("latin-1"))
    from datetime import datetime
    rows = s13.sweep(datetime(2026, 9, 2, tzinfo=s13.ET_TZ))
    assert [r["form"] for r in rows] == ["SCHEDULE 13D", "SCHEDULE 13G"]


def test_sweep_dedupes_filer_and_subject_rows(monkeypatch):
    monkeypatch.setattr("advisor.research.edgar._get",
                        lambda url, timeout=60: IDX.encode("latin-1"))
    from datetime import datetime
    rows = s13.sweep(datetime(2026, 9, 2, tzinfo=s13.ET_TZ))
    assert len(rows) == 2                     # a.txt appears twice in the index
    assert len({r["path"] for r in rows}) == 2


# --- signal construction ---------------------------------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    d = tmp_path / "schedule13"
    d.mkdir()
    monkeypatch.setattr(s13, "STORE", d)
    monkeypatch.setattr(s13, "OUT", tmp_path / "schedule13_signals.json")
    from datetime import datetime
    today = datetime.now(s13.ET_TZ).date().isoformat()

    def _write(rows):
        for r in rows:
            r.setdefault("filed", today)
        pd.DataFrame(rows).to_parquet(d / f"dt={today}.parquet", index=False)
    return _write


def _row(ticker, pct, form="SCHEDULE 13D", filer="Some Fund"):
    return {"ticker": ticker, "pct_of_class": pct, "form": form,
            "is_13d": "13D" in form, "is_amendment": form.endswith("/A"),
            "subject_cik": 1, "subject_name": ticker, "filer_name": filer,
            "n_reporting_persons": 1, "path": f"p/{ticker}/{form}"}


def test_new_13d_outranks_amendment_and_passive(store):
    store([_row("PASS", 30.0, "SCHEDULE 13G"),
           _row("AMEND", 20.0, "SCHEDULE 13D/A"),
           _row("NEW", 6.0, "SCHEDULE 13D")])
    order = [s["ticker"] for s in s13.signals()["stakes"]]
    assert order == ["NEW", "AMEND", "PASS"]


def test_sub_threshold_stakes_are_dropped(store):
    store([_row("TINY", 1.2), _row("REAL", 7.5)])
    assert [s["ticker"] for s in s13.signals()["stakes"]] == ["REAL"]


def test_control_block_is_flagged_not_dropped(store):
    """A sponsor at 58% is not the activist event, but it is still a fact."""
    store([_row("SPONSOR", 58.54)])
    s = s13.signals()["stakes"][0]
    assert s["control_block"] is True
    assert s["pct_of_class"] == 58.54


def test_multiple_filings_on_one_name_collapse_to_the_largest(store):
    store([_row("AAA", 6.0), _row("AAA", 11.0, filer="Other Fund")])
    s = s13.signals()["stakes"][0]
    assert s["pct_of_class"] == 11.0 and s["n_filings"] == 2


def test_empty_store_is_reported_not_crashed(store):
    store([])
    assert s13.signals()["stakes"] == []
