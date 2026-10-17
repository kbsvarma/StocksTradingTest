"""Verify ATM bot is properly isolated from legacy webull_bot.

These tests catch regressions where ATM accidentally writes to legacy paths
or imports legacy modules (which would couple the two bots).
"""
from pathlib import Path
import yaml


def test_all_tmp_paths_have_atm_suffix():
    """All bot-specific /tmp paths in ATM modules must end in _atm.{ext}.
    Excludes shared paths (event_queue.jsonl, telegram_sent.log, telegram_dedup.json)."""
    SHARED_PATHS = {
        "/tmp/event_queue.jsonl",
        "/tmp/telegram_sent.log",
        "/tmp/telegram_dedup.json",
        "/tmp/webull_log_archive",
    }
    atm_dir = Path(__file__).parent.parent
    bad = []
    for py in atm_dir.glob("*.py"):
        text = py.read_text()
        # Find /tmp/ paths in actual code (lines with = "..." or ('"..."')
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#"):
                continue
            # Heuristic: line containing a quoted /tmp/ string that's NOT a comment
            for quote in ('"', "'"):
                marker = f'{quote}/tmp/'
                if marker in line:
                    # Extract the path
                    start = line.find(marker) + 1
                    end = line.find(quote, start)
                    if end > start:
                        path = line[start:end]
                        # Allowed: shared paths or paths ending in _atm.x
                        if path in SHARED_PATHS:
                            continue
                        if "_atm." in path or "_atm/" in path:
                            continue
                        bad.append((py.name, path))
    assert not bad, f"ATM bot has /tmp paths missing _atm suffix:\n" + "\n".join(f"  {f}: {p}" for f, p in bad)


def test_no_imports_from_legacy_webull_bot():
    """ATM modules must NOT import from webull_bot (legacy) — would couple them."""
    atm_dir = Path(__file__).parent.parent
    bad = []
    for py in atm_dir.rglob("*.py"):
        if "tests" in py.parts:
            continue
        text = py.read_text()
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "from webull_bot." in stripped or stripped.startswith("import webull_bot."):
                # Make sure it's not webull_bot_atm
                if "webull_bot_atm" not in stripped:
                    bad.append((py.name, i, stripped))
    assert not bad, "Legacy webull_bot imports found:\n" + "\n".join(f"  {f}:{n}: {s}" for f, n, s in bad)


def test_config_paths_in_atm_subdirectory():
    """data_dir / logs_dir / state_file / trade_csv must all be under atm_bot/."""
    cfg = yaml.safe_load((Path(__file__).parent.parent / "config_atm.yaml").read_text())
    for key in ("data_dir", "logs_dir", "state_file", "trade_csv"):
        assert "atm_bot" in cfg[key], f"{key}={cfg[key]} not in atm_bot subdir"
        assert "vG_spx_w50" not in cfg[key], f"{key} accidentally points at legacy dir"


def test_launchd_label_distinct_from_legacy():
    """ATM launchd label must NOT match legacy label."""
    p = Path(__file__).parent.parent / "deploy" / "launchd" / "com.stockstest.webull-bot-atm.plist"
    assert p.exists()
    text = p.read_text()
    assert "<string>com.stockstest.webull-bot-atm</string>" in text
    # Must NOT have legacy label
    assert ">com.stockstest.webull-bot<" not in text


def test_systemd_service_distinct():
    p = Path(__file__).parent.parent / "deploy" / "systemd" / "webull-bot-atm.service"
    assert p.exists()
    text = p.read_text()
    assert "webull_bot_atm.main" in text, "Must run the ATM main module, not legacy"


def test_no_hardcoded_legacy_data_paths_in_atm_modules():
    """ATM bot must not reference 'vG_spx_w50_vix25' (legacy data dir) anywhere
    except bot_switch.py (which legitimately tracks both bots)."""
    atm_dir = Path(__file__).parent.parent
    bad = []
    allowed = {"bot_switch.py"}  # bot_switch deliberately knows about both
    for py in atm_dir.glob("*.py"):
        if py.name in allowed:
            continue
        text = py.read_text()
        for i, line in enumerate(text.splitlines(), 1):
            if "vG_spx_w50_vix25" in line and not line.strip().startswith("#"):
                bad.append((py.name, i, line.strip()))
    assert not bad, "Hardcoded legacy data paths found:\n" + "\n".join(f"  {f}:{n}: {s}" for f, n, s in bad)


def test_no_hardcoded_legacy_install_paths_in_atm():
    """ATM modules must not reference /opt/webull-bot/ (legacy EC2 install) anywhere
    except as a docstring/comment. /opt/webull-bot-atm/ is OK."""
    atm_dir = Path(__file__).parent.parent
    bad = []
    for py in atm_dir.glob("*.py"):
        text = py.read_text()
        for i, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # Look for /opt/webull-bot/ (not /opt/webull-bot-atm/)
            if "/opt/webull-bot/" in stripped and "/opt/webull-bot-atm/" not in stripped:
                bad.append((py.name, i, stripped))
    assert not bad, "ATM modules reference legacy install path /opt/webull-bot/:\n" + \
        "\n".join(f"  {f}:{n}: {s}" for f, n, s in bad)


def test_atm_heartbeat_watchdog_targets_atm_dir():
    """heartbeat_watchdog.py must monitor ATM bot's heartbeat, not legacy's."""
    text = (Path(__file__).parent.parent / "heartbeat_watchdog.py").read_text()
    assert "atm_bot" in text, "heartbeat_watchdog should point to atm_bot heartbeat.json"
    assert "vG_spx_w50_vix25" not in text, "heartbeat_watchdog must not target legacy"


def test_bot_switch_knows_about_both():
    """bot_switch.py must register both LEGACY and NEW_ATM bots."""
    from webull_bot_atm.bot_switch import BOT_LABELS, BOT_PLISTS
    assert "LEGACY" in BOT_LABELS
    assert "NEW_ATM" in BOT_LABELS
    assert BOT_LABELS["LEGACY"] != BOT_LABELS["NEW_ATM"], "Labels must differ"
    assert BOT_PLISTS["LEGACY"].name != BOT_PLISTS["NEW_ATM"].name, "Plist files must differ"
