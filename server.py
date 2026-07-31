from __future__ import annotations

import asyncio
import hashlib as _hashlib
import hmac as _hmac
import io
import json
import os
import secrets as _secrets
import sys
import time as _time
from typing import Any, Dict, List

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageDraw, ImageFilter
from starlette.concurrency import run_in_threadpool

try:
    import uvicorn
except ImportError:
    uvicorn = None

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


app = FastAPI()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from moonlit.config import BASE_DIR as _BASE_DIR, STATIC_DIR as _STATIC_DIR, CACHE_DIR
from moonlit.path import resolve_datas_path, resolve_list_path
from moonlit.stats.score import score_calc, artifact_tier, total_tier, CALC_METHOD_PROP_ID, CALC_METHOD_LABEL
from moonlit.stats.props import (
    formal_round, new_stat_totals, to_ratio_if_percent,
    apply_stat_bonus, get_stat_japanese, load_text_map,
)
from moonlit.stats.levels import get_char_level, resolve_display_skill_levels
from moonlit.showcase.normalize import normalize_avatar_id, match_avatar_id
from moonlit.showcase.enka import update_uid_data, clean_showcase_data
from moonlit.card.build import build_card_model, build_card_image_model
from moonlit.card.drawing import (
    hex_to_rgb, create_card_background, draw_figma_text_right, draw_figma_box,
    draw_figma_text, draw_figma_text_with_shadow, draw_figma_line, draw_figma_circle,
    paste_figma_image, paste_mask_image, load_fonts,
)
from moonlit.card.image_render import render_card_image
from moonlit.admin.auth import admin_sign, admin_verify, admin_token, is_admin

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "aikyu")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "iqmc1104")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET") or _secrets.token_hex(32)
ADMIN_COOKIE = "admin_session"
ADMIN_MAX_AGE = 60 * 60 * 12


def _admin_sign(payload: str) -> str:
    sig = _hmac.new(ADMIN_SECRET.encode(), payload.encode(), _hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _admin_verify(token) -> bool:
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


def _admin_token() -> str:
    return _admin_sign(f"{ADMIN_USERNAME}:{int(_time.time()) + ADMIN_MAX_AGE}")


def _is_admin(request: Request) -> bool:
    return _admin_verify(request.cookies.get(ADMIN_COOKIE))


def _get_data_manager():
    """Lazy import DataManager from admin_data module."""
    if BASE_DIR not in sys.path:
        sys.path.insert(0, BASE_DIR)
    from admindata import DataManager
    return DataManager(BASE_DIR)

@app.get("/admin/__ping")
async def admin_ping():
    return {"ok": True, "admin": True}


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    if _is_admin(request):
        return RedirectResponse("/admin", status_code=302)
    return templates.TemplateResponse("admin_login.html", {"request": request, "error": None})


@app.post("/admin/login")
async def admin_login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    ok_user = _hmac.compare_digest(username, ADMIN_USERNAME)
    ok_pass = _hmac.compare_digest(password, ADMIN_PASSWORD)
    if not (ok_user and ok_pass):
        return templates.TemplateResponse(
            "admin_login.html",
            {"request": request, "error": "ユーザー名またはパスワードが違います"},
            status_code=401,
        )
    resp = RedirectResponse("/admin", status_code=302)
    resp.set_cookie(
        ADMIN_COOKIE,
        _admin_token(),
        max_age=ADMIN_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("ADMIN_COOKIE_SECURE", "").lower() in ("1", "true", "yes"),
    )
    return resp


@app.get("/admin/logout")
@app.post("/admin/logout")
async def admin_logout():
    resp = RedirectResponse("/admin/login", status_code=302)
    resp.delete_cookie(ADMIN_COOKIE)
    return resp


@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request):
    if not _is_admin(request):
        return RedirectResponse("/admin/login", status_code=302)
    return templates.TemplateResponse("admin.html", {"request": request})


@app.get("/admin/api/status")
async def admin_status(request: Request):
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        dm = _get_data_manager()
        snapshot = await run_in_threadpool(dm.status_snapshot)
        return JSONResponse(snapshot)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.post("/admin/api/action")
async def admin_action(request: Request):
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
    action = (body or {}).get("action")
    if not action:
        return JSONResponse({"ok": False, "error": "action required"}, status_code=400)

    def _run():
        dm = _get_data_manager()
        if action == "fetch_beta_nanoka_json":
            return dm.fetch_beta_nanoka_json()
        if action == "fetch_beta_nanoka_assets":
            return dm.fetch_beta_nanoka_assets()
        if action == "fetch_beta_nanoka":
            return dm.fetch_beta_nanoka(download_images=True)
        if action == "fetch_live_nanoka_json":
            return dm.fetch_live_nanoka_json()
        if action == "fetch_live_nanoka_assets":
            return dm.fetch_live_nanoka_assets()
        if action == "fetch_live_nanoka":
            return dm.fetch_live_nanoka()
        if action == "version_upgrade_live":
            return dm.version_upgrade_live()
        if action in ("fetch_beta_lunaris", "fetch_gachabase", "promote"):
            return {"ok": False, "error": "disabled", "disabled": True}
        return {"ok": False, "error": f"unknown action: {action}"}

    try:
        result = await run_in_threadpool(_run)
        return JSONResponse(result)
    except Exception as e:
        import traceback
        return JSONResponse({"ok": False, "error": str(e), "traceback": traceback.format_exc()}, status_code=500)


print("[OK] Admin routes embedded in server.py -> /admin/login , /admin/__ping")


# =============================================================================
# Static helpers (re-exported from moonlit for backward compatibility)
# =============================================================================
FONT_PATH = os.path.join(BASE_DIR, "fonts", "font_fixed.ttf")
FONT_LIGHT_PATH = os.path.join(BASE_DIR, "fonts", "font_light.ttf")


# text_map loading
_text_map = None
def _get_text_map():
    global _text_map
    if _text_map is None:
        _text_map = load_text_map()
    return _text_map


# =============================================================================
# Public routes (delegated to moonlit.web.routes_public)
# =============================================================================
from moonlit.web.public import router as public_router
for route in public_router.routes:
    app.routes.append(route)
print(f"[OK] Public routes included ({len(public_router.routes)} routes)")

# =============================================================================
# Card routes (delegated to moonlit.web.routes_card)
# =============================================================================
from moonlit.web.card import router as card_router
for route in card_router.routes:
    app.routes.append(route)
print(f"[OK] Card routes included ({len(card_router.routes)} routes)")


# =============================================================================
# Entry point
# =============================================================================
if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)