# -*- coding: utf-8 -*-
"""公開 API ルート（/, /fetch_uid, /api/*, /generate_card_image, /serverup）。"""
import os
import json
import time
import threading
import hashlib as _hashlib
from urllib.parse import urlencode, quote

from fastapi import APIRouter, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app import get_info_state
from app.paths import STATIC_DIR, templates, SITE_VERSION
from app.core.notify import report_error_to_discord
from app.core.jsonio import write_bytes_atomic
from app.routes.params import (
    clean_uid, clean_avatar_id, clean_calc_method_strict, clean_bool_str,
    clean_base_prec, clean_substat_dots, clean_resonance, clean_bg_color,
    clean_token, clean_fake_char, clean_fake_weapon, clean_char_ids,
)
from app.card.sign import (
    _CARD_URL_SECRET, _CARD_SIGN_VALIDITY_SEC, _CARD_SIGN_RATE_LIMIT_PER_MIN, _CARD_GEN_RATE_LIMIT_PER_MIN,
    _rate_limited, _client_ip, _card_signature, _verify_card_sign,
    _team_signature, _verify_team_sign,
)
from app.core.server_stats import _server_stats_snapshot
from app.card.pool import _run_in_card_gen_pool, _card_gen_stats
from app.card.data import _load_json_auto, _build_char_list_from_showcase, _get_card_data_sync
from app.card.jsoncache import invalidate_json_cache
from app.card.image import _generate_card_image_sync
from app.card.team_image import _generate_team_image_sync
from app.card.theme_cards import _generate_theme_card_image_sync
from app.card.ui_flags import load_ui_flags
from app.card.calc_method import load_default_calc_method_map

api_router = APIRouter()

_BG_IMAGE_EXTS = {".webp", ".png", ".jpg", ".jpeg"}

# 画像生成で指定可能なカードテーマ（glass = 従来デザイン / theme 未指定）
_CARD_THEMES = {"cinema", "scorecard"}


def _clean_card_theme(theme):
    """theme パラメータを正規化。cinema / scorecard 以外は None（= glass 従来描画）。"""
    t = str(theme or "").strip().lower()
    return t if t in _CARD_THEMES else None

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

# 生成デザインを変更したときはこの値を更新する（旧デザインのディスクキャッシュを無効化）
_CARD_CACHE_VERSION = "v6-i18n-artifact-names"

# ディスクキャッシュの容量上限（MB）。超過時は mtime が最も古いファイルから削除する。
_CARD_DISK_CACHE_MAX_BYTES = int(float(os.environ.get("CARD_DISK_CACHE_MB", "512")) * 1024 * 1024)
# 淘汰走査の最小間隔（秒）。保存のたびにディレクトリ走査しないためのスロットル。
_CARD_DISK_SWEEP_MIN_INTERVAL = 60.0
# 生成カード画像レスポンスに付与する Cache-Control の max-age（秒）。
_CARD_HTTP_MAX_AGE = max(0, int(os.environ.get("CARD_HTTP_CACHE_MAX_AGE", "600")))
_CARD_HTTP_CACHE_CONTROL = f"public, max-age={_CARD_HTTP_MAX_AGE}" if _CARD_HTTP_MAX_AGE > 0 else "no-store"

_CARD_DISK_LOCK = threading.Lock()
_CARD_DISK_LAST_SWEEP = 0.0


def _card_disk_path(params):
    h = _hashlib.sha256()
    h.update(f"ver={_CARD_CACHE_VERSION}|".encode("utf-8"))
    for k in sorted(params.keys()):
        h.update(f"{k}={params.get(k) or ''}|".encode("utf-8"))
    return os.path.join(_CARDS_CACHE_DIR, h.hexdigest() + ".png")


def _serve_card_disk(params):
    path = _card_disk_path(params)
    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                data = f.read()
            # mtime を更新して LRU 淘汰順位に反映（よく配信されるカードを残す）
            try:
                os.utime(path)
            except OSError:
                pass
            return data
        except Exception:
            return None
    return None


def _evict_card_disk_over_budget():
    """容量上限を超えていれば、mtime が古いものから順に削除する。"""
    total = 0
    entries = []
    try:
        with os.scandir(_CARDS_CACHE_DIR) as it:
            for ent in it:
                try:
                    if not ent.is_file():
                        continue
                    st = ent.stat()
                    total += st.st_size
                    entries.append((st.st_mtime, st.st_size, ent.path))
                except OSError:
                    continue
    except OSError:
        return
    if total <= _CARD_DISK_CACHE_MAX_BYTES:
        return
    entries.sort()  # 最も古い（mtime 最小）ものが先頭
    deleted = 0
    for _mtime, size, path in entries:
        if total <= _CARD_DISK_CACHE_MAX_BYTES:
            break
        try:
            os.remove(path)
            total -= size
            deleted += 1
        except OSError:
            continue
    if deleted:
        print(
            f"[cache] card disk evicted {deleted} file(s), "
            f"now {total / (1024 * 1024):.1f}MB (cap {_CARD_DISK_CACHE_MAX_BYTES / (1024 * 1024):.0f}MB)",
            flush=True,
        )


def _save_card_disk(params, data):
    global _CARD_DISK_LAST_SWEEP
    try:
        write_bytes_atomic(_card_disk_path(params), data)
    except Exception as e:
        print(f"[cache] card disk save failed: {e}")
        return
    now = time.time()
    if now - _CARD_DISK_LAST_SWEEP >= _CARD_DISK_SWEEP_MIN_INTERVAL:
        with _CARD_DISK_LOCK:
            now = time.time()
            if now - _CARD_DISK_LAST_SWEEP >= _CARD_DISK_SWEEP_MIN_INTERVAL:
                _CARD_DISK_LAST_SWEEP = now
                try:
                    _evict_card_disk_over_budget()
                except Exception as e:
                    print(f"[cache] card disk sweep failed: {e}")

_TEAM_CFG_KEYS = {"calc_method", "fake_char", "fake_weapon", "bg_mode", "bg_color", "bg_region", "base_prec"}


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


@api_router.get("/api/card_gen_status")
async def api_card_gen_status():
    """カード生成プールの状況（進捗表示用）。ポーリングは軽量なのでログ除外対象。"""
    return JSONResponse({"ok": True, **_card_gen_stats()})


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

    uid = str(uid or "").strip()
    if not uid.isdigit() or len(uid) > 20:
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
    json_path = os.path.join(STATIC_DIR, "cache", f"showcase_{uid}.json")
    cache_exists = os.path.exists(json_path)

    if from_artifacter or not cache_exists:
        remaining = _enka_cooldown_remaining(uid)
        if remaining > 0:
            if not cache_exists:
                return templates.TemplateResponse("artifacter.html", {
                    "request": request,
                    "lang": "ja",
                    "error": f"Enka APIのクールタイム中です（あと{int(remaining)}秒）。しばらく待ってから再試行してください。",
                    "uid": uid,
                    "ver": ver,
                }, status_code=429)
        else:
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
        # メモリの JSON キャッシュに部分データ（avatarInfoList 無し）が載っている
        # 可能性があるため、キャッシュを無効化してディスクから再読み込みして1回だけリトライ
        invalidate_json_cache(json_path)
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

    # OGP（SNS共有時のリンクプレビュー）: 絶対URLを組み立てる
    _base = str(request.base_url).rstrip("/")
    _player_name = str((showcase_data.get("playerInfo") or {}).get("nickname") or "").strip()
    _pi = showcase_data.get("playerInfo") or {}
    _pfp_id = (_pi.get("profilePicture") or {}).get("id") or ""
    _namecard_id = _pi.get("nameCardId") or ""
    _og_image = ""
    if char_list and char_list[0].get("icon"):
        # 先頭キャラのアイコン（静的アセットなのでクローラーでも軽く取得できる）
        _icon = str(char_list[0]["icon"]).lstrip("/")
        _og_image = f"{_base}/{_icon}"

    return templates.TemplateResponse("build_card.html", {
        "request": request,
        "uid": uid,
        "char_list": char_list,
        "ver": ver,
        "player_name": _player_name,
        "player_level": (showcase_data.get("playerInfo") or {}).get("level"),
        "first_char_element": (char_list[0].get("element") if char_list else None),
        "show_team_abyss_buttons": bool(load_ui_flags().get("show_team_abyss_buttons", True)),
        "show_status_view_setting": bool(load_ui_flags().get("show_status_view_setting", False)),
        "show_score_history": bool(load_ui_flags().get("show_score_history", True)),
        "pfp_id": _pfp_id,
        "namecard_id": _namecard_id,
        "og_title": f"{_player_name} ({uid})" if _player_name else f"Genshin Build Card (uid: {uid})",
        "og_description": f"原神ビルドカード生成 | {len(char_list)}人のキャラクターのビルドを表示・カード画像を生成できます (moonlit.wiki)",
        "og_image": _og_image,
        "og_url": f"{_base}/uid/{uid}",
    })


@api_router.get("/uid/{uid}", response_class=HTMLResponse)
async def fetch_uid_short(request: Request, uid: str, ver: str = "live"):
    """安定版URL: /uid/{uid}。/fetch_uid と同じ処理（共有しやすい短いURL・og:url の正規形）。"""
    return await fetch_uid(request=request, uid=uid, from_artifacter=False, ver=ver)


_CONTACT_CATEGORIES = {"不具合報告", "機能要望", "その他"}
_CONTACT_RATE_LIMIT_PER_MIN = 5


def _contact_webhook_url():
    return (os.environ.get("TOIAWASE_webhook") or os.environ.get("TOIAWASE_WEBHOOK") or "").strip()


def _send_contact_webhook_sync(webhook_url: str, payload: dict):
    import requests
    r = requests.post(webhook_url, json=payload, timeout=10)
    if r.status_code >= 400:
        raise RuntimeError(f"webhook status {r.status_code}")


@api_router.get("/contact", response_class=HTMLResponse)
async def contact_page(request: Request):
    return templates.TemplateResponse("contact.html", {"request": request})


@api_router.post("/api/contact")
async def contact_submit(request: Request):
    if _rate_limited(f"contact:{_client_ip(request)}", _CONTACT_RATE_LIMIT_PER_MIN):
        raise HTTPException(status_code=429, detail="送信が頻繁すぎます。しばらく待ってから再試行してください。")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="リクエストが不正です。")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="リクエストが不正です。")
    if str(body.get("website") or "").strip():
        return {"ok": True}
    category = str(body.get("category") or "その他").strip()
    if category not in _CONTACT_CATEGORIES:
        category = "その他"
    name = str(body.get("name") or "").strip()[:50]
    message = str(body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="問い合わせ内容を入力してください。")
    if len(message) > 2000:
        raise HTTPException(status_code=400, detail="問い合わせ内容は2000文字以内で入力してください。")
    webhook_url = _contact_webhook_url()
    if not webhook_url:
        raise HTTPException(status_code=503, detail="問い合わせ先が設定されていません。")
    referer = str(request.headers.get("referer") or "")[:200]
    payload = {
        "username": "問い合わせフォーム",
        "embeds": [{
            "title": f"問い合わせ: {category}",
            "description": message[:3000],
            "color": 0x5EEAD4,
            "fields": [
                {"name": "名前", "value": name or "匿名", "inline": True},
                {"name": "ページ", "value": referer or "-", "inline": False},
            ],
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }],
    }
    try:
        await run_in_threadpool(_send_contact_webhook_sync, webhook_url, payload)
    except Exception as e:
        report_error_to_discord("contact webhook failed", str(e), path="/api/contact", level="warn")
        raise HTTPException(status_code=502, detail="送信に失敗しました。しばらく待ってから再試行してください。")
    return {"ok": True}


@api_router.post("/refresh_uid/{uid}")
async def refresh_uid(uid: str, ver: str = "live"):
    uid = clean_uid(uid)
    if ver != "beta":
        ver = "live"
    beta = "true" if ver == "beta" else "false"

    uid_int = int(uid)

    # クールタイム中は Enka API へリクエストを送らない（キャッシュの一覧だけ返す）
    remaining = _enka_cooldown_remaining(uid)
    if remaining > 0:
        print(f"[Info] Enka cooldown active for UID {uid}: {remaining:.0f}s left")
        char_list = []
        json_path = os.path.join(STATIC_DIR, "cache", f"showcase_{uid}.json")
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
    json_path = os.path.join(STATIC_DIR, "cache", f"showcase_{uid}.json")
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
    uid = clean_uid(uid)
    if beta != "true":
        beta = "false"
    json_path = os.path.join(STATIC_DIR, "cache", f"showcase_{uid}.json")
    if not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail=f"UID: {uid} のキャッシュデータが見つかりませんでした。")
    showcase_data = _load_json_auto(json_path)
    char_list = _build_char_list_from_showcase(showcase_data, beta)
    if not char_list:
        # 部分データがメモリキャッシュに載っている場合の自己回復
        invalidate_json_cache(json_path)
        showcase_data = _load_json_auto(json_path)
        char_list = _build_char_list_from_showcase(showcase_data, beta)
    return {"uid": uid, "char_list": char_list}


@api_router.get("/api/showcase_status/{uid}")
async def showcase_status(uid: str):
    """キャッシュ済みショーケースの状態を返す（キャラ一覧が空だったときの切り分け用）。

    - showcase_count: ショーケースに並んでいるキャラ数（showAvatarInfoList）
    - detail_count:   詳細データが公開されているキャラ数（avatarInfoList）
    - level / world_level: プレイヤーの冒険ランク / 世界ランク（キャッシュに無ければ null）
    detail_count=0 かつ showcase_count>0 なら「キャラクター詳細を公開」がオフ、
    showcase_count=0 ならショーケース自体が空、と切り分けられる。
    """
    uid = clean_uid(uid)
    json_path = os.path.join(STATIC_DIR, "cache", f"showcase_{uid}.json")
    if not os.path.exists(json_path):
        return {"uid": uid, "cached": False, "showcase_count": 0, "detail_count": 0, "nickname": ""}
    try:
        showcase_data = _load_json_auto(json_path)
    except Exception:
        return {"uid": uid, "cached": False, "showcase_count": 0, "detail_count": 0, "nickname": ""}
    player_info = showcase_data.get("playerInfo") or {}
    return {
        "uid": uid,
        "cached": True,
        "showcase_count": len(player_info.get("showAvatarInfoList") or []),
        "detail_count": len(showcase_data.get("avatarInfoList") or []),
        "nickname": str(player_info.get("nickname") or "").strip(),
    }




@api_router.get("/api/calc_method_defaults")
async def calc_method_defaults():
    """キャラ毎のデフォルト計算方式マップ {ベースキャラID: method} を返す（公開）。"""
    try:
        m = await run_in_threadpool(load_default_calc_method_map)
        return {"ok": True, "defaults": m}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@api_router.get("/api/card_data/{uid}/{avatar_id}")
async def get_card_data(uid: str, avatar_id: str, calc_method: str = "crit", fake_char: str = None, fake_weapon: str = None, beta: str = "false", growth: str = "false", base_prec: str = "0", resonance: str = None, lang: str = "ja"):
    uid = clean_uid(uid)
    avatar_id = clean_avatar_id(avatar_id)
    fake_char = clean_fake_char(fake_char)
    fake_weapon = clean_fake_weapon(fake_weapon)
    beta = clean_bool_str(beta)
    growth = clean_bool_str(growth)
    base_prec = clean_base_prec(base_prec)
    resonance = clean_resonance(resonance)
    # 表示言語（ja / en）。en の場合は名前・聖遺物・ステータス等の表示名を英語で返す
    lang = "en" if str(lang or "").lower() == "en" else "ja"
    return await run_in_threadpool(
        _get_card_data_sync, uid, avatar_id, calc_method, fake_char, fake_weapon, beta, growth, base_prec, resonance, lang
    )






@api_router.get("/api/card_sign")
async def card_sign(uid: str, avatar_id: str, calc_method: str = "crit", fake_char: str = None, fake_weapon: str = None, beta: str = "false", bg_color: str = None, img_format: str = "png", bg_mode: str = None, bg_region: str = None, growth: str = "false", base_prec: str = "0", substat_dots: str = "1", resonance: str = None, theme: str = None, light: str = "false", show_uid: str = "false", request: Request = None):
    """署名付きカード画像URLの発行（安価・IP毎レート制限付き）。
    このエンドポイントは画像生成も外部通信もしないため、
    ここへの集中攻撃はレート制限で吸収する。
    """
    img_format = str(img_format or "png").lower()
    if img_format != "png":
        # WEBP 廃止: 署名発行もしない
        raise HTTPException(status_code=400, detail="WEBP形式は廃止されました。PNG（img_format=png）のみ利用できます")
    uid = clean_uid(uid)
    avatar_id = clean_avatar_id(avatar_id)
    calc_method = clean_calc_method_strict(calc_method)
    fake_char = clean_fake_char(fake_char)
    fake_weapon = clean_fake_weapon(fake_weapon)
    beta = clean_bool_str(beta)
    growth = clean_bool_str(growth)
    base_prec = clean_base_prec(base_prec)
    substat_dots = clean_substat_dots(substat_dots)
    resonance = clean_resonance(resonance)
    bg_color = clean_bg_color(bg_color)
    bg_mode = clean_token(bg_mode, "背景モード")
    bg_region = clean_token(bg_region, "背景地域")
    theme = _clean_card_theme(theme)
    light = clean_bool_str(light)
    show_uid = clean_bool_str(show_uid)
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
        "growth": growth,
        "base_prec": base_prec,
        "substat_dots": substat_dots,
        "resonance": resonance,
        "theme": theme,
        "light": light,
        "show_uid": show_uid,
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


@api_router.get("/api/data_versions")
async def api_data_versions():
    """live / beta データの最新バージョンとサイトバージョンを返す（バージョン選択UI・ヘッダー表示用）。"""
    state_path = os.path.join(STATIC_DIR, "admin", "version_state.json")
    live = None
    beta = None
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
        live = state.get("live_version")
        beta = state.get("beta_version")
    except Exception:
        pass
    return {"ok": True, "live": live, "beta": beta, "site": SITE_VERSION}


@api_router.get("/api/update_info")
async def api_update_info():
    """ホーム画面の「was Updated!!」表示用のアップデート情報（バージョン＋機能一覧）を返す。"""
    path = os.path.join(STATIC_DIR, "admin", "update_info.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        version = data.get("version")
        features = data.get("features") or []
        if not isinstance(features, list):
            features = []
        return {"ok": True, "version": version, "features": features}
    except Exception:
        return {"ok": False, "version": None, "features": []}


@api_router.get("/api/team_card_sign")
async def team_card_sign(uid: str, char_ids: str, configs: str = "", boss: str = "", beta: str = "false", img_format: str = "png", request: Request = None):
    """編成カード画像用の署名付きURLを発行。"""
    img_format = str(img_format or "png").lower()
    if img_format != "png":
        raise HTTPException(status_code=400, detail="WEBP形式は廃止されました。PNGのみ利用できます")
    uid = clean_uid(uid)
    ids = clean_char_ids(char_ids)
    beta = clean_bool_str(beta)
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
async def generate_team_image(uid: str, char_ids: str, configs: str = "", boss: str = "", beta: str = "false", img_format: str = "png", cache: str = "", card_exp: str = None, card_sig: str = None, lang: str = "ja", request: Request = None):
    """4キャラ分の編成カード画像を生成。専用スレッドプール・署名検証・レート制限は単体カードと同様。"""
    img_format = str(img_format or "png").lower()
    # 表示言語（ja / en）。署名対象外（見た目のみのパラメータのため）
    lang = "en" if str(lang or "").lower() == "en" else "ja"
    if img_format != "png":
        raise HTTPException(status_code=400, detail="WEBP形式は廃止されました。PNGのみ利用できます")
    uid = clean_uid(uid)
    ids = clean_char_ids(char_ids)
    beta = clean_bool_str(beta)
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
        if lang == "en":
            team_cache_params["lang"] = lang
        hit = _serve_card_disk(team_cache_params)
        if hit is not None:
            return Response(content=hit, media_type="image/png",
                            headers={"Cache-Control": _CARD_HTTP_CACHE_CONTROL})

    try:
        img_bytes = await _run_in_card_gen_pool(
            _generate_team_image_sync,
            uid,
            ids,
            configs_list,
            boss_obj,
            beta,
            img_format,
            lang,
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
    return Response(content=img_bytes, media_type="image/png",
                    headers={"Cache-Control": _CARD_HTTP_CACHE_CONTROL})


@api_router.get("/generate_card_image/{uid}/{avatar_id}/{calc_method}")
async def generate_card_image(uid: str, avatar_id: str, calc_method: str, fake_char: str = None, fake_weapon: str = None, beta: str = "false", bg_color: str = None, img_format: str = "png", bg_mode: str = None, bg_region: str = None, growth: str = "false", base_prec: str = "0", substat_dots: str = "1", resonance: str = None, theme: str = None, light: str = "false", show_uid: str = "false", cache: str = "", card_exp: str = None, card_sig: str = None, lang: str = "ja", request: Request = None):
    """カード画像生成。専用スレッドプールで同時実行数を制限し、超過分は列待ち。
    待ち行列が満杯のときは 503 を返す（デフォルトの threadpool は占有しない）。
    署名検証（安価）→ IPレート制限 → プール投入の順で、
    無署名・不正な直接アクセスは生成前に拒否する（DDoS対策）。

    theme=cinema|scorecard の場合は HTML テーマと同じデザインの画像を生成する。
    light=true の場合はライトモード（明るい背景・暗い文字）で生成する。
    lang=en の場合はカード内の名前・聖遺物・見出し等を英語で描画する。
    """
    img_format = str(img_format or "png").lower()
    if img_format != "png":
        # WEBP 廃止: 生成処理にも待ち行列にも入れず、即エラーを返す
        raise HTTPException(status_code=400, detail="WEBP形式は廃止されました。PNG（img_format=png）のみ利用できます")
    theme = _clean_card_theme(theme)
    # 表示言語（ja / en）。署名対象外（見た目のみのパラメータのため）
    lang = "en" if str(lang or "").lower() == "en" else "ja"
    uid = clean_uid(uid)
    avatar_id = clean_avatar_id(avatar_id)
    calc_method = clean_calc_method_strict(calc_method)
    fake_char = clean_fake_char(fake_char)
    fake_weapon = clean_fake_weapon(fake_weapon)
    beta = clean_bool_str(beta)
    growth = clean_bool_str(growth)
    base_prec = clean_base_prec(base_prec)
    substat_dots = clean_substat_dots(substat_dots)
    resonance = clean_resonance(resonance)
    bg_color = clean_bg_color(bg_color)
    bg_mode = clean_token(bg_mode, "背景モード")
    bg_region = clean_token(bg_region, "背景地域")
    light = clean_bool_str(light)
    show_uid = clean_bool_str(show_uid)
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
            "growth": growth,
            "base_prec": base_prec,
            "substat_dots": substat_dots,
            "resonance": resonance,
            "theme": theme,
            "light": light,
            "show_uid": show_uid,
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
            "bg_region": bg_region, "growth": growth, "base_prec": base_prec, "substat_dots": substat_dots,
            "resonance": resonance,
        }
        # theme 未指定(glass)は従来キャッシュキーと同一にする（既存キャッシュを無効化しない）
        if theme:
            cache_params["theme"] = theme
        # light=false はダークモードと同一出力のため、"true" のみキャッシュキーに含める
        if light == "true":
            cache_params["light"] = light
        # show_uid=false は UID 非表示（従来出力と同一）のため、"true" のみキャッシュキーに含める
        if show_uid == "true":
            cache_params["show_uid"] = show_uid
        # lang=ja は従来出力と同一のため、"en" のみキャッシュキーに含める
        if lang == "en":
            cache_params["lang"] = lang
        hit = _serve_card_disk(cache_params)
        if hit is not None:
            return Response(content=hit, media_type="image/png",
                            headers={"Cache-Control": _CARD_HTTP_CACHE_CONTROL})

    if theme:
        # cinema / scorecard: HTML テーマと同一データ・デザインの画像生成
        try:
            img_bytes = await _run_in_card_gen_pool(
                _generate_theme_card_image_sync,
                uid,
                avatar_id,
                calc_method,
                theme,
                fake_char,
                fake_weapon,
                beta,
                base_prec,
                substat_dots,
                resonance,
                growth,
                light,
                show_uid,
                lang,
            )
        except HTTPException as he:
            if he.status_code >= 500:
                report_error_to_discord(
                    "generate_card_image(theme) HTTPException",
                    str(he.detail),
                    path=f"/generate_card_image/{uid}/{avatar_id}/{calc_method}",
                    extra={
                        "uid": uid,
                        "avatar_id": avatar_id,
                        "calc_method": calc_method,
                        "theme": theme,
                        "status": he.status_code,
                        "beta": beta,
                    },
                    level="error",
                )
            elif he.status_code == 503:
                report_error_to_discord(
                    "generate_card_image(theme) queue full",
                    str(he.detail),
                    path=f"/generate_card_image/{uid}/{avatar_id}/{calc_method}",
                    extra={"uid": uid, "avatar_id": avatar_id, "theme": theme, **_card_gen_stats()},
                    level="warn",
                )
            raise
        except Exception as e:
            import traceback
            report_error_to_discord(
                "generate_card_image(theme) failed",
                f"{type(e).__name__}: {e}",
                path=f"/generate_card_image/{uid}/{avatar_id}/{calc_method}",
                extra={
                    "uid": uid,
                    "avatar_id": avatar_id,
                    "calc_method": calc_method,
                    "theme": theme,
                    "beta": beta,
                },
                traceback_text=traceback.format_exc(),
                level="error",
            )
            raise HTTPException(status_code=500, detail=f"カード生成に失敗しました: {e}") from e

        if cache == "server":
            _save_card_disk(cache_params, img_bytes)
        return Response(content=img_bytes, media_type="image/png",
                        headers={"Cache-Control": _CARD_HTTP_CACHE_CONTROL})

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
            growth,
            base_prec,
            substat_dots,
            resonance,
            light,
            show_uid,
            lang,
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
    return Response(content=img_bytes, media_type="image/png",
                    headers={"Cache-Control": _CARD_HTTP_CACHE_CONTROL})


@api_router.get("/serverup", response_class=HTMLResponse)
@api_router.post("/serverup", response_class=HTMLResponse)
@api_router.head("/serverup", response_class=HTMLResponse)
async def serverup(request: Request):
    return HTMLResponse(content="Success to access")