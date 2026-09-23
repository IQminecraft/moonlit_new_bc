# -*- coding: utf-8 -*-
"""編成カード画像生成（1編成 = 4キャラを1枚の横長画像にまとめる）。

HTML版（build_card.html の .tc-* 系スタイル）と完全同期したレイアウト。
- .tc-root { width:1860px; gap:12px } → 上下左右 24px の余白付きで画像化
- .tc-col { width:456px; gap:12px } を左ラベル列なしで 4 列並べる
- 行パネルは .tc-cell と同じ縦グラデのガラス箱（影 + 内側白枠）
- 背景は mainDisplayBox(bg-zinc-950) と同じフラット色。
  元素ストリップ / 左ラベル列は HTML に存在しないため描かない。
"""
import io
import os
import time
from PIL import Image, ImageDraw, ImageFont

from app.paths import FONT_PATH, FONT_LIGHT_PATH, STATIC_DIR
from app.card.cache import get_cached_font
from app.card.data import _get_card_data_sync
from app.card.labels import img_t
from app.card.stats import get_stat_abbr
from app.card.draw import (
    draw_figma_box, paste_mask_image,
    paste_figma_image, draw_figma_text, draw_figma_text_right, draw_figma_line,
    figma_draw_scale, draw_figma_glass_box, draw_figma_dot, draw_figma_text_with_shadow,
    _vertical_gradient_rgba, _composite_clipped,
)

# ----------------------------------------------------------------
# レイアウト設計座標（build_card.html の .tc-* CSS と 1:1 対応）
# ----------------------------------------------------------------
_PAD = 24                                    # カード外周の余白（背景部分）
TEAM_DESIGN_W = 1860                         # .tc-root と同一
GAP_X = 12
GAP_Y = 12
COL_W = 456

# 行高さ（CSS と同一）
_HEADER_H = 84
_SPLASH_H = 148
_STATS_H = 132
_WEAPON_H = 96
_ART_H = 92
_ART_GAP = 12
_TOTAL_H = 96

# 縦方向の行 y 座標（.tc-col は gap 12 で積まれる）
_Y_HEADER = 0
_Y_SPLASH = _Y_HEADER + _HEADER_H + GAP_Y
_Y_STATS = _Y_SPLASH + _SPLASH_H + GAP_Y
_Y_WEAPON = _Y_STATS + _STATS_H + GAP_Y
_Y_ART = _Y_WEAPON + _WEAPON_H + GAP_Y
_Y_ART_END = _Y_ART + 5 * _ART_H + 4 * _ART_GAP
_Y_TOTAL = _Y_ART_END + GAP_Y
_CONTENT_BOTTOM = _Y_TOTAL + _TOTAL_H       # 1124（= .tc-emptycol の高さ）

TEAM_SCALE = 1.25
CARD_W_PX = round(TEAM_DESIGN_W * TEAM_SCALE)
CARD_H_PX = round(_CONTENT_BOTTOM * TEAM_SCALE)
BG_W_PX = CARD_W_PX + round(_PAD * 2 * TEAM_SCALE)
BG_H_PX = CARD_H_PX + round(_PAD * 2 * TEAM_SCALE)
SX_TEAM = float(CARD_W_PX) / float(TEAM_DESIGN_W)
SY_TEAM = float(CARD_H_PX) / float(_CONTENT_BOTTOM)

def _col_x(col: int) -> int:
    """列の設計x座標（4列: 4*456 + 3*12 = 1860）。"""
    return col * (COL_W + GAP_X)

_TXT = (255, 255, 255, 255)
_SUB = (255, 255, 255, 158)     # HTML: rgba(255,255,255,.62)
_LINE = (255, 255, 255, 26)     # HTML: rgba(255,255,255,.10)
_RADIUS = 14

# HTML .tc-cell と同一質感（縦グラデ + 影 + 内側白枠）
_GLASS_FILL_TOP = (20, 27, 46, 105)
_GLASS_FILL_BOTTOM = (14, 20, 38, 125)
_GLASS_BORDER_TOP = (255, 255, 255, 58)
_GLASS_BORDER_BOTTOM = (255, 255, 255, 28)

# HTML .substat-dot-N と同一配色・同一順序
_ROLL_DOT_COLORS = [(34, 197, 94, 255), (59, 130, 246, 255), (168, 85, 247, 255), (249, 115, 22, 255)]

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

# 追加効果バッジ（HTML tcBadges: swap）
_SWAP_SUFFIX = "(swap)"

_BADGE_STYLES = {
    "swap": {"outline": (255, 193, 77, 235), "text": (255, 214, 110, 255)},
}


class _DrawCtx:
    """スレッドローカルな SX/SY 差し替えコンテキスト（draw.figma_draw_scale のラッパー）。"""
    def __init__(self):
        self._ctx = figma_draw_scale(SX_TEAM, SY_TEAM)

    def __enter__(self):
        self._ctx.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._ctx.__exit__(exc_type, exc, tb)


def _font(size, light=False):
    path = FONT_LIGHT_PATH if light else FONT_PATH
    if os.path.exists(path):
        return get_cached_font(path, max(1, round(size * SY_TEAM)))
    return ImageFont.load_default()


def _fit_font(draw, text, max_w, size, light=False, min_size=10):
    """max_w（design px）に収まるようフォントを縮小して返す。"""
    s = size
    while s > min_size:
        f = _font(s, light)
        if draw.textlength(str(text), font=f) / SX_TEAM <= max_w:
            return f, s
        s -= 1
    return _font(min_size, light), min_size


def _ellipsis(draw, text, max_w, size, light=False):
    """CSS ellipsis 相当: 固定サイズのまま末尾に「…」を付けて 1 行に収める。"""
    text = str(text or "")
    f = _font(size, light)
    if draw.textlength(text, font=f) / SX_TEAM <= max_w:
        return f, text
    crop = text
    while crop and draw.textlength(crop + "…", font=f) / SX_TEAM > max_w:
        crop = crop[:-1]
    return f, (crop + "…") if crop else "…"


def _t(draw, text, x, y, font, font_size=None, fill_color=(255, 255, 255), align="left", box_width=None, stroke_width=0, stroke_fill=None):
    return draw_figma_text(draw, text, x, y, font, font_size=font_size, fill_color=fill_color,
                           align=align, box_width=box_width, stroke_width=stroke_width,
                           stroke_fill=stroke_fill)


def _tr(draw, text, x, y, font, font_size=24, fill_color=(255, 255, 255), stroke_width=0, stroke_fill=None):
    return draw_figma_text_right(draw, text, x, y, font, font_size=font_size, fill_color=fill_color,
                                 stroke_width=stroke_width, stroke_fill=stroke_fill)


# ----------------------------------------------------------------
# スプラッシュ（HTML は object-fit:cover だが、admin のスプラッシュオフセット
# （156キャラ分チューニング済み・admin プレビューも同一仕様）を適用するため
# zoom 3.0 + 中央基準オフセットで描画する。offset は paste_mask_image に素通し。
# ----------------------------------------------------------------
TEAM_SPLASH_ZOOM = 3.0
_TEAM_SPLASH_OFFSETS_PATH = os.path.join(STATIC_DIR, "data", "setting", "team_splash_offsets.json")
_splash_offsets_cache = {"map": {}, "mtime": None}


def _load_splash_offsets():
    try:
        mtime = os.path.getmtime(_TEAM_SPLASH_OFFSETS_PATH)
        if _splash_offsets_cache["mtime"] == mtime:
            return _splash_offsets_cache["map"]
        with open(_TEAM_SPLASH_OFFSETS_PATH, "r", encoding="utf-8") as f:
            data = __import__("json").load(f)
        m = data.get("offsets") if isinstance(data, dict) else {}
        _splash_offsets_cache["map"] = m or {}
        _splash_offsets_cache["mtime"] = mtime
        return _splash_offsets_cache["map"] or {}
    except Exception:
        return _splash_offsets_cache["map"] or {}


def _get_splash_offset(char_id):
    """admin で調整済みのスプラッシュオフセット (x%, y%) をそのまま返す。

    paste_mask_image の offset（中央基準 ±100%）は admin プレビュー
    （admin.html SPLASH_ZOOM=3.0 のプレビュー）と同じセマンティクスのため、
    チューニング済みの値を変換なしで適用できる。
    """
    raw = (_load_splash_offsets().get(str(char_id)) or {})
    try:
        ax = float(raw.get("x", 0) or 0)
    except (TypeError, ValueError):
        ax = 0.0
    try:
        ay = float(raw.get("y", 0) or 0)
    except (TypeError, ValueError):
        ay = 0.0
    return (max(-100.0, min(100.0, ax)), max(-100.0, min(100.0, ay)))


def _compute_team_badges(datas, configs=None):
    """バッジ（swap）を判定して data["_badges"] に格納（HTML tcBadges と同一）。"""
    for idx, d in enumerate(datas[:4]):
        if d is None:
            continue
        badges = []
        # swap（名前の「(swap)」サフィックスは除去 → スプラッシュ上のチップで表現）
        name = str(d.get("displayName") or "")
        had_swap_suffix = name.endswith(_SWAP_SUFFIX)
        if had_swap_suffix:
            d["displayName"] = name[: -len(_SWAP_SUFFIX)]
        if had_swap_suffix:
            badges.append({"kind": "swap", "text": "swap"})

        d["_badges"] = badges


def _draw_panels_one(img, data, col):
    """1列分の行パネル（HTML .tc-cell のガラス箱）を敷く。"""
    x = _col_x(col)
    element = data.get("element") or "None"
    elem_rgb = _ELEMENT_COLORS.get(element, _ELEMENT_COLORS["None"])
    hl = tuple(min(255, c + 90) for c in elem_rgb)
    draw_figma_glass_box(
        img, x=x, y=_Y_HEADER, width=COL_W, height=_HEADER_H, radius=_RADIUS,
        fill_top=_GLASS_FILL_TOP, fill_bottom=_GLASS_FILL_BOTTOM,
        border_top=(*hl, 150), border_bottom=(*elem_rgb, 90),
    )
    for row_y, row_h in [
        (_Y_SPLASH, _SPLASH_H),
        (_Y_STATS, _STATS_H),
        (_Y_WEAPON, _WEAPON_H),
        (_Y_ART, 5 * _ART_H + 4 * _ART_GAP),
        (_Y_TOTAL, _TOTAL_H),
    ]:
        draw_figma_glass_box(
            img, x=x, y=row_y, width=COL_W, height=row_h, radius=_RADIUS,
            fill_top=_GLASS_FILL_TOP, fill_bottom=_GLASS_FILL_BOTTOM,
            border_top=_GLASS_BORDER_TOP, border_bottom=_GLASS_BORDER_BOTTOM,
        )


def _draw_header_row_one(img, data, col, beta, lang="ja"):
    """ヘッダー行（HTML .tc-hd）: 名前+凸バッジ / Lv+友好度。上端に元素色ライン。"""
    x = _col_x(col)
    y = _Y_HEADER
    element = data.get("element", "None")
    elem_rgb = _ELEMENT_COLORS.get(element, _ELEMENT_COLORS["None"])
    draw = ImageDraw.Draw(img)

    # 上端の元素色ライン（HTML .tc-cell.topline）
    draw_figma_line(img, x1=x + 2, y1=y + 1, x2=x + COL_W - 2, y2=y + 1,
                    fill_color=(*elem_rgb, 230), width=3)

    # 名前（HTML ellipsis: 固定22px・はみ出しは「…」で打ち切り）
    name = data.get("displayName") or ""
    name_max = COL_W - 16 - 46 - 12 - 16  # 凸バッジ46 + 右余白16 - 左16
    f_name, name_text = _ellipsis(draw, name, name_max, 22)
    draw_figma_text_with_shadow(
        draw, text=name_text, x=x + 16, y=y + 13, font=f_name, font_size=22,
        fill_color=_TXT, shadow_color=(0, 0, 0, 190), shadow_offset=(2, 2),
        align="left",
    )

    # 凸数バッジ（右上・HTML .cbadge）
    cons = data.get("constellation")
    if cons is not None:
        badge_w, badge_h = 46, 28
        bx = x + COL_W - 16 - badge_w
        by = y + 12
        draw_figma_box(img, x=bx, y=by, width=badge_w, height=badge_h, radius=8,
                       fill_color=(0, 0, 0, 90), outline_color=(*elem_rgb, 230), outline_width=2)
        _t(draw, text=f"C{cons}", x=bx, y=by + 5,
           font=_font(15), align="center", box_width=badge_w, font_size=15,
           fill_color=_TXT)

    # 2段目: Lv のみ（HTML .r2、白）
    lv = data.get("level")
    if lv is not None:
        _t(draw, text=f"Lv.{lv}", x=x + 16, y=y + 49,
           font=_font(14, light=True), align="left", font_size=14,
           fill_color=_TXT)


def _draw_identity_row(img, data, col, beta):
    """スプラッシュ行: admin 調整済みオフセット（zoom 3.0 基準）+ 下端 56px フェード。"""
    x = _col_x(col)
    y = _Y_SPLASH
    paste_mask_image(
        img, data.get("splash") or "",
        box_x=x, box_y=y,
        box_width=COL_W, box_height=_SPLASH_H,
        radius=_RADIUS, zoom=TEAM_SPLASH_ZOOM, beta=beta,
        offset=_get_splash_offset(data.get("offset_key") or data.get("id") or ""),
    )
    fade_h = 56
    fade = _vertical_gradient_rgba(
        max(1, round(COL_W * SX_TEAM)), max(1, round(fade_h * SY_TEAM)),
        (10, 12, 18, 0), (10, 12, 18, 150),
    )
    _composite_clipped(img, fade, round(x * SX_TEAM), round((y + _SPLASH_H - fade_h) * SY_TEAM))


def _draw_talents(img, data, col, beta):
    """スプラッシュ左下の天賦クラスタ（HTML .tc-splash .tc-talents、上=通常）。"""
    skills = (data.get("skills") or [])[:3]
    if not skills:
        return
    x = _col_x(col)
    y = _Y_SPLASH
    size = 36
    gap = 5
    n = len(skills)
    cluster_h = n * size + max(0, n - 1) * gap
    bx = x + 8
    cy0 = y + _SPLASH_H - 8 - cluster_h
    draw = ImageDraw.Draw(img)
    lv_font = _font(19)
    for i, s in enumerate(skills):
        if not isinstance(s, dict):
            continue
        cy = cy0 + i * (size + gap)
        draw_figma_box(
            img, x=bx, y=cy, width=size, height=size, radius=9,
            fill_color=(10, 12, 18, 199), outline_color=(255, 255, 255, 128),
            outline_width=2, shadow=False,
        )
        icon = s.get("icon") or ""
        if icon:
            paste_figma_image(
                img, icon, box_x=bx, box_y=cy,
                box_width=size, box_height=size, radius=9, beta=beta,
            )
        lv = s.get("level")
        ltxt = str(lv if lv is not None else 1)
        lv_col = (125, 211, 252, 255) if s.get("boosted") else (255, 255, 255, 255)
        _t(draw, text=ltxt, x=bx + size + 5, y=cy + (size - 19) / 2, font=lv_font, font_size=19,
           align="left", fill_color=lv_col, stroke_width=2, stroke_fill=(0, 0, 0, 220))


def _draw_badges(img, data, col, beta):
    """バッジ（swap）をスプラッシュ左上にチップ描画（HTML .tc-splash .chips）。"""
    badges = data.get("_badges") or []
    if not badges:
        return
    x = _col_x(col)
    y = _Y_SPLASH
    max_w = COL_W
    draw = ImageDraw.Draw(img)
    font = _font(13)
    chip_h = 26
    pad_x = 9
    gap = 6
    icon_size = 15
    radius = 9
    cx = x + 8
    cy = y + 8
    row_bottom_max = y + _SPLASH_H - chip_h - 6

    for b in badges:
        text = str(b.get("text") or "")
        if not text:
            continue
        kind = b.get("kind", "swap")
        icon = b.get("icon") or ""

        tw = draw.textlength(text, font=font) / SX_TEAM
        icon_w = (icon_size + 5) if icon else 0
        w = pad_x * 2 + icon_w + tw

        if cx + w > x + max_w - 8:
            cx = x + 8
            cy += chip_h + 6
        if cy > row_bottom_max:
            break

        outline = _BADGE_STYLES.get(kind, _BADGE_STYLES["swap"])["outline"]
        text_col = _BADGE_STYLES.get(kind, _BADGE_STYLES["swap"])["text"]

        draw_figma_box(img, x=cx, y=cy, width=w, height=chip_h, radius=radius,
                       fill_color=(10, 12, 18, 185), outline_color=outline,
                       outline_width=2, shadow=False)
        tx = cx + pad_x
        if icon:
            paste_figma_image(img, icon, box_x=tx, box_y=cy + (chip_h - icon_size) / 2,
                              box_width=icon_size, box_height=icon_size, radius=4, beta=beta)
            tx += icon_size + 5
        _t(draw, text=text, x=tx, y=cy + 5, font=font, align="left", font_size=13,
           fill_color=text_col)
        cx += w + gap


def _draw_stats_row(img, data, col, beta):
    """ステータス行（HTML .tc-stats）: 2列×4行・列優先（左 HP/攻/防/熟知、右 会心/充電/元素）。"""
    x = _col_x(col)
    y = _Y_STATS
    draw = ImageDraw.Draw(img)

    main_stats = data.get("mainStats") or []
    if not main_stats:
        return

    inner_pad = 16
    col_gap = 14
    col_w = (COL_W - inner_pad * 2 - col_gap) / 2
    n = len(main_stats)
    rows = (n + 1) // 2
    pitch = (_STATS_H - 10) / max(1, rows)

    for idx, s in enumerate(main_stats):
        c = 0 if idx < rows else 1
        r = idx if idx < rows else idx - rows
        cx = x + inner_pad + c * (col_w + col_gap)
        cy = y + 5 + r * pitch
        right_edge = cx + col_w
        icon_path = s.get("icon") or ""
        if icon_path:
            paste_figma_image(img, icon_path, box_x=cx, box_y=cy + 1,
                              box_width=16, box_height=16, radius=3, beta=beta)
        label = s.get("label", "")
        f_label, f_label_size = _fit_font(draw, label, col_w - 24 - 52, 14, light=True, min_size=10)
        _t(draw, text=label, x=cx + 22, y=cy, font=f_label,
           align="left", font_size=f_label_size, fill_color=_SUB)
        f_val, f_val_size = _fit_font(draw, s.get("val", ""), 56, 15, min_size=11)
        _tr(draw, s.get("val", ""), x=right_edge, y=cy - 1, font=f_val,
            font_size=f_val_size, fill_color=_TXT)


def _draw_weapon_row(img, data, col, beta, lang="ja"):
    """武器行（HTML .tc-weapon）: アイコン62+精錬 / 名前17+Lv/武器種13 / セット48右3個。"""
    x = _col_x(col)
    y = _Y_WEAPON
    draw = ImageDraw.Draw(img)

    icon_box = 62
    cy = y + (_WEAPON_H - icon_box) / 2
    element = data.get("element", "None")
    elem_rgb = _ELEMENT_COLORS.get(element, _ELEMENT_COLORS["None"])
    hl = tuple(min(255, c + 90) for c in elem_rgb)
    weapon_icon = data.get("weaponIcon") or ""
    if weapon_icon:
        draw_figma_box(img, x=x + 14, y=cy, width=icon_box, height=icon_box, radius=10,
                       fill_color=(0, 0, 0, 110), outline_color=(*hl, 150), outline_width=2,
                       shadow=False)
        paste_figma_image(img, weapon_icon, box_x=x + 14, box_y=cy,
                          box_width=icon_box, box_height=icon_box, radius=10, beta=beta)
    affix = data.get("weaponAffix")
    if affix:
        bw, bh = 36, 20
        bx = x + 14 + icon_box - bw + 8
        by = cy - 6
        draw_figma_box(img, x=bx, y=by, width=bw, height=bh, radius=6,
                       fill_color=(11, 13, 19, 235),
                       outline_color=(255, 255, 255, 115), outline_width=1)
        _t(draw, text=f"R{affix}", x=bx, y=by + 3,
           font=_font(12), align="center", box_width=bw, font_size=12)

    set_bonuses = [sb for sb in (data.get("setBonuses") or []) if sb.get("count")]
    icon_size = 48
    gap = 6
    n = min(3, len(set_bonuses))
    sets_left = x + COL_W - 14
    if n:
        total_w = n * icon_size + (n - 1) * gap
        ax = sets_left - total_w
        ay = y + (_WEAPON_H - icon_size) / 2
        for i, sb in enumerate(set_bonuses[:3]):
            sx_ = ax + i * (icon_size + gap)
            draw_figma_box(img, x=sx_, y=ay, width=icon_size, height=icon_size, radius=9,
                           fill_color=(0, 0, 0, 80))
            icon = sb.get("icon") or ""
            if not icon and sb.get("id"):
                icon = os.path.join(STATIC_DIR, "assets", "artifacts", f"UI_RelicIcon_{sb['id']}_4.webp")
            if icon:
                paste_figma_image(img, icon, box_x=sx_ + 2, box_y=ay + 2,
                                  box_width=icon_size - 4, box_height=icon_size - 4,
                                  radius=8, beta=beta)
            badge = 19
            bx = sx_ + icon_size - badge
            by = ay + icon_size - badge
            draw_figma_box(img, x=bx, y=by, width=badge, height=badge, radius=5,
                           fill_color=(11, 13, 19, 235), outline_color=(255, 255, 255, 100), outline_width=1)
            _t(draw, text=str(sb.get("count", "")), x=bx, y=by + 2, font=_font(11),
               align="center", box_width=badge, font_size=11)

    tx = x + 14 + icon_box + 12
    name = data.get("weaponName") or img_t("未装備", lang)
    name_max = (sets_left - (total_w if n else 0) - 12) - tx if n else (sets_left - 12 - tx)
    f_name, name_text = _ellipsis(draw, name, max(name_max, 80), 17)
    _t(draw, text=name_text, x=tx, y=y + 24, font=f_name, align="left",
       font_size=17, fill_color=_TXT)
    wlv = data.get("weaponLevel")
    wtype = data.get("weaponType") or ""
    subparts = [p for p in [f"Lv.{wlv}" if wlv is not None else "", wtype] if p]
    if subparts:
        _t(draw, text=" ".join(subparts), x=tx, y=y + 54, font=_font(13, light=True),
           align="left", font_size=13, fill_color=_SUB)


def _draw_roll_dots(img, left_x, y, tiers, size=5, seg=7):
    """サブオプの伸び値ドット（HTML .tc-art .substat-dot と同色・略称の直後に左揃え）。"""
    _tiers = []
    for t in (tiers or []):
        try:
            ti = int(t)
        except (TypeError, ValueError):
            continue
        if 0 <= ti < 4:
            _tiers.append(ti)
    if not _tiers:
        return
    n = len(_tiers)
    for dk, dt in enumerate(_tiers):
        if n == 1:
            corners = (True, True, True, True)
        elif dk == 0:
            corners = (True, False, False, True)
        elif dk == n - 1:
            corners = (False, True, True, False)
        else:
            corners = (False, False, False, False)
        draw_figma_dot(img, x=left_x + dk * seg, y=y, size=size, ratio=1.4,
                       fill_color=_ROLL_DOT_COLORS[dt], corners=corners)


def _draw_artifact_row(img, art, col, y, beta, lang="ja", substat_dots="1"):
    """聖遺物1行（HTML .tc-art, 92px）: アイコン64+強化値 / メイン100px / サブ4行 / スコア86px。"""
    x = _col_x(col)
    draw = ImageDraw.Draw(img)

    if not art:
        _t(draw, text=img_t("未装備", lang), x=x, y=y + _ART_H / 2 - 9,
           font=_font(15), align="center", box_width=COL_W, font_size=15,
           fill_color=_SUB)
        return

    inner_x = x + 14
    icon_size = 64
    icon_cy = y + (_ART_H - icon_size) / 2
    icon_path = art.get("icon") or ""
    if icon_path:
        draw_figma_box(img, x=inner_x, y=icon_cy, width=icon_size, height=icon_size, radius=10,
                       fill_color=(0, 0, 0, 80))
        paste_figma_image(img, icon_path, box_x=inner_x, box_y=icon_cy,
                          box_width=icon_size, box_height=icon_size, radius=10, beta=beta)
    _t(draw, text=f"+{art.get('upgrade', 0)}", x=inner_x, y=y + _ART_H - 18, font=_font(12),
       align="center", box_width=icon_size, font_size=12, fill_color=_TXT,
       stroke_width=2, stroke_fill=(0, 0, 0, 220))

    main_x = inner_x + icon_size + 12
    main = art.get("main") or {}
    main_name = main.get("name", "")
    f_mn, f_mn_size = _fit_font(draw, main_name, 98, 12, light=True, min_size=9)
    _t(draw, text=main_name, x=main_x, y=y + 15, font=f_mn, align="left",
       font_size=f_mn_size, fill_color=_SUB)
    _t(draw, text=main.get("value", ""), x=main_x, y=y + 32, font=_font(20),
       align="left", font_size=20, fill_color=_TXT)

    subs = art.get("substats") or []
    f_sub = _font(13)
    f_abbr = _font(11)
    sub_x = main_x + 100 + 10
    score_w = 86
    score_x = x + COL_W - 14 - score_w
    sub_right = score_x - 16
    sub_y0 = y + 11
    step = (_ART_H - 22) / 4
    show_dots = str(substat_dots or "") in ("1", "true")
    for j in range(4):
        row_cy = sub_y0 + j * step
        sub = subs[j] if j < len(subs) else None
        if sub:
            sub_icon = sub.get("icon") or ""
            if sub_icon:
                paste_figma_image(img, sub_icon, box_x=sub_x, box_y=row_cy + 1,
                                  box_width=14, box_height=14, radius=3, beta=beta)
            abbr = get_stat_abbr(sub.get("name") or "", lang)
            val_s = str(sub.get("value") or "")
            if "%" in val_s and not str(abbr).endswith("%") and abbr in ("ATK", "DEF", "HP", "攻撃", "防御"):
                abbr = abbr + "%"
            _t(draw, text=abbr, x=sub_x + 17, y=row_cy + 1, font=f_abbr, align="left",
               font_size=11, fill_color=(255, 255, 255, 200))
            val = sub.get("value", "")
            _tr(draw, val, x=sub_right, y=row_cy, font=f_sub,
                font_size=13, fill_color=_TXT)
            if show_dots:
                abbr_w = draw.textlength(str(abbr), font=f_abbr) / SX_TEAM
                _draw_roll_dots(img, sub_x + 17 + abbr_w + 4, row_cy + 5, sub.get("rolls"))
        else:
            _tr(draw, "-", x=sub_right, y=row_cy, font=f_sub,
                font_size=13, fill_color=(255, 255, 255, 90))

    draw_figma_line(img, x1=score_x - 10, y1=y + 10, x2=score_x - 10, y2=y + _ART_H - 10,
                    fill_color=_LINE, width=1)
    _t(draw, text=img_t("スコア", lang), x=score_x, y=y + 11, font=_font(10, light=True),
       align="center", box_width=score_w, font_size=10, fill_color=_SUB)
    _t(draw, text=f"{float(art.get('score', 0) or 0):.1f}", x=score_x, y=y + 25,
       font=_font(19), align="center", box_width=score_w, font_size=19, fill_color=_TXT)
    tier_path = os.path.join(STATIC_DIR, "assets", "tiers", f"{art.get('tier', 'B')}.png")
    if os.path.exists(tier_path):
        paste_figma_image(img, tier_path, box_x=score_x + (score_w - 26) / 2, box_y=y + 54,
                          box_width=26, box_height=26, radius=4, beta=beta)


def _draw_total_row(img, data, col, beta, lang="ja"):
    """総合スコア行（HTML .tc-total）: ランク56左 / スコア40(影) / 計算方法右。"""
    x = _col_x(col)
    y = _Y_TOTAL
    draw = ImageDraw.Draw(img)

    rank_size = 56
    rank_path = os.path.join(STATIC_DIR, "assets", "tiers", f"{data.get('tierSum', 'B')}.png")
    if os.path.exists(rank_path):
        paste_figma_image(img, rank_path, box_x=x + 18, box_y=y + (_TOTAL_H - rank_size) / 2,
                          box_width=rank_size, box_height=rank_size, radius=6, beta=beta)

    score_txt = f"{round(float(data.get('scoreSum', 0) or 0), 1):.1f}"
    draw_figma_text_with_shadow(
        draw, text=score_txt, x=x + 90, y=y + 22, font=_font(40), font_size=40,
        fill_color=_TXT, shadow_color=(0, 0, 0, 200), shadow_offset=(2, 3), align="left",
    )

    calc_label = data.get("calcMethodLabel") or data.get("calcMethod", "")
    if calc_label:
        _t(draw, text=img_t("計算方法", lang), x=x + COL_W - 18, y=y + 22,
           font=_font(11, light=True), align="right", font_size=11, fill_color=_SUB)
        f_calc, f_calc_size = _fit_font(draw, calc_label, 130, 16)
        _t(draw, text=calc_label, x=x + COL_W - 18, y=y + 40,
           font=f_calc, align="right", font_size=f_calc_size, fill_color=_TXT)


def _draw_empty_column(img, col, lang="ja"):
    """未選択メンバー列（HTML .tc-emptycol）: 破線枠 + ＋/メンバーを追加。"""
    x = _col_x(col)
    y = _Y_HEADER
    h = _CONTENT_BOTTOM - _Y_HEADER
    draw_figma_box(img, x=x, y=y, width=COL_W, height=h, radius=_RADIUS,
                   fill_color=(20, 27, 46, 64), outline_color=(255, 255, 255, 56),
                   outline_width=2, shadow=False)
    draw = ImageDraw.Draw(img)
    f_plus = _font(56)
    _t(draw, text="＋", x=x, y=y + h / 2 - 80, font=f_plus, align="center",
       box_width=COL_W, font_size=56, fill_color=(255, 255, 255, 115))
    f_msg = _font(16)
    _t(draw, text=img_t("メンバーを追加", lang), x=x, y=y + h / 2,
       font=f_msg, align="center", box_width=COL_W, font_size=16,
       fill_color=(255, 255, 255, 115))


def _draw_error_column(img, col, error, lang="ja"):
    """取得失敗列（HTML .tc-errcol）: 赤系破線枠 + エラーメッセージ。"""
    x = _col_x(col)
    y = _Y_HEADER
    h = _CONTENT_BOTTOM - _Y_HEADER
    draw_figma_box(img, x=x, y=y, width=COL_W, height=h, radius=_RADIUS,
                   fill_color=(20, 27, 46, 64), outline_color=(248, 113, 113, 140),
                   outline_width=2, shadow=False)
    draw = ImageDraw.Draw(img)
    msg = str(error or "")
    f_msg, msg_size = _fit_font(draw, msg, COL_W - 80, 14, min_size=9)
    _t(draw, text=msg, x=x + 40, y=y + h / 2 - msg_size * 0.7, font=f_msg,
       align="center", box_width=COL_W - 80, font_size=msg_size,
       fill_color=(252, 165, 165, 230))


def _build_flat_bg(w, h):
    """mainDisplayBox(bg-zinc-950 #09090b) と同じフラット背景。"""
    return Image.new("RGBA", (max(1, w), max(1, h)), (9, 9, 11, 255))


def _generate_team_image_sync(uid: str, char_ids: list, configs=None, boss=None, beta: str = "false", img_format: str = "png", lang: str = "ja", substat_dots: str = "1"):
    """4キャラ編成カード画像を PIL で生成して PNG bytes を返す（HTML版と同期レイアウト）。

    boss 引数は後方互換のため受け付けるが、HTML 編成カードには幽境セクションが
    存在しないため描画には使用しない。
    """
    _total_start = time.perf_counter()
    if beta != "true":
        beta = "false"
    lang = "en" if str(lang or "").lower() == "en" else "ja"
    print(f"[TeamCard] START uid={uid} chars={char_ids} beta={beta}", flush=True)

    # データ取得（configs: 各キャラの計算方法・差し替え武器/キャラ。empty=true は未選択枠）
    t0 = time.perf_counter()
    datas = []
    for i, cid in enumerate(char_ids[:4]):
        cfg = (configs[i] if configs and i < len(configs) and isinstance(configs[i], dict) else {}) or {}
        if not cid or str(cfg.get("empty") or "") == "true":
            datas.append(None)
            continue
        _display_id = str(cfg.get("fake_char") or cid)
        try:
            d = _get_card_data_sync(
                uid, str(cid),
                calc_method=cfg.get("calc_method") or "crit",
                fake_char=cfg.get("fake_char") or None,
                fake_weapon=cfg.get("fake_weapon") or None,
                beta=beta,
                base_prec=cfg.get("base_prec") or "0",
                lang=lang,
            )
            d["id"] = _display_id
        except Exception as e:
            print(f"[TeamCard] card_data fetch failed for {cid}: {e}", flush=True)
            d = {"error": str(e)}
        d["offset_key"] = _display_id
        datas.append(d)
    while len(datas) < 4:
        datas.append(None)
    print(f"[TeamCard] data fetched in {(time.perf_counter()-t0)*1000:.0f}ms", flush=True)

    payload = _render_team_datas(datas, configs, beta, lang, substat_dots)
    print(f"[TeamCard] DONE total {(time.perf_counter()-_total_start)*1000:.0f}ms", flush=True)
    return payload


def _render_team_datas(datas, configs, beta="false", lang="ja", substat_dots="1"):
    """card_data のリスト（0〜4件）から編成カード画像を描画し PNG bytes を返す。

    データ取得方法（live 取得 / 共有スナップショット）によらず、HTML 編成カードと
    同じ card_data JSON が渡れば同一の描画になる（共有画像の凍結生成用）。
    """
    if beta != "true":
        beta = "false"
    lang = "en" if str(lang or "").lower() == "en" else "ja"
    datas = list(datas or [])[:4]
    while len(datas) < 4:
        datas.append(None)

    _compute_team_badges(datas, configs)

    # カード本体（1860×1124 設計）を描いてから、余白付き背景に合成する
    t0 = time.perf_counter()
    card = Image.new("RGBA", (CARD_W_PX, CARD_H_PX), (0, 0, 0, 0))
    with _DrawCtx():
        for col, data in enumerate(datas):
            if data is None:
                _draw_empty_column(card, col, lang)
                continue
            if data.get("error"):
                _draw_error_column(card, col, data.get("error"), lang)
                continue
            _draw_panels_one(card, data, col)
            _draw_header_row_one(card, data, col, beta, lang)

            t_col = time.perf_counter()
            _draw_identity_row(card, data, col, beta)
            _draw_badges(card, data, col, beta)
            _draw_talents(card, data, col, beta)
            _draw_stats_row(card, data, col, beta)
            _draw_weapon_row(card, data, col, beta, lang)

            arts = data.get("artifacts") or []
            for i in range(5):
                art = arts[i] if i < len(arts) else None
                art_y = _Y_ART + i * (_ART_H + _ART_GAP)
                _draw_artifact_row(card, art, col, art_y, beta, lang, substat_dots)

            _draw_total_row(card, data, col, beta, lang)
            print(f"[TeamCard] column {col} drawn in {(time.perf_counter()-t_col)*1000:.0f}ms", flush=True)
    print(f"[TeamCard] card drawn in {(time.perf_counter()-t0)*1000:.0f}ms", flush=True)

    img = _build_flat_bg(BG_W_PX, BG_H_PX)
    img.alpha_composite(card, (round(_PAD * SX_TEAM), round(_PAD * SY_TEAM)))

    # 保存
    t0 = time.perf_counter()
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    buf = io.BytesIO()
    img.save(buf, "PNG", compress_level=1)
    payload = buf.getvalue()
    print(f"[TeamCard] PNG encoded {len(payload)} bytes in {(time.perf_counter()-t0)*1000:.0f}ms", flush=True)
    return payload


def _generate_team_image_from_cards(cards, beta="false", lang="ja", substat_dots="1"):
    """共有スナップショット等の card_data JSON から編成カード画像を生成する（UID不要）。

    cards: 0〜4件の card_data dict（None 可）。HTML 編成カードに渡すものと同一形式。
    表示名末尾の「(swap)」等は _compute_team_badges 側で従来と同じ処理になる。
    """
    datas = []
    for i, c in enumerate((cards or [])[:4]):
        if not isinstance(c, dict):
            datas.append(None)
            continue
        d = dict(c)
        if d.get("error"):
            datas.append(d)
            continue
        _display_id = str(d.get("id") or "")
        d.setdefault("offset_key", _display_id)
        datas.append(d)
    return _render_team_datas(datas, None, beta, lang, substat_dots)
