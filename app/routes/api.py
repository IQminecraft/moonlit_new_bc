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
from app.paths import STATIC_DIR, SHARE_DATA_DIR, templates, SITE_VERSION
from app import share_store as _share_store
from app.core.notify import report_error_to_discord
from app.core.jsonio import write_bytes_atomic
from app.routes.params import (
    clean_uid, clean_avatar_id, clean_calc_method_strict, clean_bool_str,
    clean_base_prec, clean_substat_dots, clean_resonance, clean_traveler_buffs, clean_bg_color,
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
from app.card.team_image import _generate_team_image_sync, _generate_team_image_from_cards
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

# ---- 編成共有データの置き場移行（旧: static/cache/* → 新: ルート直下 share_data/） ----
_SHARE_DATA_MIGRATIONS = [
    (os.path.join(STATIC_DIR, "cache", "share_phrases.json"), "share_phrases.json"),
    (os.path.join(STATIC_DIR, "cache", "share_short"), "short"),
    (os.path.join(STATIC_DIR, "cache", "share_snapshots"), "snapshots"),
    (os.path.join(STATIC_DIR, "cache", "abyss_share_drafts"), "abyss_drafts"),
]


def _migrate_share_data_from_cache():
    """旧配置（static/cache 直下）の共有データを share_data/ へ引っ越しる（起動時1回）。

    既存の共有リンク（sid / 短縮ID）が移行後も踏めるように、ファイル単位で
    新しい側にあるものを優先しつつ旧ファイルを移動する。
    """
    import shutil
    for old, name in _SHARE_DATA_MIGRATIONS:
        new = os.path.join(SHARE_DATA_DIR, name)
        try:
            if os.path.isdir(old):
                if not os.path.exists(new):
                    os.makedirs(os.path.dirname(new) or ".", exist_ok=True)
                    shutil.move(old, new)
                else:
                    for f in sorted(os.listdir(old)):
                        src = os.path.join(old, f)
                        dst = os.path.join(new, f)
                        if os.path.isfile(src) and not os.path.exists(dst):
                            shutil.move(src, dst)
                    try:
                        os.rmdir(old)
                    except OSError:
                        pass  # 何かが残る旧ディレクトリは削除しない
            elif os.path.isfile(old):
                if not os.path.exists(new):
                    os.makedirs(os.path.dirname(new) or ".", exist_ok=True)
                    shutil.move(old, new)
        except OSError:
            pass


_migrate_share_data_from_cache()


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

_TEAM_CFG_KEYS = {"calc_method", "fake_char", "fake_weapon", "bg_mode", "bg_color", "bg_region", "base_prec", "empty"}


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
async def enka_cooldown(uid: str, beta: str = "false"):
    """指定UIDのEnka APIクールタイム残り秒数を返す。

    可視化用に、キャッシュ（ショーケースJSON）の最終取得時刻 fetched_at も返す。
    """
    uid = str(uid or "").strip()
    fetched_at = None
    cached = False
    json_path = os.path.join(STATIC_DIR, "cache", f"showcase_{uid}.json")
    if beta == "true":
        try:
            from app.card.special import resolve_datas_path
            json_path = resolve_datas_path(json_path, "true")
        except Exception:
            pass
    try:
        fetched_at = os.path.getmtime(json_path)
        cached = True
    except OSError:
        pass
    return {
        "ok": True,
        "uid": uid,
        "cooldown": round(_enka_cooldown_remaining(uid), 1),
        "cooldown_sec": _ENKA_COOLDOWN_SEC,
        "cached": cached,
        "fetched_at": fetched_at,
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
async def fetch_uid(request: Request, uid: str, from_artifacter: bool = False, ver: str = "live", auto_refresh: bool = False):
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

    if from_artifacter or auto_refresh or not cache_exists:
        # auto_refresh(= /uid/ 直アクセス) でクールタイム中の場合は、
        # エラーにせず既存キャッシュでそのまま描画する（下の remaining>0 分岐は cache 無しのみエラー）
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
    """安定版URL: /uid/{uid}。/fetch_uid と同じ処理（共有しやすい短いURL・og:url の正規形）。

    直接アクセスされたら、クールタイム（既定60秒）を開けて毎回 enka から自動再取得する。
    クールタイム中は既存キャッシュで描画（取得失敗時も同様）。
    """
    return await fetch_uid(request=request, uid=uid, from_artifacter=False, ver=ver, auto_refresh=True)


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
async def get_card_data(uid: str, avatar_id: str, calc_method: str = "crit", fake_char: str = None, fake_weapon: str = None, beta: str = "false", growth: str = "false", base_prec: str = "0", resonance: str = None, lang: str = "ja", traveler_buffs: str = None):
    uid = clean_uid(uid)
    avatar_id = clean_avatar_id(avatar_id)
    fake_char = clean_fake_char(fake_char)
    fake_weapon = clean_fake_weapon(fake_weapon)
    beta = clean_bool_str(beta)
    growth = clean_bool_str(growth)
    base_prec = clean_base_prec(base_prec)
    resonance = clean_resonance(resonance)
    traveler_buffs = clean_traveler_buffs(traveler_buffs)
    # 表示言語（ja / en）。en の場合は名前・聖遺物・ステータス等の表示名を英語で返す
    lang = "en" if str(lang or "").lower() == "en" else "ja"
    return await run_in_threadpool(
        _get_card_data_sync, uid, avatar_id, calc_method, fake_char, fake_weapon, beta, growth, base_prec, resonance, lang, traveler_buffs
    )






@api_router.get("/api/card_sign")
async def card_sign(uid: str, avatar_id: str, calc_method: str = "crit", fake_char: str = None, fake_weapon: str = None, beta: str = "false", bg_color: str = None, img_format: str = "png", bg_mode: str = None, bg_region: str = None, growth: str = "false", base_prec: str = "0", substat_dots: str = "1", resonance: str = None, theme: str = None, light: str = "false", show_uid: str = "false", traveler_buffs: str = None, request: Request = None):
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
    traveler_buffs = clean_traveler_buffs(traveler_buffs)
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
        "traveler_buffs": traveler_buffs,
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
async def public_leyline_versions(beta: str = "false"):
    """利用可能なレイライン変換済みJSON（static/data, static/beta/data）の一覧を返す。

    beta=false（既定 / live 表示）: live 領域（現行バージョンまで）のみ返す。
    beta=true（β版表示）: beta 領域を先に、その後 live 領域を返す。
    beta 領域（未実装バージョンのリークデータ）は β版表示時のみ公開する。
    各要素の beta フィールドがデータ領域を示す（クライアントは順序ではなくこの値で優先判定できる）。
    """
    want_beta = clean_bool_str(beta) == "true"
    found = []
    # β版表示では beta 領域を先に並べる（同一 leyline ID の重複は beta を優先して扱う）
    areas = []
    if want_beta:
        areas.append((os.path.join(STATIC_DIR, "beta", "data"), "/static/beta/data", True))
    areas.append((os.path.join(STATIC_DIR, "data"), "/static/data", False))
    for d, prefix, is_beta in areas:
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if f.startswith("leyline_") and f.endswith(".json"):
                v = f[len("leyline_"):-len(".json")]
                found.append({"version": v, "url": f"{prefix}/{f}", "beta": is_beta})
    return JSONResponse({"ok": True, "versions": found}, headers={"Cache-Control": "no-store"})


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
async def team_card_sign(uid: str, char_ids: str, configs: str = "", boss: str = "", beta: str = "false", img_format: str = "png", substat_dots: str = "1", request: Request = None):
    """編成カード画像用の署名付きURLを発行。"""
    img_format = str(img_format or "png").lower()
    if img_format != "png":
        raise HTTPException(status_code=400, detail="WEBP形式は廃止されました。PNGのみ利用できます")
    uid = clean_uid(uid)
    ids = clean_char_ids(char_ids)
    beta = clean_bool_str(beta)
    substat_dots = clean_substat_dots(substat_dots)
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
        "substat_dots": substat_dots,
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
async def generate_team_image(uid: str, char_ids: str, configs: str = "", boss: str = "", beta: str = "false", img_format: str = "png", substat_dots: str = "1", cache: str = "", card_exp: str = None, card_sig: str = None, lang: str = "ja", request: Request = None):
    """4キャラ分の編成カード画像を生成。専用スレッドプール・署名検証・レート制限は単体カードと同様。"""
    img_format = str(img_format or "png").lower()
    # 表示言語（ja / en）。署名対象外（見た目のみのパラメータのため）
    lang = "en" if str(lang or "").lower() == "en" else "ja"
    if img_format != "png":
        raise HTTPException(status_code=400, detail="WEBP形式は廃止されました。PNGのみ利用できます")
    uid = clean_uid(uid)
    ids = clean_char_ids(char_ids)
    beta = clean_bool_str(beta)
    substat_dots = clean_substat_dots(substat_dots)
    configs_clean = _clean_team_configs(configs, len(ids))
    configs_list = json.loads(configs_clean) if configs_clean else []
    boss_clean = _clean_team_boss(boss)
    boss_obj = json.loads(boss_clean) if boss_clean else None
    if _CARD_URL_SECRET:
        params = {"uid": uid, "char_ids": ",".join(ids), "configs": configs_clean, "boss": boss_clean, "beta": beta, "img_format": img_format, "substat_dots": substat_dots}
        if not _verify_team_sign(card_exp, card_sig, params):
            raise HTTPException(status_code=403, detail="編成カードURLの署名が無効です。ページを再読み込みしてください。")
    if _rate_limited(f"gen:{_client_ip(request)}", _CARD_GEN_RATE_LIMIT_PER_MIN):
        raise HTTPException(status_code=429, detail="画像生成のリクエストが頻繁すぎます。しばらく待って再試行してください。")

    # サーバー側ディスクキャッシュ（cache=server）: 同一パラメータなら再生成せず返す
    if cache == "server":
        team_cache_params = {
            "uid": uid, "char_ids": ",".join(ids), "configs": configs_clean,
            "boss": boss_clean, "beta": beta, "img_format": img_format,
            "substat_dots": substat_dots,
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
            substat_dots,
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


# ---- 合言葉による設定引き継ぎ（有効時間 1h/12h/24h の一時データ置き場） ----
# 電気通信事業の届出が不要な「一時的なデータ共有」の枠組み:
# 送信者が自分の設定スナップショットを合言葉付きで短期保存し、受け取者が同じ合言葉で取得するだけ。
_SHARE_PHRASES_PATH = os.path.join(SHARE_DATA_DIR, "share_phrases.json")
_SHARE_PHRASE_LOCK = threading.Lock()
_SHARE_PHRASE_TTL_HOURS = (1, 12, 24)
_SHARE_PHRASE_MAX_BYTES = 256 * 1024


def _load_share_phrases() -> dict:
    try:
        with open(_SHARE_PHRASES_PATH, "r", encoding="utf-8") as f:
            store = json.load(f)
        return store if isinstance(store, dict) else {}
    except Exception:
        return {}


def _save_share_phrases(store: dict) -> None:
    os.makedirs(os.path.dirname(_SHARE_PHRASES_PATH), exist_ok=True)
    tmp = _SHARE_PHRASES_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False)
    os.replace(tmp, _SHARE_PHRASES_PATH)


def _prune_share_phrases(store: dict) -> dict:
    now = time.time()
    return {k: v for k, v in store.items()
            if isinstance(v, dict) and isinstance(v.get("exp"), (int, float)) and v["exp"] > now}


@api_router.post("/api/share_phrase")
async def api_share_phrase_set(request: Request):
    """設定スナップショットを合言葉付きで一時保存する（TTL 1/12/24時間）。"""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="リクエストボディが不正です")
    phrase = str(body.get("phrase") or "").strip()
    ttl_hours = body.get("ttl_hours", 24)
    data = body.get("data")
    if not phrase or len(phrase) > 64:
        raise HTTPException(status_code=400, detail="合言葉は1〜64文字で指定してください")
    if ttl_hours not in _SHARE_PHRASE_TTL_HOURS:
        ttl_hours = 24
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="データが不正です")
    if len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > _SHARE_PHRASE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="データが大きすぎます")
    with _SHARE_PHRASE_LOCK:
        store = _prune_share_phrases(_load_share_phrases())
        store[phrase] = {"data": data, "exp": time.time() + ttl_hours * 3600}
        _save_share_phrases(store)
    return {"ok": True, "ttl_hours": ttl_hours}


@api_router.get("/api/share_phrase/{phrase}")
async def api_share_phrase_get(phrase: str):
    with _SHARE_PHRASE_LOCK:
        store = _prune_share_phrases(_load_share_phrases())
        _save_share_phrases(store)
        entry = store.get(str(phrase or "").strip())
    if not entry:
        raise HTTPException(status_code=404, detail="合言葉が見つからないか、有効期限が切れています")
    remaining_h = max(0.0, (entry["exp"] - time.time()) / 3600)
    return {"ok": True, "data": entry["data"], "expires_in_hours": round(remaining_h, 1)}


# ---- 編成の共有画像（1〜3編成を1枚に縦積み） ----
@api_router.post("/api/team_share_image")
async def api_team_share_image(request: Request):
    """1〜3編成のカード画像を1枚のPNGに縦積みして返す。認証不要のPOST（キャッシュしない）。

    cards モード: teams[].cards に共有スナップショットの card_data 配列を渡すと、
    UID不要・その場のショーケース参照なしで、HTML編成カードと同一データから画像を生成する
    （共有閲覧用。値は共有時点で凍結され、UID非公開共有でも生成できる）。
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="リクエストボディが不正です")
    uid = str(body.get("uid") or "").strip()
    lang = "en" if str(body.get("lang") or "").lower() == "en" else "ja"
    teams = body.get("teams")
    abyss_meta = body.get("abyss") if isinstance(body.get("abyss"), dict) else None
    abyss_mode = bool(abyss_meta) and str(body.get("mode") or "") == "abyss"
    # cards モードは全編成が cards 配列（1〜4枚・要素は dict または null）を持つとき
    cards_mode = (not abyss_mode) and isinstance(teams, list) and (1 <= len(teams) <= 3) and all(
        isinstance(t, dict) and isinstance(t.get("cards"), list) and 1 <= len(t.get("cards")) <= 4
        and all((c is None) or isinstance(c, dict) for c in t["cards"])
        for t in teams
    )
    beta = str(body.get("beta") or "").lower() == "true"
    substat_dots = clean_substat_dots(body.get("substat_dots", "1"))
    if cards_mode and _rate_limited(f"tshare:{_client_ip(request)}", 30):
        raise HTTPException(status_code=429, detail="共有画像の生成要求が多すぎます。少し待って再試行してください")
    # 幽境共有は UID非公開(uid空)でも描画可能（メンバーアイコンは sid スナップショットから解決）
    if not abyss_mode and not cards_mode and not uid.isdigit():
        raise HTTPException(status_code=400, detail="UIDが不正です")
    if abyss_meta and str(body.get("mode") or "") == "abyss":
        # 幽境共有: baseimg+ボス縦3の描画のみ（重いカード生成は不要）

        def _gen_abyss() -> bytes:
            import io as _io
            from PIL import Image as _Image, ImageDraw as _ImageDraw, ImageFont as _ImageFont
            base_path = os.path.join(STATIC_DIR, "assets", "abyss_share_base.png")
            base = _Image.open(base_path).convert("RGBA")
            outW, outH = base.size
            # admin/HTML と同じ 868×560 デザイン空間へ正規化して描画（最後に元サイズへ戻す）
            if (outW, outH) != (_ABYSS_DESIGN_W, _ABYSS_DESIGN_H):
                base = base.resize((_ABYSS_DESIGN_W, _ABYSS_DESIGN_H))
            W, H = base.size
            canvas = base.copy()
            hd = _ImageDraw.Draw(canvas)
            try:
                from app.paths import FONT_PATH
                font_path = FONT_PATH if os.path.exists(FONT_PATH) else None
            except Exception:
                font_path = None

            def _f(sz):
                return _ImageFont.truetype(font_path, sz) if font_path else _ImageFont.load_default()

            L = _load_abyss_layout()
            pad = 24
            DIFF_LABELS = {"master": "マスター", "extra": "エクストラ", "ultimate": "アルティメット"}
            head_col = (252, 97, 63, 255) if str(abyss_meta.get("difficulty") or "") == "ultimate" else (235, 224, 218, 255)
            hh = L.get("header") or {}
            hd.text((int(hh.get("x", pad)), int(hh.get("y", pad))), "Ver " + str(abyss_meta.get("version") or ""),
                    font=_f(int(hh.get("size", 34))), fill=(255, 157, 138, 240))
            diff = DIFF_LABELS.get(str(abyss_meta.get("difficulty") or ""), str(abyss_meta.get("difficulty") or ""))
            if diff:
                df = _f(18)
                tw = hd.textlength(diff, font=df)
                hd.text((W - pad - tw, int(hh.get("y", pad)) + 2), diff, font=df, fill=head_col)
            # 確認用リンク（▶ YouTube 等）は画像上には描かない（HTML側は topbar のブランドアイコンのみ）
            # {名前} : {UID} タグ（admin で x/y/サイズ/表示を調整可能、UID非公開共有は名前のみ）
            _pt = L.get("playerTag") or {}
            if _pt.get("show", True):
                _uid_ = str(abyss_meta.get("uid") or "").strip() if abyss_meta.get("show_uid") else ""
                _pname_ = str(abyss_meta.get("pname") or "").strip()
                if _pname_ and _uid_:
                    _tag_ = _pname_ + " : " + _uid_
                else:
                    _tag_ = _pname_ or ("UID " + _uid_ if _uid_ else "")
                if _tag_:
                    _pt_sz = int(_pt.get("size", 15))
                    _tf_ = _f(_pt_sz)
                    _px = int(_pt.get("x", 846))
                    if str(_pt.get("align", "right")) == "right":
                        _px -= int(hd.textlength(_tag_, font=_tf_))
                    hd.text((_px, int(_pt.get("y", 526))), _tag_, font=_tf_, fill=(255, 255, 255, 190))
            bosses = abyss_meta.get("bosses") or []
            head_col = (252, 97, 63, 255) if str(abyss_meta.get("difficulty") or "") == "ultimate" else (235, 224, 218, 255)
            if bosses:
                row_h = int(L.get("rowHeight", 120))
                row_pad = L.get("rowPadding") or {}
                row_x = int(row_pad.get("x", 18))
                row_y0 = int(row_pad.get("y", 14))
                bi_cfg = L.get("bossIcon") or {}
                nm_cfg = L.get("bossName") or {}
                tm_cfg = L.get("clearTime") or {}
                ci_cfg = L.get("charIcon") or {}
                icon_sz = int(bi_cfg.get("size", 64))
                draw_row_panel = bool(L.get("rowPanel", False))  # 既定では行背景/枠を描かない
                icons = {}
                sid_ = str(abyss_meta.get("sid") or "")
                snap = _load_share_snapshot(sid_) if sid_ else None
                if snap:
                    # スナップショット参照（uid非表示でもアイコンがある）
                    for cid, entry in (snap.get("chars") or {}).items():
                        icons[cid] = str(entry.get("icon") or "")
                else:
                    try:
                        json_path = os.path.join(STATIC_DIR, "cache", f"showcase_{uid}.json")
                        show = _load_json_auto(json_path)
                        for av in (show or {}).get("avatarInfoList") or []:
                            icons[str(av.get("avatarId"))] = str((((av.get("heroImages") or [{}])[0]).get("icon")) or "")
                    except Exception:
                        pass
                y = row_y0
                for bi, boss in enumerate(bosses):
                    by = y + bi * row_h
                    bx = row_x
                    bw = W - row_x * 2
                    if draw_row_panel:
                        hd.rounded_rectangle([bx, by, bx + bw, by + row_h - int(L.get("rowGap", 14))], radius=16,
                                             fill=(20, 16, 20, 200), outline=(230, 90, 70, 110), width=2)
                    iy = by + int(bi_cfg.get("y", 0)) + (0 if bi_cfg.get("y") else (row_h - 14 - icon_sz) // 2)
                    ix = bx + int(bi_cfg.get("x", 20))
                    img_p = str(boss.get("img") or "")
                    if img_p:
                        p_ = img_p if os.path.isabs(img_p) else os.path.join(".", img_p.lstrip("/"))
                        if os.path.exists(p_):
                            try:
                                eim = _Image.open(p_).convert("RGBA").resize((icon_sz, icon_sz))
                                cmask = _Image.new("L", (icon_sz, icon_sz), 0)
                                _ImageDraw.Draw(cmask).ellipse([0, 0, icon_sz - 1, icon_sz - 1], fill=255)
                                canvas.paste(eim, (ix, iy), cmask)
                                _ImageDraw.Draw(canvas).ellipse([ix - 1, iy - 1, ix + icon_sz + 1, iy + icon_sz + 1], outline=(137, 116, 203, 255), width=2)
                            except Exception:
                                pass
                        ix += icon_sz + 14
                    import re as _re
                    name_sz = int(nm_cfg.get("size", 21))
                    name_x = bx + int(nm_cfg.get("x", 96))
                    name_y = by + int(nm_cfg.get("y", 16))
                    clean_name = _re.sub(r"[（(][^）)]*通常状態[）)]", "", str(boss.get("name") or "")).strip()
                    hd.text((name_x, name_y), clean_name, font=_f(name_sz), fill=head_col)
                    bt = str(boss.get("time") or "")
                    if bt:
                        tf_sz = int(tm_cfg.get("size", 16))
                        tf = _f(tf_sz)
                        tx = bx + int(tm_cfg.get("x", 96))
                        ty = by + int(tm_cfg.get("y", 44))
                        hd.text((tx, ty), bt, font=tf, fill=head_col)
                    chars = boss.get("chars") or []
                    msz = int(ci_cfg.get("size", 60))
                    mgap = int(ci_cfg.get("gap", 10))
                    packed = 4
                    if ci_cfg.get("align") == "right":
                        mx = W - row_x - 24 - packed * (msz + mgap) + mgap + int(ci_cfg.get("x", 0))
                    else:
                        mx = bx + int(ci_cfg.get("x", 0))
                    my = by + int(ci_cfg.get("y", 0)) + (0 if ci_cfg.get("y") else (row_h - 14 - msz) // 2)
                    for cid in chars:
                        src_icon = icons.get(str(cid)) or ""
                        if src_icon:
                            icon_file = src_icon.replace("AvatarIcon", "Gacha_AvatarImg").replace("/characters/", "/splash/")
                            p_ = icon_file if os.path.isabs(icon_file) else os.path.join(".", icon_file.lstrip("/"))
                            if not os.path.exists(p_):
                                p_ = os.path.join(".", "static", "assets", "characters", f"{src_icon}.webp")
                            if os.path.exists(p_):
                                try:
                                    cim = _Image.open(p_).convert("RGBA").resize((msz, msz))
                                    mask = _Image.new("L", (msz, msz), 0)
                                    _ImageDraw.Draw(mask).rounded_rectangle([0, 0, msz - 1, msz - 1], radius=8, fill=255)
                                    canvas.paste(cim, (int(mx), int(my)), mask)
                                except Exception:
                                    pass
                        hd.rounded_rectangle([mx - 1, my - 1, mx + msz + 1, my + msz + 1], radius=9,
                                             outline=(255, 255, 255, 90), width=2)
                        mx += msz + mgap
            buf = _io.BytesIO()
            if (outW, outH) != (W, H):
                canvas = canvas.resize((outW, outH), _Image.LANCZOS)
            canvas.convert("RGB").save(buf, "PNG", compress_level=1)
            return buf.getvalue()

        png = await run_in_threadpool(_gen_abyss)
        return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})

    if not isinstance(teams, list) or not (1 <= len(teams) <= 3):
        raise HTTPException(status_code=400, detail="1〜3編成を指定してください")
    payloads = []
    if cards_mode:
        # 共有スナップショットの card_data から直接描画（UID・ショーケース参照なし）
        for t in teams[:3]:
            cards = (t.get("cards") or [])[:4]
            if not any(isinstance(c, dict) and not c.get("error") for c in cards):
                continue
            img = await _run_in_card_gen_pool(_generate_team_image_from_cards, cards, "true" if beta else "false", lang, substat_dots)
            payloads.append(img)
    else:
        for t in teams[:3]:
            if not isinstance(t, dict):
                continue
            chars = [str(c) for c in (t.get("chars") or []) if c][:4]
            if not chars:
                continue
            cfgs = t.get("configs") if isinstance(t.get("configs"), list) else []
            cfgs = [c if isinstance(c, dict) else {} for c in (cfgs + [{}] * 4)][:4]
            img = await _run_in_card_gen_pool(
                _generate_team_image_sync, uid, chars, cfgs, None, "false", "png", lang, substat_dots,
            )
            payloads.append(img)
    if not payloads:
        raise HTTPException(status_code=400, detail="メンバーがいる編成がありません")

    def _stack_teams(imgs: list) -> bytes:
        """編成カード画像（HTML同期レイアウト）を縦に並べて1枚のPNGにする。"""
        import io as _io
        from PIL import Image as _Image
        parts = [_Image.open(_io.BytesIO(b)).convert("RGBA") for b in imgs]
        w = max(p.width for p in parts)
        gap = 16
        total_h = sum(p.height for p in parts) + gap * (len(parts) - 1)
        canvas = _Image.new("RGBA", (w, total_h), (9, 9, 11, 255))
        yy = 0
        for p in parts:
            # 幅が異なる場合は中央寄せ（同一レイアウト生成なら同幅）
            xx = (w - p.width) // 2
            canvas.alpha_composite(p, (xx, yy))
            yy += p.height + gap
        out = _io.BytesIO()
        canvas.convert("RGB").save(out, "PNG", compress_level=1)
        return out.getvalue()

    png = await run_in_threadpool(_stack_teams, payloads)
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


@api_router.get("/generate_card_image/{uid}/{avatar_id}/{calc_method}")
async def generate_card_image(uid: str, avatar_id: str, calc_method: str, fake_char: str = None, fake_weapon: str = None, beta: str = "false", bg_color: str = None, img_format: str = "png", bg_mode: str = None, bg_region: str = None, growth: str = "false", base_prec: str = "0", substat_dots: str = "1", resonance: str = None, theme: str = None, light: str = "false", show_uid: str = "false", cache: str = "", card_exp: str = None, card_sig: str = None, lang: str = "ja", traveler_buffs: str = None, request: Request = None):
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
    traveler_buffs = clean_traveler_buffs(traveler_buffs)
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
            "traveler_buffs": traveler_buffs,
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
            "resonance": resonance, "traveler_buffs": traveler_buffs,
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
                traveler_buffs,
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
            traveler_buffs,
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

# ---- 共有閲覧ページ（/share/{data}）----
def _b64url_decode_json(data: str):
    """共有ペイロードのデコード。新形式(hex+zlib)と旧形式(base64url)の両方に対応。"""
    import base64, zlib
    s = str(data or "").strip()
    if not s:
        return None
    # 新形式: 16進(0-9a-f)のみ → zlib圧縮JSON
    if s and all(c in "0123456789abcdefABCDEF" for c in s):
        try:
            raw = zlib.decompress(bytes.fromhex(s))
            return json.loads(raw.decode("utf-8"))
        except Exception:
            pass
    # 旧形式: base64url（- と _ は URL で壊れにくいが、一部環境で欠落しうる）
    try:
        t = s.replace("-", "+").replace("_", "/")
        t += "=" * (-len(t) % 4)
        raw = base64.b64decode(t)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        # URL転送で欠落した可能性 → 寛容に「- を消して」再試行
        try:
            t = s.replace("-", "")
            t += "=" * (-len(t) % 4)
            raw = base64.b64decode(t)
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None


def _resolve_char_icons(uid: str, cids: list) -> dict:
    """ショーケースの avatarId → スプラッシュ/アイコンURL のマップを作る。"""
    icons = {}
    try:
        json_path = os.path.join(STATIC_DIR, "cache", f"showcase_{uid}.json")
        show = _load_json_auto(json_path)
        for av in (show or {}).get("avatarInfoList") or []:
            aid = str(av.get("avatarId"))
            icon = ""
            imgs = av.get("heroImages") or []
            if imgs:
                icon = str((imgs[0] or {}).get("icon") or "")
            if icon:
                splash = icon.replace("AvatarIcon", "Gacha_AvatarImg").replace("/characters/", "/splash/")
                if os.path.exists(os.path.join(".", splash.lstrip("/"))):
                    icons[aid] = "/" + splash.lstrip("/")
                    continue
            icons[aid] = f"/static/assets/characters/{icon}.webp" if icon else ""
    except Exception:
        pass
    return icons


@api_router.get("/api/share_view/{data}")
async def api_share_view(data: str):
    """共有ペイロード(b64url JSON)をデコードし、キャラアイコンを解決して返す。"""
    payload = _b64url_decode_json(data)
    if not isinstance(payload, dict) or payload.get("type") not in ("abyss", "teams"):
        raise HTTPException(status_code=404, detail="共有データが見つかりません")
    uid = str(payload.get("uid") or "")
    icons = _resolve_char_icons(uid, [])
    cards = {}
    sid = str(payload.get("sid") or "")
    snap = _load_share_snapshot(sid) if sid else None
    if snap:
        for cid, entry in (snap.get("chars") or {}).items():
            icons[cid] = str(entry.get("icon") or "")
            cards[cid] = entry.get("card") or {}
    if payload.get("type") == "abyss":
        bosses = payload.get("bosses") or []
        for b in bosses:
            b["icons"] = {str(c): icons.get(str(c), "") for c in (b.get("chars") or [])}
    else:
        for t in payload.get("teams") or []:
            t["icons"] = {str(c): icons.get(str(c), "") for c in (t.get("c") or [])}
    return {"ok": True, "data": payload, "cards": cards}


@api_router.get("/share/{data}", response_class=HTMLResponse)
async def share_view_page(request: Request, data: str):
    """共有閲覧ページ。build_card.html を共有モードで返し、topbar/ガラスカードを再利用する。"""
    payload = _b64url_decode_json(data)
    if not isinstance(payload, dict) or payload.get("type") not in ("abyss", "teams"):
        raise HTTPException(status_code=404, detail="共有データが見つかりません")
    # リンク自体にキャラデータが含まれるため、ショーケースキャッシュは読まない
    uid = str(payload.get("uid") or "")
    beta = bool(payload.get("beta"))
    char_list = []
    _player_name = str(payload.get("pname") or "").strip()
    return templates.TemplateResponse("build_card.html", {
        "request": request,
        "uid": uid if uid.isdigit() else "",
        "char_list": char_list,
        "ver": "beta" if beta else "live",
        "player_name": _player_name,
        "player_level": None,
        "first_char_element": None,
        "show_team_abyss_buttons": False,
        "show_status_view_setting": False,
        "show_score_history": False,
        "pfp_id": "",
        "namecard_id": "",
        "og_title": "編成共有 — moonlit.wiki",
        "og_description": "moonlit.wiki の編成共有リンクです。",
        "og_image": "",
        "og_url": f"{str(request.base_url).rstrip('/')}/share/{data}",
        "share_payload": data,
        "site_version": SITE_VERSION,
    })


# ---- 共有リンクの短縮（/s/{id} → /share/{data} へリダイレクト） ----
# 「jsonで乱数IDを発行し、長い圧縮リンクと紐付けて保存。/s/{id} でリダイレクトする」方式。
# データはサーバー側JSONに保存し、URL自体は短く保つ（後日の圧縮方式変更にも対応しやすい）。
_SHARE_SHORT_TTL = 30 * 24 * 3600  # 30日（teams共有のみ・踏むたびローリング。幽境は exp=0 で無期限）
_SHARE_SHORT_ID_RE_CHARS = "23456789abcdefghjkmnpqrstuvwxyz"  # 紛らわしい文字(0/o/1/l/i)を除く


def _share_short_key(sid: str) -> str:
    return f"short/{sid}.json"


def _prune_share_short_links():
    now = time.time()
    for key in _share_store.list_keys("short/"):
        try:
            raw = _share_store.get(key)
            if not raw:
                continue
            d = json.loads(raw.decode("utf-8"))
            e = float(d.get("exp", 0) or 0)
            if e > 0 and e < now:
                _share_store.delete(key)
        except Exception:
            pass


@api_router.post("/api/share_shorten")
async def api_share_shorten(request: Request):
    """長い共有リンク（/share/{data}）を短縮IDに紐付けて保存し、短縮URLを返す。

    body: {url: "<origin>/share/{data}"} → {ok, id, url: "<origin>/s/{id}"}
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="リクエストボディが不正です")
    url = str(body.get("url") or "").strip()
    # /share/{data} 形式のみ受け付ける（data 部分は hex または base64url）
    prefix = "/share/"
    idx = url.find(prefix)
    if idx < 0:
        raise HTTPException(status_code=400, detail="共有リンク（/share/...）を指定してください")
    data = url[idx + len(prefix):].strip("/")
    if not data or len(data) > 8192:
        raise HTTPException(status_code=400, detail="リンクが不正または長すぎます")
    kind = "abyss" if str(body.get("kind") or "").lower() == "abyss" else "teams"

    def _run() -> dict:
        _prune_share_short_links()
        # 既存の同一データは同じIDを再利用（生成のたびに別IDが増えないようにする）
        existing = None
        for key in _share_store.list_keys("short/"):
            try:
                raw = _share_store.get(key)
                if not raw:
                    continue
                d = json.loads(raw.decode("utf-8"))
                if d.get("data") == data:
                    existing = d
                    break
            except Exception:
                continue
        if existing:
            sid = str(existing.get("id") or "")
        else:
            import secrets as _secrets
            while True:
                sid = "".join(_secrets.choice(_SHARE_SHORT_ID_RE_CHARS) for _ in range(7))
                if _share_store.get(_share_short_key(sid)) is None:
                    break
        entry = {"id": sid, "data": data,
                 "exp": (0.0 if kind == "abyss" else time.time() + _SHARE_SHORT_TTL),
                 "kind": kind, "created": time.time()}
        _share_store.put(_share_short_key(sid), json.dumps(entry, ensure_ascii=False).encode("utf-8"))
        return {"id": sid}

    result = await run_in_threadpool(_run)
    origin = str(request.base_url).rstrip("/")
    return {"ok": True, "id": result["id"], "url": f"{origin}/s/{result['id']}"}


@api_router.get("/s/{sid}", response_class=HTMLResponse)
async def share_short_redirect(request: Request, sid: str):
    """短縮IDから共有データを復元して /share/{data} へリダイレクトする。"""
    sid = str(sid or "").strip()
    if not sid or len(sid) > 32 or not all(c in _SHARE_SHORT_ID_RE_CHARS for c in sid):
        raise HTTPException(status_code=404, detail="共有データが見つかりません")

    def _load():
        try:
            raw = _share_store.get(_share_short_key(sid))
            if not raw:
                return None
            d = json.loads(raw.decode("utf-8"))
            e = float(d.get("exp", 0) or 0)
            if e > 0 and e < time.time():
                return None
            return d
        except Exception:
            return None

    entry = await run_in_threadpool(_load)
    if not entry or not entry.get("data"):
        raise HTTPException(status_code=404, detail="共有データが見つからないか、有効期限が切れています")
    # ローリング更新: アクセスのたびに期限を延長
    def _touch():
        if float(entry.get("exp", 0) or 0) <= 0:
            return  # 無期限（幽境）は更新不要
        try:
            entry2 = dict(entry)
            entry2["exp"] = time.time() + _SHARE_SHORT_TTL
            _share_store.put(_share_short_key(sid), json.dumps(entry2, ensure_ascii=False).encode("utf-8"))
        except Exception:
            pass
    await run_in_threadpool(_touch)
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/share/{entry['data']}", status_code=302)


@api_router.get("/api/share_card_image/{sid}/{cid}")
async def api_share_card_image(sid: str, cid: str):
    """共有スナップショットのキャラ1人のビルドカード画像(単体カード)を返す。

    スナップショット保存時の card_data を単体カード生成器に渡すため、
    一時的にシャーディングした showcase ではなく card_data 直接経由で描画する。
    """
    snap = await run_in_threadpool(_load_share_snapshot, str(sid or ""))
    if not snap:
        raise HTTPException(status_code=404, detail="共有データが見つからないか、有効期限が切れています")
    entry = (snap.get("chars") or {}).get(str(cid))
    if not entry or not isinstance(entry.get("card"), dict):
        raise HTTPException(status_code=404, detail="このキャラのデータがありません")

    card = entry["card"]
    img_bytes = await _run_in_card_gen_pool(
        _generate_card_from_snapshot, card,
    )
    return Response(content=img_bytes, media_type="image/png", headers={"Cache-Control": "no-store"})


def _generate_card_from_snapshot(card: dict) -> bytes:
    """card_data(スナップショット)から単体ビルドカード画像を描く。"""
    import io as _io
    from PIL import Image as _Image, ImageDraw as _ImageDraw, ImageFont as _ImageFont
    try:
        from app.paths import FONT_PATH, FONT_LIGHT_PATH
    except Exception:
        FONT_PATH = FONT_LIGHT_PATH = ""
    font_path = FONT_PATH if os.path.exists(FONT_PATH) else None
    font_light = FONT_LIGHT_PATH if os.path.exists(FONT_LIGHT_PATH) else font_path

    def _f(sz, light=False):
        p_ = font_light if light else font_path
        return _ImageFont.truetype(p_, sz) if p_ else _ImageFont.load_default()

    W, H = 1200, 800
    elem = str(card.get("element") or "None").lower()
    ELEM_BG = {"pyro": (144, 59, 42), "hydro": (52, 69, 149), "cryo": (87, 127, 199),
               "dendro": (70, 107, 99), "geo": (106, 103, 72), "electro": (115, 74, 140),
               "anemo": (18, 149, 136), "none": (74, 85, 104)}
    base_rgb = ELEM_BG.get(elem, ELEM_BG["none"])
    canvas = _Image.new("RGBA", (W, H), base_rgb + (255,))
    # 暗めのオーバーレイで文字視認性確保
    ov = _Image.new("RGBA", (W, H), (10, 12, 18, 140))
    canvas.alpha_composite(ov)
    hd = _ImageDraw.Draw(canvas)

    pad = 36
    # 名前 + Lv + 凸
    hd.text((pad, pad), str(card.get("displayName") or ""), font=_f(44), fill=(255, 255, 255, 255))
    sub = f"Lv.{card.get('level', '?')}  C{card.get('constellation', '?')}  ♥{card.get('friendship', '?') if card.get('friendship') is not None else '?'}"
    hd.text((pad, pad + 60), sub, font=_f(20), fill=(255, 255, 255, 200))
    # 武器
    hd.text((pad, pad + 96), f"武器: {card.get('weaponName', '-')}  Lv.{card.get('weaponLevel', '?')} R{card.get('weaponAffix', 1)}",
            font=_f(18), fill=(255, 255, 255, 220))
    # 元素背景にキャラスプラッシュを敷く
    splash = str(card.get("splash") or "")
    if splash:
        p_ = splash if os.path.isabs(splash) else os.path.join(".", splash.lstrip("/"))
        if not os.path.exists(p_) and splash.startswith("/"):
            p_ = os.path.join(".", splash.lstrip("/"))
        if os.path.exists(p_):
            try:
                sim = _Image.open(p_).convert("RGBA")
                scale = max(H / sim.height, W * 0.45 / sim.width)
                sim = sim.resize((int(sim.width * scale), int(sim.height * scale)))
                sim = sim.crop((sim.width - W, 0, sim.width, H)) if sim.width > W else sim
                mask = sim.getchannel("A").point(lambda a: int(a * 0.75))
                canvas.paste(sim, (W - sim.width, 0), mask)
            except Exception:
                pass
    # ステータス8項目(左側縦並び)
    sx = pad
    sy = pad + 140
    for s in (card.get("mainStats") or []):
        hd.rounded_rectangle([sx - 8, sy - 4, sx + 330, sy + 34], radius=8, fill=(15, 18, 26, 170))
        icon_p = str(s.get("icon") or "")
        if icon_p:
            p_ = icon_p if os.path.isabs(icon_p) else os.path.join(".", icon_p.lstrip("/"))
            if os.path.exists(p_):
                try:
                    iim = _Image.open(p_).convert("RGBA").resize((28, 28))
                    canvas.alpha_composite(iim, (sx, sy + 1))
                except Exception:
                    pass
        hd.text((sx + 40, sy + 2), str(s.get("label") or ""), font=_f(18, light=True), fill=(255, 255, 255, 200))
        hd.text((sx + 200, sy), str(s.get("val") or ""), font=_f(20), fill=(255, 255, 255, 255))
        sy += 44
    # 聖遺物(右側 5行)
    ax = 520
    ay = pad + 140
    for a in (card.get("artifacts") or [])[:5]:
        if not a:
            continue
        hd.rounded_rectangle([ax, ay, ax + 620, ay + 62], radius=10, fill=(15, 18, 26, 170))
        icon_p = str(a.get("icon") or "")
        if icon_p:
            p_ = icon_p if os.path.isabs(icon_p) else os.path.join(".", icon_p.lstrip("/"))
            if os.path.exists(p_):
                try:
                    aim = _Image.open(p_).convert("RGBA").resize((48, 48))
                    canvas.alpha_composite(aim, (ax + 8, ay + 7))
                except Exception:
                    pass
        main = a.get("main") or {}
        hd.text((ax + 66, ay + 6), str(main.get("name") or ""), font=_f(15, light=True), fill=(255, 255, 255, 200))
        hd.text((ax + 66, ay + 26), str(main.get("value") or ""), font=_f(19), fill=(255, 255, 255, 255))
        subs = "  ".join(f"{s.get('name', '')} {s.get('value', '')}" for s in (a.get("substats") or []) if s)
        hd.text((ax + 230, ay + 8), subs[:60], font=_f(13), fill=(255, 255, 255, 210))
        tier = str(a.get("tier") or "B")
        tier_p = os.path.join(".", "static", "assets", "tiers", f"{tier}.png")
        if os.path.exists(tier_p):
            try:
                tim = _Image.open(tier_p).convert("RGBA").resize((34, 34))
                canvas.alpha_composite(tim, (ax + 500, ay + 14))
            except Exception:
                pass
        hd.text((ax + 545, ay + 18), f"{float(a.get('score', 0)):.1f}", font=_f(17), fill=(255, 255, 255, 240))
        ay += 70
    # 総合スコア
    hd.text((ax, ay + 4), f"総合スコア {float(card.get('scoreSum', 0)):.1f}", font=_f(24), fill=(255, 220, 120, 255))
    buf = _io.BytesIO()
    canvas.convert("RGB").save(buf, "PNG", compress_level=1)
    return buf.getvalue()


# ---- 幽境共有レイアウト設定（admin がアイコン/文字の位置を調整） ----
_ABYSS_LAYOUT_PATH = os.path.join(STATIC_DIR, "data", "setting", "abyss_share_layout.json")
_SHARE_SNAPSHOT_TTL = 30 * 24 * 3600  # 30日（共有リンクのデータ本体はこのスナップショットを参照するため）
_ABYSS_LAYOUT_DEFAULTS = {
    "bossIcon": {"x": 20, "y": 60, "size": 64},
    "charIcon": {"x": 0, "y": 0, "size": 60, "gap": 10, "align": "right"},
    "bossName": {"x": 96, "y": 62, "size": 17},
    "clearTime": {"x": 0, "y": 0, "size": 15, "align": "right"},
    "rowPadding": {"x": 18, "y": 14},
    "rowHeight": 120,
    "rowGap": 14,
    "header": {"x": 24, "y": 24, "size": 34},
    "rowPanel": False,
    # {名前} : {UID} タグ(UID非公開共有は名前のみ)。x/y は 868×560 デザイン空間。
    # align='right'(既定): x=右端の位置 / align='left': x=左端の位置
    "playerTag": {"show": True, "x": 846, "y": 526, "size": 15, "align": "right"},
}

_ABYSS_DESIGN_W, _ABYSS_DESIGN_H = 868, 560


def _player_tag_cfg(v) -> dict:
    """bool 旧形式(true/false)も許容して常に dict で返す"""
    d = dict(_ABYSS_LAYOUT_DEFAULTS["playerTag"])
    if isinstance(v, dict):
        for k, val in v.items():
            if k in d and isinstance(val, (int, float, str, bool)):
                d[k] = val
    elif isinstance(v, bool):
        d["show"] = v
    return d


def _load_abyss_layout() -> dict:
    cfg = dict(_ABYSS_LAYOUT_DEFAULTS)
    try:
        with open(_ABYSS_LAYOUT_PATH, "r", encoding="utf-8") as f:
            user = json.load(f)
        if isinstance(user, dict):
            for k, v in user.items():
                if k in cfg and isinstance(v, dict) and isinstance(cfg[k], dict):
                    cfg[k] = {**cfg[k], **v}
                elif k in cfg:
                    cfg[k] = v
    except Exception:
        pass
    cfg["playerTag"] = _player_tag_cfg(cfg.get("playerTag"))
    return cfg


@api_router.get("/api/abyss_share_layout")
async def api_abyss_share_layout_get():
    return {"ok": True, "layout": _load_abyss_layout()}


def _prune_share_snapshots():
    now = time.time()
    for key in _share_store.list_keys("snapshots/"):
        try:
            raw = _share_store.get(key)
            if not raw:
                continue
            d = json.loads(raw.decode("utf-8"))
            e = float(d.get("exp", 0) or 0)
            if e > 0 and e < now:
                _share_store.delete(key)
        except Exception:
            pass


def _snapshot_key(sid: str) -> str:
    return f"snapshots/{sid}.json"


def _load_share_snapshot(sid: str):
    # exp==0 は無期限（幽境）。ローカル/R2 の差分は share_store が吸収。
    try:
        raw = _share_store.get(_snapshot_key(str(sid or "")))
        if not raw:
            return None
        d = json.loads(raw.decode("utf-8"))
        exp = float(d.get("exp", 0) or 0)
        if exp > 0 and exp < time.time():
            return None
        return d
    except Exception:
        return None


@api_router.post("/api/share_snapshot")
async def api_share_snapshot_create(request: Request):
    """共有するキャラの card_data をスナップショット保存し sid を返す。

    body: {uid, show_uid, beta, kind, chars: [{cid, calc_method, base_prec, fake_char, fake_weapon}]}
    show_uid=false の場合はファイルに uid を書き込まない。
    kind=abyss（幽境の編成共有）は無期限(exp=0)、既定(teams)は30日TTL。
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="リクエストボディが不正です")
    uid = str(body.get("uid") or "").strip()
    show_uid = bool(body.get("show_uid"))
    beta = bool(body.get("beta"))
    chars = body.get("chars")
    if not uid.isdigit() or not isinstance(chars, list) or not chars:
        raise HTTPException(status_code=400, detail="パラメータが不正です")
    if len(chars) > 12:
        raise HTTPException(status_code=400, detail="キャラが多すぎます")
    kind = "abyss" if str(body.get("kind") or "").lower() == "abyss" else "teams"
    # 幽境共有: 管理画面で「いつの幽境か」を出せるよう、概要(バージョン/難易度/ボス要約)を凍結に同梱
    abyss_summary = None
    raw_abyss = body.get("abyss")
    if kind == "abyss" and isinstance(raw_abyss, dict):
        bosses_brief = []
        for bo in (raw_abyss.get("bosses") or [])[:3]:
            if isinstance(bo, dict):
                bosses_brief.append({
                    "name": str(bo.get("name") or "")[:40],
                    "time": str(bo.get("time") or "")[:16],
                    "n": len([c for c in (bo.get("chars") or []) if c]),
                })
        abyss_summary = {
            "version": str(raw_abyss.get("version") or "")[:16],
            "difficulty": str(raw_abyss.get("difficulty") or "")[:16],
            "bosses": bosses_brief,
        }
    sid = "s" + str(int(time.time() * 1000)) + os.urandom(3).hex()

    def _run() -> dict:
        cards = {}
        icons = _resolve_char_icons(uid, [])
        for c in chars[:12]:
            cid = str((c or {}).get("cid") or "")
            if not cid:
                continue
            try:
                card = _get_card_data_sync(
                    uid, cid,
                    calc_method=(c or {}).get("calc_method") or "crit",
                    fake_char=(c or {}).get("fake_char") or None,
                    fake_weapon=(c or {}).get("fake_weapon") or None,
                    beta="true" if beta else "false",
                    base_prec=(c or {}).get("base_prec") or "0",
                )
            except Exception as e:
                card = {"error": str(e)}
            # 標準(第1位/整数)表示版も併せて凍結: 閲覧側の精度トグルはサーバー直丸めをそのまま使い、
            # クライアント側の二重丸め誤差(例: 23.145→2桁で23.15→標準23.2≠サーバー23.1)を発生させない。
            try:
                card0 = _get_card_data_sync(
                    uid, cid,
                    calc_method=(c or {}).get("calc_method") or "crit",
                    fake_char=(c or {}).get("fake_char") or None,
                    fake_weapon=(c or {}).get("fake_weapon") or None,
                    beta="true" if beta else "false",
                    base_prec="0",
                )
            except Exception:
                card0 = None
            cards[cid] = (card, card0)
        snap = {"sid": sid, "exp": (0.0 if kind == "abyss" else time.time() + _SHARE_SNAPSHOT_TTL),
                "kind": kind, "beta": beta, "created": time.time(), "chars": {}}
        if abyss_summary:
            snap["abyss"] = abyss_summary
        if show_uid:
            snap["uid"] = str(uid)  # uid非表示時はファイルに書き込まない
        for cid, (card, card0) in cards.items():
            # スプラッシュが無い場合は card_data の charIcon(フェイスアイコン)でフォールバック
            icon = icons.get(cid, "") or str(card.get("charIcon") or "")
            entry = {"card": card, "icon": icon}
            if card0 is not None:
                entry["card_std"] = card0
            snap["chars"][cid] = entry
        _prune_share_snapshots()
        _share_store.put(_snapshot_key(sid), json.dumps(snap, ensure_ascii=False).encode("utf-8"))
        return snap

    snap = await run_in_threadpool(_run)
    return {"ok": True, "sid": sid, "count": len(snap["chars"])}


@api_router.get("/api/share_data/{sid}")
async def api_share_data_get(sid: str):
    """共有スナップショットを返す（uid非表示時は uid フィールドを含めない）。"""
    snap = await run_in_threadpool(_load_share_snapshot, str(sid or ""))
    if not snap:
        raise HTTPException(status_code=404, detail="共有データが見つからないか、有効期限が切れています")
    # uid は共有時に「UIDを表示する」がONのときだけファイルに書き込まれている
    # （非公開指定ならファイル自体に存在しない）ため、ここではそのまま返す
    return {"ok": True, "data": snap}


# ---- 幽境共有の下書き（前回の保存）: uid+β版単位でサーバーキャッシュ、次回モーダルで復元 ----
_ABYSS_DRAFT_TTL = 30 * 24 * 3600  # 保存のたびに更新されるローリング30日
_ABYSS_DRAFT_MAX_BYTES = 256 * 1024


def _abyss_draft_key(uid: str, beta: bool) -> str:
    return f"abyss_drafts/{uid}_{'beta' if beta else 'live'}.json"


@api_router.get("/api/abyss_share_draft")
async def api_abyss_share_draft_get(uid: str = "", beta: str = "false"):
    uid = str(uid or "").strip()
    if not uid.isdigit():
        raise HTTPException(status_code=400, detail="UIDが不正です")
    raw = await run_in_threadpool(_share_store.get, _abyss_draft_key(uid, beta == "true"))
    try:
        d = json.loads(raw.decode("utf-8"))
        if float(d.get("exp", 0) or 0) < time.time():
            return {"ok": True, "draft": None}
        return {"ok": True, "draft": d.get("draft")}
    except Exception:
        return {"ok": True, "draft": None}


@api_router.post("/api/abyss_share_draft")
async def api_abyss_share_draft_set(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="リクエストボディが不正です")
    uid = str(body.get("uid") or "").strip()
    beta = bool(body.get("beta"))
    draft = body.get("draft")
    if not uid.isdigit() or not isinstance(draft, dict):
        raise HTTPException(status_code=400, detail="パラメータが不正です")
    payload = json.dumps(draft, ensure_ascii=False)
    if len(payload.encode("utf-8")) > _ABYSS_DRAFT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="下書きデータが大きすぎます")
    out = {"uid": uid, "beta": beta, "exp": time.time() + _ABYSS_DRAFT_TTL,
           "saved_at": time.time(), "draft": draft}
    def _run():
        now = time.time()
        for key in _share_store.list_keys("abyss_drafts/"):
            try:
                raw = _share_store.get(key)
                if not raw:
                    continue
                jj = json.loads(raw.decode("utf-8"))
                if float(jj.get("exp", 0) or 0) < now:
                    _share_store.delete(key)
            except Exception:
                pass
        _share_store.put(_abyss_draft_key(uid, beta), json.dumps(out, ensure_ascii=False).encode("utf-8"))
    await run_in_threadpool(_run)
    return {"ok": True}


@api_router.post("/api/abyss_share_layout")
async def api_abyss_share_layout_set(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="リクエストボディが不正です")
    layout = body.get("layout")
    if not isinstance(layout, dict):
        raise HTTPException(status_code=400, detail="layout が不正です")
    cur = _load_abyss_layout()
    for k, v in layout.items():
        if k in cur and k != "playerTag":
            if isinstance(cur[k], dict) and isinstance(v, dict):
                cur[k] = {**cur[k], **{kk: vv for kk, vv in v.items() if isinstance(vv, (int, float, str))}}
            elif isinstance(v, (int, float, str)):
                cur[k] = v
    incoming_pt = layout.get("playerTag")
    cur_pt = cur.get("playerTag") if isinstance(cur.get("playerTag"), dict) else {}
    if isinstance(incoming_pt, dict):
        cur["playerTag"] = _player_tag_cfg({**cur_pt, **incoming_pt})
    elif isinstance(incoming_pt, bool):
        cur["playerTag"] = _player_tag_cfg({**cur_pt, "show": incoming_pt})
    else:
        cur["playerTag"] = _player_tag_cfg(cur_pt or cur.get("playerTag"))
    os.makedirs(os.path.dirname(_ABYSS_LAYOUT_PATH), exist_ok=True)
    tmp = _ABYSS_LAYOUT_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _ABYSS_LAYOUT_PATH)
    return {"ok": True, "layout": cur}
