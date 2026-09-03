"""Machine-readable data-source inventory, health and commercial-use status."""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from advisor.research.datastore import RESEARCH_DIR, current_meta

ET = ZoneInfo("America/New_York")


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def assess(now: datetime | None = None) -> dict:
    now = now or datetime.now(ET)
    manifest = _json(RESEARCH_DIR / "_meta" / "ingest_manifest.json")
    datasets = manifest.get("datasets") or {}
    fred = _json(RESEARCH_DIR / "macro" / "fred_latest.json")
    quotes = _json(RESEARCH_DIR.parent / "quotes" / "latest.json")
    try:
        panel = current_meta()
    except Exception:
        panel = {}
    sources = {
        "price_history": {
            "provider": panel.get("provider", "Yahoo Finance via yfinance"),
            "healthy": bool((panel.get("quality") or {}).get("ok")),
            "role": "research fallback", "commercial_redistribution": False,
            "detail": f"panel build {panel.get('build_id', 'missing')}",
        },
        "sec_edgar": {
            "provider": "SEC EDGAR data.sec.gov", "healthy": bool(
                (datasets.get("form4") or {}).get("ok")
                or (datasets.get("edgar_facts") or {}).get("ok")),
            "role": "primary filing evidence", "commercial_redistribution": None,
            "detail": "Company Facts and Form 4; issuer facts remain filing-time stamped",
        },
        "fred_macro": {
            "provider": "Federal Reserve Bank of St. Louis FRED",
            "healthy": bool(fred.get("ok")), "role": "official macro context",
            "commercial_redistribution": None,
            "detail": f"{fred.get('fresh_series', 0)}/{fred.get('required_series', 5)} fresh series",
        },
        "fundamental_estimates": {
            "provider": "Yahoo Finance snapshot endpoints",
            "healthy": bool((datasets.get("info") or {}).get("ok")
                            and (datasets.get("estimates") or {}).get("ok")),
            "role": "prospective candidate generation", "commercial_redistribution": False,
            "detail": "snapshot-only; never treated as point-in-time history before observation",
        },
        "live_quotes": {
            "provider": "Interactive Brokers TWS with delayed fallback",
            "healthy": False, "role": "display and exit monitoring",
            "commercial_redistribution": False,
            "detail": "entitlement and display rights are account-specific",
        },
    }
    try:
        quote_age = (now - datetime.fromisoformat(quotes["as_of"])).total_seconds()
        sources["live_quotes"]["healthy"] = quote_age <= 120
        sources["live_quotes"]["detail"] = (
            f"quote daemon {quote_age:.0f}s old; IBKR connected={bool(quotes.get('ib_connected'))}; "
            "entitlement and display rights are account-specific")
    except Exception:
        sources["live_quotes"]["detail"] = "quote artifact missing or stale"
    configured = {
        "massive_polygon_api": bool(os.environ.get("POLYGON_API_KEY")),
        "fred_api_key": bool(os.environ.get("FRED_API_KEY")),
    }
    critical = ("price_history", "sec_edgar", "fred_macro", "live_quotes")
    healthy_critical = sum(bool(sources[k]["healthy"]) for k in critical)
    return {"schema_version": 1, "as_of": now.isoformat(), "sources": sources,
            "configured_optional_providers": configured,
            "healthy_critical": healthy_critical, "critical_count": len(critical),
            "ok": healthy_critical == len(critical),
            "commercially_clear": all(v["commercial_redistribution"] is True
                                      for v in sources.values())}


def main() -> int:
    report = assess()
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
