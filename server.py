from fastapi import FastAPI, Request, Response, HTTPException, BackgroundTasks, Form
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
import uvicorn
import os
import sys
import asyncio
import io
import json
import time
from typing import Dict, Any
from collections import Counter
import random as _random

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from locale import normalize
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops
import get_info_state

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    await run_in_threadpool(_prebuild_backgrounds)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)

# =============================================================================
# Admin パネル
# =============================================================================
import hashlib as _hashlib
import hmac as _hmac
import secrets as _secrets
import time as _time

_ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "aikyu")
_ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "iqmc1104")
_ADMIN_SECRET = os.environ.get("ADMIN_SECRET") or _secrets.token_hex(32)
_ADMIN_COOKIE = "admin_session"
_ADMIN_MAX_AGE = 60 * 60 * 12

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
    if BASE_DIR not in sys.path:
        sys.path.insert(0, BASE_DIR)
    from admin_data import DataManager
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
    ok_user = _hmac.compare_digest(username, _ADMIN_USERNAME)
    ok_pass = _hmac.compare_digest(password, _ADMIN_PASSWORD)
    if not (ok_user and ok_pass):
        return templates.TemplateResponse(
            "admin_login.html",
            {"request": request, "error": "ユーザー名またはパスワードが違います"},
            status_code=401,
        )
    resp = RedirectResponse("/admin", status_code=302)
    resp.set_cookie(
        _ADMIN_COOKIE,
        _admin_token(),
        max_age=_ADMIN_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=os.environ.get("ADMIN_COOKIE_SECURE", "").lower() in ("1", "true", "yes"),
    )
    return resp

@app.get("/admin/logout")
@app.post("/admin/logout")
async def admin_logout():
    resp = RedirectResponse("/admin/login", status_code=302)
    resp.delete_cookie(_ADMIN_COOKIE)
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

print("[OK] Admin routes embedded in server.py → /admin/login , /admin/__ping")

FONT_PATH = os.path.join(BASE_DIR, "fonts", "font_fixed.ttf")
FONT_LIGHT_PATH = os.path.join(BASE_DIR, "fonts", "font_light.ttf")

# 設計解像度（Figma座標系）と出力解像度。
# 描画ヘルパーは設計座標で受け取り、内部でキャンバス座標へ拡大する。
DESIGN_W, DESIGN_H = 1741, 1159
CARD_W, CARD_H = 2400, 1620
SX = CARD_W / DESIGN_W
SY = CARD_H / DESIGN_H

try:
    with open(os.path.join(BASE_DIR, "external", "enka_py", "assets", "text_map.json"), "r", encoding="utf-8") as f:
        text_map_data = json.load(f)
except Exception as e:
    print(f"[Warning] text_map.json の読み込みに失敗: {e}")
    text_map_data = {}


def get_stat_japanese(append_prop_id: str) -> str:
    fallback_map = {
        "FIGHT_PROP_BASE_ATTACK": "基礎攻撃力",
        "FIGHT_PROP_CRITICAL": "会心率",
        "FIGHT_PROP_CRITICAL_HURT": "会心ダメージ",
        "FIGHT_PROP_CHARGE_EFFICIENCY": "チャージ効率",
        "FIGHT_PROP_ATTACK_PERCENT": "攻撃力%",
        "FIGHT_PROP_HP_PERCENT": "HP%",
        "FIGHT_PROP_DEFENSE_PERCENT": "防御力%",
        "FIGHT_PROP_ELEMENT_MASTERY": "熟知"
    }
    if append_prop_id in fallback_map:
        return fallback_map[append_prop_id]
    if append_prop_id in text_map_data:
        return text_map_data[append_prop_id]
    if "ja" in text_map_data and append_prop_id in text_map_data["ja"]:
        return text_map_data["ja"][append_prop_id]
    return append_prop_id


def formal_round(val):
    return int(val + 0.5) if val >= 0 else int(val - 0.5)


def get_char_level(avatar_info):
    if not avatar_info:
        return 1
    prop_map = avatar_info.get("propMap") or {}
    level_entry = prop_map.get("4001") or prop_map.get(4001)
    if isinstance(level_entry, dict):
        raw = level_entry.get("val")
        if raw is None:
            raw = level_entry.get("ival")
        if raw is not None and str(raw).strip() != "":
            try:
                return int(float(str(raw)))
            except (TypeError, ValueError):
                pass
    top_level = avatar_info.get("level")
    if top_level is not None and str(top_level).strip() != "":
        try:
            return int(float(str(top_level)))
        except (TypeError, ValueError):
            pass
    return 1


def hex_to_rgb(hex_str):
    if not hex_str:
        return None
    s = str(hex_str).strip().lstrip("#")
    if len(s) != 6:
        return None
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return None


def _shade_rgb(rgb, factor):
    return tuple(max(0, min(255, int(c * factor))) for c in rgb)


# =============================================================================
# OPTIMIZED: Global caches
# =============================================================================
_IMAGE_CACHE: dict = {}
_FONT_CACHE: dict = {}
_SPLASH_BLUR_CACHE: dict = {}
_RESIZED_CACHE: dict = {}
_PREBUILT_BGS: dict = {}


def get_cached_font(path: str, size: int):
    """OPTIMIZED: Cache ImageFont.truetype results by (path, size)."""
    if not path or not os.path.exists(path):
        return ImageFont.load_default()
    key = (path, size)
    if key not in _FONT_CACHE:
        try:
            _FONT_CACHE[key] = ImageFont.truetype(path, size)
        except Exception as e:
            print(f"[Warning] Failed to load font {path} size={size}: {e}")
            _FONT_CACHE[key] = ImageFont.load_default()
    return _FONT_CACHE[key]


def get_cached_image(path: str):
    """OPTIMIZED: Cache Image.open().convert('RGBA'). Returns a copy."""
    if not path:
        return None
    if path not in _IMAGE_CACHE:
        if not os.path.exists(path):
            return None
        try:
            _IMAGE_CACHE[path] = Image.open(path).convert("RGBA")
        except Exception as e:
            print(f"[Warning] Failed to cache image {path}: {e}")
            return None
    return _IMAGE_CACHE[path].copy()

def get_resized_image(path: str, size: tuple):
    """
    指定サイズにリサイズ済みの画像をキャッシュして返す。
    size は (width, height) のタプル。
    """
    if not path:
        return None
    key = (path, size)
    if key not in _RESIZED_CACHE:
        img = get_cached_image(path)
        if img is None:
            if not os.path.exists(path):
                return None
            try:
                img = Image.open(path).convert("RGBA")
                _IMAGE_CACHE[path] = img  # 元画像も一緒にキャッシュ
            except Exception as e:
                print(f"[Warning] Failed to load image {path}: {e}")
                return None
        # リサイズしてキャッシュ
        filt = get_resize_filter(size)
        _RESIZED_CACHE[key] = img.resize(size, filt)
    return _RESIZED_CACHE[key].copy()

def get_resize_filter(target_size):
    """
    OPTIMIZED: BILINEAR for small images.
    Pillow-SIMD note: BICUBIC is generally preferred over LANCZOS for better SIMD performance.
    """
    w, h = target_size if isinstance(target_size, (tuple, list)) else (target_size, target_size)
    """
    if max(w, h) < 100:
        return Image.Resampling.BILINEAR
    """
    return Image.Resampling.BICUBIC


def _build_base_background(width, height, base_rgb):
    """グラデーション + パーティクルのみ。スプラッシュなし。"""
    gw, gh = 96, 64
    tl = _shade_rgb(base_rgb, 0.48)
    br = _shade_rgb(base_rgb, 1.38)
    tr = _shade_rgb(base_rgb, 0.85)
    bl = _shade_rgb(base_rgb, 0.95)

    small = Image.new("RGB", (gw, gh))
    px = small.load()
    for y in range(gh):
        v = y / max(gh - 1, 1)
        for x in range(gw):
            u = x / max(gw - 1, 1)
            r = int((1 - u) * (1 - v) * tl[0] + u * (1 - v) * tr[0] + (1 - u) * v * bl[0] + u * v * br[0])
            g = int((1 - u) * (1 - v) * tl[1] + u * (1 - v) * tr[1] + (1 - u) * v * bl[1] + u * v * br[1])
            b = int((1 - u) * (1 - v) * tl[2] + u * (1 - v) * tr[2] + (1 - u) * v * bl[2] + u * v * br[2])
            px[x, y] = (r, g, b)

    bg = small.resize((width, height), Image.Resampling.BICUBIC).convert("RGBA")

    particles = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(particles)
    rng = _random.Random(hash(base_rgb) & 0xFFFFFFFF)

    for _ in range(60):
        x = rng.randint(0, width - 1)
        y = rng.randint(0, height - 1)
        rad = rng.choice([1, 1, 1, 2, 2, 3])
        alpha = rng.randint(30, 70)
        tint = _shade_rgb(base_rgb, 1.8)
        pdraw.ellipse(
            [x - rad, y - rad, x + rad, y + rad],
            fill=(min(255, tint[0] + 40), min(255, tint[1] + 40), min(255, tint[2] + 40), alpha),
        )

    for _ in range(20):
        x = rng.randint(0, width - 1)
        y = rng.randint(0, height - 1)
        rad = rng.randint(4, 10)
        alpha = rng.randint(15, 35)
        tint = _shade_rgb(base_rgb, 2.0)
        pdraw.ellipse(
            [x - rad, y - rad, x + rad, y + rad],
            fill=(min(255, tint[0] + 60), min(255, tint[1] + 60), min(255, tint[2] + 60), alpha),
        )

    bg = Image.alpha_composite(bg, particles)
    return bg


def _prebuild_backgrounds(width=CARD_W, height=CARD_H):
    """サーバー起動時に8元素の背景を事前生成してメモリに保持。"""
    global _PREBUILT_BGS
    elements = {
        "Pyro": (0x90, 0x3B, 0x2A),
        "Hydro": (0x34, 0x45, 0x95),
        "Cryo": (0x57, 0x7F, 0xC7),
        "Dendro": (0x46, 0x6B, 0x63),
        "Geo": (0x6A, 0x67, 0x48),
        "Electro": (0x73, 0x4A, 0x8C),
        "Anemo": (0x12, 0x95, 0x88),
        "None": (0x4A, 0x55, 0x68),
    }
    for elem, rgb in elements.items():
        _PREBUILT_BGS[elem] = _build_base_background(width, height, rgb)
    print(f"[Prebuild] {len(_PREBUILT_BGS)} element backgrounds cached in memory")


def create_card_background(width, height, base_rgb, splash_path=None, element_type="None", use_prebuilt=True):
    """
    OPTIMIZED:
    - Returns RGBA
    - Standard element backgrounds are prebuilt and cached
    - Splash GaussianBlur(radius=48) is cached separately
    """
    # 標準元素色の場合、事前生成背景をベースにする
    if use_prebuilt and element_type in _PREBUILT_BGS:
        bg = _PREBUILT_BGS[element_type].copy()
    else:
        bg = _build_base_background(width, height, base_rgb)

    # スプラッシュ合成（キャッシュあり）
    if splash_path and os.path.exists(splash_path):
        cache_key = (splash_path, width, height)
        if cache_key in _SPLASH_BLUR_CACHE:
            layer = _SPLASH_BLUR_CACHE[cache_key].copy()
        else:
            try:
                splash_img = get_cached_image(splash_path)
                if splash_img is None:
                    splash_img = Image.open(splash_path).convert("RGBA")
                scale = max(width / splash_img.width, height / splash_img.height) * 1.35
                nw = int(splash_img.width * scale)
                nh = int(splash_img.height * scale)
                splash_img = splash_img.resize((nw, nh), Image.Resampling.BICUBIC)
                layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                ox = (width - nw) // 2
                oy = (height - nh) // 2
                layer.paste(splash_img, (ox, oy), splash_img)
                layer = layer.filter(ImageFilter.GaussianBlur(radius=max(1, round(24 * SY))))
                r, g, b, a = layer.split()
                a = a.point(lambda p: int(p * 0.09))
                layer = Image.merge("RGBA", (r, g, b, a))
                _SPLASH_BLUR_CACHE[cache_key] = layer.copy()
            except Exception as e:
                print(f"[Warning] splash blur background failed: {e}")
                layer = None
        if layer is not None:
            bg = Image.alpha_composite(bg, layer)

    return bg  # RGBA


def new_stat_totals():
    return {
        "hp_flat": 0.0, "hp_percent": 0.0,
        "atk_flat": 0.0, "atk_percent": 0.0,
        "def_flat": 0.0, "def_percent": 0.0,
        "em": 0.0, "crit_rate": 0.0, "crit_dmg": 0.0,
        "energy_recharge": 0.0,
        "dmg_bonus_by_element": {},
    }


def to_ratio_if_percent(prop_id, value):
    if value is None:
        return 0.0
    key_upper = str(prop_id).upper()
    if "PERCENT" in key_upper or "CRITICAL" in key_upper or "CHARGE" in key_upper or "HURT" in key_upper:
        return value / 100.0
    return value


def apply_stat_bonus(totals, prop_id, value):
    if not prop_id or value in (None, ""):
        return
    key = str(prop_id).upper()
    if key == "FIGHT_PROP_HP":
        totals["hp_flat"] += value
    elif key == "FIGHT_PROP_HP_PERCENT":
        totals["hp_percent"] += value
    elif key in ("FIGHT_PROP_ATTACK", "FIGHT_PROP_BASE_ATTACK"):
        totals["atk_flat"] += value
    elif key == "FIGHT_PROP_ATTACK_PERCENT":
        totals["atk_percent"] += value
    elif key == "FIGHT_PROP_DEFENSE":
        totals["def_flat"] += value
    elif key == "FIGHT_PROP_DEFENSE_PERCENT":
        totals["def_percent"] += value
    elif key == "FIGHT_PROP_ELEMENT_MASTERY":
        totals["em"] += value
    elif key == "FIGHT_PROP_CRITICAL":
        totals["crit_rate"] += value
    elif key == "FIGHT_PROP_CRITICAL_HURT":
        totals["crit_dmg"] += value
    elif key == "FIGHT_PROP_CHARGE_EFFICIENCY":
        totals["energy_recharge"] += value
    elif key.endswith("_DMG") or key.endswith("_ADD_HURT") or "DMG_BONUS" in key:
        elem = None
        if "PYRO" in key or "FIRE" in key: elem = "Pyro"
        elif "HYDRO" in key or "WATER" in key: elem = "Hydro"
        elif "ANEMO" in key or "WIND" in key: elem = "Anemo"
        elif "ELECTRO" in key or "ELEC" in key: elem = "Electro"
        elif "DENDRO" in key or "GRASS" in key: elem = "Dendro"
        elif "CRYO" in key or "ICE" in key: elem = "Cryo"
        elif "GEO" in key or "ROCK" in key: elem = "Geo"
        elif "PHYSICAL" in key: elem = "物理"
        if elem:
            totals["dmg_bonus_by_element"][elem] = totals["dmg_bonus_by_element"].get(elem, 0.0) + value


def draw_figma_text_right(draw, text, x, y, font, font_size=24, fill_color=(255, 255, 255), **kwargs):
    text_str = str(text)
    # 設計座標 → キャンバス座標
    draw.text((x * SX, y * SY), text_str, fill=fill_color, font=font, anchor="ra")


def draw_figma_box(img, x, y, width, height, radius=15, fill_color=(60, 64, 72, 125),
                   outline_color=(140, 145, 155, 90), outline_width=1, shadow=True,
                   shadow_offset=(6, 6), shadow_blur=8, shadow_alpha=70):
    """
    OPTIMIZED: No GaussianBlur. 本体+影を覆う最小範囲の一時レイヤーに
    描画し、dest 指定で合成する（透明維持 & 全画面アロケーション回避）。
    """
    # 設計座標 → キャンバス座標
    x1, y1 = x * SX, y * SY
    x2, y2 = x1 + width * SX, y1 + height * SY
    radius = radius * SY
    ox, oy = (shadow_offset[0] * SX, shadow_offset[1] * SY) if shadow else (0, 0)
    outline_width = max(1, round(outline_width * SY)) if (outline_color and outline_width > 0) else 0

    # 本体+影を覆う最小領域（キャンバス外はクリップ、float座標でも整数に）
    canvas_w, canvas_h = img.size
    margin = max(1, outline_width)
    layer_x1 = int(max(0, min(x1, x1 + ox) - margin))
    layer_y1 = int(max(0, min(y1, y1 + oy) - margin))
    layer_x2 = int(min(canvas_w, max(x2, x2 + ox) + margin + 1))
    layer_y2 = int(min(canvas_h, max(y2, y2 + oy) + margin + 1))
    if layer_x2 <= layer_x1 or layer_y2 <= layer_y1:
        return

    # 一時レイヤーに描画してから合成（アルファを壊さない）
    overlay = Image.new("RGBA", (layer_x2 - layer_x1, layer_y2 - layer_y1), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    dx, dy = -layer_x1, -layer_y1

    if shadow:
        draw.rounded_rectangle(
            [x1 + ox + dx, y1 + oy + dy, x2 + ox + dx, y2 + oy + dy],
            radius=radius,
            fill=(0, 0, 0, shadow_alpha),
        )

    outline_kwargs = {}
    if outline_color and outline_width > 0:
        outline_kwargs = {"outline": outline_color, "width": outline_width}
    draw.rounded_rectangle([x1 + dx, y1 + dy, x2 + dx, y2 + dy], radius=radius, fill=fill_color, **outline_kwargs)

    img.alpha_composite(overlay, dest=(layer_x1, layer_y1))


def draw_figma_text(draw, text, x, y, font, font_size=None, fill_color=(255, 255, 255), align="left", box_width=None):
    text_str = str(text)
    actual_font = font

    if font_size is not None:
        # 元のフォントから path を取り出してサイズ違いを生成（キャンバス解像度へ拡大）
        scaled_size = max(1, round(font_size * SY))
        path = getattr(font, "path", None)
        if path and os.path.exists(path):
            actual_font = get_cached_font(path, scaled_size)
        elif isinstance(font, str) and os.path.exists(font):
            actual_font = get_cached_font(font, scaled_size)

    # 設計座標 → キャンバス座標（box_width も設計単位）
    x_s = x * SX
    bw_s = box_width * SX if box_width else None
    if align == "left":
        actual_x = x_s
    elif align == "right" and bw_s:
        text_width = draw.textlength(text_str, font=actual_font)
        actual_x = x_s + bw_s - text_width
    elif align == "center" and bw_s:
        text_width = draw.textlength(text_str, font=actual_font)
        actual_x = x_s + (bw_s - text_width) / 2
    else:
        actual_x = x_s

    draw.text((actual_x, y * SY), text_str, font=actual_font, fill=fill_color)


def draw_figma_text_with_shadow(draw, text, x, y, font, font_size=None, fill_color=(255, 255, 255),
                                shadow_color=(0, 0, 0, 160), shadow_offset=(2, 2),
                                align="left", box_width=None):
    """影のみ（アウトラインなし）。先に影を描き、その上に本文を重ねる。"""
    text_str = str(text)
    actual_font = font

    if font_size is not None:
        scaled_size = max(1, round(font_size * SY))
        path = getattr(font, "path", None)
        if path and os.path.exists(path):
            actual_font = get_cached_font(path, scaled_size)
        elif isinstance(font, str) and os.path.exists(font):
            actual_font = get_cached_font(font, scaled_size)

    # 設計座標 → キャンバス座標（box_width も設計単位）
    x_s = x * SX
    bw_s = box_width * SX if box_width else None
    if align == "left":
        target_x = x_s
    elif align == "right" and bw_s:
        text_width = draw.textlength(text_str, font=actual_font)
        target_x = x_s + bw_s - text_width
    elif align == "center" and bw_s:
        text_width = draw.textlength(text_str, font=actual_font)
        target_x = x_s + (bw_s - text_width) / 2
    else:
        target_x = x_s

    # 影（オフセットして半透明黒で描画）
    sox, soy = shadow_offset[0] * SX, shadow_offset[1] * SY
    y_s = y * SY
    draw.text(
        (target_x + sox, y_s + soy),
        text_str,
        font=actual_font,
        fill=shadow_color,
    )
    # 本文
    draw.text(
        (target_x, y_s),
        text_str,
        font=actual_font,
        fill=fill_color,
    )

def draw_figma_line(img, x1, y1, x2, y2, fill_color=(255, 255, 255, 50), width=1):
    """OPTIMIZED: 線分を覆う最小レイヤー + alpha_composite(dest) で透明を維持。"""
    # 設計座標 → キャンバス座標
    x1, y1, x2, y2 = x1 * SX, y1 * SY, x2 * SX, y2 * SY
    width = max(1, round(width * SY))
    canvas_w, canvas_h = img.size
    margin = max(1, width)
    layer_x1 = int(max(0, min(x1, x2) - margin))
    layer_y1 = int(max(0, min(y1, y2) - margin))
    layer_x2 = int(min(canvas_w, max(x1, x2) + margin + 1))
    layer_y2 = int(min(canvas_h, max(y1, y2) + margin + 1))
    if layer_x2 <= layer_x1 or layer_y2 <= layer_y1:
        return
    overlay = Image.new("RGBA", (layer_x2 - layer_x1, layer_y2 - layer_y1), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.line([(x1 - layer_x1, y1 - layer_y1), (x2 - layer_x1, y2 - layer_y1)], fill=fill_color, width=width)
    img.alpha_composite(overlay, dest=(layer_x1, layer_y1))


def draw_figma_circle(img, x, y, size, fill_color=(60, 64, 72, 125), outline_color=None, outline_width=0):
    """OPTIMIZED: 円を覆う最小レイヤー + alpha_composite(dest) で透明を維持。"""
    # 設計座標 → キャンバス座標（真円を保つため直径は縦横とも SY 基準）
    x, y = x * SX, y * SY
    size = size * SY
    outline_width = max(1, round(outline_width * SY)) if outline_width else 0
    canvas_w, canvas_h = img.size
    margin = max(1, outline_width) + 1
    layer_x1 = int(max(0, x - margin))
    layer_y1 = int(max(0, y - margin))
    layer_x2 = int(min(canvas_w, x + size + margin))
    layer_y2 = int(min(canvas_h, y + size + margin))
    if layer_x2 <= layer_x1 or layer_y2 <= layer_y1:
        return
    overlay = Image.new("RGBA", (layer_x2 - layer_x1, layer_y2 - layer_y1), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    draw.ellipse(
        [x - layer_x1, y - layer_y1, x + size - layer_x1, y + size - layer_y1],
        fill=fill_color,
        outline=outline_color,
        width=outline_width,
    )
    img.alpha_composite(overlay, dest=(layer_x1, layer_y1))


def resolve_datas_path(path, beta="false"):
    if beta != "true" or not path or os.path.exists(path):
        return path
    candidates = []
    if "static/assets" in path or "static\\assets" in path:
        candidates.append(
            path.replace("static/assets", "static/beta/assets", 1)
                .replace("static\\assets", "static\\beta\\assets", 1)
        )
    elif "static/data" in path or "static\\data" in path:
        candidates.append(
            path.replace("static/data", "static/beta/data", 1)
                .replace("static\\data", "static\\beta\\data", 1)
        )
    for beta_path in candidates:
        if os.path.exists(beta_path):
            return beta_path
    return path


def resolve_list_path(path, beta="false"):
    if beta == "true" and path and ("static/data/lists" in path or "static\\data\\lists" in path):
        beta_path = (
            path.replace("static/data/lists", "static/beta/data/lists", 1)
                .replace("static\\data\\lists", "static\\beta\\data\\lists", 1)
        )
        if os.path.exists(beta_path):
            return beta_path
    return path


def resolve_display_skill_levels(avatar_info, fake_char=False):
    if fake_char:
        return [9, 9, 9], [False, False, False]

    skill_map = (avatar_info or {}).get("skillLevelMap") or {}
    base_vals = list(skill_map.values())
    levels = []
    for i in range(3):
        try:
            levels.append(int(base_vals[i]) if i < len(base_vals) else 1)
        except (TypeError, ValueError):
            levels.append(1)

    constellation = len((avatar_info or {}).get("talentIdList") or [])
    extra_map = (avatar_info or {}).get("proudSkillExtraLevelMap") or {}
    extra_amounts = []
    for v in extra_map.values():
        try:
            iv = int(v)
            if iv > 0:
                extra_amounts.append(iv)
        except (TypeError, ValueError):
            pass

    e_boost = 0
    q_boost = 0
    if constellation >= 3:
        e_boost = extra_amounts[0] if len(extra_amounts) >= 1 else 3
    if constellation >= 5:
        q_boost = extra_amounts[1] if len(extra_amounts) >= 2 else 3

    boosted = [False, False, False]
    if e_boost:
        levels[1] = levels[1] + e_boost
        boosted[1] = True
    if q_boost:
        levels[2] = levels[2] + q_boost
        boosted[2] = True

    return levels, boosted


def paste_figma_image(base_img, img_path, box_x, box_y, box_width, box_height, radius=15, beta="false"):
    """OPTIMIZED: cache + cheap resize + RGBA paste.
    角丸マスクと元画像のアルファを乗算してから貼る（透明部分が黒くならない）。
    """
    img_path = resolve_datas_path(img_path, beta)
    if not img_path or not os.path.exists(img_path):
        return
    try:
        # 設計座標 → キャンバス座標
        bx, by = int(round(box_x * SX)), int(round(box_y * SY))
        bw, bh = max(1, round(box_width * SX)), max(1, round(box_height * SY))
        paste_img = get_resized_image(img_path, (bw, bh))
        if paste_img is None:
            return

        if paste_img.mode != "RGBA":
            paste_img = paste_img.convert("RGBA")

        # 小さい画像は角丸マスク省略（見た目の差がほぼない & 大幅高速化）
        if max(box_width, box_height) < 50:
            base_img.paste(paste_img, (bx, by), paste_img)
        else:
            # 角丸マスク
            corner_mask = Image.new("L", (bw, bh), 0)
            mask_draw = ImageDraw.Draw(corner_mask)
            mask_draw.rounded_rectangle([0, 0, bw, bh], radius=max(1, round(radius * SY)), fill=255)

            # 元画像のアルファ × 角丸マスク（透明ピクセルの RGB=黒がそのまま出ないようにする）
            r, g, b, a = paste_img.split()
            combined_alpha = ImageChops.multiply(a, corner_mask)
            paste_img = Image.merge("RGBA", (r, g, b, combined_alpha))

            base_img.paste(paste_img, (bx, by), paste_img)
    except Exception as e:
        print(f"[Error] Failed to paste image: {img_path}. Reason: {e}")


def paste_mask_image(base_img, img_path, box_x, box_y, box_width, box_height, radius=15, zoom=1.0, beta="false"):
    """OPTIMIZED: cache + cheap resize + RGBA paste.
    角丸マスクと元画像のアルファを乗算してから貼る（透明部分が黒くならない）。
    """
    img_path = resolve_datas_path(img_path, beta)
    if not img_path or not os.path.exists(img_path):
        return
    try:
        paste_img = get_cached_image(img_path)
        if paste_img is None:
            return
        orig_w, orig_h = paste_img.size
        # 設計座標 → キャンバス座標
        bw, bh = max(1, round(box_width * SX)), max(1, round(box_height * SY))
        new_height = int(bh * zoom)
        new_width = int(orig_w * (new_height / orig_h))
        paste_img = get_resized_image(img_path, (new_width, new_height))
        if paste_img is None:
            return

        if paste_img.mode != "RGBA":
            paste_img = paste_img.convert("RGBA")

        canvas = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        offset_x = (bw - new_width) // 2
        offset_y = (bh - new_height) // 2
        canvas.paste(paste_img, (offset_x, offset_y), paste_img)

        # 角丸マスク
        corner_mask = Image.new("L", (bw, bh), 0)
        mask_draw = ImageDraw.Draw(corner_mask)
        mask_draw.rounded_rectangle([0, 0, bw, bh], radius=max(1, round(radius * SY)), fill=255)

        # キャンバスのアルファ × 角丸マスク
        r, g, b, a = canvas.split()
        combined_alpha = ImageChops.multiply(a, corner_mask)
        canvas = Image.merge("RGBA", (r, g, b, combined_alpha))

        base_img.paste(canvas, (int(round(box_x * SX)), int(round(box_y * SY))), canvas)
    except Exception as e:
        print(f"[Error] Failed to paste mask image: {img_path}. Reason: {e}")


def score_calc(stat, critrate, critdmg, method):
    scores = critrate * 2 + critdmg
    if method == "em":
        scores += stat * 0.25
    else:
        scores += stat
    return scores


# =============================================================================
# 背景画像一覧 API（artifacter のランダム背景用）
# =============================================================================
_BG_IMAGE_EXTS = {".webp", ".png", ".jpg", ".jpeg"}


def _list_image_urls(rel_dir: str, url_prefix: str, limit: int = 200) -> list:
    """
    STATIC_DIR 配下の rel_dir を走査し、画像ファイルの公開URLリストを返す。
    例: rel_dir="assets/splash" → ["/static/assets/splash/xxx.webp", ...]
    """
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


@app.get("/api/bg_images")
async def api_bg_images(beta: str = "false"):
    """
    フロントの背景ローテーター用。
    splash（キャラスプラッシュ）と weapons（武器アイコン）の画像URLを返す。
    beta=true のときは static/beta 側も追加で探す。
    """
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


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("artifacter.html", {"request": request, "lang": "ja"})


@app.get("/fetch_uid", response_class=HTMLResponse)
async def fetch_uid(request: Request, uid: str, from_artifacter: bool = False, ver: str = "live"):
    print(f"[Info] Fetching characters via API for UID: {uid} (from_artifacter: {from_artifacter})")
    if ver != "beta":
        ver = "live"

    if not uid.isdigit():
        return HTMLResponse(content="ユーザーUIDが不正です。数字のみ入力してください。", status_code=400)

    beta = "true" if ver == "beta" else "false"
    uid_int = int(uid)
    json_path = os.path.join("static", "cache", f"showcase_{uid}.json")

    if from_artifacter or not os.path.exists(json_path):
        success, message = await get_info_state.update_uid_data(uid_int)
        if not success:
            print(f"[Warning] API Fetch failed or warning: {message}")

    if not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail=f"UID: {uid} のデータが見つかりませんでした。(APIエラーかつキャッシュなし)")

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            showcase_data = json.load(f)
    except (UnicodeDecodeError, json.JSONDecodeError):
        with open(json_path, 'r', encoding='cp932') as f:
            showcase_data = json.load(f)

    player_info = showcase_data.get("playerInfo", {})
    show_avatar_list = player_info.get("showAvatarInfoList", [])

    char_list = []
    for index, avatar in enumerate(show_avatar_list):
        current_avatar_id = str(avatar.get("avatarId"))
        if not current_avatar_id:
            continue

        if current_avatar_id in ["10000005", "10000007", "10000117", "10000118"]:
            energy_type = avatar.get("energyType")
            if energy_type is not None:
                current_avatar_id = f"{current_avatar_id}-{energy_type}"
            else:
                current_avatar_id = f"{current_avatar_id}-4"

        json_path_char = f"static/data/characters/{current_avatar_id}.json"
        json_path_char = resolve_datas_path(json_path_char, beta)

        if os.path.exists(json_path_char):
            try:
                with open(json_path_char, "r", encoding="utf-8") as f:
                    jsondata = json.load(f)
            except (UnicodeDecodeError, json.JSONDecodeError):
                with open(json_path_char, "r", encoding="cp932") as f:
                    jsondata = json.load(f)

            icon_suffix = str(jsondata["icon"])
            icon_path = resolve_datas_path(f"static/assets/characters/{icon_suffix}.webp", beta)
            char_entry = {
                "id": current_avatar_id,
                "icon": icon_path,
                "active": (index == 0)
            }
            char_list.append(char_entry)
        else:
            print(f"[Warning] キャラクターJSONが見つからないためスキップ: {json_path_char}")
            continue

    if not char_list:
        return HTMLResponse(content=f"UID: {uid} のゲーム内プロフィールで『キャラクター詳細を公開』がオンになっていないか、ショーケースが空です。", status_code=400)

    return templates.TemplateResponse("build_card.html", {
        "request": request,
        "uid": uid,
        "char_list": char_list,
        "ver": ver
    })


@app.post("/refresh_uid/{uid}")
async def refresh_uid(uid: str):
    if not uid.isdigit():
        raise HTTPException(status_code=400, detail="UIDが不正です。数字のみ入力してください。")

    uid_int = int(uid)
    print(f"[Info] Refreshing showcase data via Enka API for UID: {uid}")
    success, message = await get_info_state.update_uid_data(uid_int)

    if not success:
        print(f"[Warning] Refresh failed: {message}")
        raise HTTPException(status_code=502, detail=f"Enka APIの取得に失敗しました: {message}")

    return {"success": True, "message": message}


@app.get("/api/card_data/{uid}/{avatar_id}")
async def get_card_data(uid: str, avatar_id: str, calc_method: str = "crit", fake_char: str = None, fake_weapon: str = None, beta: str = "false"):
    return await run_in_threadpool(
        _get_card_data_sync, uid, avatar_id, calc_method, fake_char, fake_weapon, beta
    )


def _get_card_data_sync(uid: str, avatar_id: str, calc_method: str = "crit", fake_char: str = None, fake_weapon: str = None, beta: str = "false"):
    if beta != "true":
        beta = "false"

    json_path = os.path.join("static", "cache", f"showcase_{uid}.json")
    json_path = resolve_datas_path(json_path, beta)
    if not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail=f"UID: {uid} のキャッシュデータが見つかりませんでした。")

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            showcase_data = json.load(f)
    except (UnicodeDecodeError, json.JSONDecodeError):
        with open(json_path, "r", encoding="cp932") as f:
            showcase_data = json.load(f)

    avatar_list = showcase_data.get("avatarInfoList")
    if not avatar_list and "playerInfo" in showcase_data:
        player_info = showcase_data["playerInfo"]
        avatar_list = player_info.get("showAvatarInfoList") or player_info.get("show_avatar_info_list")
    avatar_list = avatar_list or []

    target_avatar_info = None
    for avatar in avatar_list:
        raw_id = str(avatar.get("avatarId"))
        loop_avatar_id = raw_id
        if raw_id in ["10000005", "10000007", "10000117", "10000118"]:
            energy_type = avatar.get("energyType")
            loop_avatar_id = f"{raw_id}-{energy_type}" if energy_type is not None else f"{raw_id}-4"
        if str(loop_avatar_id) == str(avatar_id):
            target_avatar_info = avatar
            break

    if not target_avatar_info:
        raise HTTPException(status_code=404, detail=f"Avatar ID {avatar_id} not found in showcase.")

    if fake_char:
        json_path2 = os.path.join("static", "data", "characters", f"{fake_char}.json")
        if not os.path.exists(json_path2):
            if beta == "true":
                json_path2 = os.path.join("static", "beta", "data", "characters", f"{fake_char}.json")
    else:
        json_path2 = os.path.join("static", "data", "characters", f"{avatar_id}.json")
    json_path2 = resolve_datas_path(json_path2, beta)

    if os.path.exists(json_path2):
        try:
            with open(json_path2, "r", encoding="utf-8") as f:
                chardatas = json.load(f)
        except (UnicodeDecodeError, json.JSONDecodeError):
            with open(json_path2, "r", encoding="cp932") as f:
                chardatas = json.load(f)
    else:
        base_avatar_id = str(avatar_id).split("-")[0]
        backup_path = os.path.join("static", "data", "characters", f"{base_avatar_id}.json")
        backup_path = resolve_datas_path(backup_path, beta)
        if os.path.exists(backup_path):
            try:
                with open(backup_path, "r", encoding="utf-8") as f:
                    chardatas = json.load(f)
            except (UnicodeDecodeError, json.JSONDecodeError):
                with open(backup_path, "r", encoding="cp932") as f:
                    chardatas = json.load(f)
        else:
            raise HTTPException(status_code=404, detail=f"Character JSON file not found: {json_path2}")

    element_type = chardatas.get("element", "None")
    element_ja_map = {
        "Pyro": "炎", "Hydro": "水", "Anemo": "風", "Electro": "雷",
        "Dendro": "草", "Cryo": "氷", "Geo": "岩", "None": "無"
    }
    element_ja = element_ja_map.get(element_type, "無")

    if fake_char:
        constellation = 0
    else:
        constellation = len(target_avatar_info.get("talentIdList", []))

    if fake_char:
        char_level = 90
    else:
        char_level = get_char_level(target_avatar_info)

    weapon_data = next((item for item in target_avatar_info.get("equipList", []) if "weapon" in item), None)
    weapon_name = "未知の武器"
    weapon_icon = ""
    weapon_level = None
    weapon_affix = None
    weapon_stats_list = []

    if fake_weapon:
        weapon_id = fake_weapon
        weapon_json_path = resolve_datas_path(f"static/data/weapons/{weapon_id}.json", beta)
        if not os.path.exists(weapon_json_path):
            raise HTTPException(status_code=404, detail=f"Weapon JSON file not found: {weapon_json_path}")
        with open(weapon_json_path, "r", encoding="utf-8") as f:
            weapon_jsondata = json.load(f)
        weapon_level = 90
        weapon_affix = 1
        weapon_icon = resolve_datas_path(f"static/assets/weapons/{weapon_jsondata['icon']}.webp", beta)
        weapon_name = weapon_jsondata["name"]
        keys_list = list(weapon_jsondata["stats_modifier"].keys())
        try:
            second_key = keys_list[1]
        except Exception:
            second_key = None
        stat_calc = weapon_jsondata["stats_modifier"][second_key]
        if stat_calc < 1:
            stat_calc = round(stat_calc * 100, 1)
        else:
            stat_calc = round(stat_calc)
        weapon_stats_list = [
            {'appendPropId': 'FIGHT_PROP_BASE_ATTACK', 'statValue': weapon_jsondata["stats_modifier"]["atk"]},
            {'appendPropId': second_key.upper(), 'statValue': stat_calc}
        ]
    elif weapon_data:
        weapon_id = weapon_data["itemId"]
        weapon_json_path = resolve_datas_path(f"static/data/weapons/{weapon_id}.json", beta)
        if os.path.exists(weapon_json_path):
            try:
                with open(weapon_json_path, "r", encoding="utf-8") as f:
                    weapon_jsondata = json.load(f)
                weapon_name = weapon_jsondata.get("name", "未知の武器")
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        weapon_icon = resolve_datas_path(f"static/assets/weapons/{weapon_data['flat']['icon']}.webp", beta)
        weapon_level = weapon_data["weapon"]["level"]
        weapon_affix = list(weapon_data["weapon"].get("affixMap", {}).values())[0] + 1 if weapon_data["weapon"].get("affixMap") else 1
        weapon_stats_list = weapon_data["flat"].get("weaponStats", [])

    raw_artifacts = [item for item in target_avatar_info.get("equipList", []) if "reliquary" in item]

    if fake_char or fake_weapon:
        if fake_char:
            char_stats_mod = chardatas.get("stats_modifier", {}) or {}
            base_hp = chardatas.get("hp", char_stats_mod.get("hp", 1))
            base_atk = chardatas.get("atk", char_stats_mod.get("atk", 1))
            base_def = chardatas.get("def", char_stats_mod.get("def", 1))
            base_crit_rate = chardatas.get("crit_rate", 0.05)
            base_crit_dmg = chardatas.get("crit_dmg", 0.5)
            base_em = chardatas.get("elemental_mastery", 0.0)
        else:
            base_hp = target_avatar_info.get('fightPropMap', {}).get('1', 1)
            base_atk = target_avatar_info.get('fightPropMap', {}).get('4', 1)
            base_def = target_avatar_info.get('fightPropMap', {}).get('7', 1)
            base_crit_rate = 0.05
            base_crit_dmg = 0.5
            base_em = 0.0
        base_er = 1.0

        stat_totals = new_stat_totals()
        char_stats_mod_for_bonus = chardatas.get("stats_modifier", {}) or {}

        extra_bonus = char_stats_mod_for_bonus.get("extra")
        if isinstance(extra_bonus, dict):
            for asc_key, asc_val in extra_bonus.items():
                apply_stat_bonus(stat_totals, asc_key, asc_val)
        elif isinstance(extra_bonus, list):
            for asc_entry in extra_bonus:
                for asc_key, asc_val in asc_entry.items():
                    apply_stat_bonus(stat_totals, asc_key, asc_val)

        for asc_entry in char_stats_mod_for_bonus.get("ascension", []):
            for asc_key, asc_val in asc_entry.items():
                apply_stat_bonus(stat_totals, asc_key, asc_val)

        weapon_base_atk = 0.0
        for w_entry in weapon_stats_list:
            w_prop_id = w_entry.get("appendPropId", "")
            w_val = w_entry.get("statValue", 0.0)
            if w_prop_id.upper() in ("FIGHT_PROP_BASE_ATTACK", "FIGHT_PROP_ATTACK"):
                weapon_base_atk = w_val
            else:
                apply_stat_bonus(stat_totals, w_prop_id, to_ratio_if_percent(w_prop_id, w_val))

        for art_raw in raw_artifacts:
            art_flat = art_raw.get("flat", {})
            art_main = art_flat.get("reliquaryMainstat", {})
            art_main_id = art_main.get("mainPropId", "")
            apply_stat_bonus(stat_totals, art_main_id, to_ratio_if_percent(art_main_id, art_main.get("statValue", 0.0)))
            for art_sub in art_flat.get("reliquarySubstats", []):
                art_sub_id = art_sub.get("appendPropId", "")
                apply_stat_bonus(stat_totals, art_sub_id, to_ratio_if_percent(art_sub_id, art_sub.get("statValue", 0.0)))

        total_hp = base_hp * (1 + stat_totals["hp_percent"]) + stat_totals["hp_flat"]
        total_atk = (base_atk + weapon_base_atk) * (1 + stat_totals["atk_percent"]) + stat_totals["atk_flat"]
        total_def = base_def * (1 + stat_totals["def_percent"]) + stat_totals["def_flat"]
        total_em = base_em + stat_totals["em"]
        total_crit_rate = base_crit_rate + stat_totals["crit_rate"]
        total_crit_dmg = base_crit_dmg + stat_totals["crit_dmg"]
        total_er = base_er + stat_totals["energy_recharge"]

        dmg_buff_val = "0%"
        if element_type in ("Pyro", "Hydro", "Anemo", "Electro", "Dendro", "Geo", "Cryo"):
            buff_val = stat_totals["dmg_bonus_by_element"].get(element_type, 0.0)
            if buff_val > 0:
                dmg_buff_val = str(formal_round(buff_val * 1000) / 10) + "%"

        main_stats = [
            {"label": "HP", "val": formal_round(total_hp), "base": formal_round(base_hp), "icon": "static/assets/props/hp.png"},
            {"label": "攻撃力", "val": formal_round(total_atk), "base": formal_round(base_atk + weapon_base_atk), "icon": "static/assets/props/atk.png"},
            {"label": "防禦力", "val": formal_round(total_def), "base": formal_round(base_def), "icon": "static/assets/props/def.png"},
            {"label": "元素熟知", "val": formal_round(total_em), "icon": "static/assets/props/em.png"},
            {"label": "会心率", "val": str(formal_round(total_crit_rate * 1000) / 10) + "%", "icon": "static/assets/props/rate.webp"},
            {"label": "会心ダメージ", "val": str(formal_round(total_crit_dmg * 1000) / 10) + "%", "icon": "static/assets/props/dmg.webp"},
            {"label": "元素チャージ効率", "val": str(formal_round(total_er * 1000) / 10) + "%", "icon": "static/assets/props/er.png"},
            {"label": f"{element_ja}ダメバフ", "val": dmg_buff_val, "icon": f"static/assets/props/{element_type.lower()}.png"},
        ]
    else:
        all_buff_ids = ['30', '40', '41', '42', '43', '44', '45', '46']
        fight_prop = target_avatar_info.get('fightPropMap', {})

        max_dmg_val = 0.0
        for b_id in all_buff_ids:
            val = fight_prop.get(b_id, 0.0)
            if val > max_dmg_val:
                max_dmg_val = val

        if max_dmg_val == 0.0:
            relic_buff_ids = ['50', '51', '52', '53', '54', '55', '56', '57']
            for r_id in relic_buff_ids:
                val = fight_prop.get(r_id, 0.0)
                if val > max_dmg_val:
                    max_dmg_val = val

        dmg_buff_val = str(formal_round(max_dmg_val * 1000) / 10) + "%"

        main_stats = [
            {"label": "HP", "val": formal_round(fight_prop.get('2000', 1)), "base": formal_round(fight_prop.get('1', 1)), "icon": "static/assets/props/hp.png"},
            {"label": "攻撃力", "val": formal_round(fight_prop.get('2001', 1)), "base": formal_round(fight_prop.get('4', 1)), "icon": "static/assets/props/atk.png"},
            {"label": "防禦力", "val": formal_round(fight_prop.get('2002', 1)), "base": formal_round(fight_prop.get('7', 1)), "icon": "static/assets/props/def.png"},
            {"label": "元素熟知", "val": formal_round(fight_prop.get('28', 1)), "icon": "static/assets/props/em.png"},
            {"label": "会心率", "val": str(formal_round(fight_prop.get('20', 1) * 1000) / 10) + "%", "icon": "static/assets/props/rate.webp"},
            {"label": "会心ダメージ", "val": str(formal_round(fight_prop.get('22', 1) * 1000) / 10) + "%", "icon": "static/assets/props/dmg.webp"},
            {"label": "元素チャージ効率", "val": str(formal_round(fight_prop.get('23', 1) * 1000) / 10) + "%", "icon": "static/assets/props/er.png"},
            {"label": f"{element_ja}ダメバフ", "val": dmg_buff_val, "icon": f"static/assets/props/{element_type.lower()}.png"},
        ]

    for s in main_stats:
        s["icon"] = resolve_datas_path(s["icon"], beta)

    slot_to_index = {"4": 0, "2": 1, "5": 2, "1": 3, "3": 4}
    slot_names = ["花", "羽", "時計", "杯", "冠"]
    method_to_prop_id = {
        "atk": "FIGHT_PROP_ATTACK_PERCENT",
        "hp": "FIGHT_PROP_HP_PERCENT",
        "def": "FIGHT_PROP_DEFENSE_PERCENT",
        "em": "FIGHT_PROP_ELEMENT_MASTERY",
        "charge": "FIGHT_PROP_CHARGE_EFFICIENCY"
    }
    target_prop_id = method_to_prop_id.get(calc_method, "")

    artifacts_out = [None, None, None, None, None]
    score_sum = 0

    for art in target_avatar_info.get("equipList", []):
        flat = art.get("flat", {})
        reliquary = art.get("reliquary", {})
        if flat.get("itemType") != "ITEM_RELIQUARY":
            continue
        icon_name = flat.get("icon", "")
        if "_" not in icon_name:
            continue
        last_num = icon_name.split("_")[-1]
        if last_num not in slot_to_index:
            continue
        target_idx = slot_to_index[last_num]

        name_hash = str(flat.get("nameTextMapHash", ""))
        artifact_name = text_map_data.get(name_hash, "未知の聖遺物")

        main_stat_raw = flat.get("reliquaryMainstat", {})
        main_prop_id = main_stat_raw.get("mainPropId", "")
        main_name = get_stat_japanese(main_prop_id)
        main_val = main_stat_raw.get("statValue", 0)
        if "PERCENT" in main_prop_id or "CRITICAL" in main_prop_id or "CHARGE" in main_prop_id or "HURT" in main_prop_id:
            main_value_str = f"{main_val}%"
        else:
            main_value_str = f"{int(main_val):,}"

        crit_rate, crit_dmg, target_stat_val = 0.0, 0.0, 0.0
        substats_out = []
        for sub_data in flat.get("reliquarySubstats", []):
            sub_prop_id = sub_data.get("appendPropId", "")
            sub_name = get_stat_japanese(sub_prop_id)
            sub_val = sub_data.get("statValue", 0)

            if sub_prop_id == "FIGHT_PROP_CRITICAL":
                crit_rate = sub_val
            elif sub_prop_id == "FIGHT_PROP_CRITICAL_HURT":
                crit_dmg = sub_val
            elif sub_prop_id == target_prop_id:
                target_stat_val = sub_val

            icon_file = "atk_per.png"
            if "CRITICAL" in sub_prop_id and "HURT" not in sub_prop_id: icon_file = "rate.webp"
            elif "HURT" in sub_prop_id: icon_file = "dmg.webp"
            elif "CHARGE" in sub_prop_id: icon_file = "er.png"
            elif "ELEMENT_MASTERY" in sub_prop_id: icon_file = "em.png"
            elif "HP" in sub_prop_id: icon_file = "hp_per.png" if "PERCENT" in sub_prop_id else "hp.png"
            elif "ATTACK" in sub_prop_id: icon_file = "atk_per.png" if "PERCENT" in sub_prop_id else "atk.png"
            elif "DEFENSE" in sub_prop_id: icon_file = "def_per.png" if "PERCENT" in sub_prop_id else "def.png"

            if "PERCENT" in sub_prop_id or "CRITICAL" in sub_prop_id or "CHARGE" in sub_prop_id or "HURT" in sub_prop_id:
                sub_value_str = f"{sub_val}%"
            else:
                sub_value_str = f"{int(sub_val)}"

            substats_out.append({
                "name": sub_name, "value": sub_value_str,
                "icon": resolve_datas_path(f"static/assets/props/{icon_file}", beta)
            })

        art_score = round(score_calc(stat=target_stat_val, critrate=crit_rate, critdmg=crit_dmg, method=calc_method), 1)

        if target_idx in [0, 1]:
            art_tier = "SS" if art_score >= 50.0 else "S" if art_score >= 45.0 else "A" if art_score >= 40.0 else "B"
        elif target_idx == 2:
            art_tier = "SS" if art_score >= 45.0 else "S" if art_score >= 40.0 else "A" if art_score >= 35.0 else "B"
        elif target_idx == 3:
            art_tier = "SS" if art_score >= 45.0 else "S" if art_score >= 40.0 else "A" if art_score >= 37.0 else "B"
        else:
            art_tier = "SS" if art_score >= 40.0 else "S" if art_score >= 35.0 else "A" if art_score >= 30.0 else "B"

        artifacts_out[target_idx] = {
            "slot": slot_names[target_idx],
            "set": str(flat.get("setId", "")),
            "name": artifact_name,
            "upgrade": reliquary.get("level", 1) - 1,
            "main": {"name": main_name, "value": main_value_str},
            "substats": substats_out,
            "score": art_score,
            "tier": art_tier,
            "icon": resolve_datas_path(f"static/assets/artifacts/UI_RelicIcon_{flat.get('setId','')}_{icon_name.split('_')[-1]}.webp", beta)
        }
        score_sum += art_score

    set_ids = [a["set"] for a in artifacts_out if a and a["set"] and a["set"] != "0"]
    set_counts = Counter(set_ids)
    active_sets = [(sid, cnt) for sid, cnt in set_counts.items() if cnt >= 2]

    def get_set_name(set_id_str):
        possible_paths = [
            "static/data/lists/artifacts.json",
            os.path.join(BASE_DIR, "static", "data", "lists", "artifacts.json"),
        ]
        for raw_path in possible_paths:
            path = resolve_list_path(raw_path, beta)
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f_art:
                        art_json = json.load(f_art)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    with open(path, "r", encoding="cp932") as f_art:
                        art_json = json.load(f_art)
                if set_id_str in art_json:
                    return art_json[set_id_str].get("janame", f"セット {set_id_str}")
        return f"セット {set_id_str}"

    set_bonuses = [{"name": get_set_name(sid), "count": cnt} for sid, cnt in active_sets]

    if score_sum < 180:
        tier_sum_score = "B"
    elif score_sum < 200:
        tier_sum_score = "A"
    elif score_sum < 220:
        tier_sum_score = "S"
    else:
        tier_sum_score = "SS"

    splash_path = ""
    try:
        icon_name = str(chardatas.get("icon", ""))
        if icon_name:
            splash_raw = f"static/assets/splash/{icon_name.replace('AvatarIcon', 'Gacha_AvatarImg')}.webp"
            splash_path = resolve_datas_path(splash_raw, beta)
    except Exception:
        splash_path = ""

    if fake_char:
        friendship_lv = 10
    else:
        friendship_lv = target_avatar_info.get("fetterInfo", {}).get("expLevel", 1)

    skill_icons = []
    skill_levels = []
    skill_boosted = [False, False, False]
    try:
        skills_meta = chardatas.get("skills") or []
        skill_levels, skill_boosted = resolve_display_skill_levels(target_avatar_info, fake_char=bool(fake_char))
        for i in range(min(3, len(skills_meta))):
            icon = skills_meta[i].get("icon", "")
            skill_icons.append(resolve_datas_path(f"static/assets/skills/{icon}.webp", beta) if icon else "")
        while len(skill_levels) < 3:
            skill_levels.append(1)
            skill_boosted.append(False)
    except Exception:
        skill_icons, skill_levels, skill_boosted = [], [1, 1, 1], [False, False, False]

    constellation_icons = []
    try:
        consts_meta = chardatas.get("constellations") or []
        for i in range(min(6, len(consts_meta))):
            icon = consts_meta[i].get("icon", "")
            constellation_icons.append(resolve_datas_path(f"static/assets/skills/{icon}.webp", beta) if icon else "")
    except Exception:
        constellation_icons = []

    weapon_stats_out = []
    for w_entry in weapon_stats_list:
        w_prop = w_entry.get("appendPropId", "")
        w_val = w_entry.get("statValue", 0)
        w_name = get_stat_japanese(w_prop)
        if "PERCENT" in str(w_prop).upper() or "CRITICAL" in str(w_prop).upper() or "CHARGE" in str(w_prop).upper() or "HURT" in str(w_prop).upper():
            try:
                fv = float(w_val)
                w_val_str = f"{fv}%" if fv < 1000 else str(int(fv))
            except Exception:
                w_val_str = str(w_val)
        else:
            try:
                w_val_str = str(int(float(w_val)))
            except Exception:
                w_val_str = str(w_val)
        weapon_stats_out.append({"name": w_name, "value": w_val_str})

    display_map = {
        "crit": "会心のみ",
        "atk": "攻撃力%",
        "hp": "HP%",
        "def": "DEF%",
        "em": "元素熟知",
        "charge": "チャージ効率",
    }
    display_score_way = display_map.get(calc_method, calc_method)

    return {
        "displayName": (chardatas.get("name", avatar_id) + "(swap)") if fake_char else chardatas.get("name", avatar_id),
        "element": element_type,
        "level": char_level,
        "friendship": friendship_lv,
        "constellation": constellation,
        "splash": splash_path,
        "skills": [{
            "icon": skill_icons[i] if i < len(skill_icons) else "",
            "level": skill_levels[i] if i < len(skill_levels) else 1,
            "boosted": bool(skill_boosted[i]) if i < len(skill_boosted) else False,
        } for i in range(3)],
        "constellationIcons": constellation_icons,
        "weaponName": weapon_name,
        "weaponIcon": weapon_icon,
        "weaponLevel": weapon_level,
        "weaponAffix": weapon_affix,
        "weaponStats": weapon_stats_out,
        "mainStats": main_stats,
        "artifacts": artifacts_out,
        "setBonuses": set_bonuses,
        "scoreSum": round(score_sum, 1),
        "tierSum": tier_sum_score,
        "calcMethod": calc_method,
        "calcMethodLabel": display_score_way,
    }


def _generate_card_image_sync(uid: str, avatar_id: str, calc_method: str, fake_char: str = None, fake_weapon: str = None, beta: str = "false", bg_color: str = None, img_format: str = "webp"):
    _total_start = time.perf_counter()
    if beta != "true":
        beta = "false"
    print(f"[Cache Miss] 初回生成のため、PILで気合を入れて画像を作ります...: UID:{uid} - CharID:{avatar_id} - Method:{calc_method}")

    t_start = time.perf_counter()
    target_avatar_info = None
    json_path = os.path.join("static", "cache", f"showcase_{uid}.json")
    json_path = resolve_datas_path(json_path, beta)

    if os.path.exists(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                showcase_data = json.load(f)
        except (UnicodeDecodeError, json.JSONDecodeError):
            with open(json_path, "r", encoding="cp932") as f:
                showcase_data = json.load(f)

        avatar_list = showcase_data.get("avatarInfoList")
        if not avatar_list and "playerInfo" in showcase_data:
            player_info = showcase_data["playerInfo"]
            avatar_list = player_info.get("showAvatarInfoList") or player_info.get("show_avatar_info_list")

        if avatar_list:
            for avatar in avatar_list:
                raw_id = str(avatar.get("avatarId"))
                loop_avatar_id = raw_id
                if raw_id in ["10000005", "10000007", "10000117", "10000118"]:
                    energy_type = avatar.get("energyType")
                    if energy_type is not None:
                        loop_avatar_id = f"{raw_id}-{energy_type}"
                    else:
                        loop_avatar_id = f"{raw_id}-4"
                if str(loop_avatar_id) == str(avatar_id):
                    target_avatar_info = avatar
                    break

    if not target_avatar_info:
        raise HTTPException(status_code=404, detail=f"Avatar ID {avatar_id} not found in showcase.")
    t_end = time.perf_counter()
    print(f"[Perf] JSON読み込み・パース: {(t_end - t_start)*1000:.1f}ms")

    t_start = time.perf_counter()
    if fake_char:
        json_path2 = os.path.join("static", "data", "characters", f"{fake_char}.json")
        if not os.path.exists(json_path2):
            if beta == "true":
                json_path2 = os.path.join("static", "beta", "data", "characters", f"{fake_char}.json")
    else:
        json_path2 = os.path.join("static", "data", "characters", f"{avatar_id}.json")
    json_path2 = resolve_datas_path(json_path2, beta)

    if os.path.exists(json_path2):
        try:
            with open(json_path2, "r", encoding="utf-8") as f:
                chardatas = json.load(f)
        except (UnicodeDecodeError, json.JSONDecodeError):
            with open(json_path2, "r", encoding="cp932") as f:
                chardatas = json.load(f)
    else:
        base_avatar_id = str(avatar_id).split("-")[0]
        backup_path = os.path.join("static", "data", "characters", f"{base_avatar_id}.json")
        backup_path = resolve_datas_path(backup_path, beta)
        if os.path.exists(backup_path):
            try:
                with open(backup_path, "r", encoding="utf-8") as f:
                    chardatas = json.load(f)
            except (UnicodeDecodeError, json.JSONDecodeError):
                with open(backup_path, "r", encoding="cp932") as f:
                    chardatas = json.load(f)
        else:
            raise HTTPException(status_code=404, detail=f"Character JSON file not found: {json_path2}")
    t_end = time.perf_counter()
    print(f"[Perf] キャラJSON読み込み: {(t_end - t_start)*1000:.1f}ms")

    card_width = CARD_W
    card_height = CARD_H

    element_type = chardatas.get("element", "None")
    element_colors = {
        "Pyro": (0x90, 0x3B, 0x2A),
        "Hydro": (0x34, 0x45, 0x95),
        "Cryo": (0x57, 0x7F, 0xC7),
        "Dendro": (0x46, 0x6B, 0x63),
        "Geo": (0x6A, 0x67, 0x48),
        "Electro": (0x73, 0x4A, 0x8C),
        "Anemo": (0x12, 0x95, 0x88),
        "None": (0x4A, 0x55, 0x68),
    }
    element_ja_map = {
        "Pyro": "炎元素", "Hydro": "水元素", "Anemo": "風元素",
        "Electro": "雷元素", "Dendro": "草元素", "Cryo": "氷元素", "Geo": "岩元素",
    }
    element_ja = element_ja_map.get(element_type, "なし")

    element_base_rgb = element_colors.get(element_type, (0x4A, 0x55, 0x68))
    custom_rgb = hex_to_rgb(bg_color) if bg_color else None
    bg_base_rgb = custom_rgb if custom_rgb else element_base_rgb
    base_color = (*bg_base_rgb, 255)

    splash = f"static/assets/splash/{chardatas['icon'].replace('AvatarIcon', 'Gacha_AvatarImg')}.webp"
    splash = resolve_datas_path(splash, beta)

    char_name = chardatas["name"]
    if fake_char:
        char_name = f"{char_name}(swap)"

    if fake_char:
        char_level = 90
    else:
        char_level = get_char_level(target_avatar_info)

    if fake_char:
        friendship_lv = 10
    else:
        friendship_lv = target_avatar_info.get("fetterInfo", {}).get("expLevel", 1)

    skill_level, skill_boosted = resolve_display_skill_levels(target_avatar_info, fake_char=bool(fake_char))
    skill_icon = [chardatas["skills"][0]["icon"], chardatas["skills"][1]["icon"], chardatas["skills"][2]["icon"]]

    y_C_base = 139
    circle_size = 68

    if fake_char:
        constellation_releas_num = 0
    else:
        constellation_releas_num = len(target_avatar_info.get("talentIdList", []))

    Constellation_icon = [chardatas["constellations"][i]["icon"] for i in range(6)]

    t_start = time.perf_counter()
    weapon_data = next((item for item in target_avatar_info.get("equipList", []) if "weapon" in item), None)

    if fake_weapon:
        weapon_id = fake_weapon
        weapon_json_path = resolve_datas_path(f"static/data/weapons/{weapon_id}.json", beta)
        with open(weapon_json_path, "r", encoding="utf-8") as f:
            weapon_jsondata = json.load(f)
        weapon_level = 90
        weapon_affix = 1
        weapon_icon = weapon_jsondata["icon"]
        weapon_name = weapon_jsondata["name"]
        keys_list = list(weapon_jsondata["stats_modifier"].keys())
        try:
            second_key = keys_list[1]
        except Exception:
            second_key = None
        stat_calc = weapon_jsondata["stats_modifier"][second_key]
        if stat_calc < 1:
            stat_calc = round(stat_calc * 100, 1)
        else:
            stat_calc = round(stat_calc)
        weapon_stats_list = [
            {'appendPropId': 'FIGHT_PROP_BASE_ATTACK', 'statValue': weapon_jsondata["stats_modifier"]["atk"]},
            {'appendPropId': second_key.upper(), 'statValue': stat_calc}
        ]
    else:
        weapon_id = weapon_data["itemId"]
        weapon_json_path = resolve_datas_path(f"static/data/weapons/{weapon_id}.json", beta)
        with open(weapon_json_path, "r", encoding="utf-8") as f:
            weapon_jsondata = json.load(f)
        weapon_icon = weapon_data["flat"]["icon"]
        weapon_level = weapon_data["weapon"]["level"]
        weapon_affix = list(weapon_data["weapon"].get("affixMap", {}).values())[0] + 1 if weapon_data["weapon"].get("affixMap") else 1
        weapon_name = weapon_jsondata.get("name", "未知の武器")
        weapon_stats_list = weapon_data["flat"].get("weaponStats", [])

    weapon_stat1 = None
    if len(weapon_stats_list) >= 1:
        prop_id1 = weapon_stats_list[0]["appendPropId"]
        stat_name1 = get_stat_japanese(prop_id1)
        stat_val1 = weapon_stats_list[0]["statValue"]
        if "PERCENT" in prop_id1 or "CRITICAL" in prop_id1 or "CHARGE" in prop_id1:
            stat_val1_str = f"{stat_val1}%"
        else:
            stat_val1_str = f"{int(stat_val1)}"
        weapon_stat1 = (stat_name1, stat_val1_str)

    weapon_stat2 = None
    if len(weapon_stats_list) == 2:
        prop_id2 = weapon_stats_list[1]["appendPropId"]
        stat_name2 = get_stat_japanese(prop_id2)
        stat_val2 = weapon_stats_list[1]["statValue"]
        if "PERCENT" in prop_id2 or "CRITICAL" in prop_id2 or "CHARGE" in prop_id2 or "HURT" in prop_id2:
            stat_val2_str = f"{stat_val2}%"
        else:
            stat_val2_str = f"{int(stat_val2)}"
        weapon_stat2 = (stat_name2, stat_val2_str)
    t_end = time.perf_counter()
    print(f"[Perf] 武器データ処理: {(t_end - t_start)*1000:.1f}ms")

    t_start = time.perf_counter()
    raw_artifacts = [item for item in target_avatar_info.get("equipList", []) if "reliquary" in item]

    if fake_char or fake_weapon:
        if fake_char:
            char_stats_mod = chardatas.get("stats_modifier", {}) or {}
            base_hp = chardatas.get("hp", char_stats_mod.get("hp", 1))
            base_atk = chardatas.get("atk", char_stats_mod.get("atk", 1))
            base_def = chardatas.get("def", char_stats_mod.get("def", 1))
            base_crit_rate = chardatas.get("crit_rate", 0.05)
            base_crit_dmg = chardatas.get("crit_dmg", 0.5)
            base_em = chardatas.get("elemental_mastery", 0.0)
        else:
            base_hp = target_avatar_info.get('fightPropMap', {}).get('1', 1)
            base_atk = target_avatar_info.get('fightPropMap', {}).get('4', 1)
            base_def = target_avatar_info.get('fightPropMap', {}).get('7', 1)
            base_crit_rate = 0.05
            base_crit_dmg = 0.5
            base_em = 0.0
        base_er = 1.0

        stat_totals = new_stat_totals()
        char_stats_mod_for_bonus = chardatas.get("stats_modifier", {}) or {}

        extra_bonus = char_stats_mod_for_bonus.get("extra")
        if isinstance(extra_bonus, dict):
            for asc_key, asc_val in extra_bonus.items():
                apply_stat_bonus(stat_totals, asc_key, asc_val)
        elif isinstance(extra_bonus, list):
            for asc_entry in extra_bonus:
                for asc_key, asc_val in asc_entry.items():
                    apply_stat_bonus(stat_totals, asc_key, asc_val)

        for asc_entry in char_stats_mod_for_bonus.get("ascension", []):
            for asc_key, asc_val in asc_entry.items():
                apply_stat_bonus(stat_totals, asc_key, asc_val)

        weapon_base_atk = 0.0
        for w_entry in weapon_stats_list:
            w_prop_id = w_entry.get("appendPropId", "")
            w_val = w_entry.get("statValue", 0.0)
            if w_prop_id.upper() in ("FIGHT_PROP_BASE_ATTACK", "FIGHT_PROP_ATTACK"):
                weapon_base_atk = w_val
            else:
                apply_stat_bonus(stat_totals, w_prop_id, to_ratio_if_percent(w_prop_id, w_val))

        for art_raw in raw_artifacts:
            art_flat = art_raw.get("flat", {})
            art_main = art_flat.get("reliquaryMainstat", {})
            art_main_id = art_main.get("mainPropId", "")
            apply_stat_bonus(stat_totals, art_main_id, to_ratio_if_percent(art_main_id, art_main.get("statValue", 0.0)))
            for art_sub in art_flat.get("reliquarySubstats", []):
                art_sub_id = art_sub.get("appendPropId", "")
                apply_stat_bonus(stat_totals, art_sub_id, to_ratio_if_percent(art_sub_id, art_sub.get("statValue", 0.0)))

        total_hp = base_hp * (1 + stat_totals["hp_percent"]) + stat_totals["hp_flat"]
        total_atk = (base_atk + weapon_base_atk) * (1 + stat_totals["atk_percent"]) + stat_totals["atk_flat"]
        total_def = base_def * (1 + stat_totals["def_percent"]) + stat_totals["def_flat"]
        total_em = base_em + stat_totals["em"]
        total_crit_rate = base_crit_rate + stat_totals["crit_rate"]
        total_crit_dmg = base_crit_dmg + stat_totals["crit_dmg"]
        total_er = base_er + stat_totals["energy_recharge"]

        dmg_buff_val = "0%"
        if element_type in ("Pyro", "Hydro", "Anemo", "Electro", "Dendro", "Geo", "Cryo"):
            buff_val = stat_totals["dmg_bonus_by_element"].get(element_type, 0.0)
            if buff_val > 0:
                dmg_buff_val = str(formal_round(buff_val * 1000) / 10) + "%"

        stats_mock = {
            "HP": {"val": formal_round(total_hp), "base": formal_round(base_hp), "add": "+" + str(formal_round(total_hp - base_hp)), "icon": "static/assets/props/hp.png"},
            "攻撃力": {"val": formal_round(total_atk), "base": formal_round(base_atk + weapon_base_atk), "add": "+" + str(formal_round(total_atk - (base_atk + weapon_base_atk))), "icon": "static/assets/props/atk.png"},
            "防禦力": {"val": formal_round(total_def), "base": formal_round(base_def), "add": "+" + str(formal_round(total_def - base_def)), "icon": "static/assets/props/def.png"},
            "元素熟知": {"val": formal_round(total_em), "icon": "static/assets/props/em.png"},
            "会心率": {"val": str(formal_round(total_crit_rate * 1000) / 10) + "%", "icon": "static/assets/props/rate.webp"},
            "会心ダメージ": {"val": str(formal_round(total_crit_dmg * 1000) / 10) + "%", "icon": "static/assets/props/dmg.webp"},
            "元素チャージ効率": {"val": str(formal_round(total_er * 1000) / 10) + "%", "icon": "static/assets/props/er.png"},
            f"{element_ja}ダメバフ": {"val": dmg_buff_val, "icon": f"static/assets/props/{element_type.lower()}.png"},
        }
    else:
        all_buff_ids = ['30', '40', '41', '42', '43', '44', '45', '46']
        prop_map = target_avatar_info.get('fightPropMap', {})
        max_dmg_val = 0.0
        for b_id in all_buff_ids:
            val = prop_map.get(b_id, 0.0)
            if val > max_dmg_val:
                max_dmg_val = val
        if max_dmg_val == 0.0:
            for r_id in ['50', '51', '52', '53', '54', '55', '56', '57']:
                val = prop_map.get(r_id, 0.0)
                if val > max_dmg_val:
                    max_dmg_val = val
        dmg_buff_val = str(formal_round(max_dmg_val * 1000) / 10) + "%"

        stats_mock = {
            "HP": {"val": formal_round(target_avatar_info.get('fightPropMap', {}).get('2000', 1)), "base": formal_round(target_avatar_info.get('fightPropMap', {}).get('1', 1)), "add": "+" + str(formal_round(target_avatar_info.get('fightPropMap', {}).get('2000', 1) - target_avatar_info.get('fightPropMap', {}).get('1', 1))), "icon": "static/assets/props/hp.png"},
            "攻撃力": {"val": formal_round(target_avatar_info.get('fightPropMap', {}).get('2001', 1)), "base": formal_round(target_avatar_info.get('fightPropMap', {}).get('4', 1)), "add": "+" + str(formal_round(target_avatar_info.get('fightPropMap', {}).get('2001', 1) - target_avatar_info.get('fightPropMap', {}).get('4', 1))), "icon": "static/assets/props/atk.png"},
            "防禦力": {"val": formal_round(target_avatar_info.get('fightPropMap', {}).get('2002', 1)), "base": formal_round(target_avatar_info.get('fightPropMap', {}).get('7', 1)), "add": "+" + str(formal_round(target_avatar_info.get('fightPropMap', {}).get('2002', 1) - target_avatar_info.get('fightPropMap', {}).get('7', 1))), "icon": "static/assets/props/def.png"},
            "元素熟知": {"val": formal_round(target_avatar_info.get('fightPropMap', {}).get('28', 1)), "icon": "static/assets/props/em.png"},
            "会心率": {"val": str(formal_round(target_avatar_info.get('fightPropMap', {}).get('20', 1) * 1000) / 10) + "%", "icon": "static/assets/props/rate.webp"},
            "会心ダメージ": {"val": str(formal_round(target_avatar_info.get('fightPropMap', {}).get('22', 1) * 1000) / 10) + "%", "icon": "static/assets/props/dmg.webp"},
            "元素チャージ効率": {"val": str(formal_round(target_avatar_info.get('fightPropMap', {}).get('23', 1) * 1000) / 10) + "%", "icon": "static/assets/props/er.png"},
            f"{element_ja}ダメバフ": {"val": dmg_buff_val, "icon": f"static/assets/props/{element_type.lower()}.png"},
        }

    artifact_x_list = [33, 375, 718, 1061, 1404]
    slot_to_index = {"4": 0, "2": 1, "5": 2, "1": 3, "3": 4}

    artifacts_mock = []
    for _ in range(5):
        artifacts_mock.append({
            "set": "0", "name": "未装備", "upgrade": 0,
            "Main": ["-", "-"],
            "stats": {i: ["static/assets/props/atk_per.png", "-", "-"] for i in range(4)},
            "score": 0.0, "tier": "-", "icon": ""
        })

    method_to_prop_id = {
        "atk": "FIGHT_PROP_ATTACK_PERCENT",
        "hp": "FIGHT_PROP_HP_PERCENT",
        "def": "FIGHT_PROP_DEFENSE_PERCENT",
        "em": "FIGHT_PROP_ELEMENT_MASTERY",
        "charge": "FIGHT_PROP_CHARGE_EFFICIENCY"
    }
    target_prop_id = method_to_prop_id.get(calc_method, "")

    score_sum = 0
    for art in raw_artifacts:
        flat = art.get("flat", {})
        reliquary = art.get("reliquary", {})
        icon_name = flat.get("icon", "")
        if "_" not in icon_name:
            continue
        last_num = icon_name.split("_")[-1]
        if last_num not in slot_to_index:
            continue
        target_idx = slot_to_index[last_num]

        name_hash = str(flat.get("nameTextMapHash", ""))
        artifact_name = text_map_data.get(name_hash, "未知の聖遺物")

        main_stat_raw = flat.get("reliquaryMainstat", {})
        main_prop_id = main_stat_raw.get("mainPropId", "")
        main_name = get_stat_japanese(main_prop_id)
        main_val = main_stat_raw.get("statValue", 0)
        if "PERCENT" in main_prop_id or "CRITICAL" in main_prop_id or "CHARGE" in main_prop_id or "HURT" in main_prop_id:
            main_value_str = f"{main_val}%"
        else:
            main_value_str = f"{int(main_val):,}"

        crit_rate = 0.0
        crit_dmg = 0.0
        target_stat_val = 0.0
        sub_stats_dict = {}
        sub_list = flat.get("reliquarySubstats", [])

        for idx in range(4):
            if idx < len(sub_list):
                sub_data = sub_list[idx]
                sub_prop_id = sub_data.get("appendPropId", "")
                sub_name = get_stat_japanese(sub_prop_id)
                sub_val = sub_data.get("statValue", 0)

                if sub_prop_id == "FIGHT_PROP_CRITICAL":
                    crit_rate = sub_val
                elif sub_prop_id == "FIGHT_PROP_CRITICAL_HURT":
                    crit_dmg = sub_val
                elif sub_prop_id == target_prop_id:
                    target_stat_val = sub_val

                icon_file = "atk_per.png"
                if "CRITICAL" in sub_prop_id and "HURT" not in sub_prop_id: icon_file = "rate.webp"
                elif "HURT" in sub_prop_id: icon_file = "dmg.webp"
                elif "CHARGE" in sub_prop_id: icon_file = "er.png"
                elif "ELEMENT_MASTERY" in sub_prop_id: icon_file = "em.png"
                elif "HP" in sub_prop_id: icon_file = "hp_per.png" if "PERCENT" in sub_prop_id else "hp.png"
                elif "ATTACK" in sub_prop_id: icon_file = "atk_per.png" if "PERCENT" in sub_prop_id else "atk.png"
                elif "DEFENSE" in sub_prop_id: icon_file = "def_per.png" if "PERCENT" in sub_prop_id else "def.png"

                sub_icon_path = f"static/assets/props/{icon_file}"
                if "PERCENT" in sub_prop_id or "CRITICAL" in sub_prop_id or "CHARGE" in sub_prop_id or "HURT" in sub_prop_id:
                    sub_value_str = f"{sub_val}%"
                else:
                    sub_value_str = f"{int(sub_val)}"
                sub_stats_dict[idx] = [sub_icon_path, sub_name, sub_value_str]
            else:
                sub_stats_dict[idx] = ["static/assets/props/atk_per.png", "-", "-"]

        art_score = round(score_calc(stat=target_stat_val, critrate=crit_rate, critdmg=crit_dmg, method=calc_method), 1)

        if target_idx in [0, 1]:
            art_tier = "SS" if art_score >= 50.0 else "S" if art_score >= 45.0 else "A" if art_score >= 40.0 else "B"
        elif target_idx == 2:
            art_tier = "SS" if art_score >= 45.0 else "S" if art_score >= 40.0 else "A" if art_score >= 35.0 else "B"
        elif target_idx == 3:
            art_tier = "SS" if art_score >= 45.0 else "S" if art_score >= 40.0 else "A" if art_score >= 37.0 else "B"
        else:
            art_tier = "SS" if art_score >= 40.0 else "S" if art_score >= 35.0 else "A" if art_score >= 30.0 else "B"

        artifacts_mock[target_idx] = {
            "set": str(flat.get("setId", "")),
            "name": artifact_name,
            "upgrade": reliquary.get("level", 1) - 1,
            "Main": [main_name, main_value_str],
            "stats": sub_stats_dict,
            "score": art_score,
            "tier": art_tier,
            "icon": icon_name
        }
        score_sum += art_score
    t_end = time.perf_counter()
    print(f"[Perf] 聖遺物データ処理: {(t_end - t_start)*1000:.1f}ms")

    t_start = time.perf_counter()
    artifact_image_num = [4, 2, 5, 1, 3]

    set_ids = [art["set"] for art in artifacts_mock if art["set"] and art["set"] != "0"]
    set_counts = Counter(set_ids)
    active_sets = [(set_id, count) for set_id, count in set_counts.items() if count >= 2]

    def get_set_info(set_id_str):
        possible_paths = [
            "static/data/lists/artifacts.json",
            os.path.join(BASE_DIR, "static", "data", "lists", "artifacts.json"),
            "artifacts.json"
        ]
        for raw_path in possible_paths:
            path = resolve_list_path(raw_path, beta)
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f_art:
                        art_json = json.load(f_art)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    with open(path, "r", encoding="cp932") as f_art:
                        art_json = json.load(f_art)
                try:
                    if set_id_str in art_json:
                        name = art_json[set_id_str].get("janame", f"セット {set_id_str}")
                        icon_field = art_json[set_id_str].get("icon", "UI_RelicIcon_15046_4")
                        icon_path = resolve_datas_path(f"static/assets/artifacts/{icon_field}.webp", beta)
                        return name, icon_path
                except Exception:
                    pass
        return f"セット {set_id_str}", resolve_datas_path("static/assets/artifacts/UI_RelicIcon_15046_4.webp", beta)

    sets_display = []
    if len(active_sets) == 1:
        set_id, count = active_sets[0]
        set_name, set_icon = get_set_info(set_id)
        sets_display.append({"icon": set_icon, "name": set_name, "count": str(count), "img_y": 271 - 4, "text_y": 281, "box_y": 279})
    elif len(active_sets) >= 2:
        set_id1, count1 = active_sets[0]
        set_name1, set_icon1 = get_set_info(set_id1)
        sets_display.append({"icon": set_icon1, "name": set_name1, "count": str(count1), "img_y": 242 - 4, "text_y": 252, "box_y": 250})
        set_id2, count2 = active_sets[1]
        set_name2, set_icon2 = get_set_info(set_id2)
        sets_display.append({"icon": set_icon2, "name": set_name2, "count": str(count2), "img_y": 301 - 4, "text_y": 311, "box_y": 309})

    if score_sum < 180:
        tier_sum_score = "B"
    elif score_sum < 200:
        tier_sum_score = "A"
    elif score_sum < 220:
        tier_sum_score = "S"
    else:
        tier_sum_score = "SS"

    display_map = {
        "crit": "会心のみ", "atk": "攻撃力%", "hp": "HP%",
        "def": "DEF%", "em": "元素熟知", "charge": "チャージ効率"
    }
    display_score_way = display_map[calc_method]
    t_end = time.perf_counter()
    print(f"[Perf] セット効果処理: {(t_end - t_start)*1000:.1f}ms")

    # ========== DRAWING (OPTIMIZED) ==========
    t_start = time.perf_counter()
    img = create_card_background(card_width, card_height, bg_base_rgb, splash_path=splash, element_type=element_type, use_prebuilt=(bg_color is None))
    t_end = time.perf_counter()
    print(f"[Perf] 背景生成: {(t_end - t_start)*1000:.1f}ms")
    draw = ImageDraw.Draw(img)

    t_start = time.perf_counter()
    # フォント読み込み（キャッシュ化: リクエスト間で共有、再読み込みなし。サイズはキャンバス解像度基準）
    font_stats = get_cached_font(FONT_PATH, max(1, round(28 * SY)))
    font_stats_light = get_cached_font(FONT_LIGHT_PATH, max(1, round(28 * SY)))
    t_end = time.perf_counter()
    print(f"[Perf] フォント読み込み: {(t_end - t_start)*1000:.1f}ms")

    t_start = time.perf_counter()
    draw_figma_box(img, x=33, y=30, width=694, height=671)
    draw_figma_box(img, x=753, y=30, width=549, height=671)
    draw_figma_box(img, x=1332, y=30, width=386, height=164, radius=25)
    draw_figma_box(img, x=1332, y=231, width=386, height=121, radius=25)
    draw_figma_box(img, x=1332, y=389, width=386, height=312, radius=25)

    paste_mask_image(img, splash, box_x=33, box_y=30, box_width=694, box_height=671, radius=15, zoom=1.1, beta=beta)

    # スプラッシュ枠アウトライン（設計座標 → キャンバス、最小レイヤーで合成）
    _ol_x1, _ol_y1 = int(31 * SX), int(28 * SY)
    _ol_x2, _ol_y2 = int(729 * SX) + 1, int(703 * SY) + 1
    _outline_layer = Image.new("RGBA", (_ol_x2 - _ol_x1, _ol_y2 - _ol_y1), (0, 0, 0, 0))
    _od = ImageDraw.Draw(_outline_layer)
    _od.rounded_rectangle([33 * SX - _ol_x1, 30 * SY - _ol_y1, 727 * SX - _ol_x1, 701 * SY - _ol_y1], radius=round(15 * SY), outline=(0, 0, 0, 220), width=max(1, round(1 * SY)))
    img.alpha_composite(_outline_layer, dest=(_ol_x1, _ol_y1))

    draw_figma_text_with_shadow(draw, text=char_name, x=53, y=53, font=font_stats, font_size=50)
    draw_figma_text_with_shadow(draw, text=f"Lv.{char_level}", x=53, y=117, font=font_stats, font_size=30)
    draw_figma_text_with_shadow(draw, text=f"♥ {friendship_lv}", x=53, y=162, font=font_stats, font_size=30)

    y_skill_base = 389
    for i in range(3):
        draw_figma_circle(img, x=49, y=y_skill_base + 79 * i, size=68, fill_color=(0, 0, 0, 150), outline_color=base_color, outline_width=4)
        paste_figma_image(img, f"static/assets/skills/{skill_icon[i]}.webp", box_x=49 + 5, box_y=y_skill_base + 79 * i + 4, box_width=60, box_height=60, radius=15, beta=beta)
        lv_color = (125, 210, 255) if (i < len(skill_boosted) and skill_boosted[i]) else (255, 255, 255)
        draw_figma_text_with_shadow(draw, text=f"Lv.{skill_level[i]}", x=48, y=y_skill_base + 79 * i + 45, font=font_stats, align="center", font_size=20, box_width=68, fill_color=lv_color)

    for i in range(6):
        circle_x = 637
        circle_y = y_C_base + i * 76
        icon_name = Constellation_icon[i]

        if i >= constellation_releas_num:
            draw_figma_circle(img, x=circle_x, y=circle_y, size=circle_size, fill_color=(0, 0, 0, 180), outline_color=(80, 85, 95, 255), outline_width=2)
            icon_path = resolve_datas_path(f"static/assets/skills/{icon_name}.webp", beta)
            if os.path.exists(icon_path):
                icon_img = get_resized_image(icon_path, (max(1, round(60 * SX)), max(1, round(60 * SY))))
                if icon_img is not None:
                    alpha = icon_img.getchannel('A').point(lambda p: int(p * (45 / 255.0)))
                    icon_img.putalpha(alpha)
                    img.paste(icon_img, (int(round((circle_x + 5) * SX)), int(round((circle_y + 5) * SY))), icon_img)

            # ロックアイコン（設計座標の円中心 → キャンバスへ拡大、最小レイヤーで合成）
            lock_w, lock_h = 24 * SX, 26 * SY
            lx = circle_x * SX + (circle_size * SY - lock_w) / 2
            ly = circle_y * SY + (circle_size * SY - lock_h) / 2 + 2 * SY
            _lk_x1, _lk_y1 = int(lx) - 3, int(ly) - 3
            _dx, _dy = -_lk_x1, -_lk_y1
            lock_overlay = Image.new("RGBA", (int(lock_w) + 7, int(lock_h) + 7), (0, 0, 0, 0))
            draw_lock = ImageDraw.Draw(lock_overlay)
            _lw3 = max(1, round(3 * SY))
            draw_lock.arc([lx + 4 * SX + _dx, ly + _dy, lx + lock_w - 4 * SX + _dx, ly + 16 * SY + _dy], start=180, end=0, fill=(255, 255, 255, 220), width=_lw3)
            draw_lock.line([lx + 4 * SX + _dx, ly + 8 * SY + _dy, lx + 4 * SX + _dx, ly + 12 * SY + _dy], fill=(255, 255, 255, 220), width=_lw3)
            draw_lock.line([lx + lock_w - 4 * SX + _dx, ly + 8 * SY + _dy, lx + lock_w - 4 * SX + _dx, ly + 12 * SY + _dy], fill=(255, 255, 255, 220), width=_lw3)
            draw_lock.rounded_rectangle([lx + _dx, ly + 11 * SY + _dy, lx + lock_w + _dx, ly + lock_h + _dy], radius=max(1, round(4 * SY)), fill=(20, 25, 35, 255), outline=(255, 255, 255, 220), width=max(1, round(2 * SY)))
            draw_lock.ellipse([lx + 10 * SX + _dx, ly + 16 * SY + _dy, lx + 14 * SX + _dx, ly + 20 * SY + _dy], fill=(255, 255, 255, 220))
            img.alpha_composite(lock_overlay, dest=(_lk_x1, _lk_y1))
        else:
            draw_figma_circle(img, x=circle_x, y=circle_y, size=circle_size, fill_color=(0, 0, 0, 150), outline_color=base_color, outline_width=4)
            paste_figma_image(img, f"static/assets/skills/{icon_name}.webp", box_x=circle_x + 5, box_y=circle_y + 5, box_width=60, box_height=60, radius=15, beta=beta)

    paste_figma_image(img, f"static/assets/weapons/{weapon_icon}.webp", box_x=1350, box_y=60, box_width=100, box_height=100, radius=15, beta=beta)
    draw_figma_box(img, x=1340, y=47, width=60, height=30, radius=2)
    draw_figma_text(draw, text=f"R{weapon_affix}", x=1357, y=48, font=font_stats, align="left", font_size=20)
    draw_figma_text(draw, text=weapon_name, x=1462, y=60, font=font_stats, align="left", font_size=23)
    draw_figma_text(draw, text=f"Lv.{weapon_level}", x=1462, y=90, font=font_stats, align="left", font_size=20)

    if weapon_stat1:
        stat_name1, stat_val1_str = weapon_stat1
        draw_figma_text(draw, text=stat_name1, x=1462, y=125, font=font_stats_light, align="left", font_size=18)
        draw_figma_text(draw, text=stat_val1_str, x=1635, y=125, font=font_stats_light, align="left", font_size=21)
    if weapon_stat2:
        stat_name2, stat_val2_str = weapon_stat2
        draw_figma_text(draw, text=stat_name2, x=1462, y=155, font=font_stats_light, align="left", font_size=18)
        draw_figma_text(draw, text=stat_val2_str, x=1635, y=155, font=font_stats_light, align="left", font_size=21)
    t_end = time.perf_counter()
    print(f"[Perf] 描画：ボックス・テキスト（上半分）: {(t_end - t_start)*1000:.1f}ms")

    t_start = time.perf_counter()
    base_y = 73
    max_y = 700
    row_gap = (max_y - base_y) // len(stats_mock)
    icon_size = 36
    icon_offset_y = 2

    for i, (n, data) in enumerate(stats_mock.items()):
        current_y = base_y + (i * row_gap)
        icon_path = resolve_datas_path(data["icon"], beta)
        icon_x = 840 - 60
        if icon_path and os.path.exists(icon_path):
            try:
                icon_img = get_resized_image(icon_path, (max(1, round(icon_size * SX)), max(1, round(icon_size * SY))))
                if icon_img is not None:
                    img.paste(icon_img, (int(round(icon_x * SX)), int(round((current_y + icon_offset_y) * SY))), icon_img)
            except Exception as e:
                print(f"[Error] Failed to paste status icon: {icon_path}. Reason: {e}")
        draw_figma_text(draw, text=n, x=840, y=current_y, font=font_stats, align="left")
        draw_figma_text(draw, text=data["val"], x=870, y=current_y, font=font_stats, align="right", box_width=450 - 60)

        if n in ["HP", "攻撃力", "防禦力"] and data.get("base") and data.get("add"):
            sub_y = current_y + 32
            green_text = data["add"]
            gray_text = str(data["base"])
            calc_font = get_cached_font(FONT_PATH, max(1, round(20 * SY))) if os.path.exists(FONT_PATH) else font_stats
            # textlength はキャンバスpx → 設計単位(/SX)に変換して座標計算と整合
            green_w = draw.textlength(green_text, font=calc_font) / SX
            gray_w = draw.textlength(gray_text, font=calc_font) / SX
            target_right_edge = 1260
            green_x = target_right_edge - green_w
            gray_x = green_x - 8 - gray_w
            draw_figma_text(draw, text=green_text, x=green_x, y=sub_y, font=font_stats, font_size=20, fill_color=(0, 230, 115), align="left")
            draw_figma_text(draw, text=gray_text, x=gray_x, y=sub_y, font=font_stats, font_size=20, fill_color=(160, 165, 175), align="left")
    t_end = time.perf_counter()
    print(f"[Perf] 描画：ステータス: {(t_end - t_start)*1000:.1f}ms")

    t_start = time.perf_counter()
    for x in artifact_x_list:
        draw_figma_box(img, x=x, y=738, width=314, height=399, radius=25)

    for i in range(5):
        box_x = artifact_x_list[i]
        artifact_data = artifacts_mock[i]
        artifact_img_num = artifact_image_num[i]

        draw_figma_box(img, x=box_x + 14, y=754, width=90, height=90, radius=10)
        draw_figma_box(img, x=box_x + 230, y=795, width=70, height=40, radius=10)
        paste_figma_image(img, f"static/assets/artifacts/UI_RelicIcon_{artifact_data['set']}_{artifact_img_num}.webp", box_x=box_x + 14, box_y=754, box_width=90, box_height=90, radius=15, beta=beta)
        draw_figma_text(draw, text=artifact_data["Main"][0], x=box_x + 114, y=758, font=font_stats, align="left")
        draw_figma_text(draw, text=artifact_data["Main"][1], x=box_x + 114, y=792, font=font_stats, align="left", font_size=30)
        draw_figma_text(draw, text=f"+{artifact_data['upgrade']}", x=box_x + 237, y=793, font=font_stats, align="left")

        y_base = 855
        for j in range(4):
            draw_figma_text(draw, text=artifact_data["stats"][j][1], x=box_x + 47, y=y_base + 50 * j, font=font_stats, font_size=25, align="left")
            draw_figma_text(draw, text=artifact_data["stats"][j][2], x=box_x + 218, y=y_base + 50 * j, font=font_stats, font_size=25, align="left")
            paste_figma_image(img, artifact_data["stats"][j][0], box_x=box_x + 12, box_y=y_base + 50 * j, box_width=30, box_height=30, radius=5, beta=beta)

        draw_figma_line(img, x1=box_x + 27, y1=1065, x2=box_x + 287, y2=1065, fill_color=(255, 255, 255, 50), width=1)
        # スコア数字は右端(box_x+287)基準で右揃え、小さい「スコア」ラベルは数字の左側に寄せる
        _num_font_path = getattr(font_stats, "path", None)
        _num_font = get_cached_font(_num_font_path, max(1, round(40 * SY))) if _num_font_path and os.path.exists(_num_font_path) else font_stats
        # textlength はキャンバスpx → 設計単位(/SX)に変換
        _score_left_x = box_x + 287 - draw.textlength(str(artifact_data["score"]), font=_num_font) / SX
        draw_figma_text(draw, text="スコア", x=box_x + 27, y=1090, font=font_stats_light, font_size=20, align="right", box_width=(_score_left_x - 6) - (box_x + 27))
        draw_figma_text(draw, text=artifact_data["score"], x=box_x + 207, y=1070, font=font_stats, font_size=40, align="right", box_width=80)
        paste_figma_image(img, f"static/assets/tiers/{artifact_data['tier']}.png", box_x=box_x + 27, box_y=1070, box_width=60, box_height=60, radius=15, beta=beta)
    t_end = time.perf_counter()
    print(f"[Perf] 描画：聖遺物5枠: {(t_end - t_start)*1000:.1f}ms")

    t_start = time.perf_counter()
    _name_font_path = getattr(font_stats, "path", None)
    _name_font = get_cached_font(_name_font_path, max(1, round(20 * SY))) if _name_font_path and os.path.exists(_name_font_path) else font_stats
    for s in sets_display:
        # アイコン・名前を左寄りに配置し、個数バッジは名前の直後に付ける（数字はバッジ中央揃え）
        paste_figma_image(img, s["icon"], box_x=1345, box_y=s["img_y"], box_width=60, box_height=60, radius=15, beta=beta)
        draw_figma_text(draw, text=s["name"], x=1420, y=s["text_y"], font=font_stats, align="left", font_size=20)
        _count_box_x = 1420 + draw.textlength(str(s["name"]), font=_name_font) / SX + 10
        draw_figma_box(img, x=_count_box_x, y=s["box_y"], width=35, height=28, radius=8, fill_color=(255, 255, 255, 40))
        draw_figma_text(draw, text=s["count"], x=_count_box_x, y=s["text_y"], font=font_stats, align="center", font_size=18, box_width=35)

    draw_figma_text(draw, text="総合スコア", x=1443, y=449, font=font_stats, align="left", font_size=30)
    draw_figma_text(draw, text=round(score_sum, 1), x=1332, y=480, font=font_stats, align="center", font_size=90, box_width=386)
    draw_figma_line(img, x1=1380, y1=623, x2=1670, y2=623, fill_color=(255, 255, 255, 50), width=1)
    paste_figma_image(img, f"static/assets/tiers/{tier_sum_score}.png", box_x=1620, box_y=400, box_width=80, box_height=80, radius=15, beta=beta)
    draw_figma_text(draw, text="計算方法", x=1350, y=642, font=font_stats, align="left", font_size=30)
    draw_figma_text_right(draw, text=display_score_way, x=1680, y=645, font=font_stats, align="right", font_size=35)
    t_end = time.perf_counter()
    print(f"[Perf] 描画：セット効果・総合スコア: {(t_end - t_start)*1000:.1f}ms")

    # 透明を維持するため RGBA のまま保存（黒背景への合成はしない）。形式は設定で PNG/WEBP 切替
    t_start = time.perf_counter()
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    img_io = io.BytesIO()
    if img_format == "png":
        img.save(img_io, 'PNG', compress_level=0)
    else:
        img.save(img_io, 'WEBP', quality=95, method=0)
    img_io.seek(0)
    t_end = time.perf_counter()
    print(f"[Perf] 画像保存: {(t_end - t_start)*1000:.1f}ms")
    print(f"[Perf] TOTAL: {(time.perf_counter() - _total_start)*1000:.1f}ms")
    return img_io.getvalue()


@app.get("/generate_card_image/{uid}/{avatar_id}/{calc_method}")
async def generate_card_image(uid: str, avatar_id: str, calc_method: str, fake_char: str = None, fake_weapon: str = None, beta: str = "false", bg_color: str = None, img_format: str = "webp"):
    img_format = "png" if str(img_format).lower() == "png" else "webp"
    img_bytes = await run_in_threadpool(
        _generate_card_image_sync, uid, avatar_id, calc_method, fake_char, fake_weapon, beta, bg_color, img_format
    )
    return StreamingResponse(io.BytesIO(img_bytes), media_type="image/png" if img_format == "png" else "image/webp")


@app.get("/serverup", response_class=HTMLResponse)
@app.post("/serverup", response_class=HTMLResponse)
@app.head("/serverup", response_class=HTMLResponse)
async def serverup(request: Request):
    return HTMLResponse(content="Success to access")


if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)
