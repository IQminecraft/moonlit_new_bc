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
        with open(_TEAM_SPLASH_OFFSETS_PATH, "w", encoding="utf-8") as f:
            json.dump({"offsets": cleaned}, f, indent=2, ensure_ascii=False)
        return {"ok": True, "updated": len(cleaned)}

    try:
        return JSONResponse(await run_in_threadpool(_run))
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
    with open(_LEYLINE_VERSIONS_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"versions": versions}, f, ensure_ascii=False, indent=2)


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
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(converted, f, ensure_ascii=False, indent=2)
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