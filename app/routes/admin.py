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

import requests
from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app.paths import BASE_DIR, STATIC_DIR, templates
from app.core.notify import report_error_to_discord
from app.core.jsonio import write_json_atomic
from app.card.bg import _list_region_image_names
from app.card.region import _load_region_map, _TRAVELER_BASE_IDS, clear_region_map_cache
from app.card.scorecard_splash import SCORECARD_SPLASH_OFFSETS_PATH, load_scorecard_splash_offsets
from app.card.stat_calc import INNATE_EM_PATH, load_innate_em_map
from app.card.calc_method import (
    VALID_CALC_METHODS, load_default_calc_method_map, save_default_calc_method_map,
)
from app.card.ui_flags import load_ui_flags, save_ui_flags

admin_router = APIRouter()

# admin カタログの単一エントリキャッシュ（lists の mtime 変化で無効化）
_ADMIN_CATALOG_CACHE: Dict[str, Any] = {"key": None, "entry": None}

_ADMIN_COOKIE = "admin_session"
_ADMIN_MAX_AGE = 60 * 60 * 12
_ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "").strip()
_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()
_ADMIN_SECRET = (os.environ.get("ADMIN_SECRET") or _secrets.token_hex(32)).strip()
_ADMIN_ALLOWED_IPS = {s.strip() for s in os.environ.get("ADMIN_ALLOWED_IPS", "").split(",") if s.strip()}
_ADMIN_MAX_LOGIN_FAILS = int(os.environ.get("ADMIN_MAX_LOGIN_FAILS", "5"))
_ADMIN_LOCK_MINUTES = int(os.environ.get("ADMIN_LOCK_MINUTES", "10"))
_CSP_ENABLED = os.environ.get("CSP_ENABLED", "1").lower() not in ("0", "false", "no")
_CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.tailwindcss.com; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.tailwindcss.com; "
    "font-src 'self' data: https://fonts.gstatic.com; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)
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
    path = request.url.path
    if path.startswith("/static/admin"):
        if not _is_admin(request):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
    # IP 許可リスト（設定時のみ適用）
    if path.startswith("/admin") and _ADMIN_ALLOWED_IPS:
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
    response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    if _CSP_ENABLED:
        response.headers.setdefault("Content-Security-Policy", _CSP_POLICY)
    proto = request.headers.get("x-forwarded-proto", "") or request.url.scheme
    if proto == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
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
        if action == "fetch_beta_nanoka_json_characters":
            return dm.fetch_beta_nanoka_json("characters")
        if action == "fetch_beta_nanoka_json_weapons":
            return dm.fetch_beta_nanoka_json("weapons")
        if action == "fetch_beta_nanoka_json_artifacts":
            return dm.fetch_beta_nanoka_json("artifacts")
        if action == "fetch_beta_nanoka_assets":
            return dm.fetch_beta_nanoka_assets()
        if action == "fetch_beta_nanoka":
            return dm.fetch_beta_nanoka(download_images=True)
        if action == "fetch_beta_nanoka_costumes":
            return dm.fetch_beta_nanoka_costumes()
        if action == "fetch_beta_nanoka_costumes_missing":
            return dm.fetch_beta_nanoka_costumes_missing()
        if action == "fetch_live_nanoka_json":
            return dm.fetch_live_nanoka_json()
        if action == "fetch_live_nanoka_json_characters":
            return dm.fetch_live_nanoka_json("characters")
        if action == "fetch_live_nanoka_json_weapons":
            return dm.fetch_live_nanoka_json("weapons")
        if action == "fetch_live_nanoka_json_artifacts":
            return dm.fetch_live_nanoka_json("artifacts")
        if action == "fetch_live_nanoka_assets":
            return dm.fetch_live_nanoka_assets()
        if action == "fetch_live_nanoka":
            return dm.fetch_live_nanoka()
        if action == "fetch_live_nanoka_costumes":
            return dm.fetch_live_nanoka_costumes()
        if action == "fetch_live_nanoka_costumes_missing":
            return dm.fetch_live_nanoka_costumes_missing()
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
#  チームカード スプラッシュオフセット管理 API
# ==========================================================
_TEAM_SPLASH_OFFSETS_PATH = os.path.join(STATIC_DIR, "data", "setting", "team_splash_offsets.json")
_OLD_TEAM_SPLASH_OFFSETS_PATH = os.path.join(STATIC_DIR, "cache", "team_splash_offsets.json")


def _migrate_team_splash_offsets():
    """旧パス（static/cache）から新パス（static/data/setting）へ移行する。"""
    if not os.path.exists(_TEAM_SPLASH_OFFSETS_PATH) and os.path.exists(_OLD_TEAM_SPLASH_OFFSETS_PATH):
        try:
            os.makedirs(os.path.dirname(_TEAM_SPLASH_OFFSETS_PATH), exist_ok=True)
            import shutil as _shutil
            _shutil.copy2(_OLD_TEAM_SPLASH_OFFSETS_PATH, _TEAM_SPLASH_OFFSETS_PATH)
        except Exception as e:
            print(f"[admin] team splash offsets migration failed: {e}")


@admin_router.get("/admin/api/team_splash_offsets")
async def admin_team_splash_offsets_get(request: Request):
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        def _run():
            _migrate_team_splash_offsets()
            data = {}
            if os.path.exists(_TEAM_SPLASH_OFFSETS_PATH):
                try:
                    with open(_TEAM_SPLASH_OFFSETS_PATH, "r", encoding="utf-8") as f:
                        j = json.load(f)
                    if isinstance(j, dict):
                        data = j.get("offsets") or {}
                except Exception:
                    data = {}
            catalog = _build_admin_character_catalog()
            enriched = []
            for entry in catalog:
                eid = str(entry.get("id"))
                cur = data.get(eid) or {}
                enriched.append({
                    **entry,
                    "raw_id": eid,
                    "x": cur.get("x", 0),
                    "y": cur.get("y", 0),
                })
            return {"ok": True, "offsets": data, "catalog": enriched}
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@admin_router.post("/admin/api/team_splash_offsets")
async def admin_team_splash_offsets_save(request: Request):
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "body must be {charId: {x, y}}"}, status_code=400)

    catalog = _build_admin_character_catalog()
    valid_ids = set()
    for c in catalog:
        if c.get("id") is not None:
            valid_ids.add(str(c.get("id")))
        for cost in c.get("costumes") or []:
            valid_ids.add(f'{c["id"]}:{cost["id"]}')

    cleaned = {}
    for raw_id, pos in body.items():
        raw_id = str(raw_id)
        if raw_id not in valid_ids:
            continue
        if not isinstance(pos, dict):
            continue
        try:
            x = max(-100.0, min(100.0, float(pos.get("x", 0))))
            y = max(-100.0, min(100.0, float(pos.get("y", 0))))
        except (TypeError, ValueError):
            continue
        cleaned[raw_id] = {"x": round(x, 1), "y": round(y, 1)}

    def _run():
        os.makedirs(os.path.dirname(_TEAM_SPLASH_OFFSETS_PATH), exist_ok=True)
        write_json_atomic(_TEAM_SPLASH_OFFSETS_PATH, {"offsets": cleaned})
        return {"ok": True, "updated": len(cleaned)}

    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

# ==========================================================
#  SCORECARD スプラッシュオフセット管理 API
#  （build_card.html の SCORECARD テーマバナー用・ベーススプラッシュのみ）
# ==========================================================
@admin_router.get("/admin/api/scorecard_splash_offsets")
async def admin_scorecard_splash_offsets_get(request: Request):
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        def _run():
            offsets = load_scorecard_splash_offsets() or {}
            cleaned = {}
            for key, val in offsets.items():
                if not isinstance(val, dict):
                    continue
                try:
                    cleaned[str(key)] = {"x": float(val.get("x", 0)), "y": float(val.get("y", 0))}
                except (TypeError, ValueError):
                    continue
            catalog = _build_admin_character_catalog()
            enriched = []
            for entry in catalog:
                eid = str(entry.get("id"))
                cur = cleaned.get(eid) or {}
                enriched.append({
                    **entry,
                    "raw_id": eid,
                    "x": cur.get("x", 0),
                    "y": cur.get("y", 0),
                })
            return {"ok": True, "offsets": cleaned, "catalog": enriched}
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@admin_router.post("/admin/api/scorecard_splash_offsets")
async def admin_scorecard_splash_offsets_save(request: Request):
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "body must be {charId: {x, y}}"}, status_code=400)

    valid_ids = set()
    for c in _build_admin_character_catalog():
        if c.get("id") is not None:
            valid_ids.add(str(c.get("id")))

    cleaned = {}
    for raw_id, pos in body.items():
        raw_id = str(raw_id)
        if raw_id not in valid_ids:
            continue
        if not isinstance(pos, dict):
            continue
        try:
            x = max(-100.0, min(100.0, float(pos.get("x", 0))))
            y = max(-100.0, min(100.0, float(pos.get("y", 0))))
        except (TypeError, ValueError):
            continue
        cleaned[raw_id] = {"x": round(x, 1), "y": round(y, 1)}

    def _run():
        os.makedirs(os.path.dirname(SCORECARD_SPLASH_OFFSETS_PATH), exist_ok=True)
        write_json_atomic(SCORECARD_SPLASH_OFFSETS_PATH, {"offsets": cleaned})
        return {"ok": True, "updated": len(cleaned)}

    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ==========================================================
#  固有元素熟知管理 API
# ==========================================================
@admin_router.get("/admin/api/innate_em")
async def admin_innate_em_get(request: Request):
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        def _run():
            chars = load_innate_em_map() or {}
            catalog = _build_admin_character_catalog()
            enriched = []
            for entry in catalog:
                eid = str(entry.get("id"))
                enriched.append({
                    **entry,
                    "raw_id": eid,
                    "em": chars.get(eid, 0),
                })
            return {"ok": True, "characters": chars, "catalog": enriched}
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@admin_router.post("/admin/api/innate_em")
async def admin_innate_em_save(request: Request):
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "body must be {characters: {charId: number}}"}, status_code=400)

    chars = body.get("characters")
    if not isinstance(chars, dict):
        return JSONResponse({"ok": False, "error": "body.characters must be {charId: number}"}, status_code=400)

    valid_ids = set()
    for c in _build_admin_character_catalog():
        if c.get("id") is not None:
            valid_ids.add(str(c.get("id")))

    cleaned = {}
    for raw_id, val in chars.items():
        raw_id = str(raw_id)
        if raw_id not in valid_ids:
            continue
        try:
            em = float(val)
        except (TypeError, ValueError):
            continue
        if em > 0:
            cleaned[raw_id] = round(em, 1)

    def _run():
        os.makedirs(os.path.dirname(INNATE_EM_PATH), exist_ok=True)
        write_json_atomic(INNATE_EM_PATH, {"characters": cleaned})
        return {"ok": True, "updated": len(cleaned)}

    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ==========================================================
#  キャラ毎デフォルト計算方式（calc_method）管理 API
# ==========================================================
@admin_router.get("/admin/api/default_calc_method")
async def admin_default_calc_method_get(request: Request):
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        def _run():
            chars = load_default_calc_method_map() or {}
            catalog = _build_admin_character_catalog()
            enriched = []
            for entry in catalog:
                eid = str(entry.get("id"))
                enriched.append({
                    **entry,
                    "raw_id": eid,
                    "method": chars.get(eid, ""),
                })
            return {
                "ok": True,
                "characters": chars,
                "methods": list(VALID_CALC_METHODS),
                "catalog": enriched,
            }
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@admin_router.post("/admin/api/default_calc_method")
async def admin_default_calc_method_save(request: Request):
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "body must be {characters: {charId: method}}"}, status_code=400)

    chars = body.get("characters")
    if not isinstance(chars, dict):
        return JSONResponse({"ok": False, "error": "body.characters must be {charId: method}"}, status_code=400)

    valid_ids = set()
    for c in _build_admin_character_catalog():
        if c.get("id") is not None:
            valid_ids.add(str(c.get("id")))

    cleaned_input = {}
    for raw_id, method in chars.items():
        raw_id = str(raw_id)
        if raw_id not in valid_ids:
            continue
        cleaned_input[raw_id] = method

    def _run():
        cleaned = save_default_calc_method_map(cleaned_input)
        return {"ok": True, "updated": len(cleaned)}

    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ==========================================================
#  UI 表示フラグ管理 API
#  （build_card.html の編成スロット管理/幽境モードボタンの表示切替）
# ==========================================================
@admin_router.get("/admin/api/ui_flags")
async def admin_ui_flags_get(request: Request):
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        return JSONResponse({"ok": True, "flags": await run_in_threadpool(load_ui_flags)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@admin_router.post("/admin/api/ui_flags")
async def admin_ui_flags_save(request: Request):
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "body must be {flags: {...}}"}, status_code=400)
    flags = body.get("flags", body)
    if not isinstance(flags, dict):
        return JSONResponse({"ok": False, "error": "flags must be an object"}, status_code=400)
    try:
        cleaned = await run_in_threadpool(save_ui_flags, flags)
        return JSONResponse({"ok": True, "flags": cleaned})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ==========================================================
#  Leyline（レイライン）データ取得 API
#  static.nanoka.cc/gi/{version}/leyline.json を取得する
# ==========================================================
_LEYLINE_VERSION_PATTERN = _re.compile(r"^\d+\.\d+(?:\.\d+)?$")
_LEYLINE_ENEMY_DIR = os.path.join(STATIC_DIR, "assets", "leyline")


def _nanoka_live_version():
    """nanoka マニフェストから現行（live）バージョンを返す。失敗時は None。"""
    try:
        r = requests.get("https://static.nanoka.cc/manifest.json", timeout=15)
        r.raise_for_status()
        return (r.json().get("gi") or {}).get("live")
    except Exception as e:
        print(f"[leyline] manifest fetch failed: {e}")
        return None


def _version_tuple(v):
    return tuple(int(x) for x in str(v or "").split(".") if x.isdigit())


def _leyline_dirs(version, live):
    """現行バージョン（live）より先のバージョンは beta 領域へ、live 以前は live 領域へ画像を置く。"""
    if live and _version_tuple(version) > _version_tuple(live):
        return (
            os.path.join(STATIC_DIR, "beta", "assets", "leyline"),
            "/static/beta/assets/leyline",
        )
    return (_LEYLINE_ENEMY_DIR, "/static/assets/leyline")


def _download_leyline_enemy_icon(leyline_id, icon, target_dir, url_prefix):
    """敵アイコンをローカルへ保存し、公開URLを返す（保存済みなら再利用）。"""
    dest_dir = os.path.join(target_dir, str(leyline_id))
    try:
        os.makedirs(dest_dir, exist_ok=True)
        path = os.path.join(dest_dir, f"{icon}.webp")
        if not os.path.exists(path):
            r = requests.get(f"https://static.nanoka.cc/assets/gi/{icon}.webp", timeout=15)
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)
        return f"{url_prefix}/{leyline_id}/{icon}.webp"
    except Exception as e:
        print(f"[leyline] enemy icon download failed {icon}: {e}")
        return ""


def _fetch_leyline_detail(version, id, live):
    """レイライン詳細を取得し、敵画像をローカルへダウンロードして詳細dictを返す。"""
    url = f"https://static.nanoka.cc/gi/{version}/ja/leyline/{id}.json"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    target_dir, url_prefix = _leyline_dirs(version, live)
    monsters = {}
    for lv_key, lv in (data.get("level") or {}).items():
        for cfg_key, cfg in (lv.get("level_config") or {}).items():
            if not isinstance(cfg, dict):
                continue
            icon = cfg.get("icon") or ""
            if icon and icon not in monsters:
                monsters[icon] = {"name": cfg.get("name") or "", "icon": icon}
    monster_list = []
    for m in monsters.values():
        local_img = _download_leyline_enemy_icon(id, m["icon"], target_dir, url_prefix)
        monster_list.append({"name": m["name"], "icon": m["icon"], "img": local_img})
    return {
        "ok": True,
        "version": version,
        "id": id,
        "name": data.get("name") or "",
        "begin_time": data.get("begin_time") or "",
        "end_time": data.get("end_time") or "",
        "monsters": monster_list,
    }


_LEYLINE_VERSIONS_CONFIG_PATH = os.path.join(STATIC_DIR, "admin", "leyline_versions.json")


def _load_leyline_versions_config():
    try:
        with open(_LEYLINE_VERSIONS_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("versions") or []
    except Exception:
        return []


def _save_leyline_versions_config(versions):
    os.makedirs(os.path.dirname(_LEYLINE_VERSIONS_CONFIG_PATH), exist_ok=True)
    write_json_atomic(_LEYLINE_VERSIONS_CONFIG_PATH, {"versions": versions})


@admin_router.get("/admin/api/leyline_versions")
async def admin_leyline_versions(request: Request):
    """nanoka マニフェストの live/latest/available と、admin で追加したバージョン一覧を返す。"""
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        def _run():
            r = requests.get("https://static.nanoka.cc/manifest.json", timeout=15)
            r.raise_for_status()
            gi = r.json().get("gi") or {}
            return {
                "ok": True,
                "live": gi.get("live"),
                "latest": gi.get("latest"),
                "available": gi.get("available") or [],
                "saved_versions": _load_leyline_versions_config(),
            }
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@admin_router.post("/admin/api/leyline_versions")
async def admin_leyline_versions_save(request: Request):
    """admin で追加したバージョン一覧を保存する（body: {versions: [...]}）。"""
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
    versions = body.get("versions")
    if not isinstance(versions, list):
        return JSONResponse({"ok": False, "error": "versions は配列で指定してください"}, status_code=400)
    cleaned = []
    for v in versions:
        v = str(v).strip()
        if v and _LEYLINE_VERSION_PATTERN.match(v) and v not in cleaned:
            cleaned.append(v)
    try:
        await run_in_threadpool(_save_leyline_versions_config, cleaned)
        return JSONResponse({"ok": True, "saved_versions": cleaned})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


def _leyline_list_path(version, live):
    """変換済みレイラインJSONの保存/読み込みパス（static/data 直下。現行より先のバージョンは beta 領域）。"""
    if live and _version_tuple(version) > _version_tuple(live):
        return os.path.join(STATIC_DIR, "beta", "data", f"leyline_{version}.json")
    return os.path.join(STATIC_DIR, "data", f"leyline_{version}.json")


@admin_router.get("/admin/api/leyline_data")
async def admin_leyline_data(request: Request, version: str = ""):
    """保存済みの変換済みレイラインJSONを返す（無ければ not_found）。"""
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    version = (version or "").strip()
    if not _LEYLINE_VERSION_PATTERN.match(version):
        return JSONResponse({"ok": False, "error": "バージョン形式が不正です（例: 7.0）"}, status_code=400)

    def _run():
        live = _nanoka_live_version()
        path = _leyline_list_path(version, live)
        if not os.path.exists(path):
            return {"ok": False, "not_found": True, "version": version}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {"ok": True, "version": version, "count": len(data) if isinstance(data, dict) else 0, "data": data}

    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@admin_router.get("/admin/api/leyline_detail")
async def admin_leyline_detail(request: Request, version: str = "", id: str = ""):
    """レイライン詳細（gi/{version}/ja/leyline/{id}.json）から敵3体の情報を抽出。"""
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    version = (version or "").strip()
    id = (id or "").strip()
    if not _LEYLINE_VERSION_PATTERN.match(version):
        return JSONResponse({"ok": False, "error": "バージョン形式が不正です（例: 7.0）"}, status_code=400)
    if not id.isdigit():
        return JSONResponse({"ok": False, "error": "レイラインIDが不正です"}, status_code=400)

    def _run():
        live = _nanoka_live_version()
        return _fetch_leyline_detail(version, id, live)

    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@admin_router.get("/admin/api/leyline")
async def admin_leyline(request: Request, version: str = ""):
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    version = (version or "").strip()
    if not _LEYLINE_VERSION_PATTERN.match(version):
        return JSONResponse({"ok": False, "error": "バージョン形式が不正です（例: 6.7.54）"}, status_code=400)
    url = f"https://static.nanoka.cc/gi/{version}/leyline.json"

    def _run():
        live = _nanoka_live_version()
        # 変換済みJSONの保存先（現行より先のバージョンは beta 領域）
        cache_path = _leyline_list_path(version, live)
        # 基本はネットワークから取得し、失敗時のみ変換済みデータにフォールバック
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()
            # 取得した全レイラインの詳細 + 敵画像を取得し、変換済みJSONを作成
            live = _nanoka_live_version()
            downloaded = 0
            enemy_ids = []
            details = {}
            for lid in (data.keys() if isinstance(data, dict) else []):
                try:
                    det = _fetch_leyline_detail(version, str(lid), live)
                    details[str(lid)] = det
                    n = len(det.get("monsters") or [])
                    if n:
                        downloaded += n
                        enemy_ids.append(str(lid))
                except Exception as e:
                    print(f"[leyline] detail download failed id={lid}: {e}")
            from app.convert.leyline import from_nanoka
            converted = from_nanoka(data, details)
            try:
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                write_json_atomic(cache_path, converted)
            except Exception:
                pass
            return {
                "ok": True,
                "version": version,
                "count": len(converted) if isinstance(converted, dict) else 0,
                "data": converted,
                "cached": False,
                "enemy_downloaded": downloaded,
                "enemy_ids": enemy_ids,
            }
        except Exception as e:
            if os.path.exists(cache_path):
                try:
                    with open(cache_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return {"ok": True, "version": version, "count": len(data) if isinstance(data, dict) else 0, "data": data, "cached": True, "warning": f"取得失敗のため変換済みデータを使用: {e}"}
                except Exception:
                    pass
            return {"ok": False, "error": str(e)}

    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

# ==========================================================
#  地域割り当て管理 API（characters.json = {地域名: [キャラ数値ID]}）
# ==========================================================
_REGION_KEY_PATTERN = _re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def _find_asset_path(subdir, name, ext=".webp"):
    """live → beta の順でアセットを探し、公開URL（static/...）を返す。なければ None。"""
    if not name:
        return None
    for assets_dir in (
        os.path.join(STATIC_DIR, "assets"),
        os.path.join(STATIC_DIR, "beta", "assets"),
    ):
        if os.path.exists(os.path.join(assets_dir, subdir, f"{name}{ext}")):
            prefix = "static/assets" if "beta" not in assets_dir else "static/beta/assets"
            return f"{prefix}/{subdir}/{name}{ext}"
    return None


def _build_admin_character_catalog():
    """全キャラをベースID単位に重複排除したカタログを作る（lists の更新を検知して再構築）。
    各エントリには通常スプラッシュの他、所持コスチューム一覧（costumes）を含める。"""
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
        char_data = None
        for char_dir in (
            os.path.join(STATIC_DIR, "data", "characters"),
            os.path.join(STATIC_DIR, "beta", "data", "characters"),
        ):
            char_json = os.path.join(char_dir, f"{info['char_id']}.json")
            if not os.path.exists(char_json):
                continue
            try:
                with open(char_json, "r", encoding="utf-8") as f:
                    char_data = json.load(f)
                    icon = char_data.get("icon")
            except Exception:
                icon = None
            if icon:
                break

        icon_path = _find_asset_path("characters", icon)

        # 通常スプラッシュ。UI_AvatarIcon_* → UI_Gacha_AvatarImg_*
        splash_path = None
        if icon:
            splash_path = _find_asset_path("splash", str(icon).replace("AvatarIcon", "Gacha_AvatarImg"))
        if not splash_path:
            splash_path = icon_path

        # コスチューム一覧（デフォルト衣装＝アイコン空はスキップ）
        costumes = []
        if char_data and isinstance(char_data, dict):
            for c in char_data.get("costume") or []:
                if not isinstance(c, dict):
                    continue
                c_icon = str(c.get("icon") or "")
                if not c_icon:
                    continue
                c_splash = _find_asset_path("splash", c_icon.replace("AvatarIcon", "Costume"))
                c_icon_path = _find_asset_path("characters", c_icon)
                if not c_splash:
                    c_splash = c_icon_path
                costumes.append({
                    "id": int(c.get("id", 0)),
                    "icon": c_icon_path,
                    "splash": c_splash,
                    "quality": int(c.get("quality", 0)),
                })

        catalog.append({
            "id": int(base),
            "raw_id": str(base),
            "name": entry.get("jaName") or entry.get("enName") or base,
            "enName": entry.get("enName"),
            "element": entry.get("element"),
            "icon": icon_path,
            "splash": splash_path,
            "costumes": costumes,
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
        write_json_atomic(path, cleaned)
        clear_region_map_cache()
        result = {"ok": True, "regions": {k: len(v) for k, v in cleaned.items()}}
        if skipped_traveler:
            result["notice"] = f"旅人(10000005/10000007)は元素連動のため{skipped_traveler}件をスキップしました"
        return result

    try:
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ----------------------------------------------------------
#  地域表示ラベル（ja / en）。フロントの地域名表示に使う。
#  static/data/setting/region_labels.json: {key: {ja, en}}
# ----------------------------------------------------------
def _region_labels_path() -> str:
    return os.path.join(STATIC_DIR, "data", "setting", "region_labels.json")


def _load_region_labels() -> dict:
    try:
        with open(_region_labels_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@admin_router.get("/admin/api/region_labels")
async def admin_region_labels_get(request: Request):
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return JSONResponse({"ok": True, "labels": _load_region_labels()})


@admin_router.post("/admin/api/region_labels")
async def admin_region_labels_save(request: Request):
    """地域ラベルの保存（{key: {ja, en}} のマージ。追加時は日本語/英語両方を入力してもらう）。"""
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "body must be {key: {ja, en}}"}, status_code=400)

    merged = _load_region_labels()
    for key, labels in body.items():
        key = str(key).strip()
        if not _REGION_KEY_PATTERN.match(key):
            return JSONResponse({"ok": False, "error": f"invalid region key: {key}"}, status_code=400)
        if not isinstance(labels, dict):
            continue
        ja = str(labels.get("ja") or "").strip()
        en = str(labels.get("en") or "").strip()
        if not ja and not en:
            continue
        entry = merged.get(key) if isinstance(merged.get(key), dict) else {}
        if ja:
            entry["ja"] = ja
        if en:
            entry["en"] = en
        merged[key] = entry

    try:
        def _run():
            path = _region_labels_path()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            write_json_atomic(path, merged)
            return {"ok": True, "labels": merged}
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# ----------------------------------------------------------
#  アセット欠落チェック: キャラJSONが参照するアイコンの内、
#  ローカルに無いものを列挙し、CDNから一括再取得する。
#  （初期ダウンロード時の取得漏れの再発防止用）
# ----------------------------------------------------------
def _scan_character_asset_refs(mode: str = "live") -> Dict[str, Any]:
    """キャラJSONが参照するアイコンと、ローカルに存在しないものを返す。

    種類ごとに保存先が異なる（誤検出防止のため kind を見て判定する）:
      - icon（キャラアバターアイコン）→ static/assets/characters/{icon}.webp
        （beta モード時は static/beta/assets/characters/ を優先。逆サイドもフォールバック参照）
      - skills / passives / constellations → static/assets/skills/{icon}.webp
    """
    if mode == "beta":
        char_dir = os.path.join(STATIC_DIR, "beta", "data", "characters")
        skills_dir = os.path.join(STATIC_DIR, "beta", "assets", "skills")
        skills_dirs = [skills_dir, os.path.join(STATIC_DIR, "assets", "skills")]
        icon_dirs = [
            os.path.join(STATIC_DIR, "beta", "assets", "characters"),
            os.path.join(STATIC_DIR, "assets", "characters"),
        ]
    else:
        char_dir = os.path.join(STATIC_DIR, "data", "characters")
        skills_dir = os.path.join(STATIC_DIR, "assets", "skills")
        skills_dirs = [skills_dir, os.path.join(STATIC_DIR, "beta", "assets", "skills")]
        icon_dirs = [
            os.path.join(STATIC_DIR, "assets", "characters"),
            os.path.join(STATIC_DIR, "beta", "assets", "characters"),
        ]

    refs, missing = [], []
    if not os.path.isdir(char_dir):
        return {"refs": refs, "missing": missing, "skills_dir": skills_dir, "icon_dirs": icon_dirs}
    for fn in sorted(os.listdir(char_dir)):
        if not fn.endswith(".json"):
            continue
        char_id = fn[:-5]
        try:
            with open(os.path.join(char_dir, fn), "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        icons = []
        if data.get("icon"):
            icons.append(("icon", str(data["icon"])))
        for key in ("skills", "passives", "constellations"):
            for item in data.get(key) or []:
                if isinstance(item, dict) and item.get("icon"):
                    icons.append((key, str(item["icon"])))
        for kind, icon in icons:
            # icon（アバターアイコン）は characters ディレクトリが正しい保存先
            if kind == "icon":
                exists = any(os.path.exists(os.path.join(d, f"{icon}.webp")) for d in icon_dirs)
            else:
                exists = any(os.path.exists(os.path.join(d, f"{icon}.webp")) for d in skills_dirs)
            entry = {"char": char_id, "kind": kind, "icon": icon}
            refs.append(entry)
            if not exists:
                missing.append(entry)
    return {"refs": refs, "missing": missing, "skills_dir": skills_dir, "icon_dirs": icon_dirs}


@admin_router.get("/admin/api/assets/missing_check")
async def admin_assets_missing_check(request: Request, mode: str = "live"):
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    mode = "beta" if str(mode).lower() == "beta" else "live"

    def _run():
        result = _scan_character_asset_refs(mode)
        # CDN に存在するかも確認（再取得できる見込みの有無を admin に見せる）
        checked = []
        for m in result["missing"]:
            available = None
            try:
                r = requests.head(f"https://static.nanoka.cc/assets/gi/{m['icon']}.webp", timeout=8)
                available = r.status_code == 200
            except Exception:
                available = None
            checked.append({**m, "cdn": available})
        return {"ok": True, "mode": mode, "total_refs": len(result["refs"]), "missing": checked}

    return JSONResponse(await run_in_threadpool(_run))


@admin_router.post("/admin/api/assets/missing_fix")
async def admin_assets_missing_fix(request: Request, mode: str = "live"):
    """欠落アイコンを CDN から一括再ダウンロードする。"""
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    mode = "beta" if str(mode).lower() == "beta" else "live"

    def _run():
        result = _scan_character_asset_refs(mode)
        skills_dir = result["skills_dir"]
        icon_dirs = result.get("icon_dirs") or [os.path.join(STATIC_DIR, "assets", "characters")]
        os.makedirs(skills_dir, exist_ok=True)
        for d in icon_dirs:
            os.makedirs(d, exist_ok=True)
        fixed, failed = [], []
        for m in result["missing"]:
            icon = m["icon"]
            # icon（アバターアイコン）は characters ディレクトリへ保存する
            dest_dir = icon_dirs[0] if m.get("kind") == "icon" else skills_dir
            dest = os.path.join(dest_dir, f"{icon}.webp")
            try:
                r = requests.get(f"https://static.nanoka.cc/assets/gi/{icon}.webp", timeout=15)
                r.raise_for_status()
                with open(dest, "wb") as f:
                    f.write(r.content)
                fixed.append(icon)
            except Exception as e:
                failed.append({"icon": icon, "error": str(e)})
        return {"ok": True, "mode": mode, "fixed": fixed, "failed": failed,
                "still_missing": len(result["missing"]) - len(fixed)}

    return JSONResponse(await run_in_threadpool(_run))


# ----------------------------------------------------------
#  ネームカード / プロフアイコンの一括取得
#  enka assets の namecards.json / pfps.json に基づき、
#  enka.network CDN（一次）→ nanoka CDN（代替）の順で取得する。
# ----------------------------------------------------------
_NAMECARD_DIR = os.path.join(STATIC_DIR, "assets", "namecards")
_PFP_DIR = os.path.join(STATIC_DIR, "assets", "pfps")


def _load_enka_asset_map(filename: str) -> dict:
    try:
        with open(os.path.join(BASE_DIR, "external", "enka_py", "assets", filename), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _bulk_fetch_namecards_pfps() -> Dict[str, Any]:
    namecards = _load_enka_asset_map("namecards.json")
    pfps = _load_enka_asset_map("pfps.json")
    os.makedirs(_NAMECARD_DIR, exist_ok=True)
    os.makedirs(_PFP_DIR, exist_ok=True)

    def dl_multi(icon_base: str, dest: str) -> bool:
        """enka → nanoka の順で webp/png を試す。"""
        urls = [
            f"https://enka.network/ui/{icon_base}.png",
            f"https://static.nanoka.cc/assets/gi/{icon_base}.webp",
        ]
        for url in urls:
            try:
                r = requests.get(url, timeout=15)
                if r.status_code == 200 and r.content:
                    with open(dest, "wb") as f:
                        f.write(r.content)
                    return True
            except Exception:
                continue
        return False

    nc_new = nc_skip = nc_fail = 0
    for nc_id, entry in namecards.items():
        icon = entry.get("icon", "")
        if not icon:
            continue
        dest = os.path.join(_NAMECARD_DIR, f"{nc_id}.png")
        if os.path.exists(dest):
            nc_skip += 1
            continue
        # icon は UI_NameCardPic_xxx_P 形式（フルネーム）
        if dl_multi(icon, dest):
            nc_new += 1
        else:
            nc_fail += 1

    pfp_new = pfp_skip = pfp_fail = 0
    for pfp_id, entry in pfps.items():
        icon = entry.get("iconPath", "")
        if not icon:
            continue
        dest = os.path.join(_PFP_DIR, f"{pfp_id}.png")
        if os.path.exists(dest):
            pfp_skip += 1
            continue
        # icon は UI_AvatarIcon_xxx_Circle 形式（フルネーム）
        if dl_multi(icon, dest):
            pfp_new += 1
        else:
            pfp_fail += 1

    return {
        "namecards": {"total": len(namecards), "new": nc_new, "skipped": nc_skip, "failed": nc_fail},
        "pfps": {"total": len(pfps), "new": pfp_new, "skipped": pfp_skip, "failed": pfp_fail},
    }


@admin_router.get("/admin/api/assets/namecards_status")
async def admin_assets_namecards_status(request: Request):
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)

    def _run():
        namecards = _load_enka_asset_map("namecards.json")
        pfps = _load_enka_asset_map("pfps.json")
        nc_local = len([f for f in os.listdir(_NAMECARD_DIR) if f.endswith(".png")]) if os.path.isdir(_NAMECARD_DIR) else 0
        pfp_local = len([f for f in os.listdir(_PFP_DIR) if f.endswith(".png")]) if os.path.isdir(_PFP_DIR) else 0
        # nanoka 取得を実行したことがあれば、マージ後の期待件数を state ファイルから使う
        state = {}
        try:
            with open(_ASSET_FETCH_STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
            if not isinstance(state, dict):
                state = {}
        except Exception:
            state = {}
        nc_expected = max(len(namecards), int(state.get("namecards", {}).get("total") or 0))
        pfp_expected = max(len(pfps), int(state.get("pfps", {}).get("total") or 0))
        return {
            "ok": True,
            "namecards": {"expected": nc_expected, "local": nc_local},
            "pfps": {"expected": pfp_expected, "local": pfp_local},
            "last_nanoka_fetch": state.get("last_fetch") or None,
            "last_sources": state.get("sources") or None,
        }

    return JSONResponse(await run_in_threadpool(_run))


@admin_router.post("/admin/api/assets/namecards_fetch_nanoka")
async def admin_assets_namecards_fetch_nanoka(request: Request):
    """nanoka（character.json live/beta + Amber ネームカード一覧）から一括取得する。

    - 既存ファイルはスキップ
    - 画像の取得先は nanoka CDN 優先（見つからなければ enka CDN）
    - 実行後、マージ後の期待件数を state ファイルに保存する
    """
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)

    def _run():
        result = _bulk_fetch_namecards_pfps_nanoka()
        # ステータス表示用にマージ後の期待件数を保存
        try:
            os.makedirs(os.path.dirname(_ASSET_FETCH_STATE_PATH), exist_ok=True)
            write_json_atomic(_ASSET_FETCH_STATE_PATH, {
                "namecards": result.get("namecards") or {},
                "pfps": result.get("pfps") or {},
                "versions": result.get("versions") or {},
                "sources": result.get("sources") or {},
                "last_fetch": _time.strftime("%Y-%m-%d %H:%M:%S"),
            })
        except Exception as e:
            print(f"[Warn] asset fetch state の保存に失敗しました: {e}")
        try:
            nc = result.get("namecards") or {}
            pfp = result.get("pfps") or {}
            _get_data_manager().append_log(
                "assets_nanoka_fetch", True,
                f"src={result.get('sources', {}).get('namecard_list')} / "
                f"nc new={nc.get('new')} fail={nc.get('failed')} / pfp new={pfp.get('new')} fail={pfp.get('failed')}",
            )
        except Exception:
            pass
        return result

    return JSONResponse(await run_in_threadpool(_run))


@admin_router.post("/admin/api/assets/namecards_fetch_lunaris")
async def admin_assets_namecards_fetch_lunaris(request: Request):
    """lunaris の materiallist のみをソースとして不足分を取得する（lunaris 単体検証用）。"""
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)

    def _run():
        result = _bulk_fetch_namecards_pfps_lunaris()
        try:
            nc = result.get("namecards") or {}
            pfp = result.get("pfps") or {}
            _get_data_manager().append_log(
                "assets_lunaris_fetch", bool(result.get("ok")),
                f"nc new={nc.get('new')} fail={nc.get('failed')} / pfp new={pfp.get('new')} fail={pfp.get('failed')}",
            )
        except Exception:
            pass
        return result

    return JSONResponse(await run_in_threadpool(_run))


@admin_router.post("/admin/api/assets/namecards_fetch")
async def admin_assets_namecards_fetch(request: Request):
    """ネームカード / プロフアイコンを一括取得（既存ファイルはスキップ）。"""
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)

    def _run():
        return _bulk_fetch_namecards_pfps()

    return JSONResponse(await run_in_threadpool(_run))


# ----------------------------------------------------------
#  nanoka 由来のネームカード / プロフアイコン一括取得
#  - プロフアイコン: nanoka の character.json（live / beta 両方）から
#    キャラ Circle アイコン（pfp ID = キャラ baseID）を導出。
#    ※ enka の pfps.json にはキャラ系 pfp が含まれず完全に欠落している
#  - ネームカード: nanoka には数値IDの一覧が無いため、
#    Project Amber の namecard 一覧（enka より新しく 210294 まで）を
#    数値IDソースとして使い、画像実体は nanoka CDN から取得する。
#  - 既存の enka assets マップもマージして欠落分だけ取得する。
# ----------------------------------------------------------
_AMBER_NAMECARD_URL = "https://gi.yatta.moe/api/v2/en/namecard"
# lunaris.moe: materiallist.json の MATERIAL_NAMECARD が数値ID→アイコン名の一覧
# （293種と enka/Amber より新しいうえ、version.json がバージョン自動追従。画像CDNもあり）
_LUNARIS_VERSION_URL = "https://api.lunaris.moe/data/version.json"
_LUNARIS_MATERIALLIST_URL = "https://api.lunaris.moe/data/{}/materiallist.json"
_LUNARIS_NAMECARDPIC_URL = "https://api.lunaris.moe/data/assets/namecardpic/{}.png"
# lunaris のアバターアイコン CDN（MATERIAL_AVATAR の icon は UI_AvatarIcon_xxx_Card 形式。
# 画像実体は Card 無しの UI_AvatarIcon_xxx.png で置かれているため Card を外して要求する）
_LUNARIS_AVATARICON_URL = "https://api.lunaris.moe/data/assets/avataricon/{}.png"
# pizza-studio/EnkaDBGenerator（自動生成のゲームデータ）:
# pfps.json は enka 本家より新しく（キャラポートレート系 11700/11800 を含む）、
# namecards.json も enka 本家（store/）より新しいため数値IDソースとして使う。
_PIZZA_GI_URL = "https://raw.githubusercontent.com/pizza-studio/EnkaDBGenerator/main/Sources/EnkaDBFiles/Resources/Specimen/GI/{}.json"
# 注意: アイコン名に数字のみのもの（UI_NameCardIcon_0 等）があるため
# str.format は使わず f-string / 連結で URL を組み立てる
_NANOKA_CDN_PREFIX = "https://static.nanoka.cc/assets/gi/"
_ENKA_UI_PREFIX = "https://enka.network/ui/"
_ASSET_FETCH_STATE_PATH = os.path.join(STATIC_DIR, "admin", "asset_fetch_state.json")


def _nanoka_versions() -> tuple:
    """nanoka manifest から (live, beta) のバージョン文字列を返す。"""
    try:
        r = requests.get("https://static.nanoka.cc/manifest.json", timeout=15)
        r.raise_for_status()
        gi = r.json().get("gi") or {}
        live = str(gi.get("live") or gi.get("latest") or "")
        beta = str(gi.get("latest") or live)
        return live, beta
    except Exception as e:
        print(f"[Warn] nanoka manifest 取得失敗: {e}")
        return "", ""


def _nanoka_character_names(version: str) -> Dict[str, str]:
    """nanoka の character.json から {キャラID: ベース名} を返す（失敗時は空）。"""
    if not version:
        return {}
    try:
        r = requests.get(f"https://static.nanoka.cc/gi/{version}/character.json", timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[Warn] nanoka character.json 取得失敗 ({version}): {e}")
        return {}
    out = {}
    for cid, entry in data.items():
        if not str(cid).isdigit():
            continue
        icon = str((entry or {}).get("icon") or "")
        if icon.startswith("UI_AvatarIcon_") and len(icon) > len("UI_AvatarIcon_"):
            out[str(cid)] = icon[len("UI_AvatarIcon_"):]
    return out


def _amber_namecard_bases() -> Dict[str, str]:
    """Project Amber の namecard 一覧から {数値ID: アイコンbase名} を返す（失敗時は空）。

    Amber の icon は UI_NameCardIcon_xxx 形式（背景画像は UI_NameCardPic_xxx_P）。
    """
    try:
        r = requests.get(_AMBER_NAMECARD_URL, timeout=20)
        r.raise_for_status()
        items = (r.json().get("data") or {}).get("items") or {}
    except Exception as e:
        print(f"[Warn] Amber namecard 一覧の取得に失敗しました: {e}")
        return {}
    out = {}
    for cid, entry in items.items():
        icon = str((entry or {}).get("icon") or "")
        if not icon or not str(cid).isdigit():
            continue
        base = icon.replace("UI_NameCardIcon_", "").replace("UI_NameCardPic_", "")
        if base.endswith("_P"):
            base = base[:-2]
        if base:
            out[str(cid)] = base
    return out


def _load_lunaris_namecards() -> Dict[str, str]:
    """lunaris.moe の materiallist から {数値ID: アイコンbase名} を返す（失敗時は空）。

    MATERIAL_NAMECARD タイプがネームカード一覧（293種・210298まで）。icon は
    UI_NameCardIcon_xxx / UI_NameCardPic_xxx_P のいずれかの形式で返る。
    """
    try:
        v = requests.get(_LUNARIS_VERSION_URL, timeout=15)
        v.raise_for_status()
        version = str((v.json() or {}).get("version") or "")
        if not version:
            return {}
        r = requests.get(_LUNARIS_MATERIALLIST_URL.format(version), timeout=30)
        r.raise_for_status()
        materials = r.json()
    except Exception as e:
        print(f"[Warn] lunaris materiallist の取得に失敗しました: {e}")
        return {}
    out = {}
    for cid, entry in (materials or {}).items():
        if not str(cid).isdigit() or (entry or {}).get("type") != "MATERIAL_NAMECARD":
            continue
        icon = str(entry.get("icon") or "")
        base = icon.replace("UI_NameCardIcon_", "").replace("UI_NameCardPic_", "")
        if base.endswith("_P"):
            base = base[:-2]
        if base:
            out[str(cid)] = base
    return out


def _load_lunaris_avatar_icons() -> Dict[str, str]:
    """lunaris.moe の materiallist から {数値ID: アバターアイコン名} を返す（失敗時は空）。

    MATERIAL_AVATAR タイプ（131種）がキャラアイコン一覧。icon は
    UI_AvatarIcon_xxx_Card 形式だが CDN 上は Card 無しの UI_AvatarIcon_xxx.png。
    """
    try:
        v = requests.get(_LUNARIS_VERSION_URL, timeout=15)
        v.raise_for_status()
        version = str((v.json() or {}).get("version") or "")
        if not version:
            return {}
        r = requests.get(_LUNARIS_MATERIALLIST_URL.format(version), timeout=30)
        r.raise_for_status()
        materials = r.json()
    except Exception as e:
        print(f"[Warn] lunaris materiallist の取得に失敗しました: {e}")
        return {}
    out = {}
    for cid, entry in (materials or {}).items():
        if not str(cid).isdigit() or (entry or {}).get("type") != "MATERIAL_AVATAR":
            continue
        icon = str(entry.get("icon") or "")
        if icon.startswith("UI_AvatarIcon_") and len(icon) > len("UI_AvatarIcon_"):
            base = icon[len("UI_AvatarIcon_"):]
            if base.endswith("_Card"):
                base = base[: -len("_Card")]
            if base:
                out[str(cid)] = f"UI_AvatarIcon_{base}"
    return out


def _load_pizza_asset_map(filename: str) -> Dict[str, Any]:
    """pizza-studio/EnkaDBGenerator の pfps.json / namecards.json を取得する（失敗時は空）。"""
    try:
        r = requests.get(_PIZZA_GI_URL.format(filename), timeout=20)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"[Warn] pizza-studio {filename} の取得に失敗しました: {e}")
        return {}


def _dl_first(urls: list, dest: str) -> bool:
    """候補URLのうち最初に成功したものを dest に保存する。"""
    for url in urls:
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200 and r.content:
                with open(dest, "wb") as f:
                    f.write(r.content)
                return True
        except Exception:
            continue
    return False


def _bulk_fetch_namecards_pfps_nanoka() -> Dict[str, Any]:
    """不足分のネームカード / プロフアイコンを取得する。

    ソース依存の優先順位（上にあるものが取れたら下は呼ばない）:
      ネームカードID一覧: lunaris 単体 → amber → enka（ローカル固定一覧）
      プロフアイコンID一覧: nanoka（キャラ系・live/beta 自動追従）→ enka（写真系）→ pizza-studio（ポートレート系のギャップ補完）
    画像のダウンロードは nanoka CDN 優先（無ければ lunaris CDN / enka CDN）。
    """
    live_ver, beta_ver = _nanoka_versions()
    enka_nc = _load_enka_asset_map("namecards.json")
    enka_pfp = _load_enka_asset_map("pfps.json")

    # ---- ネームカードID一覧: lunaris 単体 → amber → enka の優先度チェーン
    nc_targets: Dict[str, Dict[str, str]] = {}
    nc_list_source = ""
    lunaris_nc = _load_lunaris_namecards()
    if len(lunaris_nc) >= 100:
        # lunaris がまともな一覧を返した → これ単体で確定
        nc_targets = {cid: {"base": base, "full": ""} for cid, base in lunaris_nc.items()}
        nc_list_source = "lunaris"
    else:
        amber_nc = _amber_namecard_bases()
        if amber_nc:
            nc_targets = {cid: {"base": base, "full": ""} for cid, base in amber_nc.items()}
            nc_list_source = "amber"
    if not nc_targets:
        # 最終フォールバック: enka ローカルの固定一覧（フルアイコン名を持つ）
        for nc_id, entry in enka_nc.items():
            icon = str((entry or {}).get("icon") or "")
            if not icon:
                continue
            base = icon.replace("UI_NameCardPic_", "")
            if base.endswith("_P"):
                base = base[:-2]
            nc_targets[str(nc_id)] = {"base": base, "full": icon}
        nc_list_source = "enka"

    # ---- プロフアイコンID一覧: nanoka（キャラ系）→ enka（写真系のギャップ補完）→ pizza-studio（ポートレート系のギャップ補完）
    pfp_targets: Dict[str, str] = {}
    pfp_source_counts: Dict[str, int] = {}
    for char_names in (_nanoka_character_names(live_ver), _nanoka_character_names(beta_ver)):
        for cid, name in char_names.items():
            if cid not in pfp_targets:
                pfp_targets[cid] = f"UI_AvatarIcon_{name}_Circle"
                pfp_source_counts["nanoka"] = pfp_source_counts.get("nanoka", 0) + 1
    for pfp_id, entry in enka_pfp.items():
        icon = str((entry or {}).get("iconPath") or "")
        if icon and str(pfp_id).isdigit():
            if str(pfp_id) not in pfp_targets:
                pfp_targets[str(pfp_id)] = icon
                pfp_source_counts["enka"] = pfp_source_counts.get("enka", 0) + 1
    for pfp_id, entry in _load_pizza_asset_map("pfps").items():
        icon = str((entry or {}).get("iconPath") or "")
        if icon and str(pfp_id).isdigit():
            if str(pfp_id) not in pfp_targets:
                pfp_targets[str(pfp_id)] = icon
                pfp_source_counts["pizza"] = pfp_source_counts.get("pizza", 0) + 1
    # lunaris（MATERIAL_AVATAR）もギャップ補完。icon は Card 無しのフル名（UI_AvatarIcon_xxx）
    for pfp_id, icon in _load_lunaris_avatar_icons().items():
        if pfp_id not in pfp_targets:
            pfp_targets[pfp_id] = icon
            pfp_source_counts["lunaris"] = pfp_source_counts.get("lunaris", 0) + 1

    os.makedirs(_NAMECARD_DIR, exist_ok=True)
    os.makedirs(_PFP_DIR, exist_ok=True)

    nc_new = nc_skip = nc_fail = 0
    for nc_id, info in nc_targets.items():
        dest = os.path.join(_NAMECARD_DIR, f"{nc_id}.png")
        if os.path.exists(dest):
            nc_skip += 1
            continue
        base, full = info["base"], info["full"]
        cands = [
            f"{_NANOKA_CDN_PREFIX}UI_NameCardPic_{base}_P.webp",
            f"{_NANOKA_CDN_PREFIX}UI_NameCardIcon_{base}.webp",
            _LUNARIS_NAMECARDPIC_URL.format(f"UI_NameCardPic_{base}_P"),
            f"{_ENKA_UI_PREFIX}UI_NameCardPic_{base}_P.png",
            f"{_ENKA_UI_PREFIX}UI_NameCardIcon_{base}.png",
        ]
        if full:
            cands.insert(0, f"{_NANOKA_CDN_PREFIX}{full}.webp")
        # 重複除去（順序維持）
        seen = set()
        cands = [u for u in cands if not (u in seen or seen.add(u))]
        if _dl_first(cands, dest):
            nc_new += 1
        else:
            nc_fail += 1

    pfp_new = pfp_skip = pfp_fail = 0
    for pfp_id, icon in pfp_targets.items():
        dest = os.path.join(_PFP_DIR, f"{pfp_id}.png")
        if os.path.exists(dest):
            pfp_skip += 1
            continue
        # lunaris の MATERIAL_AVATAR 由来（UI_AvatarIcon_xxx）は Card 付き/無し両方を試す
        cands = [
            f"{_NANOKA_CDN_PREFIX}{icon}.webp",
            f"{_ENKA_UI_PREFIX}{icon}.png",
            _LUNARIS_AVATARICON_URL.format(icon),
        ]
        if icon.startswith("UI_AvatarIcon_") and not icon.endswith("_Card"):
            cands.insert(2, _LUNARIS_AVATARICON_URL.format(f"{icon}_Card"))
        # 重複除去（順序維持）
        seen = set()
        cands = [u for u in cands if not (u in seen or seen.add(u))]
        if _dl_first(cands, dest):
            pfp_new += 1
        else:
            pfp_fail += 1

    result = {
        "ok": True,
        "versions": {"live": live_ver, "beta": beta_ver},
        "sources": {"namecard_list": nc_list_source, "pfp_list": pfp_source_counts},
        "namecards": {"total": len(nc_targets), "new": nc_new, "skipped": nc_skip, "failed": nc_fail},
        "pfps": {"total": len(pfp_targets), "new": pfp_new, "skipped": pfp_skip, "failed": pfp_fail},
    }
    return result


def _bulk_fetch_namecards_pfps_lunaris() -> Dict[str, Any]:
    """lunaris の materiallist のみをソースとして不足分を取得する（lunaris 単体検証用）。

    - ネームカード一覧: lunaris MATERIAL_NAMECARD（293種）
    - プロフアイコン一覧: lunaris MATERIAL_AVATAR（131種・旧キャラのみ。新キャラは nanoka ボタンで補完）
    画像のダウンロードは nanoka CDN 優先（無ければ lunaris CDN / enka CDN）。
    """
    nc_targets: Dict[str, Dict[str, str]] = {cid: {"base": base, "full": ""} for cid, base in _load_lunaris_namecards().items()}
    pfp_targets: Dict[str, str] = dict(_load_lunaris_avatar_icons())

    os.makedirs(_NAMECARD_DIR, exist_ok=True)
    os.makedirs(_PFP_DIR, exist_ok=True)

    nc_new = nc_skip = nc_fail = 0
    for nc_id, info in nc_targets.items():
        dest = os.path.join(_NAMECARD_DIR, f"{nc_id}.png")
        if os.path.exists(dest):
            nc_skip += 1
            continue
        base = info["base"]
        cands = [
            f"{_NANOKA_CDN_PREFIX}UI_NameCardPic_{base}_P.webp",
            f"{_NANOKA_CDN_PREFIX}UI_NameCardIcon_{base}.webp",
            _LUNARIS_NAMECARDPIC_URL.format(f"UI_NameCardPic_{base}_P"),
            f"{_ENKA_UI_PREFIX}UI_NameCardPic_{base}_P.png",
            f"{_ENKA_UI_PREFIX}UI_NameCardIcon_{base}.png",
        ]
        seen = set()
        cands = [u for u in cands if not (u in seen or seen.add(u))]
        if _dl_first(cands, dest):
            nc_new += 1
        else:
            nc_fail += 1

    pfp_new = pfp_skip = pfp_fail = 0
    for pfp_id, icon in pfp_targets.items():
        dest = os.path.join(_PFP_DIR, f"{pfp_id}.png")
        if os.path.exists(dest):
            pfp_skip += 1
            continue
        cands = [
            f"{_NANOKA_CDN_PREFIX}{icon}.webp",
            f"{_ENKA_UI_PREFIX}{icon}.png",
            _LUNARIS_AVATARICON_URL.format(icon),
        ]
        seen = set()
        cands = [u for u in cands if not (u in seen or seen.add(u))]
        if _dl_first(cands, dest):
            pfp_new += 1
        else:
            pfp_fail += 1

    ok = bool(nc_targets) or bool(pfp_targets)
    return {
        "ok": ok,
        "error": None if ok else "lunaris の materiallist が取得できませんでした",
        "sources": {"namecard_list": "lunaris" if nc_targets else "", "pfp_list": {"lunaris": len(pfp_targets)}},
        "namecards": {"total": len(nc_targets), "new": nc_new, "skipped": nc_skip, "failed": nc_fail},
        "pfps": {"total": len(pfp_targets), "new": pfp_new, "skipped": pfp_skip, "failed": pfp_fail},
    }


# ==========================================================
#  聖遺物 2セット効果バフ管理 API
# ==========================================================
from app.card.set_buffs import (
    BUFF_TYPES, detect_2set_buff, buff_label,
    load_2set_buff_map, save_2set_buff_map,
)


def _load_artifact_sets():
    """ローカルの artifacts.json（live と beta）から全セット情報を読む（beta 優先マージ）。"""
    merged = {}
    for raw_path, prio in (
        (os.path.join(STATIC_DIR, "data", "lists", "artifacts.json"), 0),
        (os.path.join(STATIC_DIR, "beta", "data", "lists", "artifacts.json"), 1),
    ):
        if not os.path.exists(raw_path):
            continue
        try:
            with open(raw_path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except (UnicodeDecodeError, json.JSONDecodeError):
            try:
                with open(raw_path, "r", encoding="cp932") as f:
                    d = json.load(f)
            except Exception:
                continue
        for sid, ent in (d or {}).items():
            if isinstance(ent, dict):
                prev_prio = merged.get(str(sid), {}).get("_prio", -1)
                if prio >= prev_prio:
                    merged[str(sid)] = {**ent, "_prio": prio}
    return merged


@admin_router.get("/admin/api/artifact_2set_buffs")
async def admin_artifact_2set_buffs_get(request: Request):
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        def _run():
            sets = _load_artifact_sets()
            saved = load_2set_buff_map()
            catalog = []
            for sid in sorted(sets, key=lambda s: (len(s), s)):
                ent = sets[sid]
                if not isinstance(ent, dict) or not ent.get("janame"):
                    continue
                desc_ja = ent.get("set2_desc_ja") or ""
                detected = detect_2set_buff(desc_ja)
                cur = saved.get(str(sid))
                catalog.append({
                    "id": str(sid),
                    "janame": ent.get("janame", ""),
                    "enname": ent.get("enname", ""),
                    "icon": ent.get("icon", ""),
                    "set2_desc_ja": desc_ja,
                    "detected": detected,
                    "detected_label": (buff_label(detected["type"], detected["value"]) if detected else ""),
                    "selected": cur,
                    "selected_label": (buff_label(cur.get("type", ""), cur.get("value", 0)) if cur else ""),
                })
            type_options = [
                {"type": t, "jatype": info["jatype"], "label_placeholder": info["label"].format(v=0)}
                for t, info in BUFF_TYPES.items()
            ]
            return {
                "ok": True,
                "count": len(catalog),
                "types": type_options,
                "sets": catalog,
            }
        return JSONResponse(await run_in_threadpool(_run))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@admin_router.post("/admin/api/artifact_2set_buffs")
async def admin_artifact_2set_buffs_save(request: Request):
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"ok": False, "error": "body must be {setId: {type, value}}"}, status_code=400)
    buff_map = body.get("buff_map", body)
    if not isinstance(buff_map, dict):
        return JSONResponse({"ok": False, "error": "buff_map required"}, status_code=400)
    try:
        cleaned = await run_in_threadpool(save_2set_buff_map, buff_map)
        return JSONResponse({"ok": True, "updated": len(cleaned)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

# ---- 幽境共有データ（R2 snapshots）: 管理パネルからの一覧/削除 ----
_ABYSs_SID_RE = _re.compile(r'^s\d{13}[0-9a-f]{6}$')


@admin_router.get("/admin/api/abyss_shares")
async def admin_abyss_shares(request: Request):
    """R2 に保存された幽境編成共有スナップショットを一覧化（作成日時/UID/幽境バージョン/キャラアイコン）。"""
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)

    def _scan():
        from app import share_store
        items = []
        for key in share_store.list_keys("snapshots/"):
            try:
                raw = share_store.get(key)
                if not raw:
                    continue
                d = json.loads(raw.decode("utf-8"))
            except Exception:
                continue
            kind = d.get("kind") or ("abyss" if isinstance(d.get("abyss"), dict) else None)
            if kind != "abyss":
                continue
            sid = str(d.get("sid") or key.rsplit("/", 1)[-1][:-5])
            try:
                created = float(d.get("created") or 0)
            except Exception:
                created = 0.0
            if created <= 0:
                try:
                    created = int(sid[1:14]) / 1000.0
                except Exception:
                    created = 0.0
            icons = []
            for entry in (d.get("chars") or {}).values():
                ic = str((entry or {}).get("icon") or "")
                if ic and ic not in icons:
                    icons.append(ic)
            ab = d.get("abyss") if isinstance(d.get("abyss"), dict) else {}
            items.append({
                "sid": sid,
                "created": created,
                "uid": str(d.get("uid") or "-"),
                "version": str(ab.get("version") or "-"),
                "difficulty": str(ab.get("difficulty") or "-"),
                "bosses": ab.get("bosses") or [],
                "icons": icons[:12],
                "permanent": float(d.get("exp", 0) or 0) == 0,
            })
        items.sort(key=lambda x: -x["created"])
        return items

    try:
        items = await run_in_threadpool(_scan)
        return JSONResponse({"ok": True, "items": items})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@admin_router.post("/admin/api/abyss_shares/delete")
async def admin_abyss_shares_delete(request: Request):
    """幽境編成共有スナップショットを1件または複数削除する（リンクは即座に「見つかりません」になる）。"""
    if not _is_admin(request):
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
    raw = (body or {}).get("sids")
    if raw is None:
        raw = [(body or {}).get("sid")]
    if not isinstance(raw, list):
        raw = [raw]
    sids = []
    for s in raw:
        sid = str(s or "")
        if not sid:
            continue
        if not _ABYSs_SID_RE.match(sid):
            return JSONResponse({"ok": False, "error": "sid が不正です"}, status_code=400)
        if sid not in sids:
            sids.append(sid)
    if not sids:
        return JSONResponse({"ok": False, "error": "sid が不正です"}, status_code=400)
    if len(sids) > 200:
        return JSONResponse({"ok": False, "error": "一度に削除できる件数は200件までです"}, status_code=400)

    def _del():
        from app import share_store
        n = 0
        for sid in sids:
            key = f"snapshots/{sid}.json"
            if share_store.get(key) is not None:
                share_store.delete(key)
                n += 1
        return n

    try:
        deleted = await run_in_threadpool(_del)
        return JSONResponse({"ok": True, "deleted": deleted, "count": deleted})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
