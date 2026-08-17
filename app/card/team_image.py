# -*- coding: utf-8 -*-
"""編成カード画像生成（1編成 = 4キャラを1枚の横長画像にまとめる）。
画像のような行ベースレイアウト版。
"""
import io
import json
import os
import time
from PIL import Image, ImageDraw, ImageFont

from app.paths import FONT_PATH, FONT_LIGHT_PATH, STATIC_DIR, BASE_DIR
from app.card.bg import _build_base_background, hex_to_rgb, region_image_path, get_region_background
from app.card.cache import get_cached_font
from app.card.data import _get_card_data_sync
from app.card.draw import (
    draw_figma_box, draw_figma_circle, paste_mask_image, draw_figma_text_with_shadow,
    paste_figma_image, draw_figma_text, draw_figma_text_right, draw_figma_line,
    figma_draw_scale,
)

# ----------------------------------------------------------------
# レイアウト設計座標（この座標系で組み、SX_TEAM/SY_TEAM で実ピクセル化）
# コンテンツ末尾（TOTAL RATING行の下端 = 1393）＋幽境セクション分で余白を確保
# 1920 x 1625 @ scale 1.25 -> 2400 x 2031
# ----------------------------------------------------------------
TEAM_DESIGN_W, TEAM_DESIGN_H = 1920, 1625
TEAM_SCALE = 1.25
TEAM_W = int(TEAM_DESIGN_W * TEAM_SCALE)
TEAM_H = int(TEAM_DESIGN_H * TEAM_SCALE)
SX_TEAM = TEAM_W / TEAM_DESIGN_W
SY_TEAM = TEAM_H / TEAM_DESIGN_H

_MARGIN = 30
_ROW_LABEL_W = 160  # 左側行ラベル幅
# 4列 + 3ギャップが左右マージン・ラベルを除いた幅にぴったり収まるようにする
# (1920 - 30*2 - 160 - 20*3) / 4 = 410。ギャップを広げてボックス同士がくっつかないようにする
_COL_GAP = 20
_COL_W = 410
_COL_START_X = _MARGIN + _ROW_LABEL_W

# 行の高さ
_HEADER_H = 90
_IDENTITY_H = 150
_STATS_H = 200
_WEAPON_H = 110
_ART_H = 118
_ART_GAP = 10
_SUM_H = 120
_ABYSS_H = 210

_ELEMENT_COLORS = {
    "Pyro": (0x90, 0x3B, 0x2A),
    "Hydro": (0x34, 0x45, 0x95),
    "Cryo": (0x57, 0x7F, 0xC7),
    "Dendro": (0x46, 0x6B, 0x63),
    "Geo": (0x6A, 0x67, 0x48),
    "Electro": (0x73, 0x4A, 0x8C),
    "Anemo": (0x12, 0x95, 0x88),
    "None": (0x4A, 0x55, 0x68),
}


def _build_team_column_bg(w_px, h_px, elem_rgb):
    """列ごとの元素背景。水平方向は均一（左右対称＝中央）、縦は上(暗)→中央(明るい)→下(やや暗)。
    キャラアート（ぼかし）は重ねないので左右に寄らず、色の境目もボックスと揃う。"""
    img = Image.new("RGBA", (w_px, h_px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for yy in range(h_px):
        t = yy / max(h_px - 1, 1)
        if t < 0.5:
            f = 0.55 + 0.55 * (t / 0.5)          # 上0.55 → 中央1.10
        else:
            f = 1.10 - 0.35 * ((t - 0.5) / 0.5)  # 中央1.10 → 下0.75
        c = tuple(max(0, min(255, int(v * f))) for v in elem_rgb)
        d.line([(0, yy), (w_px, yy)], fill=(*c, 255))
    return img


class _DrawCtx:
    """スレッドローカルな SX/SY 差し替えコンテキスト（draw.figma_draw_scale のラッパー）。

    従来は app.card.draw のモジュール属性 SX/SY を直接書き換えていたため、
    カード生成プールの別スレッド（単体カード生成）と競合して描画スケールが
    混ざるデータレースがあった。draw.figma_draw_scale は threading.local を
    使うため、このスレッド中だけ効果が及び、並行する単体カード生成には
    影響しない。
    """
    def __init__(self):
        self._ctx = figma_draw_scale(SX_TEAM, SY_TEAM)

    def __enter__(self):
        self._ctx.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._ctx.__exit__(exc_type, exc, tb)


def _col_x(col: int) -> int:
    return _COL_START_X + col * (_COL_W + _COL_GAP)


def _col_w(col: int) -> int:
    """列の幅。最後の列はカード右端まで伸ばしてボックスと背景を揃える。"""
    if col == 3:
        return TEAM_DESIGN_W - _col_x(col)
    return _COL_W


def _font(size, light=False):
    path = FONT_LIGHT_PATH if light else FONT_PATH
    if os.path.exists(path):
        return get_cached_font(path, max(1, round(size * SY_TEAM)))
    return ImageFont.load_default()


# チームカード用: 影なしのラッパー
def _t(draw, text, x, y, font, font_size=None, fill_color=(255, 255, 255), align="left", box_width=None, stroke_width=0, stroke_fill=None):
    return draw_figma_text(draw, text, x, y, font, font_size=font_size, fill_color=fill_color,
                           align=align, box_width=box_width, stroke_width=stroke_width,
                           stroke_fill=stroke_fill)


def _tr(draw, text, x, y, font, font_size=24, fill_color=(255, 255, 255), stroke_width=0, stroke_fill=None):
    return draw_figma_text_right(draw, text, x, y, font, font_size=font_size, fill_color=fill_color,
                                 stroke_width=stroke_width, stroke_fill=stroke_fill)


# ----------------------------------------------------------------
# スプラッシュの拡大率・キャラ別オフセット（adminで調整できるようにする）
# ----------------------------------------------------------------
TEAM_SPLASH_ZOOM = 3.0
_SPLASH_OFFSETS_PATH = os.path.join(STATIC_DIR, "data", "setting", "team_splash_offsets.json")
_OLD_SPLASH_OFFSETS_PATH = os.path.join(STATIC_DIR, "cache", "team_splash_offsets.json")
_splash_offsets_cache = {"map": {}, "mtime": None}


def _load_splash_offsets():
    # 旧パス（static/cache）からの移行
    if not os.path.exists(_SPLASH_OFFSETS_PATH) and os.path.exists(_OLD_SPLASH_OFFSETS_PATH):
        try:
            os.makedirs(os.path.dirname(_SPLASH_OFFSETS_PATH), exist_ok=True)
            import shutil as _shutil
            _shutil.copy2(_OLD_SPLASH_OFFSETS_PATH, _SPLASH_OFFSETS_PATH)
        except Exception as e:
            print(f"[team_image] splash offsets migration failed: {e}")
    if not os.path.exists(_SPLASH_OFFSETS_PATH):
        _splash_offsets_cache["map"] = {}
        _splash_offsets_cache["mtime"] = None
        return {}
    try:
        mtime = os.path.getmtime(_SPLASH_OFFSETS_PATH)
        if _splash_offsets_cache["mtime"] == mtime:
            return _splash_offsets_cache["map"]
        with open(_SPLASH_OFFSETS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        _splash_offsets_cache["map"] = data.get("offsets") if isinstance(data, dict) else {}
        _splash_offsets_cache["mtime"] = mtime
        return _splash_offsets_cache["map"] or {}
    except Exception:
        return _splash_offsets_cache["map"] or {}


def _get_splash_offset(char_id):
    """adminで設定されたキャラ別オフセット (x%, y%) を返す。未設定は (0,0)。"""
    raw = (_load_splash_offsets().get(str(char_id)) or {})
    try:
        return (float(raw.get("x", 0)), float(raw.get("y", 0)))
    except (TypeError, ValueError):
        return (0.0, 0.0)


def _draw_row_label(img, text, y, height, beta):
    """左側の行ラベルを描画（例: IDENTITY, BASE STATS）。"""
    draw = ImageDraw.Draw(img)
    # ラベル背景（薄い枠）
    draw_figma_box(img, x=_MARGIN, y=y, width=_ROW_LABEL_W - 10, height=height, radius=8,
                    fill_color=(60, 64, 72, 135), outline_color=(140, 145, 155, 90), outline_width=1)
    # ラベルテキスト（左揃え・キャラ見出しと揃える）
    _t(draw, text=text, x=_MARGIN + 12, y=y + height/2 - 12,
                    font=_font(22, light=True), align="left",
                    font_size=22, fill_color=(255, 255, 255))


def _draw_header_row(img, datas, beta):
    """ヘッダー行: 左ラベル「キャラ」| 各キャラ列ごとに分かれた名前ボックス（凸数バッジ + Lv）。"""
    y = _MARGIN
    draw = ImageDraw.Draw(img)

    # 左側ラベル背景（キャラ列とは分けて独立したボックス）
    draw_figma_box(img, x=_MARGIN, y=y, width=_ROW_LABEL_W - 10, height=_HEADER_H,
                   radius=12, fill_color=(60, 64, 72, 110),
                   outline_color=(140, 145, 155, 90), outline_width=1)
    _t(draw, text="キャラ", x=_MARGIN + 12, y=y + _HEADER_H / 2 - 12,
                    font=_font(24), align="left", font_size=24,
                    fill_color=(255, 255, 255))

    # 各カラムヘッダー（列ごとに独立したボックス）
    for col, data in enumerate(datas[:4]):
        x = _col_x(col)
        element = data.get("element", "None")
        elem_rgb = _ELEMENT_COLORS.get(element, _ELEMENT_COLORS["None"])

        # 列ごとの背景ボックス（繋がらない）
        draw_figma_box(img, x=x, y=y, width=_col_w(col), height=_HEADER_H,
                       radius=12, fill_color=(60, 64, 72, 110),
                       outline_color=(140, 145, 155, 90), outline_width=1)

        # キャラ名（左）
        name = data.get("displayName", f"CHARACTER {chr(65+col)}")
        _t(draw, text=name, x=x + 12, y=y + 10,
                        font=_font(24), align="left", font_size=24,
                        fill_color=(255, 255, 255))
        # 凸数バッジ（名前の箱の右上・右揃え）
        cons = data.get("constellation")
        if cons is not None:
            badge_w = 48
            bx = x + _COL_W - badge_w - 12
            by = y + 10
            draw_figma_box(img, x=bx, y=by, width=badge_w, height=34, radius=8,
                           fill_color=(0, 0, 0, 150), outline_color=(*elem_rgb, 230), outline_width=2)
            _t(draw, text=f"C{cons}", x=bx, y=by + 7,
                            font=_font(20), align="center", box_width=badge_w, font_size=20,
                            fill_color=(255, 255, 255))
        # Lv（名前の下・白）
        lv = data.get("level", "?")
        _t(draw, text=f"LV. {lv}", x=x + 12, y=y + 56,
                        font=_font(16, light=True), align="left", font_size=16,
                        fill_color=(255, 255, 255))
        # 元素色の上線
        draw_figma_line(img, x1=x, y1=y, x2=x + _col_w(col), y2=y,
                        fill_color=(*elem_rgb, 200), width=3)


def _draw_identity_row(img, data, col, beta):
    """IDENTITY行: スプラッシュアートのみ（名前・凸数はヘッダー行に表示）。"""
    x = _col_x(col)
    y = _MARGIN + _HEADER_H + 15

    # 下にうっすら背景（スプラッシュの隙間のみ）
    draw_figma_box(img, x=x, y=y, width=_col_w(col), height=_IDENTITY_H, radius=12,
                   fill_color=(60, 64, 72, 125), outline_color=(140, 145, 155, 90), outline_width=1)

    # スプラッシュアート（ズーム・中心据え + キャラ別オフセット）
    splash_path = data.get("splash") or ""
    offset = _get_splash_offset(data.get("offset_key") or data.get("id") or "")
    paste_mask_image(
        img, splash_path,
        box_x=x, box_y=y,
        box_width=_col_w(col), box_height=_IDENTITY_H,
        radius=12, zoom=TEAM_SPLASH_ZOOM, beta=beta, offset=offset,
    )


def _draw_stats_row(img, data, col, beta):
    """BASE STATS行: 基本ステータスを2列で表示（アイコン付き）。"""
    x = _col_x(col)
    y = _MARGIN + _HEADER_H + 15 + _IDENTITY_H + 12
    draw_figma_box(img, x=x, y=y, width=_col_w(col), height=_STATS_H, radius=12,
                   fill_color=(60, 64, 72, 125), outline_color=(140, 145, 155, 90), outline_width=1)

    draw = ImageDraw.Draw(img)
    f_label = _font(14)
    f_val = _font(17)
    f_small = _font(11, light=True)

    main_stats = data.get("mainStats") or []
    n = len(main_stats)
    if n == 0:
        return

    half = (n + 1) // 2
    col_w = (_COL_W - 24 - 10) / 2  # 左右パディング12, 列間10
    row_h = (_STATS_H - 20) / half

    for idx, s in enumerate(main_stats):
        c = 0 if idx < half else 1
        r = idx if idx < half else idx - half
        cx = x + 12 + c * (col_w + 10)
        cy = y + 10 + r * row_h
        right_edge = cx + col_w
        icon_path = s.get("icon") or ""
        if icon_path:
            paste_figma_image(img, icon_path, box_x=cx, box_y=cy + 3,
                              box_width=20, box_height=20, radius=3, beta=beta)
        _t(draw, text=s.get("label", ""), x=cx + 25, y=cy, font=f_label,
                        align="left", font_size=14, fill_color=(255, 255, 255))
        _tr(draw, s.get("val", ""), x=right_edge, y=cy - 1, font=f_val,
                              fill_color=(255, 255, 255))
        if s.get("base") is not None and s.get("add") is not None:
            sub_y = cy + 20
            _tr(draw, str(s.get("add")), x=right_edge, y=sub_y, font=f_small,
                                  fill_color=(255, 255, 255))
            add_w = draw.textlength(str(s.get("add")), font=f_small) / SX_TEAM
            _tr(draw, str(s.get("base")), x=right_edge - add_w - 4, y=sub_y,
                                  font=f_small, fill_color=(255, 255, 255))


def _draw_weapon_row(img, data, col, beta):
    """WEAPON行: 武器アイコン + 名前 + レアリティ + 聖遺物セットアイコン。"""
    x = _col_x(col)
    y = _MARGIN + _HEADER_H + 15 + _IDENTITY_H + 12 + _STATS_H + 12
    draw_figma_box(img, x=x, y=y, width=_col_w(col), height=_WEAPON_H, radius=12,
                   fill_color=(60, 64, 72, 125), outline_color=(140, 145, 155, 90), outline_width=1)

    # 武器アイコン
    icon_box = 72
    weapon_icon = data.get("weaponIcon") or ""
    if weapon_icon:
        paste_figma_image(img, weapon_icon, box_x=x + 12, box_y=y + 14,
                          box_width=icon_box, box_height=icon_box, radius=8, beta=beta)

    draw = ImageDraw.Draw(img)

    # 精錬バッジ（アイコンの右上）
    affix = data.get("weaponAffix")
    if affix:
        badge_w, badge_h = 40, 22
        draw_figma_box(img, x=x + 12 + icon_box - badge_w + 6, y=y + 10, width=badge_w, height=badge_h,
                       radius=4, fill_color=(0, 0, 0, 140))
        _t(draw, text=f"R{affix}", x=x + 12 + icon_box - badge_w + 6, y=y + 13,
                        font=_font(13), align="center", box_width=badge_w, font_size=13)

    # 武器名
    _t(draw, text=data.get("weaponName") or "未装備", x=x + 96, y=y + 20,
                    font=_font(19), align="left", box_width=_COL_W - 180, font_size=19)
    # 武器Lv
    lv = data.get("weaponLevel")
    _t(draw, text=f"Lv.{lv}" if lv else "Lv.-", x=x + 96, y=y + 50,
                    font=_font(15, light=True), align="left", font_size=15,
                    fill_color=(255, 255, 255))

    # 聖遺物セットアイコン（右揃え・各アイコン右下に個数。武器アイコン72pxより少し小さい56px）
    set_bonuses = data.get("setBonuses") or []
    icon_size = 56
    gap = 8
    right_x = x + _COL_W - 10
    for sb in reversed(set_bonuses[:3]):
        ax = right_x - icon_size
        ay = y + (_WEAPON_H - icon_size) // 2
        draw_figma_box(img, x=ax, y=ay, width=icon_size, height=icon_size, radius=9,
                       fill_color=(0, 0, 0, 80))
        icon = sb.get("icon") or ""
        if not icon and sb.get("id"):
            icon = os.path.join("static", "assets", "artifacts", f"UI_RelicIcon_{sb['id']}_4.webp")
        if icon:
            paste_figma_image(img, icon, box_x=ax + 3, box_y=ay + 3,
                              box_width=icon_size - 6, box_height=icon_size - 6,
                              radius=7, beta=beta)
        # 個数バッジ
        badge_size = 21
        bx = ax + icon_size - badge_size
        by = ay + icon_size - badge_size
        draw_figma_box(img, x=bx, y=by, width=badge_size, height=badge_size, radius=6,
                       fill_color=(0, 0, 0, 180), outline_color=(255, 255, 255, 100), outline_width=1)
        _t(draw, text=str(sb.get("count", "")), x=bx, y=by + 2, font=_font(13),
                        align="center", box_width=badge_size, font_size=13)
        right_x = ax - gap


def _draw_artifact_row(img, art, col, y, beta):
    """聖遺物 1 部位。左: 大アイコン(+Lv) / メインステ（大きく）/ サブステ縦1列（アイコン+値・値は右揃え）。
    右: 「スコア」ラベル + 数値 + ランク。"""
    x = _col_x(col)
    draw_figma_box(img, x=x, y=y, width=_col_w(col), height=_ART_H, radius=12,
                   fill_color=(60, 64, 72, 125), outline_color=(140, 145, 155, 90), outline_width=1)
    draw = ImageDraw.Draw(img)

    if not art:
        _t(draw, text="未装備", x=x, y=y + _ART_H / 2 - 10, font=_font(17),
                        align="center", box_width=_COL_W, font_size=17,
                        fill_color=(255, 255, 255))
        return

    # スコアブロック（右側）
    score_x = x + _COL_W - 90
    draw_figma_line(img, x1=score_x - 6, y1=y + 10, x2=score_x - 6, y2=y + _ART_H - 10,
                    fill_color=(255, 255, 255, 25), width=1)
    _t(draw, text="スコア", x=score_x, y=y + 14, font=_font(12, light=True),
                    align="center", box_width=80, font_size=12, fill_color=(255, 255, 255))
    _t(draw, text=f"{art.get('score', 0):.1f}", x=score_x, y=y + 30,
                    font=_font(24), align="center", box_width=80, font_size=24)
    tier_path = os.path.join("static", "assets", "tiers", f"{art.get('tier', 'B')}.png")
    if os.path.exists(tier_path):
        paste_figma_image(img, tier_path, box_x=score_x + 26, box_y=y + 62,
                          box_width=32, box_height=32, radius=5, beta=beta)

    # 大アイコン + 部位Lv
    icon_size = 84
    icon_path = art.get("icon") or ""
    if icon_path:
        paste_figma_image(img, icon_path, box_x=x + 10, box_y=y + 10,
                          box_width=icon_size, box_height=icon_size, radius=8, beta=beta)
    _t(draw, text=f"{art.get('slot', '')} +{art.get('upgrade', 0)}",
                    x=x + 10, y=y + 96, font=_font(13, light=True),
                    align="left", font_size=13, fill_color=(255, 255, 255))

    # メインステ（大きく）
    main = art.get("main") or {}
    _t(draw, text=main.get("name", ""), x=x + 104, y=y + 12,
                    font=_font(17, light=True), align="left", font_size=17,
                    fill_color=(255, 255, 255))
    _t(draw, text=main.get("value", ""), x=x + 104, y=y + 32,
                    font=_font(28), align="left", font_size=28)

    # サブステ 4本を縦1列（カード全体を使い最大サイズに）
    subs = art.get("substats") or []
    f_sub = _font(16)
    sub_x = x + 216
    sub_right = score_x - 12
    sub_y = y + 12
    sub_step = (_ART_H - 24) / 4  # y+12 から y+(_ART_H-12) まで4行
    for j in range(4):
        sub = subs[j] if j < len(subs) else None
        row_cy = sub_y + j * sub_step
        if sub:
            sub_icon = sub.get("icon") or ""
            if sub_icon:
                paste_figma_image(img, sub_icon, box_x=sub_x, box_y=row_cy + 1,
                                  box_width=18, box_height=18, radius=4, beta=beta)
            _tr(draw, sub.get("value", ""), x=sub_right, y=row_cy,
                                  font=f_sub, fill_color=(255, 255, 255))
        else:
            _tr(draw, "-", x=sub_right, y=row_cy, font=f_sub,
                                  fill_color=(255, 255, 255))


def _draw_total_row(img, data, col, beta):
    """TOTAL RATING行: 総合スコア。"""
    x = _col_x(col)
    y = _MARGIN + _HEADER_H + 15 + _IDENTITY_H + 12 + _STATS_H + 12 + _WEAPON_H + 12
    # 5つの聖遺物行 + ギャップを加算
    y += 5 * _ART_H + 4 * _ART_GAP + 12

    draw_figma_box(img, x=x, y=y, width=_col_w(col), height=_SUM_H, radius=12,
                    fill_color=(60, 64, 72, 135), outline_color=(140, 145, 155, 90), outline_width=1)

    draw = ImageDraw.Draw(img)
    _t(draw, text="総合スコア", x=x, y=y + 13, font=_font(18, light=True),
                    align="center", box_width=_COL_W, font_size=18,
                    fill_color=(255, 255, 255))
    # ランク画像（右端。スコア値は箱全体の中央に揃える）
    tier_path = os.path.join("static", "assets", "tiers", f"{data.get('tierSum', 'B')}.png")
    _t(draw, text=f"{round(data.get('scoreSum', 0), 1):.1f}", x=x, y=y + 32,
                    font=_font(42), align="center", box_width=_COL_W, font_size=42)
    if os.path.exists(tier_path):
        paste_figma_image(img, tier_path, box_x=x + _COL_W - 58, box_y=y + 30,
                          box_width=48, box_height=48, radius=5, beta=beta)
    # 計算方法（数値と同じ高さで左寄せ。ラベル + 半透明線 + 値）
    calc_label = data.get("calcMethodLabel") or data.get("calcMethod", "")
    if calc_label:
        _t(draw, text="計算方法", x=x + 15, y=y + 34,
                        font=_font(18, light=True), align="left", font_size=18,
                        fill_color=(255, 255, 255))
        draw_figma_line(img, x1=x + 15, y1=y + 56, x2=x + 110, y2=y + 56,
                        fill_color=(255, 255, 255, 55), width=1)
        _t(draw, text=calc_label, x=x + 15, y=y + 60,
                        font=_font(21), align="left", font_size=21,
                        fill_color=(255, 255, 255))


def _draw_abyss_section(img, boss, y, beta):
    """幽境（レイライン）セクションを画像最下部に描画。
    箱は使わず、左に「ver x.x」とボス名（2キャラ分くらいの大きい文字）、
    右にボスアイコン（右から2キャラ目 = 3列目の下に配置・はみ出さないサイズ）。
    """
    if not boss or not isinstance(boss, dict):
        return
    ver = str(boss.get("version") or "")
    name = str(boss.get("name") or "")
    img_url = str(boss.get("img") or "")

    draw = ImageDraw.Draw(img)
    # 左: 幽境ラベル（箱なし・縦中央・特大）
    _t(draw, text="幽境", x=_MARGIN + 12, y=y + _ABYSS_H / 2 - 26,
                    font=_font(42), align="left", font_size=42,
                    fill_color=(255, 255, 255))
    # 幽境の右（ver x.x の左）に薄い白い縦線（境界）
    draw_figma_line(img, x1=_MARGIN + _ROW_LABEL_W + 2, y1=y + 14,
                    x2=_MARGIN + _ROW_LABEL_W + 2, y2=y + _ABYSS_H - 14,
                    fill_color=(255, 255, 255, 85), width=2)

    # 左: ver + ボス名（大きく・2キャラ分程度の領域を使用）
    tx = _MARGIN + _ROW_LABEL_W + 24
    _t(draw, text=f"ver {ver}", x=tx, y=y + 4,
                    font=_font(42), align="left", font_size=42,
                    fill_color=(255, 255, 255))
    if name:
        _t(draw, text=name, x=tx, y=y + 64,
                        font=_font(24, light=True), align="left", font_size=24,
                        fill_color=(255, 255, 255))

    # 右: ボスアイコン（赤黒の渦巻きオーラを背後に敷き、その上に配置）
    if img_url:
        file_path = os.path.join(BASE_DIR, img_url.lstrip("/"))
        if os.path.exists(file_path):
            icon_size = _ABYSS_H - 40  # セクション高に収まる
            col2_center = _col_x(2) + _COL_W / 2
            # アイコンをセクションの縦中央に配置
            icon_y = y + (_ABYSS_H - icon_size) / 2
            paste_figma_image(
                img, file_path,
                box_x=col2_center - icon_size / 2, box_y=icon_y,
                box_width=icon_size, box_height=icon_size,
                radius=14, beta=beta,
            )


def _generate_team_image_sync(uid: str, char_ids: list, configs=None, boss=None, beta: str = "false", img_format: str = "png"):
    """4 キャラ編成カード画像を PIL で生成して PNG bytes を返す。（行ベースレイアウト版）"""
    _total_start = time.perf_counter()
    if beta != "true":
        beta = "false"
    print(f"[TeamCard] START uid={uid} chars={char_ids} beta={beta}", flush=True)

    # データ取得（configs: 各キャラの計算方法・差し替え武器/キャラ）
    t0 = time.perf_counter()
    datas = []
    for i, cid in enumerate(char_ids):
        cfg = (configs[i] if configs and i < len(configs) and isinstance(configs[i], dict) else {}) or {}
        # 差し替えキャラ表示時はオフセット等を「表示中キャラ」に紐付ける
        _display_id = str(cfg.get("fake_char") or cid)
        try:
            d = _get_card_data_sync(
                uid, str(cid),
                calc_method=cfg.get("calc_method") or "crit",
                fake_char=cfg.get("fake_char") or None,
                fake_weapon=cfg.get("fake_weapon") or None,
                beta=beta,
                base_prec=cfg.get("base_prec") or "0",
            )
            d["id"] = _display_id
        except Exception as e:
            print(f"[TeamCard] card_data fetch failed for {cid}: {e}", flush=True)
            d = {
                "displayName": str(cid), "element": "None", "level": None,
                "friendship": None, "constellation": None, "splash": "",
                "skills": [], "weaponName": "", "weaponIcon": "",
                "weaponLevel": None, "weaponAffix": None, "mainStats": [],
                "artifacts": [None] * 5, "setBonuses": [],
                "scoreSum": 0, "tierSum": "B", "calcMethodLabel": "会心",
                "id": str(cid), "costumeId": None,
            }
        # オフセットのキー: 衣装適用時は「キャラID:衣装ID」、それ以外はキャラID
        _cid = str(d.get("id") or cid)
        _cst = d.get("costumeId")
        d["offset_key"] = f"{_cid}:{_cst}" if _cst else _cid
        datas.append(d)
    print(f"[TeamCard] data fetched in {(time.perf_counter()-t0)*1000:.0f}ms", flush=True)

    # 幽境セクションを描画するか（ボス設定がある場合のみ）
    _has_boss = bool(boss and isinstance(boss, dict) and boss.get("version"))

    # 背景（キャンバス高さは幽境セクション有無で変える。無い時は伸ばさない）
    t0 = time.perf_counter()
    _y_bg_id = _MARGIN + _HEADER_H + 15
    _y_bg_stats = _y_bg_id + _IDENTITY_H + 12
    _y_bg_weapon = _y_bg_stats + _STATS_H + 12
    _y_bg_art = _y_bg_weapon + _WEAPON_H + 12
    _y_bg_sum = _y_bg_art + 5 * _ART_H + 4 * _ART_GAP + 12
    _content_bottom = _y_bg_sum + _SUM_H
    _bg_height_px = max(1, round(_content_bottom * SY_TEAM))
    _design_h = _content_bottom + 15 + ((12 + _ABYSS_H) if _has_boss else 0)
    _canvas_h = max(1, round(_design_h * TEAM_SCALE))
    img = _build_base_background(TEAM_W, _canvas_h, (0x1E, 0x22, 0x2C))

    # 各キャラ列に元素背景を敷く。
    # 列間の黒い隙間を埋めるため、各列の色をギャップの半分まで伸ばす
    # （左半分＝左キャラ色、右半分＝右キャラ色になる）
    # 左の行ラベル列はニュートラルのまま。
    _cols = datas[:4]
    half_gap = _COL_GAP / 2
    for col, data in enumerate(_cols):
        cfg = (configs[col] if configs and col < len(configs) and isinstance(configs[col], dict) else {}) or {}
        element = data.get("element", "None")
        elem_rgb = _ELEMENT_COLORS.get(element, _ELEMENT_COLORS["None"])
        x = _col_x(col)
        # 左右のギャップ半分まで拡張（最後の列はカード右端まで伸ばす）
        left_ext = half_gap if col > 0 else 0
        if col < len(_cols) - 1:
            right_ext = half_gap
        else:
            right_ext = (TEAM_DESIGN_W - x) - _COL_W  # 右端の余白を埋める
        bg_x = x - left_ext
        bg_w = _COL_W + left_ext + right_ext
        w_px = max(1, round(bg_w * SX_TEAM))

        # 背景: カスタム色 > 地域画像 > 元素色
        base_rgb = elem_rgb
        if str(cfg.get("bg_mode") or "").lower() == "custom" and cfg.get("bg_color"):
            base_rgb = hex_to_rgb(str(cfg.get("bg_color")).lstrip("#")) or elem_rgb
        bg_region = cfg.get("bg_region")
        if bg_region and region_image_path(bg_region):
            col_bg = get_region_background(w_px, _bg_height_px, bg_region)
            if col_bg is None:
                col_bg = _build_team_column_bg(w_px, _bg_height_px, base_rgb)
            else:
                # 文字が見えるよう暗めオーバーレイ
                ov = Image.new("RGBA", col_bg.size, (8, 10, 16, 90))
                col_bg = Image.alpha_composite(col_bg, ov)
        else:
            col_bg = _build_team_column_bg(w_px, _bg_height_px, base_rgb)
        img.alpha_composite(col_bg, (round(bg_x * SX_TEAM), 0))
    print(f"[TeamCard] background in {(time.perf_counter()-t0)*1000:.0f}ms", flush=True)

    # 描画
    with _DrawCtx():
        # ヘッダー行
        _draw_header_row(img, datas, beta)

        # 立ち絵行ラベル
        _y_id = _MARGIN + _HEADER_H + 15
        _draw_row_label(img, "立ち絵", _y_id, _IDENTITY_H, beta)

        # ステータス行ラベル
        _y_stats = _y_id + _IDENTITY_H + 12
        _draw_row_label(img, "ステータス", _y_stats, _STATS_H, beta)

        # 武器行ラベル
        _y_weapon = _y_stats + _STATS_H + 12
        _draw_row_label(img, "武器", _y_weapon, _WEAPON_H, beta)

        # 聖遺物行ラベル
        _y_art = _y_weapon + _WEAPON_H + 12
        _draw_row_label(img, "聖遺物", _y_art, 5*_ART_H + 4*_ART_GAP, beta)

        # 総合スコア行ラベル
        _y_sum = _y_art + 5*_ART_H + 4*_ART_GAP + 12
        _draw_row_label(img, "総合スコア", _y_sum, _SUM_H, beta)

        # 各カラム
        for col, data in enumerate(datas[:4]):
            t_col = time.perf_counter()
            _draw_identity_row(img, data, col, beta)
            _draw_stats_row(img, data, col, beta)
            _draw_weapon_row(img, data, col, beta)

            # 聖遺物5行
            arts = data.get("artifacts") or []
            for i in range(5):
                art = arts[i] if i < len(arts) else None
                art_y = _y_art + i * (_ART_H + _ART_GAP)
                _draw_artifact_row(img, art, col, art_y, beta)

            _draw_total_row(img, data, col, beta)
            print(f"[TeamCard] column {col} drawn in {(time.perf_counter()-t_col)*1000:.0f}ms", flush=True)

        # 幽境セクション（ボス設定がある場合のみ）
        if boss and isinstance(boss, dict) and boss.get("version"):
            _y_abyss = _y_sum + _SUM_H + 12
            _draw_abyss_section(img, boss, _y_abyss, beta)
            print(f"[TeamCard] abyss section drawn", flush=True)

    # 保存
    t0 = time.perf_counter()
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=1)
    payload = buf.getvalue()
    print(f"[TeamCard] PNG encoded {len(payload)} bytes in {(time.perf_counter()-t0)*1000:.0f}ms", flush=True)
    print(f"[TeamCard] DONE total {(time.perf_counter()-_total_start)*1000:.0f}ms", flush=True)
    return payload