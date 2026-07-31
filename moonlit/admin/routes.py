"""Admin HTTP routes (FastAPI APIRouter)."""
from __future__ import annotations

import sys
import traceback

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool

from moonlit.admin.auth import admin_token, is_admin
from moonlit.config import ADMIN_MAX_AGE, BASE_DIR

router = APIRouter()


def _get_data_manager():
    """Lazy import DataManager from admin_data module."""
    if BASE_DIR not in sys.path:
        sys.path.insert(0, BASE_DIR)
    from admindata import DataManager
    return DataManager(BASE_DIR)


@router.get("/admin/__ping")
async def admin_ping():
    return {"ok": True, "admin": True}


@router.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    from moonlit.config import TEMPLATES_DIR
    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    if is_admin(request):
        return RedirectResponse("/admin", status_code=302)
    return templates.TemplateResponse("admin_login.html", {"request": request, "error": None})


@router.post("/admin/login")
async def admin_login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    from moonlit.config import TEMPLATES_DIR
    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory=TEMPLATES_DIR)

    ok_user = _hmac.compare_digest(username, ADMIN_USERNAME)
    ok_pass = _hmac.compare_digest(password, ADMIN_PASSWORD)
    if not (ok_user and ok_pass):
        return templates.TemplateResponse(
            "admin_login.html",
            {"request": request, "error": "ユーザー名またはパスワードが違います"},
            status_code=401,
        )
    resp = RedirectResponse("/admin", status_code=302)
    import os
    resp.set_cookie(
        ADMIN_COOKIE,
        token,
        max_age=ADMIN_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("ADMIN_COOKIE_SECURE", "").lower() in ("1", "true", "yes"),
    )
    return resp


@router.get("/admin/logout")
@router.post("/admin/logout")
async def admin_logout():
    resp = RedirectResponse("/admin/login", status_code=302)
    resp.delete_cookie(ADMIN_COOKIE)
    return resp


@router.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request):
    from moonlit.config import TEMPLATES_DIR
    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    if not is_admin(request):
        return RedirectResponse("/admin/login", status_code=302)
    return templates.TemplateResponse("admin.html", {"request": request})


@router.get("/admin/api/status")
async def admin_status(request: Request):
    if not is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        dm = _get_data_manager()
        snapshot = await run_in_threadpool(dm.status_snapshot)
        return JSONResponse(snapshot)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.post("/admin/api/action")
async def admin_action(request: Request):
    if not is_admin(request):
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
        # Version upgrade = full live re-fetch (NOT copy from beta)
        if action == "version_upgrade_live":
            return dm.version_upgrade_live()
        # Disabled sources
        if action in ("fetch_beta_lunaris", "fetch_gachabase", "promote"):
            return {"ok": False, "error": "disabled", "disabled": True}
        return {"ok": False, "error": f"unknown action: {action}"}

    try:
        result = await run_in_threadpool(_run)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e), "traceback": traceback.format_exc()}, status_code=500)