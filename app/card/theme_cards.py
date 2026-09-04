# -*- coding: utf-8 -*-
"""カードテーマ画像生成（cinema / scorecard）。

HTML テーマ（templates/build_card.html の .theme-cinema / .theme-scorecard、
元デザイン: static/proposals/ui1_cinema.html / ui2v2_scorecard.html）を
Pillow で再現する。データは HTML テーマと同一の _get_card_data_sync を使う。

設計座標系は HTML と同じ横幅 1200px。THEME_SCALE=2 で 2400px 幅キャンバスに
描画する（単体カードの CARD_W と同幅）。描画は draw.py の figma_* ヘルパを
figma_draw_scale(2,2) 内で使う方式（team_image.py と同じ）。
"""
import io
import math
import os
import time
from PIL import Image, ImageChops, ImageDraw

from app.paths import FONT_PATH, FONT_LIGHT_PATH
from app.card.cache import get_cached_font, get_resized_image
from app.card.data import _get_card_data_sync
from app.card.draw import (
    draw_figma_box, draw_figma_text, draw_figma_line,
    paste_mask_image, figma_draw_scale, safe_rounded_rectangle,
)
from app.card.special import resolve_datas_path
from app.card.labels import img_t
from app.card.image import ROLL_DOT_COLORS, _draw_growth_panel

# ----------------------------------------------------------------
# 設計座標 / キャンバス
# ----------------------------------------------------------------
THEME_DESIGN_W = 1200
THEME_SCALE = 2.0
THEME_W = int(THEME_DESIGN_W * THEME_SCALE)
THEME_SX = THEME_SCALE
THEME_SY = THEME_SCALE

CINEMA_DESIGN_H = 780

# フロントエンド ELEMENT_COLORS と同一
_ELEM_ACCENT = {
    "Pyro": (0xFF, 0x6B, 0x4A),
    "Hydro": (0x4A, 0xA4, 0xFF),
    "Anemo": (0x74, 0xE0, 0xC5),
    "Electro": (0xC9, 0x7B, 0xFF),
    "Dendro": (0xA0, 0xE0, 0x5A),
    "Cryo": (0x9A, 0xD4, 0xFF),
    "Geo": (0xF0, 0xC6, 0x5A),
    "None": (0xAA, 0xAA, 0xAA),
}
_ELEM_JA = {
    "Pyro": "炎", "Hydro": "水", "Anemo": "風", "Electro": "雷",
    "Dendro": "草", "Cryo": "氷", "Geo": "岩", "None": "無",
}

# cinema 配色
_CINE_GOLD = (255, 210, 125, 255)
_CINE_TXT = (244, 247, 252, 255)
_CINE_GLASS = (8, 12, 22, 140)       # rgba(8,12,22,0.55)
_CINE_GLASS_BRD = (255, 255, 255, 41)  # rgba(255,255,255,0.16)

# scorecard 配色
_SCC_GOLD = (255, 205, 120, 255)
_SCC_TXT = (237, 241, 249, 255)
_SCC_DIM = (138, 147, 173, 255)      # #8a93ad

# cinema ライト/ダークパレット（light パラメータで切り替え）
_CINE_PALETTE = {
    "dark": {
        "bg": (5, 7, 13, 255),
        "gold": _CINE_GOLD,
        "txt": _CINE_TXT,
        "glass": _CINE_GLASS,
        "glass_brd": _CINE_GLASS_BRD,
        "scrim": (3, 5, 10),
        "chip_fill": (8, 14, 26, 140),
        "icon_fill": (8, 12, 22, 158),
        "icon_brd": _CINE_GLASS_BRD,
        "icon_box_fill": (10, 15, 28, 255),
        "icon_box_brd": (255, 255, 255, 51),
        "lv_chip_fill": (10, 15, 28, 255),
        "lv_chip_brd": (255, 255, 255, 128),
        "lv_chip_txt": _CINE_TXT,
        "const_off_fill": (24, 28, 38, 200),
        "const_off_brd": (255, 255, 255, 64),
        "cell_fill": (8, 12, 22, 128),
        "cell_brd": (255, 255, 255, 31),
        "art_fill": (8, 12, 22, 158),
        "art_brd": (255, 255, 255, 36),
        "sep": (255, 255, 255, 31),
        "meta_sep": (255, 255, 255, 71),
        "name_shadow": (0, 0, 0, 190),
        "heart": (255, 125, 171, 255),
        "boost": (126, 224, 163, 255),
        "boost_brd": (126, 224, 163, 166),
        "icon_back": None,
        "elem_mix": 0.0,
    },
    "light": {
        "bg": (248, 250, 252, 255),
        "gold": (180, 83, 9, 255),
        "txt": (17, 24, 39, 255),
        "glass": (255, 255, 255, 165),
        "glass_brd": (15, 23, 42, 38),
        "scrim": (255, 255, 255),
        "chip_fill": (255, 255, 255, 175),
        "icon_fill": (15, 23, 42, 80),
        "icon_brd": (15, 23, 42, 45),
        "icon_box_fill": (15, 23, 42, 90),
        "icon_box_brd": (15, 23, 42, 45),
        "lv_chip_fill": (10, 15, 28, 255),
        "lv_chip_brd": (255, 255, 255, 128),
        "lv_chip_txt": _CINE_TXT,
        "const_off_fill": (24, 28, 38, 200),
        "const_off_brd": (15, 23, 42, 60),
        "cell_fill": (255, 255, 255, 150),
        "cell_brd": (15, 23, 42, 30),
        "art_fill": (255, 255, 255, 165),
        "art_brd": (15, 23, 42, 33),
        "sep": (15, 23, 42, 40),
        "meta_sep": (15, 23, 42, 70),
        "name_shadow": (255, 255, 255, 200),
        "heart": (190, 24, 93, 255),
        "boost": (126, 224, 163, 255),
        "boost_brd": (126, 224, 163, 166),
        "icon_back": (15, 23, 42, 80),
        "elem_mix": 0.42,
    },
}

# scorecard ライト/ダークパレット
_SCC_PALETTE = {
    "dark": {
        "bg": ((14, 20, 36), (9, 13, 24), (12, 17, 32)),
        "gold": _SCC_GOLD,
        "txt": _SCC_TXT,
        "dim": _SCC_DIM,
        "grid": (255, 255, 255, 38),
        "scrim": (9, 13, 24),
        "chip_fill": (10, 16, 30, 179),
        "chip_brd": (255, 255, 255, 31),
        "chip_bold": (255, 255, 255, 255),
        "icon_box_fill": (16, 22, 42, 255),
        "icon_box_brd": (255, 255, 255, 51),
        "weapon_box_brd": (255, 255, 255, 38),
        "talent_fill": (255, 255, 255, 10),
        "talent_brd": (255, 255, 255, 26),
        "lv_chip_fill": (13, 18, 34, 255),
        "lv_chip_brd": (255, 255, 255, 115),
        "lv_chip_txt": _SCC_TXT,
        "const_off_fill": (20, 26, 44, 210),
        "const_off_brd": (255, 255, 255, 51),
        "tile_fill": (255, 255, 255, 8),
        "tile_brd": (255, 255, 255, 18),
        "sep": (255, 255, 255, 13),
        "row_sep": (255, 255, 255, 10),
        "art_sep": (255, 255, 255, 20),
        "gauge_track": (255, 255, 255, 20),
        "cv_bar": (255, 255, 255, 5),
        "cv_line": (255, 255, 255, 13),
        "cv_track": (255, 255, 255, 18),
        "name_shadow": (0, 0, 0, 200),
        "best_brd": (255, 205, 120, 140),
        "strip_to": (255, 205, 120),
        "boost": (126, 224, 163, 255),
        "boost_brd": (126, 224, 163, 153),
        "icon_back": None,
        "elem_mix": 0.0,
    },
    "light": {
        "bg": ((248, 250, 252), (241, 245, 249), (248, 250, 252)),
        "gold": (180, 83, 9, 255),
        "txt": (17, 24, 39, 255),
        "dim": (15, 23, 42, 170),
        "grid": (15, 23, 42, 22),
        "scrim": (255, 255, 255),
        "chip_fill": (255, 255, 255, 185),
        "chip_brd": (15, 23, 42, 30),
        "chip_bold": (17, 24, 39, 255),
        "icon_box_fill": (15, 23, 42, 90),
        "icon_box_brd": (15, 23, 42, 45),
        "weapon_box_brd": (15, 23, 42, 45),
        "talent_fill": (15, 23, 42, 80),
        "talent_brd": (15, 23, 42, 45),
        "lv_chip_fill": (13, 18, 34, 255),
        "lv_chip_brd": (255, 255, 255, 115),
        "lv_chip_txt": _SCC_TXT,
        "const_off_fill": (20, 26, 44, 210),
        "const_off_brd": (15, 23, 42, 50),
        "tile_fill": (255, 255, 255, 155),
        "tile_brd": (15, 23, 42, 28),
        "sep": (15, 23, 42, 25),
        "row_sep": (15, 23, 42, 20),
        "art_sep": (15, 23, 42, 30),
        "gauge_track": (15, 23, 42, 25),
        "cv_bar": (15, 23, 42, 8),
        "cv_line": (15, 23, 42, 25),
        "cv_track": (15, 23, 42, 25),
        "name_shadow": (255, 255, 255, 210),
        "best_brd": (180, 83, 9, 150),
        "strip_to": (180, 83, 9),
        "boost": (126, 224, 163, 255),
        "boost_brd": (126, 224, 163, 153),
        "icon_back": (15, 23, 42, 80),
        "elem_mix": 0.42,
    },
}


def _palette(palettes, light):
    return palettes["light" if str(light or "") == "true" else "dark"]


def _mix_rgb(rgb, target, f):
    """rgb を target へ割合 f で混ぜる（ライトモードの元素アクセント暗化用）。"""
    if f <= 0:
        return rgb
    return tuple(int(c + (t - c) * f) for c, t in zip(rgb, target))


def _icon_back(img, pal, x, y, w, h, radius):
    """白系アイコン用に暗めの台座を描く（ライトモードのみ。ダークでは何もしない）。"""
    if not pal.get("icon_back"):
        return
    draw_figma_box(img, x=x, y=y, width=w, height=h, radius=radius,
                   fill_color=pal["icon_back"], outline_color=None,
                   outline_width=0, shadow=False)


def _font(size, light=False):
    path = FONT_LIGHT_PATH if light else FONT_PATH
    return get_cached_font(path, max(1, round(size * THEME_SY)))


def _dim(color, alpha_frac):
    """RGBA のアルファを割合で薄める。"""
    return (color[0], color[1], color[2], int(color[3] * alpha_frac))


def _hgrad(w, h, rgb, stops):
    """横方向グラデーション層。stops: [(pos0-1, alpha0-255), ...]"""
    n = max(2, int(w))
    strip = Image.new("RGBA", (n, 1), (0, 0, 0, 0))
    px = strip.load()
    for x in range(n):
        t = x / (n - 1)
        a = _interp_stops(stops, t)
        px[x, 0] = (rgb[0], rgb[1], rgb[2], a)
    return strip.resize((int(w), int(h)), Image.Resampling.BILINEAR)


def _vgrad(w, h, rgb, stops):
    """縦方向グラデーション層。stops: [(pos0-1, alpha0-255), ...] pos=0 が上端。"""
    n = max(2, int(h))
    strip = Image.new("RGBA", (1, n), (0, 0, 0, 0))
    px = strip.load()
    for y in range(n):
        t = y / (n - 1)
        a = _interp_stops(stops, t)
        px[0, y] = (rgb[0], rgb[1], rgb[2], a)
    return strip.resize((int(w), int(h)), Image.Resampling.BILINEAR)


def _interp_stops(stops, t):
    if t <= stops[0][0]:
        return int(stops[0][1])
    if t >= stops[-1][0]:
        return int(stops[-1][1])
    for i in range(len(stops) - 1):
        p0, a0 = stops[i]
        p1, a1 = stops[i + 1]
        if p0 <= t <= p1:
            f = 0.0 if p1 == p0 else (t - p0) / (p1 - p0)
            return int(a0 + (a1 - a0) * f)
    return int(stops[-1][1])


def _radial_layer(w, h, cx, cy, radius, rgb, max_alpha):
    """中心から減衰する円形グラデーション層（近似）。"""
    size = 256
    half = size // 2
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    for i in range(half, 0, -1):
        a = int(max_alpha * (i / half) ** 1.6)
        d.ellipse([half - i, half - i, half + i, half + i], fill=a)
    m = m.resize((max(1, int(radius * 2)), max(1, int(radius * 2))), Image.Resampling.BILINEAR)
    tint = Image.new("RGBA", m.size, (rgb[0], rgb[1], rgb[2], 255))
    tint.putalpha(m)
    layer = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    layer.alpha_composite(tint, (int(cx - radius), int(cy - radius)))
    return layer


def _star_polygon(cx, cy, r):
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rad = r if i % 2 == 0 else r * 0.42
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    return pts


def _spaced_text(draw, text, x, y, font, fill, spacing, anchor="left", design=True):
    """letter-spacing 付きテキスト。返り値は描画幅（design px）。"""
    s = str(text)
    widths = [draw.textlength(ch, font=font) / (THEME_SX if design else 1) for ch in s]
    total = sum(widths) + spacing * max(0, len(s) - 1)
    cx = x
    if anchor == "center":
        cx = x - total / 2
    elif anchor == "right":
        cx = x - total
    for ch, cw in zip(s, widths):
        draw_figma_text(draw, text=ch, x=cx, y=y, font=font, fill_color=fill)
        cx += cw + spacing
    return total


def _truncate(draw, text, font, max_w):
    s = str(text)
    if draw.textlength(s, font=font) / THEME_SX <= max_w:
        return s
    while s and draw.textlength(s + "…", font=font) / THEME_SX > max_w:
        s = s[:-1]
    return (s + "…") if s else "…"


def _fit_text(draw, text, sizes, max_w):
    """max_w に収まるようフォントを縮小し、収まらなければ省略。返り値 (text, font)。"""
    s = str(text)
    for sz in sizes:
        f = _font(sz)
        if draw.textlength(s, font=f) / THEME_SX <= max_w:
            return s, f
    f = _font(sizes[-1])
    return _truncate(draw, s, f, max_w), f


def _paste_rounded(img, path, x, y, w, h, radius, beta="false", alpha=1.0):
    """角丸マスク付きペースト（design 座標）。radius=辺の半分なら円形。"""
    path = resolve_datas_path(path, beta)
    if not path or not os.path.exists(path):
        return
    try:
        bx, by = int(round(x * THEME_SX)), int(round(y * THEME_SY))
        bw, bh = max(1, round(w * THEME_SX)), max(1, round(h * THEME_SY))
        src = get_resized_image(path, (bw, bh))
        if src is None:
            return
        if src.mode != "RGBA":
            src = src.convert("RGBA")
        if alpha < 1.0:
            a = src.getchannel("A").point(lambda p: int(p * alpha))
            src.putalpha(a)
        mask = Image.new("L", (bw, bh), 0)
        safe_rounded_rectangle(ImageDraw.Draw(mask), [0, 0, bw, bh], radius=max(1, round(radius * THEME_SY)), fill=255)
        r, g, b, a = src.split()
        src = Image.merge("RGBA", (r, g, b, ImageChops.multiply(a, mask)))
        img.paste(src, (bx, by), src)
    except Exception as e:
        print(f"[Error] theme paste failed: {path}: {e}", flush=True)


def _dot_row(img, x, y, tiers, dot_w, dot_h, gap):
    """サブステ伸び値ドット列（design 座標）。tiers: 0-3 のリスト。"""
    tiers = [t for t in (tiers or []) if 0 <= int(t) < 4]
    if not tiers:
        return
    d = ImageDraw.Draw(img)
    cx = x * THEME_SX
    cy = y * THEME_SY
    dw, dh, gp = dot_w * THEME_SX, dot_h * THEME_SY, gap * THEME_SX
    # PIL の部分角丸(corners指定)は半径 r+1 の余裕が必要なため -1 する
    # （draw_figma_dot と同じ半径計算）
    rad = max(1, int(min(dw, dh) // 2) - 1)
    for i, t in enumerate(tiers):
        col = ROLL_DOT_COLORS[int(t)]
        if len(tiers) == 1:
            corners = (True, True, True, True)
        elif i == 0:
            corners = (True, False, False, True)
        elif i == len(tiers) - 1:
            corners = (False, True, True, False)
        else:
            corners = (False, False, False, False)
        safe_rounded_rectangle(d, [cx, cy, cx + dw, cy + dh], radius=rad, fill=col, corners=corners)
        cx += dw + gp


def _circle_layer(img, cx, cy, r, fill_color, outline_color=None, outline_width=0):
    """半透明円をアルファ合成で描く（design 座標 cx/cy=中心, r=半径）。"""
    pad = max(2, outline_width + 1)
    x0 = int((cx - r) * THEME_SX) - pad
    y0 = int((cy - r) * THEME_SY) - pad
    x1 = int((cx + r) * THEME_SX) + pad
    y1 = int((cy + r) * THEME_SY) + pad
    lay = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    ImageDraw.Draw(lay).ellipse(
        [cx * THEME_SX - r * THEME_SX - x0, cy * THEME_SY - r * THEME_SY - y0,
         cx * THEME_SX + r * THEME_SX - x0, cy * THEME_SY + r * THEME_SY - y0],
        fill=fill_color, outline=outline_color,
        width=max(1, outline_width) if outline_width else 0,
    )
    img.alpha_composite(lay, dest=(x0, y0))


def _round_card(img, radius):
    """カード全体を角丸で抜く（外側を透明に）。"""
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    safe_rounded_rectangle(ImageDraw.Draw(mask), [0, 0, w, h], radius=max(1, round(radius * THEME_SCALE)), fill=255)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _gradient_strip_horizontal(w, h, rgb_from, rgb_to):
    """横方向 RGB 補間（グラデーションバー用）。"""
    n = max(2, int(w))
    strip = Image.new("RGB", (n, 1))
    px = strip.load()
    for x in range(n):
        t = x / (n - 1)
        px[x, 0] = tuple(int(rgb_from[i] + (rgb_to[i] - rgb_from[i]) * t) for i in range(3))
    return strip.resize((int(w), max(1, int(h))), Image.Resampling.BILINEAR).convert("RGBA")


# ================================================================
#  CINEMA（1200 x 780）
# ================================================================
def _draw_cinema_card(data, beta, substat_dots, light="false", uid="", show_uid="false", lang="ja"):
    W, H = THEME_DESIGN_W, CINEMA_DESIGN_H
    pal = _palette(_CINE_PALETTE, light)
    img = Image.new("RGBA", (THEME_W, int(H * THEME_SCALE)), pal["bg"])

    elem = data.get("element") or "None"
    elem_rgb = _ELEM_ACCENT.get(elem, _ELEM_ACCENT["None"])
    elem_acc_rgb = _mix_rgb(elem_rgb, (15, 23, 42), pal["elem_mix"])
    elem_rgba = (*elem_acc_rgb, 255)
    gold = pal["gold"]

    with figma_draw_scale(THEME_SX, THEME_SY):
        draw = ImageDraw.Draw(img)

        # 背景: スプラッシュ全画面（center 22% / scale 1.04 相当）
        splash = data.get("splash") or ""
        if splash and os.path.exists(resolve_datas_path(splash, beta)):
            paste_mask_image(img, splash, box_x=0, box_y=0, box_width=W, box_height=H,
                             radius=0, zoom=1.04, beta=beta, offset=(0, 56))

        # 可読性スクリーン（左→右 / 下→上 の暗転。ライトでは白転）
        scrim = _hgrad(W * THEME_SX, H * THEME_SY, pal["scrim"],
                       [(0.0, 224), (0.30, 140), (0.55, 31), (1.0, 77)])
        img.alpha_composite(scrim)
        bottom = _vgrad(W * THEME_SX, H * THEME_SY, pal["scrim"],
                        [(0.48, 0), (0.74, 140), (1.0, 240)])
        img.alpha_composite(bottom)
        # 元素ティント（左下のほのかな発色。soft-light の近似）
        tint = _radial_layer(W * THEME_SX, H * THEME_SY,
                             int(W * 0.20 * THEME_SX), int(H * 1.0 * THEME_SY),
                             int(W * 0.75 * THEME_SX), elem_rgb, 46)
        img.alpha_composite(tint)
        draw = ImageDraw.Draw(img)

        # ---- 左上: 元素チップ + レアリティ + 名前 + メタ ----
        x0, y0 = 44, 40
        elem_ja = (str(elem) if str(elem) != "None" else "None") if lang == "en" else _ELEM_JA.get(elem, "無")
        chip_font = _font(13)
        chip_txt_w = draw.textlength(elem_ja, font=chip_font) / THEME_SX
        chip_w = 13 + 18 + 7 + chip_txt_w + 13
        chip_h = 28
        draw_figma_box(img, x=x0, y=y0, width=chip_w, height=chip_h, radius=chip_h / 2,
                       fill_color=pal["chip_fill"], outline_color=pal["glass_brd"],
                       outline_width=1, shadow=False)
        _icon_back(img, pal, x0 + 11, y0 + 3, 22, 22, 6)
        _paste_rounded(img, f"static/assets/props/{str(elem).lower()}.png",
                       x0 + 13, y0 + 5, 18, 18, 4, beta)
        draw_figma_text(draw, text=elem_ja, x=x0 + 13 + 18 + 7, y=y0 + 7,
                        font=chip_font, fill_color=pal["txt"])

        rarity = 4 if int(data.get("rarity") or 5) == 4 else 5
        sx = x0 + chip_w + 10
        for i in range(rarity):
            cx, cy = sx + i * 17 + 7.5, y0 + chip_h / 2
            draw.polygon(_star_polygon(cx * THEME_SX, cy * THEME_SY, 7.5 * THEME_SX),
                         fill=gold)

        disp = str(data.get("displayName") or "")
        swap = disp.endswith("(swap)")
        name_txt = disp[: -len("(swap)")] if swap else disp
        name_font = _font(64)
        name_y = y0 + chip_h + 14
        draw_figma_text(draw, text=name_txt, x=x0 + 2, y=name_y + 3,
                        font=name_font, fill_color=pal["name_shadow"])
        draw_figma_text(draw, text=name_txt, x=x0, y=name_y, font=name_font, fill_color=pal["txt"])
        if swap:
            swap_font = _font(42)
            nw = draw.textlength(name_txt, font=name_font) / THEME_SX
            draw_figma_text(draw, text="(swap)", x=x0 + nw + 10, y=name_y + 20,
                            font=swap_font, fill_color=_dim(pal["txt"], 0.66))

        meta_y = name_y + 66 + 16
        meta_font = _font(15)
        lv_font = _font(22)
        mx = x0
        draw_figma_text(draw, text=f"Lv.{data.get('level', '?')}", x=mx, y=meta_y,
                        font=lv_font, fill_color=pal["txt"])
        mx += draw.textlength(f"Lv.{data.get('level', '?')}", font=lv_font) / THEME_SX + 10
        draw.line([(mx * THEME_SX, (meta_y + 4) * THEME_SY), (mx * THEME_SX, (meta_y + 20) * THEME_SY)],
                  fill=pal["meta_sep"], width=max(1, round(THEME_SY)))
        mx += 11
        friendship = data.get("friendship")
        if friendship is not None:
            draw_figma_text(draw, text="♥", x=mx, y=meta_y + 2, font=meta_font,
                            fill_color=pal["heart"])
            mx += 19
            draw_figma_text(draw, text=str(friendship), x=mx, y=meta_y + 2,
                            font=meta_font, fill_color=_dim(pal["txt"], 0.66))
            mx += draw.textlength(str(friendship), font=meta_font) / THEME_SX + 10
            draw.line([(mx * THEME_SX, (meta_y + 4) * THEME_SY), (mx * THEME_SX, (meta_y + 20) * THEME_SY)],
                      fill=pal["meta_sep"], width=max(1, round(THEME_SY)))
            mx += 11
        const_n = int(data.get("constellation") or 0)
        const_label = ("C6" if const_n >= 6 else f"C{const_n}") if lang == "en" else ("完凸" if const_n >= 6 else f"C{const_n}")
        draw_figma_text(draw, text=const_label, x=mx, y=meta_y + 2,
                        font=meta_font, fill_color=_dim(pal["txt"], 0.66))

        # ---- 左レール: 天賦 / 命ノ星座 ----
        rail_x, rail_y = 44, 230
        label_font = _font(11)
        _spaced_text(draw, "TALENTS", rail_x, rail_y, label_font, _dim(pal["txt"], 0.42), 3.1)
        by = rail_y + 15 + 9
        skills = data.get("skills") or []
        for i in range(3):
            s = skills[i] if i < len(skills) else {}
            bx = rail_x + i * (46 + 9)
            draw_figma_box(img, x=bx, y=by, width=46, height=46, radius=14,
                           fill_color=pal["icon_fill"], outline_color=pal["icon_brd"],
                           outline_width=1, shadow=False)
            if s.get("icon"):
                _paste_rounded(img, s["icon"], bx + 8, by + 8, 30, 30, 6, beta)
            lv = s.get("level", 1)
            boosted = bool(s.get("boosted"))
            lv_font_s = _font(10)
            ltxt = str(lv)
            lw = draw.textlength(ltxt, font=lv_font_s) / THEME_SX + 12
            lh = 15
            lx = bx + (46 - lw) / 2
            ly = by + 46 - 7
            draw_figma_box(img, x=lx, y=ly, width=lw, height=lh, radius=7,
                           fill_color=pal["lv_chip_fill"],
                           outline_color=pal["boost_brd"] if boosted else pal["lv_chip_brd"],
                           outline_width=1, shadow=False)
            draw_figma_text(draw, text=ltxt, x=lx, y=ly + 2, font=lv_font_s,
                            align="center", box_width=lw,
                            fill_color=pal["boost"] if boosted else pal["lv_chip_txt"])

        const_icons = data.get("constellationIcons") or []
        if const_icons:
            cy0 = by + 46 + 22
            _spaced_text(draw, "CONSTELLATION", rail_x, cy0, label_font, _dim(pal["txt"], 0.42), 3.1)
            iy = cy0 + 15 + 9
            for i in range(6):
                on = i < const_n
                cx = rail_x + i * (34 + 8)
                fill_col = (*[int(v * 0.35 + 8) for v in elem_rgb], 200) if on else pal["const_off_fill"]
                brd = elem_rgba if on else pal["const_off_brd"]
                _circle_layer(img, cx + 17, iy + 17, 17, fill_col, brd,
                              outline_width=max(1, round(1.5 * THEME_SY)))
                icon = const_icons[i] if i < len(const_icons) else ""
                if icon:
                    _paste_rounded(img, icon, cx + 6.5, iy + 6.5, 21, 21, 5, beta,
                                   alpha=1.0 if on else 0.38)

        # ---- 右カラム: 武器 / スコア / セットチップ ----
        rc_w, rc_x = 300, W - 40 - 300
        ry = 40
        panel_fill = pal["glass"]
        panel_brd = pal["glass_brd"]

        # 武器パネル
        wp_h = 96
        draw_figma_box(img, x=rc_x, y=ry, width=rc_w, height=wp_h, radius=20,
                       fill_color=panel_fill, outline_color=panel_brd, outline_width=1, shadow=False)
        if data.get("weaponIcon"):
            draw_figma_box(img, x=rc_x + 16, y=ry + 16, width=64, height=64, radius=14,
                           fill_color=pal["icon_box_fill"], outline_color=pal["icon_box_brd"],
                           outline_width=1, shadow=False)
            _paste_rounded(img, data["weaponIcon"], rc_x + 16, ry + 16, 64, 64, 14, beta)
        wname_font = _font(17)
        wsub_font = _font(12)
        wname = str(data.get("weaponName") or "")
        wbody_x = rc_x + 16 + 64 + 14
        wbody_w = rc_w - (16 + 64 + 14) - 62 - 16
        lines = []
        if " " in wname:
            # 英語等の空白を含む名前は単語単位で折り返す（単語中で分断されないように）
            cur = ""
            for word in wname.split(" "):
                cand = f"{cur} {word}" if cur else word
                if draw.textlength(cand, font=wname_font) / THEME_SX <= wbody_w or not cur:
                    cur = cand
                else:
                    lines.append(cur)
                    cur = word
                    if len(lines) == 2:
                        break
            if cur and len(lines) < 2:
                lines.append(cur)
        else:
            cur = ""
            for ch in wname:
                if draw.textlength(cur + ch, font=wname_font) / THEME_SX <= wbody_w:
                    cur += ch
                else:
                    lines.append(cur)
                    cur = ch
                    if len(lines) == 2:
                        break
            if cur and len(lines) < 2:
                lines.append(cur)
        if "".join(lines) != wname:
            lines = lines[:2] or [""]
            t = lines[-1]
            while t and draw.textlength(t + "…", font=wname_font) / THEME_SX > wbody_w:
                t = t[:-1]
            lines[-1] = t + "…"
        wy = ry + (24 if len(lines) > 1 else 26)
        for ln in lines:
            draw_figma_text(draw, text=ln, x=wbody_x, y=wy, font=wname_font, fill_color=pal["txt"])
            wy += 21
        wstats = data.get("weaponStats") or []
        if wstats:
            wsub_lines = [f"{wstats[0].get('name','')} {wstats[0].get('value','')}"]
            if len(wstats) > 1:
                wsub_lines.append((" · " if lang == "en" else " ・ ").join(f"{w.get('name','')} {w.get('value','')}" for w in wstats[1:]))
            wsy = wy + 2
            for ln in wsub_lines:
                draw_figma_text(draw, text=_truncate(draw, ln, wsub_font, wbody_w),
                                x=wbody_x, y=wsy, font=wsub_font, fill_color=_dim(pal["txt"], 0.66))
                wsy += 14
        wr_font = _font(12)
        wr_x = rc_x + rc_w - 16
        draw_figma_text(draw, text=f"Lv.{data.get('weaponLevel', '?')}", x=wr_x, y=ry + 24,
                        font=wr_font, align="right", fill_color=_dim(pal["txt"], 0.42))
        draw_figma_text(draw, text=f"R{data.get('weaponAffix') or 1}", x=wr_x, y=ry + 44,
                        font=_font(15), align="right", fill_color=gold)

        # スコアパネル
        ry2 = ry + wp_h + 14
        sp_h = 100
        draw_figma_box(img, x=rc_x, y=ry2, width=rc_w, height=sp_h, radius=20,
                       fill_color=panel_fill, outline_color=panel_brd, outline_width=1, shadow=False)
        tier_sum = data.get("tierSum") or "B"
        _paste_rounded(img, f"static/assets/tiers/{tier_sum}.png", rc_x + 20, ry2 + 18, 64, 64, 8, beta)
        _spaced_text(draw, "TOTAL SCORE", rc_x + 20 + 64 + 16, ry2 + 20,
                     _font(11), _dim(pal["txt"], 0.42), 2.2)
        score_sum = float(data.get("scoreSum") or 0)
        draw_figma_text(draw, text=f"{score_sum:.1f}", x=rc_x + 20 + 64 + 16, y=ry2 + 34,
                        font=_font(42), fill_color=pal["txt"])
        draw_figma_text(draw, text=str(data.get("calcMethodLabel") or ""), x=rc_x + 20 + 64 + 16,
                        y=ry2 + 80, font=wsub_font, fill_color=_dim(pal["txt"], 0.66))

        # セットチップ（右揃え縦積み）
        chips = []
        for s in data.get("setBonuses") or []:
            chips.append({"icon": "", "name": str(s.get("name") or ""), "count": f"×{s.get('count')}"})
        cy = ry2 + sp_h + 14
        chip_font = _font(12)
        for c in chips:
            txt = c["name"]
            txt_w = draw.textlength(txt, font=chip_font) / THEME_SX
            cnt_w = draw.textlength(c["count"], font=chip_font) / THEME_SX if c["count"] else 0
            icon_w = 21 if c["icon"] else 0
            cw = 12 + icon_w + txt_w + (6 + cnt_w if cnt_w else 0) + 12
            ch = 28
            cx = rc_x + rc_w - cw
            draw_figma_box(img, x=cx, y=cy, width=cw, height=ch, radius=ch / 2,
                           fill_color=pal["chip_fill"], outline_color=pal["glass_brd"],
                           outline_width=1, shadow=False)
            tx = cx + 12
            if c["icon"]:
                _icon_back(img, pal, tx - 2, cy + 5, 18, 18, 5)
                _paste_rounded(img, c["icon"], tx, cy + 7, 14, 14, 3, beta)
                tx += 21
            draw_figma_text(draw, text=txt, x=tx, y=cy + 7, font=chip_font, fill_color=pal["txt"])
            tx += txt_w
            if cnt_w:
                draw_figma_text(draw, text=c["count"], x=tx + 6, y=cy + 7,
                                font=chip_font, fill_color=elem_rgba)
            cy += ch + 6

        # 共鳴チップ（聖遺物行とカード下端の隙間・右揃え横並び。カード種類で位置を統一）
        reso = []
        for b in data.get("resonanceBadges") or []:
            reso.append({"icon": f"static/assets/props/{str(b.get('elem') or '').lower()}.png",
                         "name": str(b.get("text") or ""), "count": str(b.get("label") or "")})
        if reso:
            r_font = _font(12)
            r_right = W - 40
            r_h = 28
            r_gap_x = 6
            # 下端の隙間: 聖遺物行の下端 (H-34) 〜 カード下端 (H) = 34px
            r_y = H - 34 + (34 - r_h) / 2
            dims = []
            for c in reso:
                txt_w = draw.textlength(c["name"], font=r_font) / THEME_SX
                cnt_w = draw.textlength(c["count"], font=r_font) / THEME_SX if c["count"] else 0
                icon_w = 21 if c["icon"] else 0
                cw = 12 + icon_w + txt_w + (6 + cnt_w if cnt_w else 0) + 12
                dims.append((c, txt_w, cnt_w, cw))
            total_w = sum(cw for *_, cw in dims) + r_gap_x * (len(dims) - 1)
            cx = r_right - total_w
            for c, txt_w, cnt_w, cw in dims:
                draw_figma_box(img, x=cx, y=r_y, width=cw, height=r_h, radius=r_h / 2,
                               fill_color=pal["chip_fill"], outline_color=pal["glass_brd"],
                               outline_width=1, shadow=False)
                tx = cx + 12
                if c["icon"]:
                    _icon_back(img, pal, tx - 2, r_y + 5, 18, 18, 5)
                    _paste_rounded(img, c["icon"], tx, r_y + 7, 14, 14, 3, beta)
                    tx += 21
                draw_figma_text(draw, text=c["name"], x=tx, y=r_y + 7, font=r_font, fill_color=pal["txt"])
                tx += txt_w
                if cnt_w:
                    draw_figma_text(draw, text=c["count"], x=tx + 6, y=r_y + 7,
                                    font=r_font, fill_color=elem_rgba)
                cx += cw + r_gap_x

        # UID 表示（聖遺物行とカード下端の隙間・左下。共鳴チップと同じ高さ）
        if show_uid == "true" and uid:
            u_font = _font(12)
            u_text = f"UID {uid}"
            u_h = 28
            u_y = H - 34 + (34 - u_h) / 2
            draw_figma_text(draw, text=u_text, x=40, y=u_y + 7, font=u_font,
                            fill_color=_dim(pal["txt"], 0.7))

        # ---- ステータス: 4列 x 2行 ----
        st_left, st_right = 44, W - 40
        grid_w = st_right - st_left
        gap = 10
        cell_w = (grid_w - gap * 3) / 4
        cell_h = 66
        stats = data.get("mainStats") or []
        stats_top = H - 246 - (cell_h * 2 + gap)
        for idx, s in enumerate(stats[:8]):
            row, col = divmod(idx, 4)
            cx = st_left + col * (cell_w + gap)
            cy = stats_top + row * (cell_h + gap)
            draw_figma_box(img, x=cx, y=cy, width=cell_w, height=cell_h, radius=14,
                           fill_color=pal["cell_fill"], outline_color=pal["cell_brd"],
                           outline_width=1, shadow=False)
            k_font = _font(11)
            v_font = _font(19)
            label = str(s.get("label") or "")
            if s.get("icon"):
                _icon_back(img, pal, cx + 11, cy + 10, 19, 19, 5)
                _paste_rounded(img, s["icon"], cx + 13, cy + 12, 15, 15, 3, beta)
            draw_figma_text(draw, text=_truncate(draw, label, k_font, cell_w - 13 - 19 - 13),
                            x=cx + 13 + 21, y=cy + 13, font=k_font,
                            fill_color=_dim(pal["txt"], 0.66))
            is_crit = label in ("会心率", "会心ダメージ", "CRIT Rate", "CRIT DMG")
            draw_figma_text(draw, text=str(s.get("val") or ""), x=cx + 13, y=cy + 33,
                            font=v_font, fill_color=elem_rgba if is_crit else pal["txt"])

        # ---- 聖遺物フィルムストリップ: 5枚 ----
        a_left, a_right = 40, W - 40
        a_gap = 12
        aw = (a_right - a_left - a_gap * 4) / 5
        ah = 195
        ay = H - 34 - ah
        arts = data.get("artifacts") or []
        a_font_slot = _font(11)
        a_font_main = _font(13)
        a_font_sub = _font(11.5)
        for i in range(5):
            ax = a_left + i * (aw + a_gap)
            art = arts[i] if i < len(arts) else None
            draw_figma_box(img, x=ax, y=ay, width=aw, height=ah, radius=18,
                           fill_color=pal["art_fill"], outline_color=pal["art_brd"],
                           outline_width=1, shadow=False)
            if not art:
                draw_figma_text(draw, text=img_t("未装備", lang), x=ax, y=ay + ah / 2 - 8, font=a_font_main,
                                align="center", box_width=aw, fill_color=_dim(pal["txt"], 0.3))
                continue
            icon_x, icon_y = ax + 14, ay + 13
            _paste_rounded(img, art.get("icon") or "", icon_x, icon_y, 42, 42, 10, beta)
            slot = str(art.get("slot") or "")
            main = art.get("main") or {}
            # テキスト開始(14+42+10)からティアバッジ左端(14+26)までの利用可能幅
            txt_max_w = aw - (14 + 42 + 10) - (14 + 26) - 2
            draw_figma_text(draw, text=_truncate(draw, slot, a_font_slot, txt_max_w),
                            x=icon_x + 42 + 10, y=ay + 14, font=a_font_slot,
                            fill_color=_dim(pal["txt"], 0.42))
            main_txt = f"{main.get('name', '')} {main.get('value', '')}"
            main_txt, main_font = _fit_text(draw, main_txt, (13, 12, 11, 10.5, 10), txt_max_w)
            draw_figma_text(draw, text=main_txt,
                            x=icon_x + 42 + 10, y=ay + 29, font=main_font, fill_color=pal["txt"])
            _paste_rounded(img, f"static/assets/tiers/{art.get('tier') or 'B'}.png",
                           ax + aw - 14 - 26, ay + 16, 26, 26, 4, beta)
            sy0 = ay + 13 + 42 + 9
            for j, sub in enumerate((art.get("substats") or [])[:4]):
                sy = sy0 + j * 21
                nm = _truncate(draw, str(sub.get("name") or ""), a_font_sub, aw - 28 - 70)
                draw_figma_text(draw, text=nm, x=ax + 14, y=sy, font=a_font_sub,
                                fill_color=_dim(pal["txt"], 0.66))
                if substat_dots == "1":
                    nm_w = draw.textlength(nm, font=a_font_sub) / THEME_SX
                    _dot_row(img, ax + 14 + nm_w + 6, sy + 7, sub.get("rolls") or [], 11, 3.5, 2)
                draw_figma_text(draw, text=str(sub.get("value") or ""), x=ax + aw - 14, y=sy,
                                font=a_font_sub, align="right", fill_color=pal["txt"])
            fy = ay + ah - 12 - 20
            draw_figma_line(img, x1=ax + 14, y1=fy - 8, x2=ax + aw - 14, y2=fy - 8,
                            fill_color=pal["sep"], width=1)
            _spaced_text(draw, "SCORE", ax + 14, fy, _font(10), _dim(pal["txt"], 0.42), 0.8)
            draw_figma_text(draw, text=f"{float(art.get('score') or 0):.1f}", x=ax + aw - 14,
                            y=fy - 5, font=_font(16), align="right", fill_color=gold)

    return _round_card(img, 28)


# ================================================================
#  SCORECARD（1200 x 707）
# ================================================================
_SCC_H = 707
_SCC_BANNER_H = 216
_SCC_CVBAR_H = 48
_SCC_MID_TOP_PAD = 18
_SCC_MID_H = 185
_SCC_ARTS_TOP_PAD = 14
_SCC_ART_H = 200
_SCC_BOTTOM_PAD = 26


def _draw_scorecard_card(data, beta, substat_dots, light="false", uid="", show_uid="false", lang="ja"):
    W, H = THEME_DESIGN_W, _SCC_H
    pal = _palette(_SCC_PALETTE, light)
    img = Image.new("RGBA", (THEME_W, int(H * THEME_SCALE)), (*pal["bg"][1], 255))

    elem = data.get("element") or "None"
    elem_rgb = _ELEM_ACCENT.get(elem, _ELEM_ACCENT["None"])
    elem_acc_rgb = _mix_rgb(elem_rgb, (15, 23, 42), pal["elem_mix"])
    elem_rgba = (*elem_acc_rgb, 255)
    gold = pal["gold"]

    with figma_draw_scale(THEME_SX, THEME_SY):
        # カード背景グラデーション（160deg 近似: 縦グラデで代用）
        grad = Image.new("RGBA", (1, int(H * THEME_SCALE)))
        gpx = grad.load()
        c0, c1, c2 = pal["bg"]
        for y in range(int(H * THEME_SCALE)):
            t = y / max(1, int(H * THEME_SCALE) - 1)
            if t < 0.6:
                f = t / 0.6
                col = tuple(int(c0[i] + (c1[i] - c0[i]) * f) for i in range(3))
            else:
                f = (t - 0.6) / 0.4
                col = tuple(int(c1[i] + (c2[i] - c1[i]) * f) for i in range(3))
            gpx[0, y] = (col[0], col[1], col[2], 255)
        img.alpha_composite(grad.resize((THEME_W, int(H * THEME_SCALE)), Image.Resampling.BILINEAR))

        draw = ImageDraw.Draw(img)
        B = _SCC_BANNER_H

        # ---- バナー: スプラッシュ + グリッド + スクリム ----
        splash = data.get("splash") or ""
        off = data.get("splashOffset") or None
        if off is not None:
            splash_offset = (-float(off.get("x") or 0), -float(off.get("y") or 0))
        else:
            splash_offset = (0, 64)  # CSS 既定 center 18%
        if splash and os.path.exists(resolve_datas_path(splash, beta)):
            paste_mask_image(img, splash, box_x=0, box_y=0, box_width=W, box_height=B,
                             radius=0, zoom=1.03, beta=beta, offset=splash_offset)

        # グリッドライン（opacity 0.15 / 40px 格子 / 下方向フェード）
        grid = Image.new("RGBA", (THEME_W, int(B * THEME_SY)), (0, 0, 0, 0))
        gd = ImageDraw.Draw(grid)
        step = int(40 * THEME_SCALE)
        lw = max(1, round(THEME_SCALE))
        for gx in range(0, grid.width, step):
            gd.line([(gx, 0), (gx, grid.height)], fill=pal["grid"], width=lw)
        for gy in range(0, grid.height, step):
            gd.line([(0, gy), (grid.width, gy)], fill=pal["grid"], width=lw)
        fade = _vgrad(grid.width, grid.height, (0, 0, 0), [(0.0, 255), (0.9, 0), (1.0, 0)])
        ga = ImageChops.multiply(
            grid.getchannel("A"), fade.getchannel("A"))
        grid.putalpha(ga)
        img.alpha_composite(grid)

        # スクリム: 左暗→右 + 下端をカード色へ（ライトでは白転）
        scrim = _hgrad(W * THEME_SX, B * THEME_SY, pal["scrim"],
                       [(0.0, 245), (0.45, 140), (0.75, 26), (1.0, 115)])
        img.alpha_composite(scrim)
        scrim_b = _vgrad(W * THEME_SX, B * THEME_SY, pal["scrim"],
                         [(0.70, 0), (1.0, 255)])
        img.alpha_composite(scrim_b)
        draw = ImageDraw.Draw(img)

        # キャラアイコン
        icon_y = (B - 118) / 2
        draw_figma_box(img, x=44, y=icon_y, width=118, height=118, radius=24,
                       fill_color=pal["icon_box_fill"], outline_color=pal["icon_box_brd"],
                       outline_width=2, shadow=False)
        if data.get("charIcon"):
            _paste_rounded(img, data["charIcon"], 44, icon_y, 118, 118, 24, beta)

        # 名前 + チップ
        bx = 44 + 118 + 18
        name_font = _font(44)
        disp = str(data.get("displayName") or "")
        name_y = 62
        draw_figma_text(draw, text=disp, x=bx + 2, y=name_y + 3, font=name_font,
                        fill_color=pal["name_shadow"])
        draw_figma_text(draw, text=disp, x=bx, y=name_y, font=name_font, fill_color=pal["txt"])

        const_n = int(data.get("constellation") or 0)
        chips = [
            {"icon": f"static/assets/props/{str(elem).lower()}.png", "text": (str(elem) if lang == "en" else f"{_ELEM_JA.get(elem, '無')}元素"), "bold": ""},
            {"icon": "", "text": "Lv.", "bold": str(data.get("level", "?"))},
        ]
        if data.get("friendship") is not None:
            chips.append({"icon": "", "text": ("Friendship " if lang == "en" else "好感度 "), "bold": str(data["friendship"])})
        chips.append({"icon": "", "text": ("Const. " if lang == "en" else ("完凸 " if const_n >= 6 else "命星座 ")), "bold": f"C{const_n}"})
        if data.get("weaponType"):
            chips.append({"icon": "", "text": str(data["weaponType"]), "bold": ""})

        chip_font = _font(12)
        cx, cy = bx, name_y + 52
        gauge_x = W - 44 - 150
        max_cx = gauge_x - 18
        for c in chips:
            tw = draw.textlength(c["text"], font=chip_font) / THEME_SX
            bw = draw.textlength(c["bold"], font=chip_font) / THEME_SX if c["bold"] else 0
            iw = 19 if c["icon"] else 0
            cw = 11 + iw + tw + bw + 11
            if cx + cw > max_cx:
                cx = bx
                cy += 26 + 8
            ch = 26
            draw_figma_box(img, x=cx, y=cy, width=cw, height=ch, radius=9,
                           fill_color=pal["chip_fill"], outline_color=pal["chip_brd"],
                           outline_width=1, shadow=False)
            tx = cx + 11
            if c["icon"]:
                _icon_back(img, pal, tx - 2, cy + 4, 18, 18, 5)
                _paste_rounded(img, c["icon"], tx, cy + 6, 14, 14, 3, beta)
                tx += 19
            draw_figma_text(draw, text=c["text"], x=tx, y=cy + 6, font=chip_font,
                            fill_color=_dim(pal["txt"], 0.85))
            tx += tw
            if bw:
                draw_figma_text(draw, text=c["bold"], x=tx, y=cy + 6, font=chip_font,
                                fill_color=pal["chip_bold"])
            cx += cw + 8

        # 円形スコアゲージ
        gx0, gy0, gs = gauge_x, (B - 150) / 2, 150
        gcx, gcy = (gx0 + gs / 2) * THEME_SX, (gy0 + gs / 2) * THEME_SY
        gr = 65 * THEME_SCALE
        track_aw = max(1, round(10 * THEME_SCALE))
        _tpad = track_aw + 2
        _tlx0, _tly0 = int(gcx - gr) - _tpad, int(gcy - gr) - _tpad
        _tlx1, _tly1 = int(gcx + gr) + _tpad, int(gcy + gr) + _tpad
        track = Image.new("RGBA", (_tlx1 - _tlx0, _tly1 - _tly0), (0, 0, 0, 0))
        ImageDraw.Draw(track).ellipse(
            [gcx - gr - _tlx0, gcy - gr - _tly0, gcx + gr - _tlx0, gcy + gr - _tly0],
            outline=pal["gauge_track"], width=track_aw)
        img.alpha_composite(track, dest=(_tlx0, _tly0))
        score = float(data.get("scoreSum") or 0)
        frac = max(0.0, min(1.0, score / 250.0))
        if frac > 0.004:
            segs = 56
            aw_px = max(1, round(10 * THEME_SCALE))
            pad = aw_px + 2
            lx0 = int(gcx - gr) - pad
            ly0 = int(gcy - gr) - pad
            lx1 = int(gcx + gr) + pad
            ly1 = int(gcy + gr) + pad
            arc = Image.new("RGBA", (lx1 - lx0, ly1 - ly0), (0, 0, 0, 0))
            ad = ImageDraw.Draw(arc)
            bbox = [gcx - gr - lx0, gcy - gr - ly0, gcx + gr - lx0, gcy + gr - ly0]
            for si in range(segs):
                t0 = si / segs
                t1 = (si + 1) / segs
                if t0 >= frac:
                    break
                t1 = min(t1, frac)
                tt = (t0 + t1) / 2 / max(frac, 1e-6)
                col = tuple(int(elem_acc_rgb[i] + (gold[i] - elem_acc_rgb[i]) * tt) for i in range(3))
                a0 = -90 + t0 * 360
                a1 = -90 + t1 * 360 + 0.8
                ad.arc(bbox, start=a0, end=a1, fill=(*col, 255), width=aw_px)
            img.alpha_composite(arc, dest=(lx0, ly0))
        _paste_rounded(img, f"static/assets/tiers/{data.get('tierSum') or 'B'}.png",
                       gx0 + gs / 2 - 15, gy0 + gs / 2 - 40, 30, 30, 4, beta)
        draw_figma_text(draw, text=f"{score:.1f}", x=gx0, y=gy0 + gs / 2 - 2,
                        font=_font(30), align="center", box_width=gs, fill_color=pal["txt"])
        _spaced_text(draw, "TOTAL SCORE", gx0 + gs / 2, gy0 + gs / 2 + 34,
                     _font(10), pal["dim"], 1.8, anchor="center")

        # ---- Crit Value 帯 ----
        cvy = B
        cvbar = Image.new("RGBA", (THEME_W, int(_SCC_CVBAR_H * THEME_SY)), pal["cv_bar"])
        img.alpha_composite(cvbar, (0, int(cvy * THEME_SY)))
        draw_figma_line(img, x1=0, y1=cvy, x2=W, y2=cvy, fill_color=pal["cv_line"], width=1)
        draw_figma_line(img, x1=0, y1=cvy + _SCC_CVBAR_H, x2=W, y2=cvy + _SCC_CVBAR_H,
                        fill_color=pal["cv_line"], width=1)
        cv_font = _font(11)
        _spaced_text(draw, "CRIT VALUE", 44, cvy + 17, cv_font, pal["dim"], 1.5)
        crit_value = data.get("critValue")
        cv_val_txt = f"{float(crit_value):.1f}" if crit_value is not None else "—"
        draw_figma_text(draw, text=cv_val_txt, x=160, y=cvy + 12, font=_font(20), fill_color=gold)
        stats = data.get("mainStats") or []
        crit_rate = next((s.get("val") for s in stats if s.get("label") == "会心率"), "—")
        crit_dmg = next((s.get("val") for s in stats if s.get("label") == "会心ダメージ"), "—")
        note_font = _font(11)
        note = (f"CRIT Rate {crit_rate} / CRIT DMG {crit_dmg}" if lang == "en" else f"会心率 {crit_rate} ／ 会心ダメ {crit_dmg}")
        note_w = draw.textlength(note, font=note_font) / THEME_SX
        note_x = W - 44 - note_w
        draw_figma_text(draw, text=note, x=note_x, y=cvy + 18, font=note_font, fill_color=pal["dim"])
        track_x, track_w = 250, note_x - 12 - 250
        ty = cvy + _SCC_CVBAR_H / 2 - 3
        draw_figma_box(img, x=track_x, y=ty, width=track_w, height=6, radius=3,
                       fill_color=pal["cv_track"], outline_color=None, outline_width=0, shadow=False)
        if crit_value is not None:
            fill_frac = max(0.0, min(1.0, float(crit_value) / 300.0))
            if fill_frac > 0.005 and track_w > 0:
                fw = max(1, int(track_w * fill_frac * THEME_SX))
                strip = _gradient_strip_horizontal(fw, int(6 * THEME_SY), elem_acc_rgb, pal["strip_to"])
                mask = Image.new("L", (fw, int(6 * THEME_SY)), 0)
                safe_rounded_rectangle(ImageDraw.Draw(mask), [0, 0, fw, int(6 * THEME_SY)],
                                       radius=max(1, round(3 * THEME_SY)), fill=255)
                strip.putalpha(ImageChops.multiply(
                    strip.getchannel("A"), mask))
                img.alpha_composite(strip, (int(track_x * THEME_SX), int(ty * THEME_SY)))

        # ---- 中段グリッド: ステータス(2列幅) / 武器・セット / 天賦・星座 ----
        my = cvy + _SCC_CVBAR_H + _SCC_MID_TOP_PAD
        px0 = 44
        gap = 14
        col_w = (W - 88 - gap * 3) / 4
        tile_fill = pal["tile_fill"]
        tile_brd = pal["tile_brd"]
        label_font = _font(10)

        def _tile(x, w):
            draw_figma_box(img, x=x, y=my, width=w, height=_SCC_MID_H, radius=20,
                           fill_color=tile_fill, outline_color=tile_brd, outline_width=1, shadow=False)

        # Status タイル
        st_w = col_w * 2 + gap
        _tile(px0, st_w)
        _spaced_text(draw, "STATUS", px0 + 16, my + 17, label_font, pal["dim"], 1.8)
        inner_y = my + 16 + 22
        inner_w = st_w - 32
        col_gap_s = 26
        half_w = (inner_w - col_gap_s) / 2
        row_h = 27
        for idx, s in enumerate(stats[:8]):
            row, col = divmod(idx, 2)
            sx0 = px0 + 16 + col * (half_w + col_gap_s)
            sy = inner_y + row * row_h
            label = str(s.get("label") or "")
            if s.get("icon"):
                _icon_back(img, pal, sx0 - 2, sy, 19, 19, 5)
                _paste_rounded(img, s["icon"], sx0, sy + 2, 15, 15, 3, beta)
            is_crit = label in ("会心率", "会心ダメージ", "CRIT Rate", "CRIT DMG")
            draw_figma_text(draw, text=_truncate(draw, label, _font(12), half_w - 15 - 7 - 60),
                            x=sx0 + 22, y=sy + 2, font=_font(12), fill_color=pal["dim"])
            draw_figma_text(draw, text=str(s.get("val") or ""), x=sx0 + half_w, y=sy + 1,
                            font=_font(14), align="right",
                            fill_color=gold if is_crit else pal["txt"])
            if row < 3:
                draw_figma_line(img, x1=sx0, y1=sy + row_h - 3, x2=sx0 + half_w, y2=sy + row_h - 3,
                                fill_color=pal["row_sep"], width=1)

        # Weapon / Set タイル
        wx0 = px0 + st_w + gap
        _tile(wx0, col_w)
        _spaced_text(draw, "WEAPON / SET", wx0 + 16, my + 17, label_font, pal["dim"], 1.8)
        wy0 = my + 16 + 24
        iw = 56
        if data.get("weaponIcon"):
            draw_figma_box(img, x=wx0 + 16, y=wy0, width=iw, height=iw, radius=13,
                           fill_color=pal["icon_box_fill"], outline_color=pal["weapon_box_brd"],
                           outline_width=1, shadow=False)
            _paste_rounded(img, data["weaponIcon"], wx0 + 16, wy0, iw, iw, 13, beta)
        wname_font = _font(14)
        wsub_font = _font(11)
        wbody_x = wx0 + 16 + iw + 12
        wbody_w = col_w - 16 - iw - 12 - 52 - 16
        wname = _truncate(draw, str(data.get("weaponName") or ""), wname_font, wbody_w)
        draw_figma_text(draw, text=wname, x=wbody_x, y=wy0 + 6, font=wname_font, fill_color=pal["txt"])
        wstats = data.get("weaponStats") or []
        if wstats:
            wsub_lines = [f"{wstats[0].get('name','')} {wstats[0].get('value','')}"]
            if len(wstats) > 1:
                wsub_lines.append((" · " if lang == "en" else " ・ ").join(f"{w.get('name','')} {w.get('value','')}" for w in wstats[1:]))
            wsy = wy0 + 27
            for ln in wsub_lines:
                draw_figma_text(draw, text=_truncate(draw, ln, wsub_font, wbody_w),
                                x=wbody_x, y=wsy, font=wsub_font, fill_color=pal["dim"])
                wsy += 14
        draw_figma_text(draw, text=f"Lv.{data.get('weaponLevel', '?')}", x=wx0 + col_w - 16,
                        y=wy0 + 8, font=wname_font, align="right", fill_color=pal["txt"])
        draw_figma_text(draw, text=f"R{data.get('weaponAffix') or 1}", x=wx0 + col_w - 16,
                        y=wy0 + 29, font=_font(12), align="right", fill_color=gold)
        set_y = wy0 + iw + 10
        sets = data.get("setBonuses") or []
        if sets:
            for s in sets[:2]:
                draw_figma_line(img, x1=wx0 + 16, y1=set_y, x2=wx0 + col_w - 16, y2=set_y,
                                fill_color=pal["sep"], width=1)
                set_y += 8
                cnt = f"×{s.get('count')}"
                draw_figma_text(draw, text=cnt, x=wx0 + 16, y=set_y, font=_font(12),
                                fill_color=elem_rgba)
                cnt_w = draw.textlength(cnt, font=_font(12)) / THEME_SX
                nm = _truncate(draw, str(s.get("name") or ""), _font(12), col_w - 32 - cnt_w - 7 - 90)
                draw_figma_text(draw, text=nm, x=wx0 + 16 + cnt_w + 7, y=set_y,
                                font=_font(12), fill_color=pal["txt"])
                if s.get("buff"):
                    draw_figma_text(draw, text=_truncate(draw, str(s["buff"]), _font(10.5), 88),
                                    x=wx0 + col_w - 16, y=set_y + 1, font=_font(10.5),
                                    align="right", fill_color=pal["dim"])
                set_y += 22
        else:
            draw_figma_line(img, x1=wx0 + 16, y1=set_y, x2=wx0 + col_w - 16, y2=set_y,
                            fill_color=pal["sep"], width=1)
            draw_figma_text(draw, text=img_t("セット効果なし", lang), x=wx0 + 16, y=set_y + 8,
                            font=_font(12), fill_color=_dim(pal["txt"], 0.35))

        # Talents / Constellation タイル
        tx0 = wx0 + col_w + gap
        _tile(tx0, col_w)
        _spaced_text(draw, "TALENTS", tx0 + 16, my + 17, label_font, pal["dim"], 1.8)
        ty0 = my + 16 + 26
        skills = data.get("skills") or []
        for i in range(3):
            s = skills[i] if i < len(skills) else {}
            bxx = tx0 + 16 + i * (44 + 8)
            draw_figma_box(img, x=bxx, y=ty0, width=44, height=44, radius=13,
                           fill_color=pal["talent_fill"], outline_color=pal["talent_brd"],
                           outline_width=1, shadow=False)
            if s.get("icon"):
                _paste_rounded(img, s["icon"], bxx + 8, ty0 + 8, 28, 28, 6, beta)
            lv = str(s.get("level", 1))
            boosted = bool(s.get("boosted"))
            lv_font_s = _font(10)
            lw = draw.textlength(lv, font=lv_font_s) / THEME_SX + 10
            lx = bxx + (44 - lw) / 2
            ly = ty0 + 44 - 6
            draw_figma_box(img, x=lx, y=ly, width=lw, height=14, radius=6,
                           fill_color=pal["lv_chip_fill"],
                           outline_color=pal["boost_brd"] if boosted else pal["lv_chip_brd"],
                           outline_width=1, shadow=False)
            draw_figma_text(draw, text=lv, x=lx, y=ly + 2, font=lv_font_s, align="center",
                            box_width=lw,
                            fill_color=pal["boost"] if boosted else pal["lv_chip_txt"])

        const_icons = data.get("constellationIcons") or []
        if const_icons:
            clabel_y = my + _SCC_MID_H - 16 - 14 - 32 - 10
            _spaced_text(draw, "CONSTELLATION", tx0 + 16, clabel_y, label_font, pal["dim"], 1.8)
            const_label = ("C6" if const_n >= 6 else f"C{const_n}") if lang == "en" else ("完凸" if const_n >= 6 else f"C{const_n}")
            draw_figma_text(draw, text=const_label, x=tx0 + col_w - 16, y=clabel_y,
                            font=_font(11), align="right", fill_color=gold)
            ciy = clabel_y + 22
            for i in range(6):
                on = i < const_n
                ccx = tx0 + 16 + i * (32 + 7)
                fill_col = (*[int(v * 0.35 + 10) for v in elem_rgb], 210) if on else pal["const_off_fill"]
                brd = elem_rgba if on else pal["const_off_brd"]
                _circle_layer(img, ccx + 16, ciy + 16, 16, fill_col, brd,
                              outline_width=max(1, round(1.5 * THEME_SCALE)))
                icon = const_icons[i] if i < len(const_icons) else ""
                if icon:
                    _paste_rounded(img, icon, ccx + 6, ciy + 6, 20, 20, 5, beta,
                                   alpha=1.0 if on else 0.38)

        # ---- 聖遺物グリッド ----
        ay0 = my + _SCC_MID_H + _SCC_ARTS_TOP_PAD
        a_gap = 12
        aw = (W - 88 - a_gap * 4) / 5
        arts = data.get("artifacts") or []
        best_idx, best_score = -1, -1.0
        for i, a in enumerate(arts):
            sc = float((a or {}).get("score") or 0) if a else -1.0
            if sc > best_score:
                best_score, best_idx = sc, i
        a_font_slot = _font(10.5)
        a_font_main = _font(12.5)
        a_font_sub = _font(11)
        for i in range(5):
            ax = 44 + i * (aw + a_gap)
            art = arts[i] if i < len(arts) else None
            best = (i == best_idx)
            draw_figma_box(img, x=ax, y=ay0, width=aw, height=_SCC_ART_H, radius=18,
                           fill_color=tile_fill,
                           outline_color=pal["best_brd"] if best else tile_brd,
                           outline_width=2 if best else 1, shadow=False)
            if not art:
                draw_figma_text(draw, text=img_t("未装備", lang), x=ax, y=ay0 + _SCC_ART_H / 2 - 8,
                                font=a_font_main, align="center", box_width=aw,
                                fill_color=_dim(pal["txt"], 0.3))
                continue
            _paste_rounded(img, art.get("icon") or "", ax + 13, ay0 + 12, 40, 40, 10, beta)
            slot = str(art.get("slot") or "")
            main = art.get("main") or {}
            # テキスト開始(13+40+9)からティアバッジ左端(13+24)までの利用可能幅
            txt_max_w = aw - (13 + 40 + 9) - (13 + 24) - 2
            draw_figma_text(draw, text=_truncate(draw, slot, a_font_slot, txt_max_w),
                            x=ax + 13 + 40 + 9, y=ay0 + 13, font=a_font_slot, fill_color=pal["dim"])
            main_txt = f"{main.get('name', '')} {main.get('value', '')}"
            main_txt, main_font = _fit_text(draw, main_txt, (12.5, 12, 11, 10.5, 10), txt_max_w)
            draw_figma_text(draw, text=main_txt, x=ax + 13 + 40 + 9, y=ay0 + 27,
                            font=main_font, fill_color=pal["txt"])
            _paste_rounded(img, f"static/assets/tiers/{art.get('tier') or 'B'}.png",
                           ax + aw - 13 - 24, ay0 + 15, 24, 24, 4, beta)
            sy0 = ay0 + 12 + 40 + 9
            for j, sub in enumerate((art.get("substats") or [])[:4]):
                sy = sy0 + j * 20
                nm = _truncate(draw, str(sub.get("name") or ""), a_font_sub, aw - 26 - 64)
                draw_figma_text(draw, text=nm, x=ax + 13, y=sy, font=a_font_sub, fill_color=pal["dim"])
                if substat_dots == "1":
                    nm_w = draw.textlength(nm, font=a_font_sub) / THEME_SX
                    _dot_row(img, ax + 13 + nm_w + 5, sy + 6, sub.get("rolls") or [], 10, 3, 2)
                draw_figma_text(draw, text=str(sub.get("value") or ""), x=ax + aw - 13, y=sy,
                                font=a_font_sub, align="right", fill_color=pal["txt"])
            fy = ay0 + _SCC_ART_H - 11 - 19
            draw_figma_line(img, x1=ax + 13, y1=fy - 7, x2=ax + aw - 13, y2=fy - 7,
                            fill_color=pal["art_sep"], width=1)
            _spaced_text(draw, "SCORE", ax + 13, fy, _font(9.5), pal["dim"], 0.8)
            draw_figma_text(draw, text=f"{float(art.get('score') or 0):.1f}", x=ax + aw - 13,
                            y=fy - 4, font=_font(15), align="right", fill_color=gold)

        # 共鳴チップ（聖遺物行とカード下端の隙間・右揃え横並び。カード種類で位置を統一）
        reso = []
        for b in data.get("resonanceBadges") or []:
            reso.append({"icon": f"static/assets/props/{str(b.get('elem') or '').lower()}.png",
                         "text": f"{b.get('text', '')} ", "bold": str(b.get("label") or "")})
        if reso:
            r_font = _font(11)
            r_right = W - 44
            r_h = 22
            r_gap_x = 6
            # 下端の隙間: 聖遺物行の下端 (H-26) 〜 カード下端 (H) = 26px
            r_y = H - 26 + (26 - r_h) / 2
            dims = []
            for c in reso:
                tw = draw.textlength(c["text"], font=r_font) / THEME_SX
                bw = draw.textlength(c["bold"], font=r_font) / THEME_SX if c["bold"] else 0
                iw = 17 if c["icon"] else 0
                cw = 10 + iw + tw + bw + 10
                dims.append((c, tw, bw, cw))
            total_w = sum(cw for *_, cw in dims) + r_gap_x * (len(dims) - 1)
            cx = r_right - total_w
            for c, tw, bw, cw in dims:
                draw_figma_box(img, x=cx, y=r_y, width=cw, height=r_h, radius=8,
                               fill_color=pal["chip_fill"], outline_color=pal["chip_brd"],
                               outline_width=1, shadow=False)
                tx = cx + 10
                if c["icon"]:
                    _icon_back(img, pal, tx - 2, r_y + 3, 16, 16, 4)
                    _paste_rounded(img, c["icon"], tx, r_y + 4, 14, 14, 3, beta)
                    tx += 17
                draw_figma_text(draw, text=c["text"], x=tx, y=r_y + 5, font=r_font,
                                fill_color=_dim(pal["txt"], 0.85))
                tx += tw
                if bw:
                    draw_figma_text(draw, text=c["bold"], x=tx, y=r_y + 5, font=r_font,
                                    fill_color=pal["chip_bold"])
                cx += cw + r_gap_x

        # UID 表示（聖遺物行とカード下端の隙間・左下。共鳴チップと同じ高さ）
        if show_uid == "true" and uid:
            u_font = _font(11)
            u_text = f"UID {uid}"
            u_h = 22
            u_y = H - 26 + (26 - u_h) / 2
            draw_figma_text(draw, text=u_text, x=44, y=u_y + 5, font=u_font,
                            fill_color=_dim(pal["txt"], 0.6))

    return _round_card(img, 28)


# ================================================================
#  エントリポイント
# ================================================================
_THEMES = {
    "cinema": _draw_cinema_card,
    "scorecard": _draw_scorecard_card,
}


# 育成モード右パネル寸法（image.py の _GROWTH_PANEL_* と同一: 2400px キャンバス）
_THEME_GROWTH_PANEL_W = 540
_THEME_GROWTH_PANEL_GAP = 0
_THEME_GROWTH_PANEL_MARGIN = 0
# 育成パネル右側の背景色（テーマカードの地色に合わせる）
_THEME_GROWTH_BASE = {
    "cinema": (5, 7, 13),
    "scorecard": (12, 17, 32),
}
_THEME_GROWTH_BASE_LIGHT = {
    "cinema": (248, 250, 252),
    "scorecard": (241, 245, 249),
}


def _attach_growth_panel_theme(img, panel, theme, base_prec="0", light="false"):
    """テーマカード（cinema/scorecard）を等方縮小し、右側に育成パネルを追加する。

    image.py の _attach_growth_panel と同じ方針（幅は変えず、カードを縮小して
    右の余白にパネルを描く）。テーマカードは角丸＋独自背景のため、右側の地色は
    テーマごとの基本色で塗る。
    """
    card_w, card_h = img.size
    panel_w = _THEME_GROWTH_PANEL_W
    content_w = max(1200, int(round(card_w * ((card_w - panel_w - _THEME_GROWTH_PANEL_GAP - _THEME_GROWTH_PANEL_MARGIN) / card_w))))
    k = content_w / card_w
    content_h = max(1, int(round(card_h * k)))
    skinned = img.resize((content_w, content_h), Image.LANCZOS)

    base_table = _THEME_GROWTH_BASE_LIGHT if str(light or "") == "true" else _THEME_GROWTH_BASE
    base_rgb = base_table.get(theme, (9, 13, 24))
    bg_full = Image.new("RGBA", (card_w, card_h), (*base_rgb, 255))
    bg_full.paste(skinned, (0, 0), skinned)

    panel_x0 = content_w + _THEME_GROWTH_PANEL_GAP
    panel_x1 = card_w - _THEME_GROWTH_PANEL_MARGIN
    _draw_growth_panel(bg_full, panel, panel_x0, panel_x1, content_h, base_rgb, base_prec, light)
    # 縮小カード＋パネルの下端で切り抜き、下部の余白を除去
    return bg_full.crop((0, 0, card_w, content_h))


def _generate_theme_card_image_sync(uid: str, avatar_id: str, calc_method: str, theme: str,
                                    fake_char: str = None, fake_weapon: str = None,
                                    beta: str = "false", base_prec: str = "0",
                                    substat_dots: str = "1", resonance: str = None,
                                    growth: str = "false", light: str = "false",
                                    show_uid: str = "false", lang: str = "ja") -> bytes:
    """cinema / scorecard テーマのカード画像を生成して PNG bytes を返す。"""
    t_total = time.perf_counter()
    theme = theme if theme in _THEMES else "cinema"
    # 表示言語（ja / en）。_get_card_data_sync 側で名前・聖遺物等の表示名が切替わる
    lang = "en" if str(lang or "").lower() == "en" else "ja"
    if beta != "true":
        beta = "false"
    base_prec = str(base_prec or "0")
    if base_prec not in ("0", "2", "4"):
        base_prec = "0"
    substat_dots = "1" if str(substat_dots or "") in ("1", "true") else "0"
    growth = "true" if str(growth or "") == "true" else "false"
    light = "true" if str(light or "") == "true" else "false"
    show_uid = "true" if str(show_uid or "") == "true" else "false"

    print(f"[Theme] generate begin theme={theme} uid={uid} avatar={avatar_id} method={calc_method} growth={growth} light={light}", flush=True)

    t0 = time.perf_counter()
    data = _get_card_data_sync(uid, avatar_id, calc_method, fake_char, fake_weapon,
                               beta, growth, base_prec, resonance, lang=lang)
    print(f"[Perf][theme] card_data: {(time.perf_counter() - t0) * 1000:.1f}ms", flush=True)

    t0 = time.perf_counter()
    img = _THEMES[theme](data, beta, substat_dots, light, uid, show_uid, lang)
    print(f"[Perf][theme] draw({theme}): {(time.perf_counter() - t0) * 1000:.1f}ms size={img.size}", flush=True)

    if growth == "true" and data.get("growth"):
        t0 = time.perf_counter()
        img = _attach_growth_panel_theme(img, data["growth"], theme, base_prec, light)
        print(f"[Perf][theme] growth panel: {(time.perf_counter() - t0) * 1000:.1f}ms size={img.size}", flush=True)

    if img.mode != "RGBA":
        img = img.convert("RGBA")
    img_io = io.BytesIO()
    img.save(img_io, "PNG", compress_level=1)
    payload = img_io.getvalue()
    print(f"[Perf][theme] total: {(time.perf_counter() - t_total) * 1000:.1f}ms bytes={len(payload)}", flush=True)
    return payload
