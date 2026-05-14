"""Single-instance mutex via SSM Parameter Store.

Source of truth: SSM parameter `/webull-bot/active-instance` whose value is
`"mac"`, `"ec2"`, or `"none"`. The bot that finds its own name in this value
is the LIVE bot — all others run as paper / dry-run regardless of their
local WEBULL_DRY_RUN setting.

Identity: each bot reads its own name from env var WEBULL_INSTANCE_NAME.
Defaults to "unknown" if unset (which is treated as never-active).

Cached for 30 seconds to avoid hammering SSM. Cache resets on TTL expiry,
so a switchover via SSM update is picked up within 30 sec on the next call.

Failure mode: any error reading SSM returns is_active=False (fail-safe —
no real orders if we can't verify we're authorized).

Usage:
    from webull_bot.active_instance import is_active_instance, my_name

    if not is_active_instance():
        # I'm not the active bot — force dry-run mode for this call
        return dry_run_synth_fill(...)
"""
from __future__ import annotations

import os
import time
from typing import Optional

# boto3 is imported lazily inside active_instance() so this module can be
# imported on machines that don't have boto3 installed (e.g. the Mac, where
# the bot reads creds from .webull_env and never touches SSM). On such
# machines, active_instance() will fail-closed → bot runs paper, which is
# what we want when SSM mutex isn't reachable.

PARAM_NAME = "/webull-bot/active-instance"
REGION = "us-east-1"
CACHE_TTL_SEC = 30

_cache: dict = {"value": None, "ts": 0.0}


# Sentinel used when we can't determine the answer. NEVER matches a valid
# instance name, so any comparison with this value yields False (fail-closed).
_UNKNOWN = "__unknown_unset__"

# Valid known names. Anything else from SSM is treated as invalid.
_VALID_NAMES = frozenset({"mac", "ec2"})


def my_name() -> str:
    """This instance's own name from env WEBULL_INSTANCE_NAME.

    Returns the configured value, or _UNKNOWN sentinel if unset/blank/invalid.
    The sentinel can never match SSM values, so a missing env var means
    is_active_instance() always returns False (fail-closed).
    """
    raw = (os.environ.get("WEBULL_INSTANCE_NAME") or "").strip().lower()
    if raw not in _VALID_NAMES:
        return _UNKNOWN
    return raw


def active_instance(use_cache: bool = True) -> str:
    """Read the current active-instance value from SSM. Cached 30s.

    Returns one of {"mac", "ec2", "none"} on success, or _UNKNOWN sentinel
    on any error (fail-closed). Values from SSM that are not in _VALID_NAMES
    or "none" are also treated as _UNKNOWN.
    """
    now = time.time()
    if use_cache and _cache["value"] is not None and now - _cache["ts"] < CACHE_TTL_SEC:
        return _cache["value"]
    try:
        # Lazy import — see module docstring above
        import boto3
        import botocore.exceptions
        ssm = boto3.client("ssm", region_name=REGION)
        r = ssm.get_parameter(Name=PARAM_NAME)
        raw = r["Parameter"]["Value"].strip().lower()
        if raw in _VALID_NAMES or raw == "none":
            val = raw
        else:
            val = _UNKNOWN
    except ImportError:
        # boto3 not installed (Mac case) — can't reach SSM, fail-closed
        val = _UNKNOWN
    except botocore.exceptions.ClientError as e:  # type: ignore[name-defined]
        code = e.response.get("Error", {}).get("Code", "")
        if code == "ParameterNotFound":
            val = "none"
        else:
            val = _UNKNOWN
    except Exception:
        val = _UNKNOWN
    _cache["value"] = val
    _cache["ts"] = now
    return val


def _trust_local_role() -> bool:
    """True if the bot is configured to trust env WEBULL_INSTANCE_NAME without
    consulting SSM. Used on hosts that lack AWS credentials (e.g. Mac, where
    boto3 has no creds to read /webull-bot/active-instance).

    SAFETY TRADE-OFF: with this enabled, an SSM mutex flip won't auto-disable
    this instance — the operator is responsible for stopping the bot manually.
    Only set on a host whose role is fixed (always-live or always-paper).
    """
    return os.environ.get("WEBULL_TRUST_LOCAL_ROLE") == "1"


def is_active_instance() -> bool:
    """True if this instance is the currently-active live trader.

    Default: fail-CLOSED. Returns False if either side is the _UNKNOWN
    sentinel, or if names don't match. Will only ever return True when both
    env var and SSM value are valid AND identical.

    Override: when WEBULL_TRUST_LOCAL_ROLE=1, trust the local env var only
    (no SSM call). Used on hosts without AWS credentials.
    """
    me = my_name()
    if me == _UNKNOWN:
        return False
    if _trust_local_role():
        return me in _VALID_NAMES
    active = active_instance()
    if active == _UNKNOWN:
        return False
    return me == active and me in _VALID_NAMES


def status_line() -> str:
    """Human-readable line for logs/dashboards."""
    me = my_name()
    me_disp = "<UNSET>" if me == _UNKNOWN else me
    if _trust_local_role():
        if is_active_instance():
            return f"MUTEX: I am '{me_disp}' (TRUST_LOCAL_ROLE=1, SSM bypassed) — real trades enabled."
        return f"MUTEX: I am '{me_disp}' (TRUST_LOCAL_ROLE=1, but env invalid) — paper-only."
    active = active_instance()
    active_disp = "<UNKNOWN>" if active == _UNKNOWN else active
    if is_active_instance():
        return f"MUTEX: I am '{me_disp}' and I am the ACTIVE instance — real trades enabled."
    return f"MUTEX: I am '{me_disp}' but active='{active_disp}' — paper-only (orders will be synthesized)."
