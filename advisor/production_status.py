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

ET = ZoneInfo("America/New_York")
REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "advisor" / "data"
RUNTIME_SERVICES = ("advisor-terminal.service", "advisor-quoted.service",
                    "advisor-exitwatch.service")


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


def _check_panel(now_s: float) -> tuple[str, str]:
    try:
        meta = current_meta()
        age_h = (now_s - float(meta["built_unix"])) / 3600
        if not meta.get("quality", {}).get("ok"):
            return "block", "current price panel failed its quality gate"
        build = current_build_dir()
        for name, expected in (meta.get("sha256") or {}).items():
            got = hashlib.sha256((build / name).read_bytes()).hexdigest()
            if got != expected:
                return "block", f"price panel integrity mismatch: {name}"
        if age_h > 30:
            return "block", f"price panel is {age_h:.1f}h old"
        if age_h > 20:
            return "warn", f"price panel is approaching stale ({age_h:.1f}h)"
        return "pass", f"price panel verified ({age_h:.1f}h old)"
    except Exception as exc:
        return "block", f"price panel unavailable: {type(exc).__name__}"


def assess(now: datetime | None = None) -> dict:
    now = now or datetime.now(ET)
    checks: dict[str, dict[str, str]] = {}

    level, detail = _check_dependencies()
    checks["dependencies"] = {"level": level, "detail": detail}

    level, detail = _check_runtime_services()
    checks["runtime_services"] = {"level": level, "detail": detail}

    level, detail = _check_execution_isolation()
    checks["execution_isolation"] = {"level": level, "detail": detail}

    level, detail = _check_panel(now.timestamp())
    checks["market_data"] = {"level": level, "detail": detail}

    signals = _json(DATA / "research" / "signals_latest.json")
    try:
        age_h = (now - datetime.fromisoformat(signals["as_of"])).total_seconds() / 3600
        level = "pass" if age_h <= 30 else "block"
        detail = f"signals {age_h:.1f}h old"
    except Exception:
        level, detail = "block", "signals timestamp missing or invalid"
    checks["signals"] = {"level": level, "detail": detail}

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
    today = now.date().isoformat()
    brief = DATA / "context" / today / "brief.json"
    receipt = _json(DATA / "context" / today / "publication_commit.json")
    publication_attested = False
    if pipe.get("date") == today and pipe.get("state") == "complete" and brief.exists():
        try:
            digest = hashlib.sha256(brief.read_bytes()).hexdigest()
            validation_errors, _ = validate_brief(brief)
            publication_attested = (receipt.get("brief_sha256") == digest
                                    and not validation_errors)
            if publication_attested:
                level, detail = "pass", "today's validated publication and commit receipt verified"
            else:
                level, detail = "block", "publication artifact or commit receipt failed attestation"
        except Exception as exc:
            level, detail = "block", f"publication attestation failed: {type(exc).__name__}"
    elif pipe.get("date") == today and pipe.get("state") == "running":
        level, detail = "warn", f"pipeline running: {pipe.get('stage', 'unknown')}"
    elif now.weekday() <= 4 and now.strftime("%H:%M") >= "09:45":
        level = "block"
        detail = f"today's publication unavailable ({pipe.get('reason', 'no status')})"
    else:
        level, detail = "warn", "today's validated publication is not complete"
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
