"""Admin authentication helpers (cookie-based HMAC session)."""
from __future__ import annotations

import hashlib as _hashlib
import hmac as _hmac
import time as _time

from fastapi import Request

from moonlit.config import ADMIN_USERNAME, ADMIN_PASSWORD, ADMIN_SECRET, ADMIN_COOKIE, ADMIN_MAX_AGE


def admin_sign(payload: str) -> str:
    sig = _hmac.new(ADMIN_SECRET.encode(), payload.encode(), _hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def admin_verify(token) -> bool:
    if not token or "." not in token:
        return False
    payload, sig = token.rsplit(".", 1)
    expected = _hmac.new(ADMIN_SECRET.encode(), payload.encode(), _hashlib.sha256).hexdigest()
    if not _hmac.compare_digest(expected, sig):
        return False
    try:
        user, exp_s = payload.split(":", 1)
        return user == ADMIN_USERNAME and int(exp_s) >= int(_time.time())
    except Exception:
        return False


def admin_token() -> str:
    return admin_sign(f"{ADMIN_USERNAME}:{int(_time.time()) + ADMIN_MAX_AGE}")


def is_admin(request: Request) -> bool:
    return admin_verify(request.cookies.get(ADMIN_COOKIE))