import os
import time
import random as _random
from PIL import Image, ImageDraw, ImageFilter
from app.paths import CARD_W, CARD_H, SY, _REGION_STATES_DIR
from app.card.cache import (
    _PREBUILT_BGS, REGION_BGS, SPLASH_BLUR_CACHE,
    get_cached_image,
)


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


def _build_base_background(width, height, base_rgb):
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


def _build_region_background(width, height, region):
    if not region:
        return None
    region_path = os.path.join(_REGION_STATES_DIR, f"{region}.png")
    if not os.path.exists(region_path):
        return None
    try:
        src = get_cached_image(region_path)
        if src is None:
            return None
        scale = max(width / src.width, height / src.height)
        nw = max(1, int(src.width * scale + 0.5))
        nh = max(1, int(src.height * scale + 0.5))
        resized = src.resize((nw, nh), Image.Resampling.BICUBIC)
        left = max(0, (nw - width) // 2)
        top = max(0, (nh - height) // 2)
        return resized.crop((left, top, left + width, top + height))
    except Exception as e:
        print(f"[Warning] region background load failed ({region_path}): {e}")
        return None


def get_region_background(width, height, region):
    if not region:
        return None
    key = (region, width, height)
    hit = REGION_BGS.get(key)
    if hit is not None:
        return hit
    bg = _build_region_background(width, height, region)
    if bg is not None:
        REGION_BGS.put(key, bg)
        return bg.copy()
    return None


def region_image_path(region):
    """地域背景画像のパスを返す（存在しなければ None）。"""
    if not region:
        return None
    path = os.path.join(_REGION_STATES_DIR, f"{region}.png")
    return path if os.path.exists(path) else None


def _list_region_image_names():
    if not os.path.isdir(_REGION_STATES_DIR):
        return []
    return sorted(fn[:-4] for fn in os.listdir(_REGION_STATES_DIR) if fn.endswith(".png"))


def _prebuild_backgrounds(width=CARD_W, height=CARD_H):
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
        # 背景はアルファ不使用のため RGB で保持（RGBA 比で約 25% 省メモリ）。
        # 使用時の convert("RGBA") で変換とコピーが同時に行われる。
        _PREBUILT_BGS[elem] = _build_base_background(width, height, rgb).convert("RGB")
    print(f"[Prebuild] {len(_PREBUILT_BGS)} element backgrounds cached in memory")
    prebuilt_regions = 0
    for region in _list_region_image_names():
        if get_region_background(width, height, region) is not None:
            prebuilt_regions += 1
    if prebuilt_regions:
        print(f"[Prebuild] {prebuilt_regions} region backgrounds cached in memory")


def create_card_background(width, height, base_rgb, splash_path=None, element_type="None", use_prebuilt=True, region=None):
    def _bglog(msg: str) -> None:
        print(f"[Perf][bg] {msg}", flush=True)

    _bglog(f"start w={width} h={height} element={element_type} region={region!r} use_prebuilt={use_prebuilt} splash={bool(splash_path)}")
    t0 = time.perf_counter()

    bg = None
    if region:
        _bglog(f"region load begin region={region}")
        bg = get_region_background(width, height, region)
        _bglog(f"region load done ok={bg is not None} {(time.perf_counter()-t0)*1000:.0f}ms")

    if bg is None:
        if use_prebuilt and element_type in _PREBUILT_BGS:
            _bglog(f"prebuilt copy element={element_type}")
            # RGB 保持のためここで RGBA に変換（convert は新画像を返す＝コピー込み）
            bg = _PREBUILT_BGS[element_type].convert("RGBA")
            _bglog(f"prebuilt copy done {(time.perf_counter()-t0)*1000:.0f}ms")
        else:
            _bglog("build_base_background begin")
            bg = _build_base_background(width, height, base_rgb)
            _bglog(f"build_base_background done {(time.perf_counter()-t0)*1000:.0f}ms")

    if splash_path and os.path.exists(splash_path):
        cache_key = (splash_path, width, height)
        layer = SPLASH_BLUR_CACHE.get(cache_key)  # ヒット時はコピー済み
        if layer is not None:
            _bglog(f"splash blur CACHE HIT (copy done) {(time.perf_counter()-t0)*1000:.0f}ms")
        else:
            layer = None
            try:
                _bglog(f"splash blur MISS path={splash_path}")
                splash_img = get_cached_image(splash_path)
                if splash_img is None:
                    _bglog("splash Image.open ...")
                    splash_img = Image.open(splash_path).convert("RGBA")
                _bglog(f"splash loaded size={splash_img.size}")
                # 以前 1.35 倍 + 強 blur で固まることがあったため縮小
                scale = max(width / splash_img.width, height / splash_img.height) * 1.15
                nw = int(splash_img.width * scale)
                nh = int(splash_img.height * scale)
                _bglog(f"splash resize begin -> {nw}x{nh}")
                splash_img = splash_img.resize((nw, nh), Image.Resampling.BICUBIC)
                _bglog(f"splash resize done {(time.perf_counter()-t0)*1000:.0f}ms")
                layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                ox = (width - nw) // 2
                oy = (height - nh) // 2
                layer.paste(splash_img, (ox, oy), splash_img)
                # radius を抑える（旧: 24*SY ≈ 33）。大きい GaussianBlur がハングの主因になりやすい
                blur_r = max(1, round(12 * SY))
                _bglog(f"GaussianBlur begin radius={blur_r}")
                layer = layer.filter(ImageFilter.GaussianBlur(radius=blur_r))
                _bglog(f"GaussianBlur done {(time.perf_counter()-t0)*1000:.0f}ms")
                r, g, b, a = layer.split()
                a = a.point(lambda p: int(p * 0.09))
                layer = Image.merge("RGBA", (r, g, b, a))
                # put で所有権をキャッシュに移す（以降 layer は変更しない）
                SPLASH_BLUR_CACHE.put(cache_key, layer)
                _bglog(f"splash blur cached {(time.perf_counter()-t0)*1000:.0f}ms")
            except Exception as e:
                print(f"[Warning] splash blur background failed: {e}", flush=True)
                layer = None
        if layer is not None:
            _bglog("alpha_composite splash begin")
            bg = Image.alpha_composite(bg, layer)
            _bglog(f"alpha_composite splash done {(time.perf_counter()-t0)*1000:.0f}ms")
    else:
        _bglog("no splash (missing path or empty)")

    _bglog(f"return total {(time.perf_counter()-t0)*1000:.0f}ms")
    return bg  # RGBA
