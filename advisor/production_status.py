"""Deterministic readiness/trust report for the Advisor Terminal.

This is intentionally independent of the language-model pipeline.  It gives
operators and UI readers one fail-closed answer about data, publication,
model, journal and release state without making any network calls.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from advisor.journal import integrity_report
from advisor.brief_check import validate as validate_brief
from advisor.research.datastore import current_build_dir, current_meta
from advisor.release_integrity import tree_digest
from advisor.actionability import is_actionable
from advisor.source_health import assess as assess_sources
from advisor.suggestion_policy import last_complete_session

ET = ZoneInfo("America/New_York")
REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "advisor" / "data"
RUNTIME_SERVICES = ("advisor-terminal.service", "advisor-quoted.service",
                    "advisor-exitwatch.service", "advisor-intelligence.timer")


def _check_dependencies() -> tuple[str, str]:
    lock = REPO / "advisor" / "requirements.lock"
    try:
        expected = {}
        for line in lock.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                name, version = line.split("==", 1)
                expected[name.lower()] = version
        drift = []
        for name, version in expected.items():
            try:
                actual = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                actual = "missing"
            if actual != version:
                drift.append(f"{name} {actual}!={version}")
        if drift:
            return "block", "dependency drift: " + ", ".join(drift[:4])
        return "pass", f"{len(expected)} locked runtime dependencies verified"
    except Exception as exc:
        return "block", f"dependency lock check failed: {type(exc).__name__}"


def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _check_runtime_services() -> tuple[str, str]:
    if sys.platform != "linux":
        return "warn", "systemd runtime health is assessed only on the Linux host"
    try:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", *RUNTIME_SERVICES],
            capture_output=True, text=True, timeout=5)
        states = result.stdout.splitlines()
        bad = [f"{unit}={states[i] if i < len(states) else 'unknown'}"
               for i, unit in enumerate(RUNTIME_SERVICES)
               if i >= len(states) or states[i] != "active"]
        if bad:
            return "block", "required service failure: " + ", ".join(bad)
        timers=[unit for unit in RUNTIME_SERVICES if unit.endswith('.timer')]
        enabled=subprocess.run(["systemctl", "--user", "is-enabled", *timers],
                               capture_output=True,text=True,timeout=5).stdout.splitlines()
        disabled=[unit for i,unit in enumerate(timers)
                  if i>=len(enabled) or enabled[i] not in {'enabled','static'}]
        if disabled:
            return "block", "required timer will not survive restart: " + ", ".join(disabled)
        return "pass", f"{len(RUNTIME_SERVICES)} required services active"
    except Exception as exc:
        return "block", f"runtime service health unavailable: {type(exc).__name__}"


def _check_execution_isolation() -> tuple[str, str]:
    try:
        config = yaml.safe_load((REPO / "advisor" / "config_advisor.yaml").read_text()) or {}
        config_enabled = (config.get("execution") or {}).get("enabled") is True
        env_enabled = os.environ.get("ADVISOR_EXECUTION_ENABLED") == "1"
        if config_enabled or env_enabled:
            return "block", "live execution capability is enabled in the research runtime"
        return "pass", "live execution disabled by configuration and runtime environment"
    except Exception as exc:
        return "block", f"execution isolation cannot be verified: {type(exc).__name__}"


def _required_market_session(now: datetime) -> str:
    """One exchange-calendar definition for every research freshness gate."""
    return last_complete_session(now)


def _check_panel(now: datetime) -> tuple[str, str]:
    try:
        meta = current_meta()
        age_h = (now.timestamp() - float(meta["built_unix"])) / 3600
        required = _required_market_session(now)
        price_bar = str(meta.get("price_bar") or (meta.get("quality") or {}).get("latest_market_date") or "")
        if not meta.get("quality", {}).get("ok"):
            return "block", "current price panel failed its quality gate"
        build = current_build_dir()
        for name, expected in (meta.get("sha256") or {}).items():
            got = hashlib.sha256((build / name).read_bytes()).hexdigest()
            if got != expected:
                return "block", f"price panel integrity mismatch: {name}"
        if age_h < -0.1:
            return "block", "price panel build timestamp is in the future"
        if price_bar != required:
            return "block", f"price panel ends {price_bar or 'unknown'}; required completed session {required}"
        return "pass", f"price panel verified through completed session {required} (built {age_h:.1f}h ago)"
    except Exception as exc:
        return "block", f"price panel unavailable: {type(exc).__name__}"


def _check_worker(now: datetime) -> tuple[str, str]:
    worker = _json(DATA / "intelligence" / "worker_status.json")
    try:
        observed = datetime.fromisoformat(worker["as_of"])
        if observed.tzinfo is None:
            raise ValueError("timezone missing")
        age_min = (now - observed.astimezone(ET)).total_seconds() / 60
        if age_min < -5:
            return "block", "intelligence worker timestamp is in the future"
        if age_min > 5:
            return "block", f"intelligence reassessment is stale ({age_min:.0f}m old)"
        if worker.get("status") != "healthy":
            return "block", f"intelligence worker is {worker.get('status', 'unknown')}"
        return "pass", f"continuous intelligence reassessment healthy ({age_min:.1f}m old)"
    except Exception as exc:
        return "block", f"intelligence reassessment unavailable: {type(exc).__name__}"


def _check_investigator_runtime() -> tuple[str,str]:
    try:
        from advisor.investigator.runtime import status
        runtime=status()
        if not runtime.get('configured'):
            return 'block','on-demand research model is not configured'
        model=str(runtime.get('model') or '').strip()
        if not model or model=='unconfigured':
            return 'block','on-demand research model identity is unavailable'
        return 'pass',f"on-demand research model configured ({runtime.get('provider')}: {model})"
    except Exception as exc:
        return 'block',f'on-demand research runtime unavailable: {type(exc).__name__}'


def _check_research_acceptance() -> tuple[str,str]:
    try:
        report=_json(DATA/'intelligence'/'research_acceptance.json')
        cases=report.get('cases')
        errors=report.get('critical_errors')
        accuracy=report.get('material_claim_accuracy')
        reviewer=report.get('independent_reviewer')
        if (type(cases) is int and cases>=40 and errors==0 and
                isinstance(accuracy,(int,float)) and not isinstance(accuracy,bool) and accuracy>=.95 and reviewer):
            return 'pass',f'independent research acceptance passed ({cases} cases, {accuracy:.1%} material-claim accuracy)'
        observed=cases if type(cases) is int and cases>=0 else 0
        return 'warn',f'independent research acceptance incomplete ({observed}/40 cases)'
    except Exception as exc:
        return 'warn',f'independent research acceptance unreadable: {type(exc).__name__}'


def _check_ranking_evidence() -> tuple[str,str]:
    try:
        evaluation=_json(DATA/'research'/'ranking_evaluation.json')
        versions=evaluation.get('by_scoring_version') or {}
        if not isinstance(versions,dict):raise ValueError('invalid versions')
        mature=[str(name) for name,row in versions.items()
                if isinstance(row,dict) and type(row.get('n_nonoverlapping_windows')) is int
                and row['n_nonoverlapping_windows']>=30]
        if not mature:
            return 'warn','prospective ranking evidence has fewer than 30 non-overlapping windows'
        approval=_json(DATA/'research'/'ranking_approval.json')
        encoded=json.dumps(evaluation,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
        fingerprint=hashlib.sha256(encoded).hexdigest()
        if (approval.get('approved') is True and approval.get('evaluation_sha256')==fingerprint
                and str(approval.get('scoring_version')) in mature and approval.get('independent_reviewer')):
            return 'pass',f"prospective ranking v{approval['scoring_version']} independently approved"
        return 'warn','mature prospective ranking evidence requires hash-bound independent approval'
    except Exception as exc:
        return 'warn',f'prospective ranking evidence unreadable: {type(exc).__name__}'


def _publication_day(now:datetime,required_session:str) -> str:
    """Use today's attested issue when present, otherwise the session issue."""
    today=now.date().isoformat()
    if (DATA/'context'/today/'brief.json').is_file():return today
    return required_session


def _receipt_matches_market(receipt:dict,signals:dict,required_session:str) -> bool:
    """Require a publication to prove exactly which market snapshot it used."""
    return (receipt.get('schema_version') == 2
            and receipt.get('price_bar') == required_session
            and bool(receipt.get('factor_sheet_sha256'))
            and bool(receipt.get('panel_build_id'))
            and receipt.get('panel_build_id') == signals.get('panel_build_id'))


def _check_signals(now: datetime) -> tuple[str, str]:
    signals = _json(DATA / "research" / "signals_latest.json")
    try:
        issued = datetime.fromisoformat(signals["as_of"])
        if issued.tzinfo is None or issued > now:
            raise ValueError("invalid issue timestamp")
        required = _required_market_session(now)
        signal_bar = str((signals.get("data_quality") or {}).get("latest_market_date") or "")
        return (("pass", f"signals verified through completed session {required}")
                if signal_bar == required else
                ("block", f"signals end {signal_bar or 'unknown'}; required completed session {required}"))
    except Exception:
        return "block", "signals timestamp missing or invalid"


def assess(now: datetime | None = None) -> dict:
    now = now or datetime.now(ET)
    checks: dict[str, dict[str, str]] = {}

    level, detail = _check_dependencies()
    checks["dependencies"] = {"level": level, "detail": detail}

    level, detail = _check_runtime_services()
    checks["runtime_services"] = {"level": level, "detail": detail}

    level, detail = _check_execution_isolation()
    checks["execution_isolation"] = {"level": level, "detail": detail}

    level, detail = _check_panel(now)
    checks["market_data"] = {"level": level, "detail": detail}

    signals = _json(DATA / "research" / "signals_latest.json")
    level, detail = _check_signals(now)
    checks["signals"] = {"level": level, "detail": detail}

    level, detail = _check_worker(now)
    checks["continuous_reassessment"] = {"level": level, "detail": detail}

    level,detail=_check_investigator_runtime()
    checks['investigator_runtime']={'level':level,'detail':detail}

    level,detail=_check_research_acceptance()
    checks['research_acceptance']={'level':level,'detail':detail}

    level,detail=_check_ranking_evidence()
    checks['prospective_ranking']={'level':level,'detail':detail}

    technical = _json(DATA / "research" / "technical_latest.json")
    if technical.get("n_profiled", 0) >= 500 and technical.get("panel_build_id") == signals.get("panel_build_id"):
        level, detail = "pass", (f"technical state covers {technical['n_profiled']} names "
                                  "on the current panel")
    else:
        level, detail = "warn", "technical state is missing, thin, or not aligned to current panel"
    checks["technical_state"] = {"level": level, "detail": detail}

    try:
        source_report = assess_sources(now)
        level = "pass" if source_report["ok"] else "warn"
        detail = (f"{source_report['healthy_critical']}/{source_report['critical_count']} "
                  "critical research sources healthy; provenance inventory available")
    except Exception as exc:
        level, detail = "warn", f"source-health inventory unavailable: {type(exc).__name__}"
    checks["data_sources"] = {"level": level, "detail": detail}

    ingest = _json(DATA / "research" / "_meta" / "ingest_manifest.json")
    info = (ingest.get("datasets") or {}).get("info") or {}
    universe_n = int(ingest.get("n_tickers") or 0)
    info_rows = int(info.get("coverage_rows") or info.get("rows") or 0)
    info_coverage = (info_rows / universe_n) if universe_n else 0.0
    if info.get("ok") and info_coverage >= 0.80:
        level, detail = "pass", f"fundamental snapshot coverage {info_coverage:.1%}"
    else:
        level, detail = "warn", (f"fundamental snapshot coverage {info_coverage:.1%}; "
                                  "value/quality screens disabled")
    checks["fundamental_ingest"] = {"level": level, "detail": detail}

    model = signals.get("model_validation", {})
    model_state = model.get("status", "unknown")
    checks["factor_model"] = {
        "level": "pass" if model_state == "production_eligible" else "warn",
        "detail": ("factor model production-eligible" if model_state == "production_eligible"
                   else "factor model is discovery-only; it cannot substantiate an edge"),
    }

    pipe = _json(DATA / "pipeline_status.json")
    required_session = _required_market_session(now)
    publication_date = _publication_day(now,required_session)
    brief = DATA / "context" / publication_date / "brief.json"
    receipt = _json(DATA / "context" / publication_date / "publication_commit.json")
    fabrication = _json(DATA / "research" / "fabrication_audit.json")
    publication_attested = False
    if pipe.get("date") == publication_date and pipe.get("state") == "complete" and brief.exists():
        try:
            digest = hashlib.sha256(brief.read_bytes()).hexdigest()
            validation_errors, _ = validate_brief(brief)
            session_audit = next((row for row in fabrication.get("days", [])
                                  if row.get("date") == publication_date), {})
            fabrication_clear = (session_audit.get("usable") is True
                                 and int(session_audit.get("contradicted") or 0) == 0)
            publication_attested = (receipt.get("brief_sha256") == digest
                                    and _receipt_matches_market(receipt,signals,required_session)
                                    and not validation_errors and fabrication_clear)
            if publication_attested:
                level, detail = ("pass", f"issue {publication_date} verified against "
                                         f"market session {required_session} and panel "
                                         f"{receipt['panel_build_id']}")
            else:
                level, detail = "block", "publication artifact or commit receipt failed attestation"
        except Exception as exc:
            level, detail = "block", f"publication attestation failed: {type(exc).__name__}"
    elif pipe.get("date") == publication_date and pipe.get("state") == "running":
        level, detail = "warn", f"pipeline running: {pipe.get('stage', 'unknown')}"
    else:
        level = "block"
        detail = f"validated publication for completed session {publication_date} unavailable ({pipe.get('reason', 'no status')})"
    checks["publication"] = {"level": level, "detail": detail}

    try:
        report = integrity_report()
        bad = len(report.get("collisions", {}))
        level = "pass" if report.get("ok") else "block"
        detail = "decision journal integrity verified" if report.get("ok") else f"journal ID collisions: {bad}"
    except Exception as exc:
        level, detail = "block", f"journal audit failed: {type(exc).__name__}"
    checks["decision_journal"] = {"level": level, "detail": detail}

    release = _json(DATA / "deployment_manifest.json")
    release_ok = False
    release_detail = "deployment has no verifiable release identity"
    if release.get("release_id"):
        try:
            actual_tree = tree_digest(REPO / "advisor")
            release_ok = actual_tree == release.get("advisor_tree_sha256")
            release_detail = (f"release {release['release_id']} integrity verified"
                              if release_ok else
                              f"release {release['release_id']} code drift detected")
        except Exception as exc:
            release_detail = f"release integrity check failed: {type(exc).__name__}"
    checks["release"] = {
        "level": "pass" if release_ok else ("block" if release.get("release_id") else "warn"),
        "detail": release_detail,
    }

    blockers = [k for k, v in checks.items() if v["level"] == "block"]
    warnings = [k for k, v in checks.items() if v["level"] == "warn"]
    verdict = "blocked" if blockers else ("degraded" if warnings else "ready")
    brief_data = _json(brief) if publication_attested else {}
    portfolio_status = (brief_data.get("portfolio_context") or {}).get("status")
    actionable_views = []
    for view in brief_data.get("views", []):
        record = {**view, "portfolio_context_status": portfolio_status}
        if is_actionable(record):
            actionable_views.append(view)
    commercial_blockers = [
        "replace/contractually license Yahoo-derived research data for redistribution",
        "obtain market-data display/redistribution rights for every live feed",
        "deploy TLS plus per-user identity, authorization, audit and session controls",
        "complete investment-adviser/broker-dealer regulatory and counsel review",
    ]
    actionable_required = ("dependencies", "runtime_services", "execution_isolation",
                           "continuous_reassessment",
                           "investigator_runtime",
                           "research_acceptance", "prospective_ranking",
                           "market_data", "signals", "technical_state", "data_sources", "publication",
                           "decision_journal", "release")
    actionable_blockers = [name for name in actionable_required
                           if checks[name]["level"] != "pass"]
    actionable_allowed = bool(actionable_views) and not actionable_blockers
    return {"schema_version": 1, "as_of": now.isoformat(), "verdict": verdict,
            "actionable_recommendations_allowed": actionable_allowed,
            "actionable_view_count": len(actionable_views),
            "actionable_blockers": actionable_blockers,
            "commercial_launch_allowed": False,
            "commercial_blockers": commercial_blockers,
            "blockers": blockers, "warnings": warnings, "checks": checks}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report = assess()
    print(json.dumps(report, indent=2) if args.json else
          f"{report['verdict'].upper()}: " + ", ".join(report["blockers"] or report["warnings"] or ["all checks pass"]))
    return 0 if report["verdict"] == "ready" else (1 if report["verdict"] == "degraded" else 2)


if __name__ == "__main__":
    raise SystemExit(main())
