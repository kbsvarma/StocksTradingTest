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

import boto3
import botocore

PARAM_NAME = "/webull-bot/active-instance"
REGION = "us-east-1"
CACHE_TTL_SEC = 30

_cache: dict = {"value": None, "ts": 0.0}


def my_name() -> str:
    """This instance's own name. From env WEBULL_INSTANCE_NAME, default 'unknown'."""
    return os.environ.get("WEBULL_INSTANCE_NAME", "unknown")


def active_instance(use_cache: bool = True) -> str:
    """Read the current active-instance value from SSM. Cached 30s.

    Returns: "mac", "ec2", "none", or "unknown" (on error).
    """
    now = time.time()
    if use_cache and _cache["value"] is not None and now - _cache["ts"] < CACHE_TTL_SEC:
        return _cache["value"]
    try:
        ssm = boto3.client("ssm", region_name=REGION)
        r = ssm.get_parameter(Name=PARAM_NAME)
        val = r["Parameter"]["Value"].strip().lower()
    except botocore.exceptions.ClientError as e:
        # Parameter doesn't exist yet, or AccessDenied — fail-safe
        code = e.response.get("Error", {}).get("Code", "")
        if code == "ParameterNotFound":
            val = "none"
        else:
            val = "unknown"
    except Exception:
        val = "unknown"
    _cache["value"] = val
    _cache["ts"] = now
    return val


def is_active_instance() -> bool:
    """True if this instance is the currently-active live trader."""
    return active_instance() == my_name()


def status_line() -> str:
    """Human-readable line for logs/dashboards."""
    me = my_name()
    active = active_instance()
    if active == me:
        return f"MUTEX: I am '{me}' and I am the ACTIVE instance — real trades enabled."
    return f"MUTEX: I am '{me}' but active='{active}' — paper-only (orders will be synthesized)."
