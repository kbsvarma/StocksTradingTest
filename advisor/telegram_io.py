"""Advisor Telegram I/O — standalone send + reply polling.

Deliberately independent of webull_bot's event-queue → telegram_service path:
the advisor must work even when the bot stack is down, and the approval
listener needs getUpdates (inbound), which nothing else in the repo uses —
so there is no polling conflict on the bot token.

Env (from ~/.webull_env): TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID.
All sends carry the 🧭 ADVISOR prefix so they are visually distinct from bot
alerts. Sends are best-effort: failures return False, never raise.

CLI:
    python -m advisor.telegram_io --send "text"
    python -m advisor.telegram_io --send-file path [--html] [--no-prefix]
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("ADVISOR_DATA_DIR", str(REPO_ROOT / "advisor" / "data")))
OFFSET_FILE = DATA_DIR / "telegram_offset.json"

_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
PREFIX = "🧭 ADVISOR"
MAX_LEN = 4000  # Telegram hard cap is 4096; leave headroom


def enabled() -> bool:
    return bool(_TOKEN and _CHAT_ID)


def authorized_chat_id() -> str:
    return str(_CHAT_ID)


def _api(method: str) -> str:
    return f"https://api.telegram.org/bot{_TOKEN}/{method}"


def _post(method: str, params: dict, timeout: float = 35.0) -> dict:
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(_api(method), data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _chunks(text: str) -> list[str]:
    """Split on newlines into <=MAX_LEN chunks; hard-cut pathological lines."""
    out: list[str] = []
    cur = ""
    for line in text.split("\n"):
        while len(line) > MAX_LEN:           # single line too long — hard cut
            out.append(line[:MAX_LEN])
            line = line[MAX_LEN:]
        if len(cur) + len(line) + 1 > MAX_LEN:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out or [""]


def muted() -> bool:
    """Opt-out switch: ADVISOR_TELEGRAM_MUTE=1 suppresses delivery.

    Distinct from `enabled()` — a muted send reports SUCCESS so the publish
    stage's "confirm exit 0" step still passes. Silencing the channel must
    never look like a failed brief, and must never make the pipeline retry.
    The brief artifacts (brief.md / brief.json) are written before any send,
    so muting costs no output — only the push notification.
    """
    return os.environ.get("ADVISOR_TELEGRAM_MUTE", "").strip() in ("1", "true", "yes")


def send(text: str, *, html: bool = False, prefix: bool = True) -> bool:
    """Send a message to the authorized chat. Chunks long messages."""
    if muted():
        print("[telegram] MUTED (ADVISOR_TELEGRAM_MUTE=1) — not delivered; "
              "brief artifacts are unaffected", file=sys.stderr)
        return True
    if not enabled():
        print("[telegram] disabled — TELEGRAM_BOT_TOKEN/CHAT_ID not set", file=sys.stderr)
        return False
    body = f"{PREFIX}\n{text}" if prefix else text
    ok = True
    for chunk in _chunks(body):
        params = {
            "chat_id": _CHAT_ID,
            "text": chunk,
            "disable_web_page_preview": "true",
        }
        if html:
            params["parse_mode"] = "HTML"
        try:
            resp = _post("sendMessage", params)
            if not resp.get("ok"):
                print(f"[telegram] send not ok: {resp}", file=sys.stderr)
                ok = False
        except Exception as exc:
            print(f"[telegram] send failed: {exc}", file=sys.stderr)
            ok = False
    return ok


# ── Inbound (approval listener only) ─────────────────────────────────────────

def load_offset() -> int:
    try:
        return int(json.loads(OFFSET_FILE.read_text()).get("offset", 0))
    except Exception:
        return 0


def save_offset(update_id: int) -> None:
    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = OFFSET_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"offset": int(update_id)}))
    os.replace(tmp, OFFSET_FILE)


def get_updates(timeout: int = 50) -> list[dict]:
    """Long-poll for new messages. Caller must save_offset() after handling."""
    if not enabled():
        return []
    params = {
        "timeout": timeout,
        "offset": load_offset() + 1,
        "allowed_updates": '["message"]',
    }
    resp = _post("getUpdates", params, timeout=timeout + 10)
    return resp.get("result", []) if resp.get("ok") else []


def main() -> int:
    args = sys.argv[1:]
    html = "--html" in args
    prefix = "--no-prefix" not in args
    if "--send" in args:
        text = args[args.index("--send") + 1]
    elif "--send-file" in args:
        text = Path(args[args.index("--send-file") + 1]).read_text(encoding="utf-8")
    else:
        print(__doc__)
        return 2
    return 0 if send(text, html=html, prefix=prefix) else 1


if __name__ == "__main__":
    sys.exit(main())
