# -*- coding: utf-8 -*-
"""公開 API ルート（/, /fetch_uid, /api/*, /generate_card_image, /serverup）。"""
import os
import time
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
)
from app.core.server_stats import _server_stats_snapshot
from app.card.pool import _run_in_card_gen_pool, _card_gen_stats
from app.card.data import _load_json_auto, _build_char_list_from_showcase, _get_card_data_sync
from app.card.image import _generate_card_image_sync

api_router = APIRouter()

_BG_IMAGE_EXTS = {".webp", ".png", ".jpg", ".jpeg"}


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
    print(f"[Info] Refreshing showcase data via Enka API for UID: {uid} ({ver})")
    success, message = await get_info_state.update_uid_data(uid_int)

    if not success:
        print(f"[Warning] Refresh failed: {message}")
        raise HTTPException(status_code=502, detail=f"Enka APIの取得に失敗しました: {message}")

    # 再取得後のキャラ一覧を返す（クライアント側でサムネイル行を同期するため）
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


@api_router.get("/generate_card_image/{uid}/{avatar_id}/{calc_method}")
async def generate_card_image(uid: str, avatar_id: str, calc_method: str, fake_char: str = None, fake_weapon: str = None, beta: str = "false", bg_color: str = None, img_format: str = "png", bg_mode: str = None, bg_region: str = None, card_exp: str = None, card_sig: str = None, request: Request = None):
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
    # StreamingResponse(io.BytesIO) はバイナリを改行(0x0A)ごとに分割して
    # チャンク毎にスレッドプール往復するため、4MB 級の PNG で転送に数秒かかる。
    # 生成済みの bytes を丸ごと返す Response にすることで Content-Length も付き即完了。
    return Response(content=img_bytes, media_type="image/png")


@api_router.get("/serverup", response_class=HTMLResponse)
@api_router.post("/serverup", response_class=HTMLResponse)
@api_router.head("/serverup", response_class=HTMLResponse)
async def serverup(request: Request):
    return HTMLResponse(content="Success to access")