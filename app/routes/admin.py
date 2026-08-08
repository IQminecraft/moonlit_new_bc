# -*- coding: utf-8 -*-
"""Admin 系ルート（/admin/*）。認証・ログイン・region_map 管理 API・カタログ構築。"""
import os
import json
import hashlib as _hashlib
import hmac as _hmac
import re as _re
import secrets as _secrets
import time as _time
from typing import Dict, Any, Optional

from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app.paths import BASE_DIR, STATIC_DIR, templates
from app.core.notify import report_error_to_discord
from app.card.cache import _ADMIN_CATALOG_CACHE
from app.card.bg import _list_region_image_names
from app.card.region import _load_region_map, _TRAVELER_BASE_IDS, clear_region_map_cache

admin_router = APIRouter()

_ADMIN_COOKIE = "admin_session"
_ADMIN_MAX_AGE = 60 * 60 * 12
_ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "").strip()
_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()
_ADMIN_SECRET = (os.environ.get("ADMIN_SECRET") or _secrets.token_hex(32)).strip()
_ADMIN_ALLOWED_IPS = {s.strip() for s in os.environ.get("ADMIN_ALLOWED_IPS", "").split(",") if s.strip()}
_ADMIN_MAX_LOGIN_FAILS = int(os.environ.get("ADMIN_MAX_LOGIN_FAILS", "5"))
_ADMIN_LOCK_MINUTES = int(os.environ.get("ADMIN_LOCK_MINUTES", "10"))
# IP -> {count: 失敗回数, locked_until: ロック解除時刻(epoch秒)}（成功または期限切れで消える）
_ADMIN_LOGIN_FAILS: Dict[str, Dict[str, Any]] = {}



def _validate_admin_config() -> None:
    """セキュリティ要件を満たさない設定は起動時にエラーで止める（fail closed）。"""
    problems = []
    if not _ADMIN_USERNAME:
        problems.append("ADMIN_USERNAME が未設定です（.env に設定してください）")
    if len(_ADMIN_PASSWORD) < 12:
        problems.append("ADMIN_PASSWORD が短すぎます（12文字以上必要）。.env に強力なパスワードを設定してください")
    if _ADMIN_PASSWORD and _ADMIN_PASSWORD in ("iqmc1104",):
        problems.append("ADMIN_PASSWORD に既知のデフォルト値が使われています。.env で変更してください")
    if problems:
        raise RuntimeError("Admin 設定エラー:\n" + "\n".join(problems))
    if "ADMIN_SECRET" not in os.environ:
        print("[WARN] ADMIN_SECRET 未設定のため起動毎にランダム生成します（複数プロセス運用時は .env に固定値を設定してください）")


_validate_admin_config()

def _admin_sign(payload: str) -> str:
    sig = _hmac.new(_ADMIN_SECRET.encode(), payload.encode(), _hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"

def _admin_verify(token) -> bool:
    if not token or "." not in token:
        return False
    payload, sig = token.rsplit(".", 1)
    expected = _hmac.new(_ADMIN_SECRET.encode(), payload.encode(), _hashlib.sha256).hexdigest()
    if not _hmac.compare_digest(expected, sig):
        return False
    try:
        user, exp_s = payload.split(":", 1)
        return user == _ADMIN_USERNAME and int(exp_s) >= int(_time.time())
    except Exception:
        return False

def _admin_token() -> str:
    return _admin_sign(f"{_ADMIN_USERNAME}:{int(_time.time()) + _ADMIN_MAX_AGE}")

def _is_admin(request: Request) -> bool:
    return _admin_verify(request.cookies.get(_ADMIN_COOKIE))

def _get_data_manager():
    from app.admin_data import DataManager
    return DataManager(BASE_DIR)

def _admin_client_ip(request: Request) -> str:
    """クライアント IP（uvicorn の proxy_headers が信頼プロキシの XFF を解決済み）。"""
    return request.client.host if request.client else "unknown"

def _admin_login_lock_remaining(ip: str) -> Optional[float]:
    """ロック中の残り秒数を返す。ロックされていなければ None。"""
    if len(_ADMIN_LOGIN_FAILS) > 1000:
        now = _time.time()
        for key in [k for k, v in _ADMIN_LOGIN_FAILS.items() if v["locked_until"] < now]:
            _ADMIN_LOGIN_FAILS.pop(key, None)
    entry = _ADMIN_LOGIN_FAILS.get(ip)
    if not entry:
        return None
    now = _time.time()
    if entry["locked_until"] > now:
        return entry["locked_until"] - now
    if entry["locked_until"] > 0:
        # ロック期限切れ: 失敗カウントごとリセット
        _ADMIN_LOGIN_FAILS.pop(ip, None)
    return None

async def _admin_security_middleware(request: Request, call_next):
    # IP 許可リスト（設定時のみ適用）
    if request.url.path.startswith("/admin") and _ADMIN_ALLOWED_IPS:
        if _admin_client_ip(request) not in _ADMIN_ALLOWED_IPS:
            return JSONResponse({"detail": "Forbidden"}, status_code=403)
    try:
        response = await call_next(request)
    except HTTPException:
        raise
    except Exception as e:
        # 未処理例外を Discord へ（カード生成など個別で報告済みでも二重になる場合あり → レート制限で抑制）
        import traceback
        report_error_to_discord(
            "unhandled exception",
            f"{type(e).__name__}: {e}",
            path=str(request.url.path),
            extra={
                "method": request.method,
                "query": str(request.url.query)[:200] if request.url.query else "",
            },
            traceback_text=traceback.format_exc(),
            level="error",
        )
        raise
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response

@admin_router.get("/admin/__ping")
async def admin_ping(request: Request):
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return {"ok": True, "admin": True}

@admin_router.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    if _is_admin(request):
        return RedirectResponse("/admin", status_code=302)
    return templates.TemplateResponse("admin_login.html", {"request": request, "error": None})

@admin_router.post("/admin/login")
async def admin_login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    ip = _admin_client_ip(request)
    lock_remaining = _admin_login_lock_remaining(ip)
    if lock_remaining is not None:
        return templates.TemplateResponse(
            "admin_login.html",
            {"request": request, "error": f"試行回数が多すぎます。{int(lock_remaining)}秒後に再度お試しください"},
            status_code=429,
        )

    ok_user = _hmac.compare_digest(username, _ADMIN_USERNAME)
    ok_pass = _hmac.compare_digest(password, _ADMIN_PASSWORD)
    if not (ok_user and ok_pass):
        entry = _ADMIN_LOGIN_FAILS.setdefault(ip, {"count": 0.0, "locked_until": 0.0})
        entry["count"] += 1
        if entry["count"] >= _ADMIN_MAX_LOGIN_FAILS:
            entry["locked_until"] = _time.time() + _ADMIN_LOCK_MINUTES * 60
            entry["count"] = 0.0
        try:
            _get_data_manager().append_log(
                "admin_login_failed", False, f"login failed from {ip} (user={username!r})"
            )
        except Exception:
            pass
        return templates.TemplateResponse(
            "admin_login.html",
            {"request": request, "error": "ユーザー名またはパスワードが違います"},
            status_code=401,
        )

    _ADMIN_LOGIN_FAILS.pop(ip, None)
    resp = RedirectResponse("/admin", status_code=302)
    secure = os.environ.get("ADMIN_COOKIE_SECURE", "").lower()
    if secure not in ("0", "false", "no"):
        # 未指定の場合は HTTPS（またはリバースプロキシの X-Forwarded-Proto）なら Secure を付ける
        proto = request.headers.get("x-forwarded-proto", "") or request.url.scheme
        secure = "1" if proto == "https" else ""
    resp.set_cookie(
        _ADMIN_COOKIE,
        _admin_token(),
        max_age=_ADMIN_MAX_AGE,
        httponly=True,
        samesite="strict",
        secure=secure in ("1", "true", "yes"),
    )
    try:
        _get_data_manager().append_log("admin_login", True, f"login ok from {ip}")
    except Exception:
        pass
    return resp

@admin_router.get("/admin/logout")
@admin_router.post("/admin/logout")
async def admin_logout():
    resp = RedirectResponse("/admin/login", status_code=302)
    resp.delete_cookie(_ADMIN_COOKIE)
    return resp

@admin_router.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request):
    if not _is_admin(request):
        return RedirectResponse("/admin/login", status_code=302)
    return templates.TemplateResponse("admin.html", {"request": request})

@admin_router.get("/admin/api/status")
async def admin_status(request: Request):
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        dm = _get_data_manager()
        snapshot = await run_in_threadpool(dm.status_snapshot)
        return JSONResponse(snapshot)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

@admin_router.post("/admin/api/action")
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

print("[OK] Admin routes embedded in server.py → /admin/login , /admin/__ping")

# ==========================================================
#  地域割り当て管理 API（characters.json = {地域名: [キャラ数値ID]}）
# ==========================================================
_REGION_KEY_PATTERN = _re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def _build_admin_character_catalog():
    """全キャラをベースID単位に重複排除したカタログを作る（lists の更新を検知して再構築）。"""
    list_paths = [
        os.path.join(STATIC_DIR, "data", "lists", "characters.json"),
        os.path.join(STATIC_DIR, "beta", "data", "lists", "characters.json"),
    ]
    cache_key = tuple(
        (p, os.path.getmtime(p)) if os.path.exists(p) else (p, None)
        for p in list_paths
    )
    cached = _ADMIN_CATALOG_CACHE.get("entry")
    if cached is not None and _ADMIN_CATALOG_CACHE.get("key") == cache_key:
        return cached

    merged = {}
    for list_path in list_paths:
        if not os.path.exists(list_path):
            continue
        try:
            with open(list_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for char_id, entry in data.items():
            base = str(char_id).split("-")[0]
            if base in merged:
                continue
            merged[base] = {"char_id": str(char_id), "entry": entry or {}}

    catalog = []
    for base in sorted(merged, key=lambda b: int(b)):
        info = merged[base]
        entry = info["entry"]
        icon = None
        for char_dir in (
            os.path.join(STATIC_DIR, "data", "characters"),
            os.path.join(STATIC_DIR, "beta", "data", "characters"),
        ):
            char_json = os.path.join(char_dir, f"{info['char_id']}.json")
            if not os.path.exists(char_json):
                continue
            try:
                with open(char_json, "r", encoding="utf-8") as f:
                    icon = json.load(f).get("icon")
            except Exception:
                icon = None
            if icon:
                break
        icon_path = None
        if icon:
            for assets_dir in (
                os.path.join(STATIC_DIR, "assets", "characters"),
                os.path.join(STATIC_DIR, "beta", "assets", "characters"),
            ):
                if os.path.exists(os.path.join(assets_dir, f"{icon}.webp")):
                    prefix = "static/assets" if "beta" not in assets_dir else "static/beta/assets"
                    icon_path = f"{prefix}/characters/{icon}.webp"
                    break
        catalog.append({
            "id": int(base),
            "name": entry.get("jaName") or entry.get("enName") or base,
            "enName": entry.get("enName"),
            "element": entry.get("element"),
            "icon": icon_path,
        })

    _ADMIN_CATALOG_CACHE["key"] = cache_key
    _ADMIN_CATALOG_CACHE["entry"] = catalog
    return catalog


@admin_router.get("/admin/api/region_map")
async def admin_region_map_get(request: Request):
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        def _run():
            return {
                "ok": True,
                "region_map": _load_region_map(),
                "catalog": _build_admin_character_catalog(),
                "region_images": _list_region_image_names(),
            }
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@admin_router.post("/admin/api/region_map")
async def admin_region_map_save(request: Request):
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "body must be {region: [ids]}"}, status_code=400)

    cleaned = {}
    for region, ids in body.items():
        region = str(region).strip()
        if not _REGION_KEY_PATTERN.match(region):
            return JSONResponse({"ok": False, "error": f"invalid region key: {region}"}, status_code=400)
        if not isinstance(ids, list):
            return JSONResponse({"ok": False, "error": f"invalid ids for {region}"}, status_code=400)
        cleaned_ids = []
        skipped_traveler = 0
        for cid in ids:
            try:
                cid_int = int(cid)
            except (TypeError, ValueError):
                return JSONResponse({"ok": False, "error": f"invalid char id in {region}: {cid}"}, status_code=400)
            if cid_int in _TRAVELER_BASE_IDS:
                # 旅人は元素連動のため編集不可（無視して保存）
                skipped_traveler += 1
                continue
            if cid_int not in cleaned_ids:
                cleaned_ids.append(cid_int)
        cleaned[region] = cleaned_ids

    def _run():
        path = os.path.join(STATIC_DIR, "assets", "characters", "characters.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, indent=2, ensure_ascii=False)
        clear_region_map_cache()
        result = {"ok": True, "regions": {k: len(v) for k, v in cleaned.items()}}
        if skipped_traveler:
            result["notice"] = f"旅人(10000005/10000007)は元素連動のため{skipped_traveler}件をスキップしました"
        return result

    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)