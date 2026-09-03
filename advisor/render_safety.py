"""Escaping helpers for untrusted research content rendered in the portal."""
from __future__ import annotations

import html
import hmac
from urllib.parse import urlsplit


def text(value) -> str:
    """Escape a value for HTML text or quoted-attribute context."""
    return html.escape(str(value), quote=True).replace("$", "&#36;")


def http_url(value) -> str | None:
    """Return an escaped absolute HTTP(S) URL, otherwise None."""
    raw = str(value or "").strip()
    if any(ord(ch) < 32 for ch in raw):
        return None
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return text(raw)


def secret_equal(provided, expected) -> bool:
    """Constant-time comparison for string bearer secrets."""
    if not isinstance(provided, str) or not isinstance(expected, str):
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
