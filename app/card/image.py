import json
import os
import io
import time
import math
from collections import Counter
from fastapi import HTTPException
from PIL import Image, ImageDraw
from app.paths import BASE_DIR, STATIC_DIR, CARD_W, CARD_H, SX, SY, FONT_PATH, FONT_LIGHT_PATH
from app.card.cache import get_cached_font, get_resized_image
from app.card.jsoncache import load_json_cached
from app.card.stats import (
    get_stat_label, get_text_map_name, get_char_level,
    score_calc,
    sum_affix_substat_values, is_percent_prop, format_base_value, format_var_base_add,
    format_decimal_value, artifact_substat_rolls,
)
from app.card.labels import img_t
from app.card.special import (
    SPECIAL_ELEMENT_CHARACTERS, build_special_energy_hint_map,
    resolve_special_avatar_id, resolve_datas_path, resolve_list_path,
    resolve_display_skill_levels, resolve_costume_splash, _special_raw_id,
    _NO_CONSTELLATION_CHARS, _NO_FRIENDSHIP_CHARS,
)
from app.card.region import find_regions_for_character
from app.card.set_buffs import set_buff_label
from app.card.resonance import resonance_badges, parse_resonance_param
from app.card.stat_calc import compute_manual_totals
from app.card.calc_method import resolve_calc_method
from app.card.growth import build_growth_panel, build_growth_from_fake
from app.card.bg import hex_to_rgb, create_card_background, region_image_path
from app.card.draw import (
    draw_figma_box, paste_mask_image,
    draw_figma_circle, paste_figma_image, draw_figma_text, draw_figma_line,
    draw_figma_text_right,
    draw_figma_dot, draw_figma_glass_box, figma_draw_theme,
    safe_rounded_rectangle,
)

# 育成モードの右パネル寸法（キャンバスピクセル）: 全体を等方縮小して右に追加する
# HTML 版（1200px デザイン: カード930 + パネル270、余白なし）を 2400px キャンバスに 2倍で再現
_GROWTH_PANEL_W = 540
_GROWTH_PANEL_GAP = 0
_GROWTH_PANEL_MARGIN = 0

# サブステ伸び値タイアの色（0=青 / 1=黄緑 / 2=黄 / 3=赤）
ROLL_DOT_COLORS = [(34, 197, 94, 255), (59, 130, 246, 255), (168, 85, 247, 255), (249, 115, 22, 255)]


def _wrap_jp(draw, text, font, max_w):
    """JP テキストを幅に合わせて折り返す（禁則処理なしの簡易版）。"""
    lines = []
    cur = ""
    for ch in str(text):
        if not cur:
            cur = ch
            continue
        if draw.textlength(cur + ch, font=font) <= max_w:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def _draw_wrapped(draw, text, x, y, font, fill, max_w, line_h):
    lines = _wrap_jp(draw, text, font, max_w)
    for ln in lines:
        draw.text((x, y), ln, font=font, fill=fill)
        y += line_h
    return y


def _r2(v) -> float | None:
    """HTML版 roundTo2 相当: 小数第2位まで（Math.round(v*100)/100）"""
    if v is None:
        return None
    if v >= 0:
        return math.floor(v * 100 + 0.5) / 100.0
    return math.ceil(v * 100 - 0.5) / 100.0


def _draw_panel_text_with_shadow(draw, xy, text, font, fill, shadow=(0, 0, 0, 200), offset=2, anchor=None):
    """1px〜2px の黒影を付けてテキストを描く（HTML の text-shadow 相当）。"""
    x, y = xy
    sx, sy = int(offset), int(offset)
    draw.text((x + sx, y + sy), text, font=font, fill=shadow, anchor=anchor)
    draw.text((x, y), text, font=font, fill=fill, anchor=anchor)


# 英語モードで聖遺物ボックス内（幅が狭い）に使う短縮ラベル
_STAT_ABBR_EN = {"Elemental Mastery": "EM", "Energy Recharge": "ER"}


def _stat_abbr_en(name: str) -> str:
    return _STAT_ABBR_EN.get(name, name)


def _draw_uid_badge(img, draw, uid, beta):
    """聖遺物行とカード下端の隙間・左下へ UID テキストを描画する。"""
    text = f"UID {uid}" if uid else ""
    if not text:
        return
    font_badge = get_cached_font(FONT_PATH, max(1, round(14 * SY)))
    # 下端の隙間: 聖遺物行の下端 y=1137 〜 カード下端 y=1159。左端は聖遺物行の左端 x=33 に揃える
    left_x = 33
    cy = 1139
    draw_figma_text(draw, text=text, x=left_x, y=cy + 2, font=font_badge, align="left",
                    font_size=14, fill_color=(255, 255, 255, 220),
                    stroke_width=2, stroke_fill=(0, 0, 0, 160))


# 元素共鳴チップ（カード下端・右寄せ）。HTML ガラスカードの .resonance-corner と同じ見た目を狙う。
_RESONANCE_NAME_COLORS = {
    "dark": {"bg": (8, 12, 22, 158), "border": (255, 255, 255, 46), "name": (125, 255, 175, 255), "buff": (255, 210, 125, 255)},
    "light": {"bg": (255, 255, 255, 184), "border": (15, 23, 42, 31), "name": (4, 120, 87, 255), "buff": (180, 83, 9, 255)},
}


def _draw_resonance_badges(img, draw, resonance, beta, lang="ja", light="false"):
    """単体カード下端の隙間・右側に元素共鳴チップを描画する（HTMLの右下ピルと同じ仕様）。"""
    badges = resonance_badges(resonance, lang)
    if not badges:
        return
    pal = _RESONANCE_NAME_COLORS["light" if str(light or "") == "true" else "dark"]
    font_name = get_cached_font(FONT_PATH, max(1, round(14 * SY)))
    font_buff = get_cached_font(FONT_PATH, max(1, round(13 * SY)))
    chip_h = 20
    pad_x = 10
    gap_x = 6
    icon = 12
    # 下端の隙間: 聖遺物行の下端 y=1137 〜 カード下端 y=1159（UIDと同じ列の高さ）
    right_x = 1715
    cy = 1137
    dims = []
    for b in badges:
        text = str(b.get("text") or "")
        label = str(b.get("label") or "")
        if not text:
            continue
        elem = b.get("elem")
        icon_path = f"static/assets/props/{str(elem).lower()}.png" if elem else ""
        w_name = draw.textlength(text, font=font_name) / SX
        w_buff = (draw.textlength(label, font=font_buff) / SX + 4) if label else 0
        w_icon = (icon + 4) if icon_path else 0
        dims.append((text, label, icon_path, pad_x * 2 + w_icon + w_name + w_buff))
    if not dims:
        return
    total_w = sum(w for *_, w in dims) + gap_x * (len(dims) - 1)
    cx = right_x - total_w
    radius = chip_h / 2
    for text, label, icon_path, w in dims:
        draw_figma_box(img, x=cx, y=cy, width=w, height=chip_h, radius=radius,
                       fill_color=pal["bg"], outline_color=pal["border"], outline_width=1, shadow=False)
        tx = cx + pad_x
        if icon_path:
            paste_figma_image(img, icon_path, box_x=tx, box_y=cy + (chip_h - icon) / 2,
                              box_width=icon, box_height=icon, radius=4, beta=beta)
            tx += icon + 4
        draw_figma_text(draw, text=text, x=tx, y=cy + 3, font=font_name, align="left",
                        font_size=14, fill_color=pal["name"])
        tx += draw.textlength(text, font=font_name) / SX
        if label:
            draw_figma_text(draw, text=label, x=tx + 4, y=cy + 4, font=font_buff, align="left",
                            font_size=13, fill_color=pal["buff"])
        cx += w + gap_x


def _lighten_background(img, alpha=110):
    """ライトモード用: 背景全体を白で薄くする（立ち絵は後から貼るため影響しない）。"""
    overlay = Image.new("RGBA", img.size, (255, 255, 255, alpha))
    return Image.alpha_composite(img, overlay)


def _draw_growth_panel(img, panel, panel_x0, panel_x1, panel_h, bg_base_rgb, base_prec="0", light="false", lang="ja"):
    """カード右側に育成パネルを描画する。HTML 版 growth-panel と同レイアウト。

    - パネル幅 = 540（HTML の 270px を 2400px キャンバスに 2倍で再現）
    - 高さ = 縮小カードと同じ panel_h（HTML の 620px 相当）
    - セクション: 聖遺物セット効果 / 基礎ステータス %換算 / サブステ伸び平均
      （「1回あたりの平均」サブヘッド + HP/攻撃/防御 の %・実数を緑で表示）
    - 武器（精錬効果）セクションは HTML と同様に非表示
    """
    is_light = str(light or "") == "true"
    x0 = panel_x0
    w = panel_x1 - x0
    h = int(panel_h)
    y0 = 0

    # パネル背景（HTML の linear-gradient + border 相当）
    if is_light:
        top_rgb = (255, 255, 255)
        bot_rgb = (241, 245, 249)
        panel_alpha = 0.96
        border_c = (15, 23, 42, 40)
    else:
        top_rgb = (20, 24, 34)
        bot_rgb = (14, 16, 24)
        panel_alpha = 0.92
        border_c = (255, 255, 255, 46)
    gradient = Image.new("RGBA", (1, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(gradient)
    for yy in range(h):
        t = yy / max(1, h - 1)
        col = tuple(int(top_rgb[i] + (bot_rgb[i] - top_rgb[i]) * t) for i in range(3))
        gd.point((0, yy), fill=(col[0], col[1], col[2], 255))
    gradient = gradient.resize((w, h), Image.LANCZOS)

    radius = 32
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    safe_rounded_rectangle(md, [0, 0, w - 1, h - 1], radius=radius, fill=255)
    gradient.putalpha(mask.point(lambda p: int(p * panel_alpha)))

    layer = Image.new("RGBA", (w + 2, h + 2), (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    safe_rounded_rectangle(
        ld,
        [0, 0, w, h], radius=radius,
        outline=border_c, width=2,
    )
    layer.alpha_composite(gradient, (1, 1))
    img.alpha_composite(layer, dest=(x0 - 1, y0 - 1))

    draw = ImageDraw.Draw(img)
    pad = 28
    inner_x = x0 + pad
    right_x = panel_x1 - pad
    max_w = w - pad * 2
    cur_y = y0 + pad

    if is_light:
        gold = (180, 83, 9, 255)
        head_c = (3, 105, 161, 255)
        white = (17, 24, 39, 255)
        label_c = (15, 23, 42, 170)
        desc_c = (15, 23, 42, 140)
        subhead_c = (15, 23, 42, 110)
        green = (4, 120, 87, 255)
        sep_c = (15, 23, 42, 40)
        shadow_c = (255, 255, 255, 220)
    else:
        gold = (255, 205, 120, 255)
        head_c = (125, 210, 255, 255)
        white = (240, 244, 250, 255)
        label_c = (205, 211, 222, 255)          # rgba(255,255,255,0.8) 相当
        desc_c = (168, 176, 190, 255)           # rgba(255,255,255,0.65) 相当
        subhead_c = (150, 158, 174, 255)        # rgba(255,255,255,0.55) 相当
        green = (110, 230, 160, 255)
        sep_c = (255, 255, 255, 255)
        shadow_c = (0, 0, 0, 200)

    title_font = get_cached_font(FONT_PATH, 48)
    head_font = get_cached_font(FONT_PATH, 32)
    subhead_font = get_cached_font(FONT_PATH, 28)
    set_name_font = get_cached_font(FONT_PATH, 36)
    set_desc_font = get_cached_font(FONT_LIGHT_PATH, 30)
    stat_label_font = get_cached_font(FONT_LIGHT_PATH, 34)
    stat_value_font = get_cached_font(FONT_PATH, 34)

    def _section_head(label):
        nonlocal cur_y
        _draw_panel_text_with_shadow(draw, (inner_x, cur_y), label, head_font, head_c, shadow=shadow_c)
        cur_y += int(head_font.size * 1.35) + 8
        draw.line((inner_x, cur_y, right_x, cur_y), fill=sep_c, width=2)
        cur_y += 14

    def _stat_row(label, value):
        nonlocal cur_y
        _draw_panel_text_with_shadow(draw, (inner_x, cur_y), label, stat_label_font, label_c, shadow=shadow_c, offset=1)
        _draw_panel_text_with_shadow(draw, (right_x, cur_y), value, stat_value_font, green, shadow=shadow_c, offset=1, anchor="ra")
        cur_y += int(stat_value_font.size * 1.4) + 10

    # タイトル
    _draw_panel_text_with_shadow(draw, (inner_x, cur_y), img_t("育成メモ", lang), title_font, gold, shadow=shadow_c)
    cur_y += int(title_font.size * 1.4) + 10

    if not panel:
        return
    cur_y += 2

    # 1) 聖遺物セット効果
    if panel.get("sets"):
        _section_head(img_t("聖遺物セット効果", lang))
        for st in panel["sets"]:
            _draw_panel_text_with_shadow(draw, (inner_x, cur_y), f"{st['name']} ×{st['count']}", set_name_font, white, shadow=shadow_c, offset=1)
            cur_y += int(set_name_font.size * 1.35) + 6
            if st.get("set2"):
                # ステータス反映済み（apply_2set_buffs）の2セット効果は白文字で強調
                _2set_c = white if st.get("buff_applied") else desc_c
                cur_y = _draw_wrapped(draw, f"{img_t('2セット:', lang)} {st['set2']}", inner_x, cur_y, set_desc_font, _2set_c, max_w, int(set_desc_font.size * 1.5))
            # 4セット効果は4点以上装備時のみ表示（2セット/2セット 編成では非表示）
            if int(st.get("count", 0)) >= 4 and st.get("set4"):
                cur_y = _draw_wrapped(draw, f"{img_t('4セット:', lang)} {st['set4']}", inner_x, cur_y, set_desc_font, desc_c, max_w, int(set_desc_font.size * 1.5))
            cur_y += 8
        cur_y += 18

    # 2) 基礎ステータス %換算
    sp = panel.get("stat1pct")
    if sp:
        _section_head(img_t("基礎ステータス %換算", lang))
        _stat_row(f"{sp['label']} {img_t('1%あたり', lang)}", str(sp["value"]))
        cur_y += 18

    # 3) サブステ伸び平均（1回あたり）: HP / 攻撃 / 防御 を % と実数で表示
    sub_list = panel.get("subavg_all") or []
    rows = []
    if lang == "en":
        short = {"hp": "HP", "atk": "ATK", "def": "DEF"}
    else:
        short = {"hp": "HP", "atk": "攻撃", "def": "防御"}
    for s in sub_list:
        label = short.get(s.get("key"), s.get("label") or "")
        if s.get("pct_avg") is not None:
            paren = f" ({format_decimal_value(s['flat_equiv'], base_prec)})" if s.get("flat_equiv") is not None else ""
            rows.append((f"{label}%", f"{_r2(s['pct_avg']):.2f}%{paren}"))
        if s.get("flat_avg") is not None:
            rows.append((f"{label}{img_t('実数', lang)}", format_decimal_value(s['flat_avg'], base_prec)))
    if rows:
        _section_head(img_t("サブステ伸び平均", lang))
        _draw_panel_text_with_shadow(draw, (inner_x, cur_y), img_t("1回あたりの平均", lang), subhead_font, subhead_c, shadow=shadow_c, offset=1)
        cur_y += int(subhead_font.size * 1.4) + 8
        for label, value in rows:
            _stat_row(label, value)


def _attach_growth_panel(img, panel, bg_base_rgb, splash_path, element_type,
                         use_prebuilt, region, panel_w=_GROWTH_PANEL_W,
                         gap=_GROWTH_PANEL_GAP, margin=_GROWTH_PANEL_MARGIN,
                         base_prec="0", light="false", lang="ja"):
    """カード全体を等方縮小し、右側に育成パネルを追加する（幅は変えない）。

    元 img は CARD_W x CARD_H。縮小率 k = (CARD_W - panel_w - gap - margin) / CARD_W で
    縦横同じ割合で縮小し、右の余白に panel_w 分のパネルを描く。
    """
    card_w, card_h = CARD_W, CARD_H
    content_w = max(1200, int(round(card_w * ((card_w - panel_w - gap - margin) / card_w))))
    k = content_w / card_w
    content_h = max(1, int(round(card_h * k)))

    skinned = img.resize((content_w, content_h), Image.LANCZOS)

    # 新しいキャンバス（背景は同じ設定で再生成 → 縮小領域外を覆う）
    bg_full = create_card_background(
        card_w, card_h, bg_base_rgb,
        splash_path=splash_path,
        element_type=element_type,
        use_prebuilt=use_prebuilt,
        region=region,
    )
    if str(light or "") == "true":
        bg_full = _lighten_background(bg_full)
    bg_full.paste(skinned, (0, 0), skinned)

    panel_x0 = content_w + gap
    panel_x1 = card_w - margin
    _draw_growth_panel(bg_full, panel, panel_x0, panel_x1, content_h, bg_base_rgb, base_prec, light, lang)
    # 縮小カード+パネルの下端（content_h）で切り抜き、下部の余白を除去する
    bg_full = bg_full.crop((0, 0, card_w, content_h))
    return bg_full


def _generate_card_image_sync(uid: str, avatar_id: str, calc_method: str, fake_char: str = None, fake_weapon: str = None, beta: str = "false", bg_color: str = None, img_format: str = "png", bg_mode: str = None, bg_region: str = None, growth: str = "false", base_prec: str = "0", substat_dots: str = "1", resonance: str = None, light: str = "false", show_uid: str = "false", lang: str = "ja"):
    _total_start = time.perf_counter()

    def _plog(msg: str) -> None:
        """画像生成の区間ログ。バッファで消えないよう必ず flush。"""
        elapsed = (time.perf_counter() - _total_start) * 1000.0
        print(f"[Perf] +{elapsed:.0f}ms | {msg}", flush=True)

    if beta != "true":
        beta = "false"
    # カード画像の表示言語（"ja" / "en"）。名前・聖遺物・見出しなどの描画テキストを切替える。
    lang = "en" if str(lang or "").lower() == "en" else "ja"

    if lang == "en":
        def _stat_abbr(name):
            # 聖遺物ボックス内は幅が狭いため長いラベルを短縮形にする
            return _STAT_ABBR_EN.get(name, name)
    else:
        def _stat_abbr(name):
            return name
    growth = "true" if str(growth or "") == "true" else "false"
    light = "true" if str(light or "") == "true" else "false"
    show_uid = "true" if str(show_uid or "") == "true" else "false"
    base_prec = str(base_prec or "0")
    if base_prec not in ("0", "2", "4"):
        base_prec = "0"
    substat_dots = "1" if str(substat_dots or "") in ("1", "true") else "0"
    # 計算方式が未指定/不正ならキャラ毎デフォルト（admin 設定）→ "crit" に解決する。
    calc_method = resolve_calc_method(calc_method, avatar_id)
    _plog(
        f"START uid={uid} avatar={avatar_id} method={calc_method} "
        f"format={img_format} beta={beta} fake_char={fake_char} bg_mode={bg_mode} bg_region={bg_region} growth={growth}"
    )
    print(
        f"[Cache Miss] 初回生成のため、PILで気合を入れて画像を作ります...: "
        f"UID:{uid} - CharID:{avatar_id} - Method:{calc_method}",
        flush=True,
    )

    t_start = time.perf_counter()
    target_avatar_info = None
    json_path = os.path.join(STATIC_DIR, "cache", f"showcase_{uid}.json")
    json_path = resolve_datas_path(json_path, beta)

    if os.path.exists(json_path):
        # mtime 無効化のメモリキャッシュ: 同一UIDの連続生成で再パースを省略
        showcase_data = load_json_cached(json_path)

        avatar_list = showcase_data.get("avatarInfoList")
        if not avatar_list and "playerInfo" in showcase_data:
            player_info = showcase_data["playerInfo"]
            avatar_list = player_info.get("showAvatarInfoList") or player_info.get("show_avatar_info_list")

        if avatar_list:
            energy_hint_map = build_special_energy_hint_map(showcase_data)
            for avatar in avatar_list:
                raw_id = str(avatar.get("avatarId"))
                loop_avatar_id = raw_id
                if raw_id in SPECIAL_ELEMENT_CHARACTERS:
                    loop_avatar_id = resolve_special_avatar_id(avatar, beta, energy_hint_map.get(raw_id))
                if str(loop_avatar_id) == str(avatar_id):
                    target_avatar_info = avatar
                    break

    if not target_avatar_info:
        raise HTTPException(status_code=404, detail=f"Avatar ID {avatar_id} not found in showcase.")
    t_end = time.perf_counter()
    print(f"[Perf] JSON読み込み・パース: {(t_end - t_start)*1000:.1f}ms", flush=True)

    t_start = time.perf_counter()
    if fake_char:
        json_path2 = os.path.join(STATIC_DIR, "data", "characters", f"{fake_char}.json")
        if not os.path.exists(json_path2):
            if beta == "true":
                json_path2 = os.path.join(STATIC_DIR, "beta", "data", "characters", f"{fake_char}.json")
    else:
        json_path2 = os.path.join(STATIC_DIR, "data", "characters", f"{avatar_id}.json")
    json_path2 = resolve_datas_path(json_path2, beta)

    if os.path.exists(json_path2):
        chardatas = load_json_cached(json_path2)
    else:
        base_avatar_id = str(avatar_id).split("-")[0]
        backup_path = os.path.join(STATIC_DIR, "data", "characters", f"{base_avatar_id}.json")
        backup_path = resolve_datas_path(backup_path, beta)
        if os.path.exists(backup_path):
            chardatas = load_json_cached(backup_path)
        else:
            raise HTTPException(status_code=404, detail=f"Character JSON file not found: {json_path2}")
    t_end = time.perf_counter()
    print(f"[Perf] キャラJSON読み込み: {(t_end - t_start)*1000:.1f}ms", flush=True)

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

    # 言語別ラベル（カード画像の描画テキスト用）
    if lang == "en":
        _lb_atk, _lb_def, _lb_em = "ATK", "DEF", "Elemental Mastery"
        _lb_cr, _lb_cd, _lb_er = "CRIT Rate", "CRIT DMG", "Energy Recharge"
        _dmg_buff_label = f"{element_type} DMG Bonus" if element_type != "None" else "DMG Bonus"
        _txt_no_weapon = "Not equipped"
        _txt_unknown_weapon = "Unknown Weapon"
        _txt_unknown_artifact = "Unknown Artifact"
    else:
        _lb_atk, _lb_def, _lb_em = "攻撃力", "防御力", "元素熟知"
        _lb_cr, _lb_cd, _lb_er = "会心率", "会心ダメージ", "元素チャージ効率"
        _dmg_buff_label = f"{element_ja}ダメバフ"
        _txt_no_weapon = "未装備"
        _txt_unknown_weapon = "未知の武器"
        _txt_unknown_artifact = "未知の聖遺物"

    element_base_rgb = element_colors.get(element_type, (0x4A, 0x55, 0x68))
    custom_rgb = hex_to_rgb(bg_color) if bg_color else None
    bg_base_rgb = custom_rgb if custom_rgb else element_base_rgb
    base_color = (*bg_base_rgb, 255)

    char_regions = find_regions_for_character(_special_raw_id(fake_char or avatar_id), element_type)

    # 展示データの costumeId があればコスチュームスプラッシュ（UI_Costume_*）を使用
    splash = resolve_costume_splash(chardatas, target_avatar_info, beta)
    if not splash:
        splash = f"static/assets/splash/{chardatas['icon'].replace('AvatarIcon', 'Gacha_AvatarImg')}.webp"
        splash = resolve_datas_path(splash, beta)

    char_name = chardatas["name"]
    if lang == "en":
        # キャラ詳細 JSON の en_name を優先（無ければ日本語名にフォールバック）
        char_name = chardatas.get("en_name") or char_name
    if fake_char:
        char_name = f"{char_name}(swap)"

    if fake_char:
        char_level = 90
    else:
        char_level = get_char_level(target_avatar_info)

    raw_special_id = _special_raw_id(avatar_id)
    if fake_char:
        friendship_lv = 10
    elif raw_special_id in _NO_FRIENDSHIP_CHARS:
        friendship_lv = None
    else:
        friendship_lv = target_avatar_info.get("fetterInfo", {}).get("expLevel", 1)

    skill_level, skill_boosted = resolve_display_skill_levels(target_avatar_info, fake_char=bool(fake_char))
    skill_icon = [chardatas["skills"][0]["icon"], chardatas["skills"][1]["icon"], chardatas["skills"][2]["icon"]]

    y_C_base = 139
    circle_size = 68

    # 命の星座は「表示中キャラ」（差し替えがあれば差し替え先）の有無に従う。
    # 表示中キャラを基準にしないと、ドールへ差し替えた旅人が
    # 存在しない constellations を参照してエラーになる。
    hide_constellation = _special_raw_id(fake_char or avatar_id) in _NO_CONSTELLATION_CHARS
    if fake_char or hide_constellation:
        constellation_releas_num = 0
    else:
        constellation_releas_num = len(target_avatar_info.get("talentIdList", []))

    if hide_constellation:
        Constellation_icon = []
    else:
        Constellation_icon = [chardatas["constellations"][i]["icon"] for i in range(6)]

    t_start = time.perf_counter()
    weapon_data = next((item for item in target_avatar_info.get("equipList", []) if "weapon" in item), None)
    # 未装備（ショーケースに武器が無い）でもカード生成を続行する
    weapon_name = _txt_no_weapon
    weapon_icon = ""
    weapon_level = 0
    weapon_affix = 0
    weapon_stats_list = []
    weapon_id = None

    if fake_weapon:
        weapon_id = fake_weapon
        weapon_json_path = resolve_datas_path(f"static/data/weapons/{weapon_id}.json", beta)
        weapon_jsondata = load_json_cached(weapon_json_path)
        # レアリティ1・2の武器はLv70（3以上はLv90）
        weapon_level = 70 if int(weapon_jsondata.get("rarity", 3)) in (1, 2) else 90
        weapon_affix = 1
        weapon_icon = weapon_jsondata.get("icon", "")
        weapon_name = weapon_jsondata.get("name", _txt_unknown_weapon)
        # サブオプション（会心率・チャージ効率など）は武器によって存在しないため、
        # 無い場合はエントリごとパスする（PIL描画側も None でスキップされる）
        stats_modifier = weapon_jsondata.get("stats_modifier") or {}
        weapon_stats_list = [{'appendPropId': 'FIGHT_PROP_BASE_ATTACK', 'statValue': stats_modifier.get("atk", 0.0)}]
        sub_keys = [k for k in stats_modifier.keys() if k != "atk"]
        if sub_keys:
            second_key = sub_keys[0]
            stat_calc = stats_modifier[second_key]
            if stat_calc < 1:
                stat_calc = round(stat_calc * 100, 1)
            else:
                stat_calc = round(stat_calc)
            weapon_stats_list.append({'appendPropId': second_key.upper(), 'statValue': stat_calc})
    elif weapon_data:
        weapon_id = weapon_data["itemId"]
        weapon_json_path = resolve_datas_path(f"static/data/weapons/{weapon_id}.json", beta)
        weapon_name = _txt_unknown_weapon
        if os.path.exists(weapon_json_path):
            try:
                weapon_jsondata = load_json_cached(weapon_json_path)
                weapon_name = weapon_jsondata.get("name", _txt_unknown_weapon)
            except (UnicodeDecodeError, json.JSONDecodeError, OSError):
                pass
        weapon_icon = (weapon_data.get("flat") or {}).get("icon") or ""
        weapon_level = (weapon_data.get("weapon") or {}).get("level") or 1
        affix_map = (weapon_data.get("weapon") or {}).get("affixMap") or {}
        weapon_affix = (list(affix_map.values())[0] + 1) if affix_map else 1
        weapon_stats_list = (weapon_data.get("flat") or {}).get("weaponStats") or []
    else:
        print("[Warning] no weapon equipped for avatar (uid showcase) — drawing as 未装備")

    # 英語モード時は武器名を lists/weapons.json の enName で置き換える
    # （武器詳細 JSON には日本語 name しか無いため）
    if lang == "en" and weapon_id:
        try:
            _wp_list_path = resolve_list_path("static/data/lists/weapons.json", beta)
            if os.path.exists(_wp_list_path):
                _wp_entry = (load_json_cached(_wp_list_path) or {}).get(str(weapon_id)) or {}
                weapon_name = _wp_entry.get("enName") or weapon_name
        except Exception:
            pass

    # キー欠落のエントリはパス（描画側も None でスキップされる）
    weapon_stat1 = None
    if len(weapon_stats_list) >= 1:
        entry1 = weapon_stats_list[0] or {}
        prop_id1 = entry1.get("appendPropId", "")
        if prop_id1:
            stat_name1 = get_stat_label(prop_id1, lang)
            stat_val1 = entry1.get("statValue", 0.0)
            try:
                if "PERCENT" in prop_id1 or "CRITICAL" in prop_id1 or "CHARGE" in prop_id1:
                    stat_val1_str = f"{format_decimal_value(float(stat_val1), base_prec)}%"
                else:
                    stat_val1_str = f"{int(stat_val1)}"
            except Exception:
                stat_val1_str = str(stat_val1)
            weapon_stat1 = (stat_name1, stat_val1_str)

    weapon_stat2 = None
    if len(weapon_stats_list) >= 2:
        entry2 = weapon_stats_list[1] or {}
        prop_id2 = entry2.get("appendPropId", "")
        if prop_id2:
            stat_name2 = get_stat_label(prop_id2, lang)
            stat_val2 = entry2.get("statValue", 0.0)
            try:
                if "PERCENT" in prop_id2 or "CRITICAL" in prop_id2 or "CHARGE" in prop_id2 or "HURT" in prop_id2:
                    stat_val2_str = f"{format_decimal_value(float(stat_val2), base_prec)}%"
                else:
                    stat_val2_str = f"{int(stat_val2)}"
            except Exception:
                stat_val2_str = str(stat_val2)
            weapon_stat2 = (stat_name2, stat_val2_str)
    t_end = time.perf_counter()
    print(f"[Perf] 武器データ処理: {(t_end - t_start)*1000:.1f}ms", flush=True)

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
            # 差し替えキャラの基礎攻撃力には武器基礎攻撃力が含まれないため加算する
            weapon_base_included = False
        else:
            base_hp = target_avatar_info.get('fightPropMap', {}).get('1', 1)
            base_atk = target_avatar_info.get('fightPropMap', {}).get('4', 1)
            base_def = target_avatar_info.get('fightPropMap', {}).get('7', 1)
            base_crit_rate = 0.05
            base_crit_dmg = 0.5
            base_em = 0.0
            # 差し替え武器の基礎攻撃力を別途加算する
            weapon_base_included = False

        totals = compute_manual_totals(
            base_hp=base_hp,
            base_atk=base_atk,
            base_def=base_def,
            base_crit_rate=base_crit_rate,
            base_crit_dmg=base_crit_dmg,
            base_em=base_em,
            weapon_stats_list=weapon_stats_list,
            raw_artifacts=raw_artifacts,
            chardatas=chardatas,
            element_type=element_type,
            beta=beta,
            weapon_base_included_in_base_atk=weapon_base_included,
            resonance=resonance,
        )

        dmg_buff_val = "0%"
        if element_type in ("Pyro", "Hydro", "Anemo", "Electro", "Dendro", "Geo", "Cryo"):
            buff_val = totals["dmg_buff"]["val"]
            if buff_val > 0:
                dmg_buff_val = f"{format_decimal_value(buff_val * 100, base_prec)}%"

        _hp_v, _hp_b, _hp_a = format_var_base_add(totals["hp"]["val"], totals["hp"]["base"], base_prec)
        _atk_v, _atk_b, _atk_a = format_var_base_add(totals["atk"]["val"], totals["atk"]["base"], base_prec)
        _def_v, _def_b, _def_a = format_var_base_add(totals["def"]["val"], totals["def"]["base"], base_prec)
        stats_mock = {
            "HP": {"val": _hp_v, "base": _hp_b, "add": "+" + _hp_a, "icon": "static/assets/props/hp.png"},
            _lb_atk: {"val": _atk_v, "base": _atk_b, "add": "+" + _atk_a, "icon": "static/assets/props/atk.png"},
            _lb_def: {"val": _def_v, "base": _def_b, "add": "+" + _def_a, "icon": "static/assets/props/def.png"},
            _lb_em: {"val": format_base_value(totals["em"]["val"], base_prec), "icon": "static/assets/props/em.png"},
            _lb_cr: {"val": f"{format_decimal_value(totals['crit_rate']['val'] * 100, base_prec)}%", "icon": "static/assets/props/rate.webp"},
            _lb_cd: {"val": f"{format_decimal_value(totals['crit_dmg']['val'] * 100, base_prec)}%", "icon": "static/assets/props/dmg.webp"},
            _lb_er: {"val": f"{format_decimal_value(totals['er']['val'] * 100, base_prec)}%", "icon": "static/assets/props/er.png"},
            _dmg_buff_label: {"val": dmg_buff_val, "icon": f"static/assets/props/{element_type.lower()}.png"},
        }
    else:
        prop_map = target_avatar_info.get('fightPropMap', {})

        # 実キャラも差し替えと同一の手動計算でステータスを導出する。
        # fightPropMap['4'](基礎攻撃力) には武器基礎攻撃力が既に含まれるため、
        # weapon_base_included_in_base_atk=True で二重加算を防ぐ。
        # 共鳴なしの実キャラは fightPropMap の最終値を EM/ER に採用（丸め境界対策）。
        _has_resonance = bool(parse_resonance_param(resonance))
        totals = compute_manual_totals(
            base_hp=prop_map.get('1', 1),
            base_atk=prop_map.get('4', 1),
            base_def=prop_map.get('7', 1),
            base_crit_rate=0.05,
            base_crit_dmg=0.5,
            base_em=0.0,
            weapon_stats_list=weapon_stats_list,
            raw_artifacts=raw_artifacts,
            chardatas=chardatas,
            element_type=element_type,
            beta=beta,
            weapon_base_included_in_base_atk=True,
            resonance=resonance,
            authoritative_em=None if _has_resonance else prop_map.get('28'),
            authoritative_er=None if _has_resonance else prop_map.get('23'),
        )

        dmg_buff_val = "0%"
        if totals["dmg_buff"]["val"] > 0:
            dmg_buff_val = f"{format_decimal_value(totals['dmg_buff']['val'] * 100, base_prec)}%"

        _hp_v, _hp_b, _hp_a = format_var_base_add(totals["hp"]["val"], totals["hp"]["base"], base_prec)
        _atk_v, _atk_b, _atk_a = format_var_base_add(totals["atk"]["val"], totals["atk"]["base"], base_prec)
        _def_v, _def_b, _def_a = format_var_base_add(totals["def"]["val"], totals["def"]["base"], base_prec)
        stats_mock = {
            "HP": {"val": _hp_v, "base": _hp_b, "add": "+" + _hp_a, "icon": "static/assets/props/hp.png"},
            _lb_atk: {"val": _atk_v, "base": _atk_b, "add": "+" + _atk_a, "icon": "static/assets/props/atk.png"},
            _lb_def: {"val": _def_v, "base": _def_b, "add": "+" + _def_a, "icon": "static/assets/props/def.png"},
            _lb_em: {"val": format_base_value(totals["em"]["val"], base_prec), "icon": "static/assets/props/em.png"},
            _lb_cr: {"val": f"{format_decimal_value(totals['crit_rate']['val'] * 100, base_prec)}%", "icon": "static/assets/props/rate.webp"},
            _lb_cd: {"val": f"{format_decimal_value(totals['crit_dmg']['val'] * 100, base_prec)}%", "icon": "static/assets/props/dmg.webp"},
            _lb_er: {"val": f"{format_decimal_value(totals['er']['val'] * 100, base_prec)}%", "icon": "static/assets/props/er.png"},
            _dmg_buff_label: {"val": dmg_buff_val, "icon": f"static/assets/props/{element_type.lower()}.png"},
        }

    artifact_x_list = [33, 375, 718, 1061, 1404]
    slot_to_index = {"4": 0, "2": 1, "5": 2, "1": 3, "3": 4}

    artifacts_mock = []
    for _ in range(5):
        artifacts_mock.append({
            "set": "0", "name": _txt_no_weapon, "upgrade": 0,
            "Main": ["-", "-"],
            "stats": {i: ["static/assets/props/atk_per.png", "-", "-", []] for i in range(4)},
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
        # text_map.json は {言語: {キー: テキスト}} 構造のため言語サブ辞書から引く
        artifact_name = get_text_map_name(name_hash, lang, _txt_unknown_artifact)

        main_stat_raw = flat.get("reliquaryMainstat", {})
        main_prop_id = main_stat_raw.get("mainPropId", "")
        main_name = _stat_abbr(get_stat_label(main_prop_id, lang))
        main_val = main_stat_raw.get("statValue", 0)
        if "PERCENT" in main_prop_id or "CRITICAL" in main_prop_id or "CHARGE" in main_prop_id or "HURT" in main_prop_id:
            main_value_str = f"{format_decimal_value(main_val, base_prec)}%"
        else:
            main_value_str = format_decimal_value(main_val, base_prec)

        crit_rate = 0.0
        crit_dmg = 0.0
        target_stat_val = 0.0
        sub_stats_dict = {}
        sub_list = flat.get("reliquarySubstats", [])
        sub_sums = sum_affix_substat_values(reliquary.get("appendPropIdList"))
        sub_rolls = artifact_substat_rolls(reliquary)

        for idx in range(4):
            if idx < len(sub_list):
                sub_data = sub_list[idx]
                sub_prop_id = sub_data.get("appendPropId", "")
                sub_name = _stat_abbr(get_stat_label(sub_prop_id, lang))
                sub_val = sub_sums.get(sub_prop_id, sub_data.get("statValue", 0))

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
                if is_percent_prop(sub_prop_id):
                    sub_value_str = f"{format_decimal_value(sub_val, base_prec)}%"
                else:
                    sub_value_str = format_decimal_value(sub_val, base_prec)
                sub_roll_tiers = (sub_rolls.get(sub_prop_id) or {}).get("tiers", [])
                sub_stats_dict[idx] = [sub_icon_path, sub_name, sub_value_str, list(sub_roll_tiers)]
            else:
                sub_stats_dict[idx] = ["static/assets/props/atk_per.png", "-", "-", []]

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
            "score": round(art_score, 1),
            "tier": art_tier,
            "icon": icon_name
        }
        score_sum += art_score
    t_end = time.perf_counter()
    print(f"[Perf] 聖遺物データ処理: {(t_end - t_start)*1000:.1f}ms", flush=True)

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
        _fallback_name = f"Set {set_id_str}" if lang == "en" else f"セット {set_id_str}"
        for raw_path in possible_paths:
            path = resolve_list_path(raw_path, beta)
            if os.path.exists(path):
                art_json = load_json_cached(path)
                try:
                    if set_id_str in art_json:
                        _entry = art_json[set_id_str]
                        # 英語モード時は enname を優先（無ければ janame にフォールバック）
                        if lang == "en":
                            name = _entry.get("enname") or _entry.get("janame") or _fallback_name
                        else:
                            name = _entry.get("janame", _fallback_name)
                        icon_field = _entry.get("icon", "UI_RelicIcon_15046_4")
                        icon_path = resolve_datas_path(f"static/assets/artifacts/{icon_field}.webp", beta)
                        return name, icon_path
                except Exception:
                    pass
        return _fallback_name, resolve_datas_path("static/assets/artifacts/UI_RelicIcon_15046_4.webp", beta)

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

    if lang == "en":
        # 計算方法ボックスは幅が狭いため長い値は短縮形にする（文字重なり防止）
        display_map = {
            "crit": "CRIT only", "atk": "ATK%", "hp": "HP%",
            "def": "DEF%", "em": "EM", "charge": "ER"
        }
    else:
        display_map = {
            "crit": "会心のみ", "atk": "攻撃力%", "hp": "HP%",
            "def": "防御%", "em": "元素熟知", "charge": "チャージ効率"
        }
    display_score_way = display_map[calc_method]
    t_end = time.perf_counter()
    print(f"[Perf] セット効果処理: {(t_end - t_start)*1000:.1f}ms", flush=True)

    # 育成モード: 右側パネル用データ（画像描画で使用）
    growth_panel = {}
    if growth == "true":
        _g_set_bonuses = []
        for _sid, _cnt in active_sets:
            _g_name, _g_icon = get_set_info(_sid)
            _g_set_bonuses.append({"id": str(_sid), "name": _g_name, "count": _cnt, "buff": set_buff_label(str(_sid), lang)})
        _wjsondata = weapon_jsondata if "weapon_jsondata" in locals() else None
        if fake_char or fake_weapon:
            growth_panel = build_growth_from_fake(
                calc_method,
                base_hp=base_hp,
                base_atk=base_atk,
                base_def=base_def,
                weapon_affix=weapon_affix,
                weapon_jsondata=_wjsondata,
                raw_artifacts=raw_artifacts,
                set_bonuses=_g_set_bonuses,
                beta=beta,
                lang=lang,
            )
        else:
            growth_panel = build_growth_panel(
                calc_method,
                base_hp=target_avatar_info.get("fightPropMap", {}).get("1", 0),
                base_atk=target_avatar_info.get("fightPropMap", {}).get("4", 0),
                base_def=target_avatar_info.get("fightPropMap", {}).get("7", 0),
                weapon_affix=weapon_affix,
                weapon_refinement=(_wjsondata.get("refinement") or {} if _wjsondata else {}),
                raw_artifacts=raw_artifacts,
                set_bonuses=_g_set_bonuses,
                beta=beta,
                lang=lang,
            )

    t_start = time.perf_counter()
    # 背景選択: bg_color > bg_mode=element > 地域（bg_region指定 → 所属地域の先頭）> 元素背景
    _plog("背景生成: select region begin")
    selected_region = None
    if bg_color is None and str(bg_mode or "").lower() != "element":
        candidates = []
        if bg_region:
            candidates.append(str(bg_region))
        candidates.extend(char_regions)
        for cand in candidates:
            if region_image_path(cand):
                selected_region = cand
                break
    _plog(f"背景生成: selected_region={selected_region!r} splash={splash!r} element={element_type}")
    img = create_card_background(
        card_width, card_height, bg_base_rgb,
        splash_path=splash,
        element_type=element_type,
        use_prebuilt=(bg_color is None and selected_region is None),
        region=selected_region,
    )
    is_light = light == "true"
    if is_light:
        # ライトモード: 背景を白で薄くする（立ち絵は後から貼るため鮮明さを保てる）
        img = _lighten_background(img)
    t_end = time.perf_counter()
    print(f"[Perf] 背景生成: {(t_end - t_start)*1000:.1f}ms", flush=True)
    _plog(f"背景生成: done img.size={getattr(img, 'size', None)}")
    draw = ImageDraw.Draw(img)

    t_start = time.perf_counter()
    font_stats = get_cached_font(FONT_PATH, max(1, round(28 * SY)))
    font_stats_light = get_cached_font(FONT_LIGHT_PATH, max(1, round(28 * SY)))
    t_end = time.perf_counter()
    print(f"[Perf] フォント読み込み: {(t_end - t_start)*1000:.1f}ms", flush=True)

    t_start = time.perf_counter()
    with figma_draw_theme(light=is_light):
        draw_figma_box(img, x=33, y=30, width=694, height=671)
        draw_figma_glass_box(img, x=753, y=30, width=549, height=671)
        draw_figma_glass_box(img, x=1332, y=30, width=386, height=164, radius=25)
        draw_figma_glass_box(img, x=1332, y=231, width=386, height=121, radius=25)
        draw_figma_glass_box(img, x=1332, y=389, width=386, height=312, radius=25)

        paste_mask_image(img, splash, box_x=33, box_y=30, box_width=694, box_height=671, radius=15, zoom=1.1, beta=beta)

        _ol_x1, _ol_y1 = int(31 * SX), int(28 * SY)
        _ol_x2, _ol_y2 = int(729 * SX) + 1, int(703 * SY) + 1
        _outline_layer = Image.new("RGBA", (_ol_x2 - _ol_x1, _ol_y2 - _ol_y1), (0, 0, 0, 0))
        _od = ImageDraw.Draw(_outline_layer)
        safe_rounded_rectangle(_od, [33 * SX - _ol_x1, 30 * SY - _ol_y1, 727 * SX - _ol_x1, 701 * SY - _ol_y1], radius=round(15 * SY), outline=(0, 0, 0, 220), width=max(1, round(1 * SY)))
        img.alpha_composite(_outline_layer, dest=(_ol_x1, _ol_y1))

        if substat_dots == "1":
            # 伸び値凡例: スプラッシュ枠（y=701）と聖遺物行（y=738）の間の空間に配置し、
            # 文字は花ボックス（1番左の聖遺物 x=33）の左端に揃える。バーは文字の右に続ける
            legend_y = 713
            legend_dot_size = 10
            legend_seg_w = round(legend_dot_size * 2.2)
            _legend_label = img_t("伸び値", lang)
            _legend_font = get_cached_font(FONT_PATH, max(1, round(22 * SY)))
            _label_w = draw.textlength(_legend_label, font=_legend_font) / SX
            _legend_x = 33
            draw_figma_text(draw, text=_legend_label, x=_legend_x, y=legend_y - 6, font=font_stats, align="left", font_size=22, fill_color=(15, 23, 42, 170) if is_light else (255, 255, 255, 220))
            _bars_x0 = _legend_x + _label_w + 14
            for li, color in enumerate(ROLL_DOT_COLORS):
                corners = (True, False, False, True) if li == 0 else ((False, True, True, False) if li == len(ROLL_DOT_COLORS) - 1 else (False, False, False, False))
                draw_figma_dot(img, x=_bars_x0 + li * legend_seg_w, y=legend_y, size=legend_dot_size, fill_color=color, corners=corners)

        draw_figma_text(draw, text=char_name, x=56, y=57, font=font_stats, font_size=50, fill_color=(0, 0, 0, 190))
        draw_figma_text(draw, text=char_name, x=53, y=53, font=font_stats, font_size=50, fill_color=(255, 255, 255))
        draw_figma_text(draw, text=f"Lv.{char_level}", x=56, y=121, font=font_stats, font_size=30, fill_color=(0, 0, 0, 190))
        draw_figma_text(draw, text=f"Lv.{char_level}", x=53, y=117, font=font_stats, font_size=30, fill_color=(255, 255, 255))
        if friendship_lv is not None:
            draw_figma_text(draw, text=f"♥ {friendship_lv}", x=56, y=166, font=font_stats, font_size=30, fill_color=(0, 0, 0, 190))
            draw_figma_text(draw, text=f"♥ {friendship_lv}", x=53, y=162, font=font_stats, font_size=30, fill_color=(255, 255, 255))

        # UID 表示（表示方法トグル。カード下端の隙間・左下）
        if show_uid == "true":
            _draw_uid_badge(img, draw, uid, beta)

        # 元素共鳴チップ（HTMLガラスカード右下 .resonance-corner と同じ位置・スタイル）
        _draw_resonance_badges(img, draw, resonance, beta, lang, light)

        y_skill_base = 389
        for i in range(3):
            draw_figma_circle(img, x=49, y=y_skill_base + 79 * i, size=68, fill_color=(0, 0, 0, 150), outline_color=base_color, outline_width=4)
            paste_figma_image(img, f"static/assets/skills/{skill_icon[i]}.webp", box_x=49 + 5, box_y=y_skill_base + 79 * i + 4, box_width=60, box_height=60, radius=15, beta=beta)
            lv_color = (125, 210, 255) if (i < len(skill_boosted) and skill_boosted[i]) else (255, 255, 255)
            draw_figma_text(draw, text=f"Lv.{skill_level[i]}", x=48, y=y_skill_base + 79 * i + 45, font=font_stats, align="center", font_size=20, box_width=68, fill_color=lv_color, stroke_width=2, stroke_fill=(0, 0, 0, 200))

        for i in range(6 if Constellation_icon else 0):
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
                safe_rounded_rectangle(draw_lock, [lx + _dx, ly + 11 * SY + _dy, lx + lock_w + _dx, ly + lock_h + _dy], radius=max(1, round(4 * SY)), fill=(20, 25, 35, 255), outline=(255, 255, 255, 220), width=max(1, round(2 * SY)))
                draw_lock.ellipse([lx + 10 * SX + _dx, ly + 16 * SY + _dy, lx + 14 * SX + _dx, ly + 20 * SY + _dy], fill=(255, 255, 255, 220))
                img.alpha_composite(lock_overlay, dest=(_lk_x1, _lk_y1))
            else:
                draw_figma_circle(img, x=circle_x, y=circle_y, size=circle_size, fill_color=(0, 0, 0, 150), outline_color=base_color, outline_width=4)
                paste_figma_image(img, f"static/assets/skills/{icon_name}.webp", box_x=circle_x + 5, box_y=circle_y + 5, box_width=60, box_height=60, radius=15, beta=beta)

        if weapon_icon:
            paste_figma_image(img, f"static/assets/weapons/{weapon_icon}.webp", box_x=1350, box_y=60, box_width=100, box_height=100, radius=15, beta=beta)
        draw_figma_box(img, x=1340, y=47, width=60, height=30, radius=2)
        if weapon_affix:
            draw_figma_text(draw, text=f"R{weapon_affix}", x=1357, y=48, font=font_stats, align="left", font_size=20)
        else:
            draw_figma_text(draw, text="-", x=1357, y=48, font=font_stats, align="left", font_size=20)
        # 長い武器名（英語など）はカード右端にはみ出すため省略する
        _wn_font = get_cached_font(FONT_PATH, max(1, round(23 * SY)))
        _wn_max = (2388 - 1462) * SX
        _wn_text = weapon_name
        if draw.textlength(_wn_text, font=_wn_font) > _wn_max:
            while _wn_text and draw.textlength(_wn_text + "...", font=_wn_font) > _wn_max:
                _wn_text = _wn_text[:-1]
            _wn_text = _wn_text.rstrip() + "..."
        draw_figma_text(draw, text=_wn_text, x=1462, y=60, font=font_stats, align="left", font_size=23)
        if weapon_level:
            draw_figma_text(draw, text=f"Lv.{weapon_level}", x=1462, y=90, font=font_stats, align="left", font_size=20)
        else:
            draw_figma_text(draw, text="Lv.-", x=1462, y=90, font=font_stats, align="left", font_size=20)

        if weapon_stat1:
            stat_name1, stat_val1_str = weapon_stat1
            draw_figma_text(draw, text=stat_name1, x=1462, y=125, font=font_stats_light, align="left", font_size=18)
            draw_figma_text(draw, text=stat_val1_str, x=1635, y=125, font=font_stats_light, align="left", font_size=21)
        if weapon_stat2:
            stat_name2, stat_val2_str = weapon_stat2
            draw_figma_text(draw, text=stat_name2, x=1462, y=155, font=font_stats_light, align="left", font_size=18)
            draw_figma_text(draw, text=stat_val2_str, x=1635, y=155, font=font_stats_light, align="left", font_size=21)
        t_end = time.perf_counter()
        print(f"[Perf] 描画：ボックス・テキスト（上半分）: {(t_end - t_start)*1000:.1f}ms", flush=True)

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
                        if is_light:
                            # 白系アイコンが白パネルに沈まないよう暗めの台座を敷く
                            draw_figma_circle(img, x=icon_x - 4, y=current_y + icon_offset_y - 4, size=icon_size + 8, fill_color=(15, 23, 42, 90))
                        img.paste(icon_img, (int(round(icon_x * SX)), int(round((current_y + icon_offset_y) * SY))), icon_img)
                except Exception as e:
                    print(f"[Error] Failed to paste status icon: {icon_path}. Reason: {e}")
            draw_figma_text(draw, text=n, x=840, y=current_y, font=font_stats, align="left")
            draw_figma_text(draw, text=data["val"], x=870, y=current_y, font=font_stats, align="right", box_width=450 - 60)

            if n in ("HP", _lb_atk, _lb_def) and data.get("base") and data.get("add"):
                sub_y = current_y + 32
                green_text = data["add"]
                gray_text = str(data["base"])
                calc_font = get_cached_font(FONT_PATH, max(1, round(20 * SY))) if os.path.exists(FONT_PATH) else font_stats
                green_w = draw.textlength(green_text, font=calc_font) / SX
                gray_w = draw.textlength(gray_text, font=calc_font) / SX
                target_right_edge = 1260
                green_x = target_right_edge - green_w
                gray_x = green_x - 8 - gray_w
                draw_figma_text(draw, text=green_text, x=green_x, y=sub_y, font=font_stats, font_size=20, fill_color=(4, 120, 87) if is_light else (0, 230, 115), align="left")
                draw_figma_text(draw, text=gray_text, x=gray_x, y=sub_y, font=font_stats, font_size=20, fill_color=(100, 116, 139) if is_light else (160, 165, 175), align="left")
        t_end = time.perf_counter()
        print(f"[Perf] 描画：ステータス: {(t_end - t_start)*1000:.1f}ms", flush=True)

        t_start = time.perf_counter()
        for x in artifact_x_list:
            draw_figma_glass_box(img, x=x, y=738, width=314, height=399, radius=25)

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
            sub_val_font_size = 23 if base_prec == "2" else 25
            for j in range(4):
                draw_figma_text(draw, text=artifact_data["stats"][j][1], x=box_x + 47, y=y_base + 50 * j, font=font_stats, font_size=25, align="left")
                draw_figma_text(draw, text=artifact_data["stats"][j][2], x=box_x + 218, y=y_base + 50 * j, font=font_stats, font_size=sub_val_font_size, align="left")
                if is_light:
                    draw_figma_circle(img, x=box_x + 10, y=y_base + 50 * j - 2, size=34, fill_color=(15, 23, 42, 90))
                paste_figma_image(img, artifact_data["stats"][j][0], box_x=box_x + 12, box_y=y_base + 50 * j, box_width=30, box_height=30, radius=5, beta=beta)
                roll_tiers = artifact_data["stats"][j][3] if len(artifact_data["stats"][j]) > 3 else []
                if substat_dots == "1":
                    _tiers = [dt for dt in roll_tiers if 0 <= dt < 4]
                    if _tiers:
                        _seg_w = 24
                        for dk, dt in enumerate(_tiers):
                            if len(_tiers) == 1:
                                corners = (True, True, True, True)
                            elif dk == 0:
                                corners = (True, False, False, True)
                            elif dk == len(_tiers) - 1:
                                corners = (False, True, True, False)
                            else:
                                corners = (False, False, False, False)
                            draw_figma_dot(img, x=box_x + 47 + dk * _seg_w, y=y_base + 50 * j + 36, size=11, fill_color=ROLL_DOT_COLORS[dt], corners=corners)

            draw_figma_line(img, x1=box_x + 27, y1=1065, x2=box_x + 287, y2=1065, width=1)
            _num_font_path = getattr(font_stats, "path", None)
            _num_font = get_cached_font(_num_font_path, max(1, round(40 * SY))) if _num_font_path and os.path.exists(_num_font_path) else font_stats
            _score_left_x = box_x + 287 - draw.textlength(str(artifact_data["score"]), font=_num_font) / SX
            draw_figma_text(draw, text=img_t("スコア", lang), x=box_x + 27, y=1090, font=font_stats_light, font_size=20, align="right", box_width=(_score_left_x - 6) - (box_x + 27))
            draw_figma_text(draw, text=artifact_data["score"], x=box_x + 207, y=1070, font=font_stats, font_size=40, align="right", box_width=80)
            paste_figma_image(img, f"static/assets/tiers/{artifact_data['tier']}.png", box_x=box_x + 27, box_y=1070, box_width=60, box_height=60, radius=15, beta=beta)
        t_end = time.perf_counter()
        print(f"[Perf] 描画：聖遺物5枠: {(t_end - t_start)*1000:.1f}ms", flush=True)

        t_start = time.perf_counter()
        _name_font_path = getattr(font_stats, "path", None)
        _name_font = get_cached_font(_name_font_path, max(1, round(20 * SY))) if _name_font_path and os.path.exists(_name_font_path) else font_stats
        for s in sets_display:
            # アイコン・名前を左寄りに配置し、個数バッジは名前の直後に付ける（数字はバッジ中央揃え）
            paste_figma_image(img, s["icon"], box_x=1345, box_y=s["img_y"], box_width=60, box_height=60, radius=15, beta=beta)
            draw_figma_text(draw, text=s["name"], x=1420, y=s["text_y"], font=font_stats, align="left", font_size=20)
            _count_box_x = 1420 + draw.textlength(str(s["name"]), font=_name_font) / SX + 10
            draw_figma_box(img, x=_count_box_x, y=s["box_y"], width=35, height=28, radius=8, fill_color=(15, 23, 42, 28) if is_light else (255, 255, 255, 40))
            draw_figma_text(draw, text=s["count"], x=_count_box_x, y=s["text_y"], font=font_stats, align="center", font_size=18, box_width=35)

        draw_figma_text(draw, text=img_t("総合スコア", lang), x=1443, y=449, font=font_stats, align="left", font_size=30)
        draw_figma_text(draw, text=round(score_sum, 1), x=1332, y=480, font=font_stats, align="center", font_size=90, box_width=386)
        draw_figma_line(img, x1=1380, y1=623, x2=1670, y2=623, width=1)
        paste_figma_image(img, f"static/assets/tiers/{tier_sum_score}.png", box_x=1620, box_y=400, box_width=80, box_height=80, radius=15, beta=beta)
        draw_figma_text(draw, text=img_t("計算方法", lang), x=1350, y=642, font=font_stats, align="left", font_size=30)
        draw_figma_text_right(draw, text=display_score_way, x=1680, y=645, font=font_stats, align="right", font_size=35)
        t_end = time.perf_counter()
        print(f"[Perf] 描画：セット効果・総合スコア: {(t_end - t_start)*1000:.1f}ms", flush=True)
        _plog(f"描画完了 size={img.size} mode={img.mode}")

    # 育成モード: カード全体を等方縮小し右側にパネルを追加（幅は変えない）
    if growth == "true" and growth_panel:
        t_start = time.perf_counter()
        img = _attach_growth_panel(
            img, growth_panel, bg_base_rgb, splash, element_type,
            use_prebuilt=(bg_color is None and selected_region is None),
            region=selected_region,
            base_prec=base_prec,
            light=light,
            lang=lang,
        )
        t_end = time.perf_counter()
        print(f"[Perf] 育成パネル追加: {(t_end - t_start)*1000:.1f}ms", flush=True)
        _plog(f"育成パネル追加後 size={img.size}")

    # 透明を維持するため RGBA のまま保存。ハングしやすい区間なので段階ログを細かく出す。
    t_start = time.perf_counter()
    _plog(f"画像保存: begin format={img_format}")

    if img.mode != "RGBA":
        _plog(f"画像保存: convert {img.mode} -> RGBA ...")
        img = img.convert("RGBA")
        _plog("画像保存: convert done")
    else:
        _plog("画像保存: already RGBA")

    w, h = img.size
    _plog(f"画像保存: encode start {w}x{h} format={img_format}")

    img_io = io.BytesIO()
    used_format = img_format
    try:
        if img_format == "png":
            _plog("画像保存: PNG compress_level=1 ...")
            img.save(img_io, "PNG", compress_level=1)
        else:
            # quality=95 は pillow-simd で極端に遅い／固まる事例あり
            _plog("画像保存: WEBP quality=88 method=0 ...")
            img.save(img_io, "WEBP", quality=88, method=0)
        _plog(f"画像保存: encode done bytes={img_io.tell()}")
    except Exception as enc_err:
        _plog(f"画像保存: {used_format} failed ({type(enc_err).__name__}: {enc_err}) -> PNG fallback")
        img_io = io.BytesIO()
        used_format = "png"
        img.save(img_io, "PNG", compress_level=1)
        _plog(f"画像保存: PNG fallback done bytes={img_io.tell()}")

    _plog("画像保存: seek(0) + getvalue ...")
    img_io.seek(0)
    payload = img_io.getvalue()
    t_end = time.perf_counter()
    print(f"[Perf] 画像保存: {(t_end - t_start)*1000:.1f}ms format={used_format} bytes={len(payload)}", flush=True)
    print(f"[Perf] 合計: {(time.perf_counter() - _total_start)*1000:.1f}ms", flush=True)
    _plog(f"DONE format={used_format} bytes={len(payload)}")
    return payload
