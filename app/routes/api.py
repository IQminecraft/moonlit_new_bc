# -*- coding: utf-8 -*-
"""公開 API ルート（/, /fetch_uid, /api/*, /generate_card_image, /serverup）。"""
import os
import json
import time
import hashlib as _hashlib
from urllib.parse import urlencode, quote

from fastapi import APIRouter, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app import get_info_state
from app.paths import STATIC_DIR, templates
from app.core.notify import report_error_to_discord
from app.card.sign import (
    _CARD_URL_SECRET, _CARD_SIGN_VALIDITY_SEC, _CARD_SIGN_RATE_LIMIT_PER_MIN, _CARD_GEN_RATE_LIMIT_PER_MIN,
    _rate_limited, _client_ip, _card_signature, _verify_card_sign,
    _team_signature, _verify_team_sign,
)
from app.core.server_stats import _server_stats_snapshot
from app.card.pool import _run_in_card_gen_pool, _card_gen_stats
from app.card.data import _load_json_auto, _build_char_list_from_showcase, _get_card_data_sync
from app.card.image import _generate_card_image_sync
from app.card.team_image import _generate_team_image_sync

api_router = APIRouter()

_BG_IMAGE_EXTS = {".webp", ".png", ".jpg", ".jpeg"}

# ---- Enka API クールタイム管理（UID ごと） ----
_ENKA_COOLDOWN_SEC = max(0, float(os.environ.get("ENKA_COOLDOWN_SEC", "60")))
_enka_last_fetch = {}  # uid_str -> epoch


def _enka_mark_fetched(uid):
    _enka_last_fetch[str(uid)] = time.time()


def _enka_cooldown_remaining(uid):
    if _ENKA_COOLDOWN_SEC <= 0:
        return 0.0
    last = _enka_last_fetch.get(str(uid))
    if last is None:
        return 0.0
    return max(0.0, last + _ENKA_COOLDOWN_SEC - time.time())


# ---- 生成カード画像のサーバー側ディスクキャッシュ（cache=server 時） ----
_CARDS_CACHE_DIR = os.path.join(STATIC_DIR, "cache", "cards")


def _card_disk_path(params):
    h = _hashlib.sha256()
    for k in sorted(params.keys()):
        h.update(f"{k}={params.get(k) or ''}|".encode("utf-8"))
    return os.path.join(_CARDS_CACHE_DIR, h.hexdigest() + ".png")


def _serve_card_disk(params):
    path = _card_disk_path(params)
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                return f.read()
        except Exception:
            return None
    return None


def _save_card_disk(params, data):
    try:
        os.makedirs(_CARDS_CACHE_DIR, exist_ok=True)
        with open(_card_disk_path(params), "wb") as f:
            f.write(data)
    except Exception as e:
        print(f"[cache] card disk save failed: {e}")

_TEAM_CFG_KEYS = {"calc_method", "fake_char", "fake_weapon", "bg_mode", "bg_color", "bg_region"}


def _clean_team_configs(configs: str, n: int) -> str:
    """configs（JSON文字列）を検証・正規化してJSON文字列で返す。空なら "[]"。"""
    if not configs:
        return ""
    try:
        raw = json.loads(configs)
    except Exception:
        raise HTTPException(status_code=400, detail="configs が不正なJSONです。")
    if not isinstance(raw, list) or len(raw) != n:
        raise HTTPException(status_code=400, detail="configs はキャラ数分のリストが必要です。")
    cleaned = []
    for entry in raw:
        if not isinstance(entry, dict):
            cleaned.append({})
            continue
        out = {}
        for k, v in entry.items():
            if k in _TEAM_CFG_KEYS and isinstance(v, str):
                out[k] = v
        cleaned.append(out)
    return json.dumps(cleaned, ensure_ascii=False)


def _list_image_urls(rel_dir: str, url_prefix: str, limit: int = 200) -> list:
    abs_dir = os.path.join(STATIC_DIR, rel_dir)
    if not os.path.isdir(abs_dir):
        return []
    urls = []
    try:
        for name in sorted(os.listdir(abs_dir)):
            ext = os.path.splitext(name)[1].lower()
            if ext not in _BG_IMAGE_EXTS:
                continue
            if name.startswith(".") or name.startswith("~"):
                continue
            urls.append(f"{url_prefix.rstrip('/')}/{name}")
            if len(urls) >= limit:
                break
    except OSError as e:
        print(f"[Warning] bg image list failed for {abs_dir}: {e}")
    return urls


@api_router.get("/api/bg_images")
async def api_bg_images(beta: str = "false"):
    splash = _list_image_urls("assets/splash", "/static/assets/splash")
    weapons = _list_image_urls("assets/weapons", "/static/assets/weapons")

    if str(beta).lower() in ("1", "true", "yes"):
        splash_beta = _list_image_urls("beta/assets/splash", "/static/beta/assets/splash")
        weapons_beta = _list_image_urls("beta/assets/weapons", "/static/beta/assets/weapons")
        seen_s = set(splash)
        seen_w = set(weapons)
        for u in splash_beta:
            if u not in seen_s:
                splash.append(u)
                seen_s.add(u)
        for u in weapons_beta:
            if u not in seen_w:
                weapons.append(u)
                seen_w.add(u)

    return JSONResponse({
        "splash": splash,
        "weapons": weapons,
        "all": splash + weapons,
        "count": {
            "splash": len(splash),
            "weapons": len(weapons),
            "all": len(splash) + len(weapons),
        },
    })




@api_router.get("/api/enka_cooldown")
async def enka_cooldown(uid: str):
    """指定UIDのEnka APIクールタイム残り秒数を返す。"""
    return {
        "ok": True,
        "uid": uid,
        "cooldown": round(_enka_cooldown_remaining(uid), 1),
        "cooldown_sec": _ENKA_COOLDOWN_SEC,
    }


@api_router.get("/api/server_stats")
async def api_server_stats():
    """システム全体の CPU / メモリ使用率。"""
    try:
        return JSONResponse(await run_in_threadpool(_server_stats_snapshot))
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@api_router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("artifacter.html", {"request": request, "lang": "ja"})


@api_router.get("/fetch_uid", response_class=HTMLResponse)
async def fetch_uid(request: Request, uid: str, from_artifacter: bool = False, ver: str = "live"):
    print(f"[Info] Fetching characters via API for UID: {uid} (from_artifacter: {from_artifacter})")
    if ver != "beta":
        ver = "live"

    if not uid.isdigit():
        # 取得失敗時はリダイレクトせず、artifacter ページ上にエラーを表示する
        return templates.TemplateResponse("artifacter.html", {
            "request": request,
            "lang": "ja",
            "error": "UIDは数字のみで入力してください。",
            "uid": uid,
            "ver": ver,
        }, status_code=400)

    beta = "true" if ver == "beta" else "false"
    uid_int = int(uid)
    json_path = os.path.join("static", "cache", f"showcase_{uid}.json")

    if from_artifacter or not os.path.exists(json_path):
        success, message = await get_info_state.update_uid_data(uid_int)
        if success:
            _enka_mark_fetched(uid_int)
        if not success:
            print(f"[Warning] API Fetch failed or warning: {message}")

    if not os.path.exists(json_path):
        # 取得失敗時はリダイレクトせず、artifacter ページ上にエラーを表示する
        return templates.TemplateResponse("artifacter.html", {
            "request": request,
            "lang": "ja",
            "error": "指定されたUIDのデータを取得できませんでした。UIDが存在しないか、ゲーム内プロフィールが公開されていない可能性があります。",
            "uid": uid,
            "ver": ver,
        }, status_code=404)

    showcase_data = _load_json_auto(json_path)

    char_list = _build_char_list_from_showcase(showcase_data, beta)

    if not char_list:
        # 取得失敗時はリダイレクトせず、artifacter ページ上にエラーを表示する
        return templates.TemplateResponse("artifacter.html", {
            "request": request,
            "lang": "ja",
            "error": "指定されたUIDのゲーム内プロフィールで『キャラクター詳細を公開』がオンになっていないか、ショーケースが空です。",
            "uid": uid,
            "ver": ver,
        }, status_code=400)

    return templates.TemplateResponse("build_card.html", {
        "request": request,
        "uid": uid,
        "char_list": char_list,
        "ver": ver
    })


@api_router.post("/refresh_uid/{uid}")
async def refresh_uid(uid: str, ver: str = "live"):
    if not uid.isdigit():
        raise HTTPException(status_code=400, detail="UIDが不正です。数字のみ入力してください。")
    if ver != "beta":
        ver = "live"
    beta = "true" if ver == "beta" else "false"

    uid_int = int(uid)

    # クールタイム中は Enka API へリクエストを送らない（キャッシュの一覧だけ返す）
    remaining = _enka_cooldown_remaining(uid)
    if remaining > 0:
        print(f"[Info] Enka cooldown active for UID {uid}: {remaining:.0f}s left")
        char_list = []
        json_path = os.path.join("static", "cache", f"showcase_{uid}.json")
        if os.path.exists(json_path):
            try:
                char_list = _build_char_list_from_showcase(_load_json_auto(json_path), beta)
            except Exception:
                char_list = []
        return {
            "success": False,
            "cooldown": round(remaining, 1),
            "message": f"Enka APIのクールタイム中です（あと{int(remaining)}秒）",
            "char_list": char_list,
        }

    print(f"[Info] Refreshing showcase data via Enka API for UID: {uid} ({ver})")
    success, message = await get_info_state.update_uid_data(uid_int)
    if success:
        _enka_mark_fetched(uid)

    if not success:
        print(f"[Warning] Refresh failed: {message}")
        raise HTTPException(status_code=502, detail=f"Enka APIの取得に失敗しました: {message}")

    # 再取得後のキャラ一覧（クライアント側でサムネイル行を同期するため）
    char_list = []
    json_path = os.path.join("static", "cache", f"showcase_{uid}.json")
    if os.path.exists(json_path):
        try:
            char_list = _build_char_list_from_showcase(_load_json_auto(json_path), beta)
        except Exception as e:
            print(f"[Warning] 再取得後のキャラ一覧構築に失敗: {e}")
            char_list = []

    return {"success": True, "message": message, "char_list": char_list}


@api_router.get("/api/char_list/{uid}")
async def get_char_list(uid: str, beta: str = "false"):
    """現在キャッシュされているショーケースのキャラ一覧を返す（再取得後のUI同期用）。"""
    if beta != "true":
        beta = "false"
    json_path = os.path.join("static", "cache", f"showcase_{uid}.json")
    if not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail=f"UID: {uid} のキャッシュデータが見つかりませんでした。")
    showcase_data = _load_json_auto(json_path)
    return {"uid": uid, "char_list": _build_char_list_from_showcase(showcase_data, beta)}




@api_router.get("/api/card_data/{uid}/{avatar_id}")
async def get_card_data(uid: str, avatar_id: str, calc_method: str = "crit", fake_char: str = None, fake_weapon: str = None, beta: str = "false"):
    return await run_in_threadpool(
        _get_card_data_sync, uid, avatar_id, calc_method, fake_char, fake_weapon, beta
    )






@api_router.get("/api/card_sign")
async def card_sign(uid: str, avatar_id: str, calc_method: str = "crit", fake_char: str = None, fake_weapon: str = None, beta: str = "false", bg_color: str = None, img_format: str = "png", bg_mode: str = None, bg_region: str = None, request: Request = None):
    """署名付きカード画像URLの発行（安価・IP毎レート制限付き）。
    このエンドポイントは画像生成も外部通信もしないため、
    ここへの集中攻撃はレート制限で吸収する。
    """
    img_format = str(img_format or "png").lower()
    if img_format != "png":
        # WEBP 廃止: 署名発行もしない
        raise HTTPException(status_code=400, detail="WEBP形式は廃止されました。PNG（img_format=png）のみ利用できます")
    if _rate_limited(f"sign:{_client_ip(request)}", _CARD_SIGN_RATE_LIMIT_PER_MIN):
        raise HTTPException(status_code=429, detail="署名の取得が頻繁すぎます。しばらく待って再試行してください。")
    params = {
        "uid": uid,
        "avatar_id": avatar_id,
        "calc_method": calc_method,
        "fake_char": fake_char,
        "fake_weapon": fake_weapon,
        "beta": beta,
        "bg_color": bg_color,
        "img_format": img_format,
        "bg_mode": bg_mode,
        "bg_region": bg_region,
    }
    if _CARD_URL_SECRET:
        exp = int(time.time()) + int(_CARD_SIGN_VALIDITY_SEC)
        params["card_exp"] = exp
        params["card_sig"] = _card_signature(exp, params)
    else:
        print("[CardSign] CARD_URL_SECRET 未設定のため署名なしURLを発行（本番では設定推奨）", flush=True)
    query = urlencode({k: str(v) for k, v in params.items() if v is not None and v != ""})
    return {
        "url": f"/generate_card_image/{quote(str(uid))}/{quote(str(avatar_id))}/{quote(str(calc_method))}?{query}",
        "exp": params.get("card_exp", 0),
    }


def _clean_team_boss(boss: str):
    """幽境ボス情報（JSON文字列）を検証・正規化してJSON文字列で返す。"""
    if not boss:
        return ""
    try:
        raw = json.loads(boss)
    except Exception:
        raise HTTPException(status_code=400, detail="boss が不正なJSONです。")
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="boss はオブジェクトで指定してください。")
    out = {}
    for k in ("version", "name", "img"):
        v = raw.get(k)
        if isinstance(v, str) and v:
            out[k] = v
    return json.dumps(out, ensure_ascii=False)


@api_router.get("/api/leyline_versions")
async def public_leyline_versions():
    """利用可能なレイライン変換済みJSON（static/data, static/beta/data）の一覧を返す。"""
    found = []
    for sub, beta in (("data", False), ("beta", "data")):
        d = os.path.join(STATIC_DIR, sub) if not beta else os.path.join(STATIC_DIR, "beta", "data")
        if not os.path.isdir(d):
            continue
        prefix = "/static/data" if not beta else "/static/beta/data"
        for f in sorted(os.listdir(d)):
            if f.startswith("leyline_") and f.endswith(".json"):
                v = f[len("leyline_"):-len(".json")]
                found.append({"version": v, "url": f"{prefix}/{f}"})
    return {"ok": True, "versions": found}


@api_router.get("/api/team_card_sign")
async def team_card_sign(uid: str, char_ids: str, configs: str = "", boss: str = "", beta: str = "false", img_format: str = "png", request: Request = None):
    """編成カード画像用の署名付きURLを発行。"""
    img_format = str(img_format or "png").lower()
    if img_format != "png":
        raise HTTPException(status_code=400, detail="WEBP形式は廃止されました。PNGのみ利用できます")
    ids = [s.strip() for s in str(char_ids or "").split(",") if s.strip()]
    if len(ids) != 4:
        raise HTTPException(status_code=400, detail="キャラは4体選択してください。")
    configs_clean = _clean_team_configs(configs, len(ids))
    boss_clean = _clean_team_boss(boss)
    if _rate_limited(f"sign:{_client_ip(request)}", _CARD_SIGN_RATE_LIMIT_PER_MIN):
        raise HTTPException(status_code=429, detail="署名の取得が頻繁すぎます。しばらく待って再試行してください。")
    params = {
        "uid": uid,
        "char_ids": ",".join(ids),
        "configs": configs_clean,
        "boss": boss_clean,
        "beta": beta,
        "img_format": img_format,
    }
    if _CARD_URL_SECRET:
        exp = int(time.time()) + int(_CARD_SIGN_VALIDITY_SEC)
        params["card_exp"] = exp
        params["card_sig"] = _team_signature(exp, params)
    else:
        print("[CardSign] CARD_URL_SECRET 未設定のため署名なしURLを発行（本番では設定推奨）", flush=True)
    query = urlencode({k: str(v) for k, v in params.items() if v is not None and v != ""})
    return {
        "url": f"/generate_team_image/{quote(str(uid))}?{query}",
        "exp": params.get("card_exp", 0),
    }


@api_router.get("/generate_team_image/{uid}")
async def generate_team_image(uid: str, char_ids: str, configs: str = "", boss: str = "", beta: str = "false", img_format: str = "png", cache: str = "", card_exp: str = None, card_sig: str = None, request: Request = None):
    """4キャラ分の編成カード画像を生成。専用スレッドプール・署名検証・レート制限は単体カードと同様。"""
    img_format = str(img_format or "png").lower()
    if img_format != "png":
        raise HTTPException(status_code=400, detail="WEBP形式は廃止されました。PNGのみ利用できます")
    ids = [s.strip() for s in str(char_ids or "").split(",") if s.strip()]
    if len(ids) != 4:
        raise HTTPException(status_code=400, detail="キャラは4体選択してください。")
    configs_clean = _clean_team_configs(configs, len(ids))
    configs_list = json.loads(configs_clean) if configs_clean else []
    boss_clean = _clean_team_boss(boss)
    boss_obj = json.loads(boss_clean) if boss_clean else None
    if _CARD_URL_SECRET:
        params = {"uid": uid, "char_ids": ",".join(ids), "configs": configs_clean, "boss": boss_clean, "beta": beta, "img_format": img_format}
        if not _verify_team_sign(card_exp, card_sig, params):
            raise HTTPException(status_code=403, detail="編成カードURLの署名が無効です。ページを再読み込みしてください。")
    if _rate_limited(f"gen:{_client_ip(request)}", _CARD_GEN_RATE_LIMIT_PER_MIN):
        raise HTTPException(status_code=429, detail="画像生成のリクエストが頻繁すぎます。しばらく待って再試行してください。")

    # サーバー側ディスクキャッシュ（cache=server）: 同一パラメータなら再生成せず返す
    if cache == "server":
        team_cache_params = {
            "uid": uid, "char_ids": ",".join(ids), "configs": configs_clean,
            "boss": boss_clean, "beta": beta, "img_format": img_format,
        }
        hit = _serve_card_disk(team_cache_params)
        if hit is not None:
            return Response(content=hit, media_type="image/png")

    try:
        img_bytes = await _run_in_card_gen_pool(
            _generate_team_image_sync,
            uid,
            ids,
            configs_list,
            boss_obj,
            beta,
            img_format,
        )
    except HTTPException as he:
        if he.status_code >= 500:
            report_error_to_discord(
                "generate_team_image HTTPException",
                str(he.detail),
                path=f"/generate_team_image/{uid}",
                extra={"uid": uid, "char_ids": char_ids, "beta": beta, "status": he.status_code},
                level="error",
            )
        raise
    except Exception as e:
        import traceback
        report_error_to_discord(
            "generate_team_image failed",
            f"{type(e).__name__}: {e}",
            path=f"/generate_team_image/{uid}",
            extra={"uid": uid, "char_ids": char_ids, "beta": beta},
            traceback_text=traceback.format_exc(),
            level="error",
        )
        raise HTTPException(status_code=500, detail=f"編成カード生成に失敗しました: {e}") from e

    if cache == "server":
        _save_card_disk(team_cache_params, img_bytes)
    return Response(content=img_bytes, media_type="image/png")


@api_router.get("/generate_card_image/{uid}/{avatar_id}/{calc_method}")
async def generate_card_image(uid: str, avatar_id: str, calc_method: str, fake_char: str = None, fake_weapon: str = None, beta: str = "false", bg_color: str = None, img_format: str = "png", bg_mode: str = None, bg_region: str = None, cache: str = "", card_exp: str = None, card_sig: str = None, request: Request = None):
    """カード画像生成。専用スレッドプールで同時実行数を制限し、超過分は列待ち。
    待ち行列が満杯のときは 503 を返す（デフォルトの threadpool は占有しない）。
    署名検証（安価）→ IPレート制限 → プール投入の順で、
    無署名・不正な直接アクセスは生成前に拒否する（DDoS対策）。
    """
    img_format = str(img_format or "png").lower()
    if img_format != "png":
        # WEBP 廃止: 生成処理にも待ち行列にも入れず、即エラーを返す
        raise HTTPException(status_code=400, detail="WEBP形式は廃止されました。PNG（img_format=png）のみ利用できます")
    if _CARD_URL_SECRET:
        # 署名検証: 不正・期限切れ・パラメータ不一致は生成前に安価に拒否
        params = {
            "uid": uid,
            "avatar_id": avatar_id,
            "calc_method": calc_method,
            "fake_char": fake_char,
            "fake_weapon": fake_weapon,
            "beta": beta,
            "bg_color": bg_color,
            "img_format": img_format,
            "bg_mode": bg_mode,
            "bg_region": bg_region,
        }
        if not _verify_card_sign(card_exp, card_sig, params):
            raise HTTPException(status_code=403, detail="カード画像URLの署名が無効です。ページを再読み込みしてください。")
    if _rate_limited(f"gen:{_client_ip(request)}", _CARD_GEN_RATE_LIMIT_PER_MIN):
        raise HTTPException(status_code=429, detail="画像生成のリクエストが頻繁すぎます。しばらく待って再試行してください。")

    # サーバー側ディスクキャッシュ（cache=server）: 同一パラメータなら再生成せず返す
    if cache == "server":
        cache_params = {
            "uid": uid, "avatar_id": avatar_id, "calc_method": calc_method,
            "fake_char": fake_char, "fake_weapon": fake_weapon, "beta": beta,
            "bg_color": bg_color, "img_format": img_format, "bg_mode": bg_mode,
            "bg_region": bg_region,
        }
        hit = _serve_card_disk(cache_params)
        if hit is not None:
            return Response(content=hit, media_type="image/png")

    try:
        img_bytes = await _run_in_card_gen_pool(
            _generate_card_image_sync,
            uid,
            avatar_id,
            calc_method,
            fake_char,
            fake_weapon,
            beta,
            bg_color,
            img_format,
            bg_mode,
            bg_region,
        )
    except HTTPException as he:
        if he.status_code >= 500:
            report_error_to_discord(
                "generate_card_image HTTPException",
                str(he.detail),
                path=f"/generate_card_image/{uid}/{avatar_id}/{calc_method}",
                extra={
                    "uid": uid,
                    "avatar_id": avatar_id,
                    "calc_method": calc_method,
                    "status": he.status_code,
                    "beta": beta,
                },
                level="error",
            )
        elif he.status_code == 503:
            report_error_to_discord(
                "generate_card_image queue full",
                str(he.detail),
                path=f"/generate_card_image/{uid}/{avatar_id}/{calc_method}",
                extra={"uid": uid, "avatar_id": avatar_id, **_card_gen_stats()},
                level="warn",
            )
        raise
    except Exception as e:
        import traceback
        report_error_to_discord(
            "generate_card_image failed",
            f"{type(e).__name__}: {e}",
            path=f"/generate_card_image/{uid}/{avatar_id}/{calc_method}",
            extra={
                "uid": uid,
                "avatar_id": avatar_id,
                "calc_method": calc_method,
                "fake_char": fake_char,
                "beta": beta,
            },
            traceback_text=traceback.format_exc(),
            level="error",
        )
        raise HTTPException(status_code=500, detail=f"カード生成に失敗しました: {e}") from e

    if cache == "server":
        _save_card_disk(cache_params, img_bytes)
    # StreamingResponse(io.BytesIO) はバイナリを改行(0x0A)ごとに分割して
    # チャンク毎にスレッドプール往復するため、4MB 級の PNG で転送に数秒かかる。
    # 生成済みの bytes を丸ごと返す Response にすることで Content-Length も付き即完了。
    return Response(content=img_bytes, media_type="image/png")


@api_router.get("/serverup", response_class=HTMLResponse)
@api_router.post("/serverup", response_class=HTMLResponse)
@api_router.head("/serverup", response_class=HTMLResponse)
async def serverup(request: Request):
    return HTMLResponse(content="Success to access")