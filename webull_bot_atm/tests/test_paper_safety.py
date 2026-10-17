"""ATM bot MUST refuse to run live without explicit override.
Validates require_live_override gate in run()."""
import os
import subprocess
import sys
from pathlib import Path
import pytest


REPO = Path(__file__).resolve().parents[2]


def _run_atm(env_extra: dict, timeout=5):
    """Run `python -m webull_bot_atm.main` with extra env vars. Returns CompletedProcess."""
    env = os.environ.copy()
    # Strip any inherited live/dry-run flags so test is hermetic
    env.pop("WEBULL_DRY_RUN", None)
    env.pop("ALLOW_LIVE_TRADING", None)
    env.update(env_extra)
    # Use stub mode so the bot doesn't actually try to connect to Webull/IBKR
    env.setdefault("WEBULL_DRY_RUN", "")  # Don't set unless test wants it
    try:
        return subprocess.run(
            [sys.executable, "-m", "webull_bot_atm.main"],
            cwd=str(REPO),
            env=env,
            capture_output=True, text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        # Bot started OK and is running — that's "didn't refuse"
        return None


def test_bot_refuses_without_safety_overrides():
    """No DRY_RUN, no ALLOW_LIVE → bot must exit immediately with code 2."""
    env = {}
    # Remove any defaults that would let it through
    if "WEBULL_DRY_RUN" in env: del env["WEBULL_DRY_RUN"]
    if "ALLOW_LIVE_TRADING" in env: del env["ALLOW_LIVE_TRADING"]
    result = _run_atm(env, timeout=8)
    if result is None:
        pytest.fail("Bot did NOT refuse — it kept running (this is a safety regression)")
    assert result.returncode == 2, f"Expected exit code 2, got {result.returncode}\nstdout: {result.stdout}\nstderr: {result.stderr}"
    assert "REFUS" in result.stdout or "REFUS" in result.stderr, \
        "Expected refusal message containing 'REFUSED'"


def test_bot_accepts_dry_run_override():
    """WEBULL_DRY_RUN=1 should let the bot start (will then connect/fail later, but not immediately refuse)."""
    result = _run_atm({"WEBULL_DRY_RUN": "1"}, timeout=8)
    # If timed out → bot started and is running → did NOT refuse. That's success for this test.
    if result is None:
        return
    # If it exited, it must NOT be the safety refusal (code 2)
    # Real connection failures will be different exit codes
    if result.returncode == 2:
        # Check it's not the safety refusal
        assert "REFUS" not in result.stdout and "REFUS" not in result.stderr, \
            f"Bot still refused despite DRY_RUN=1: {result.stdout}{result.stderr}"
