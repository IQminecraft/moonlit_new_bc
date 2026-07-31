from __future__ import annotations

import io
import os
from collections import Counter
from PIL import Image, ImageDraw, ImageFont

from moonlit.card.drawing import (
    create_card_background,
    draw_figma_text_right,
    draw_figma_box,
    draw_figma_text,
    draw_figma_text_with_shadow,
    draw_figma_line,
    draw_figma_circle,
    paste_figma_image,
    paste_mask_image,
    load_fonts,
    resolve_datas_path,
)
from moonlit.stats.score import total_tier, CALC_METHOD_LABEL
from moonlit.stats.props import formal_round, get_stat_japanese, load_text_map

CARD_WIDTH = 1741
CARD_HEIGHT = 1159
SLOT_TO_INDEX = {"4": 0, "2": 1, "5": 2, "1": 3, "3": 4}
ARTIFACT_X_LIST = [33, 375, 718, 1061, 1404]
ARTIFACT_IMAGE_NUM = [4, 2, 5, 1, 3]


def render_card_image(model: dict) -> bytes:
    card_data = model["card_data"]
    chardatas = model["chardatas"]
    bg_base_rgb = model["bg_base_rgb"]
    beta = model["beta"]
    fake_char = model["fake_char"]
    fake_weapon = model["fake_weapon"]

    font_stats, font_stats_light = load_fonts()

    img = create_card_background(CARD_WIDTH, CARD_HEIGHT, bg_base_rgb,
                                 splash_path=card_data.get("splash"))
    draw = ImageDraw.Draw(img)

    draw_figma_box(img, x=33, y=30, width=694, height=671)
    draw_figma_box(img, x=753, y=30, width=549, height=671)
    draw_figma_box(img, x=1332, y=30, width=386, height=164, radius=25)
    draw_figma_box(img, x=1332, y=231, width=386, height=121, radius=25)
    draw_figma_box(img, x=1332, y=389, width=386, height=312, radius=25)

    paste_mask_image(img, card_data.get("splash", ""),
                     box_x=33, box_y=30, box_width=694, box_height=671,
                     radius=15, zoom=1.1, beta=beta)
    _splash_outline = Image.new("RGBA", img.size, (0, 0, 0, 0))
    _od = ImageDraw.Draw(_splash_outline)
    _od.rounded_rectangle([33, 30, 33 + 694, 30 + 671], radius=15, outline=(0, 0, 0, 220), width=1)
    img.paste(Image.alpha_composite(img.convert("RGBA"), _splash_outline).convert("RGB"))

    char_name = card_data.get("displayName", "")
    char_level = card_data.get("level", 1)
    friendship_lv = card_data.get("friendship", 1)
    draw_figma_text_with_shadow(draw, text=char_name, x=53, y=53, font=font_stats, font_size=50)
    draw_figma_text_with_shadow(draw, text=f"Lv.{char_level}", x=53, y=117, font=font_stats, font_size=30)
    draw_figma_text_with_shadow(draw, text=f"♥ {friendship_lv}", x=53, y=162, font=font_stats, font_size=30)

    element_type = card_data.get("element", "None")
    base_color = model.get("element_base_rgb", (0x4A, 0x55, 0x68)) + (255,)
    skills = card_data.get("skills", [])
    skill_levels = [s.get("level", 1) for s in skills]
    skill_boosted = [s.get("boosted", False) for s in skills]
    skill_icons = [s.get("icon", "") for s in skills]

    y_skill_base = 389
    for i in range(3):
        circle_center_x = 49 + 34
        draw_figma_circle(img, x=49, y=y_skill_base + 79 * i, size=68,
                          fill_color=(0, 0, 0, 150), outline_color=base_color, outline_width=4)
        paste_figma_image(img, f"static/assets/skills/{skill_icons[i]}.webp",
                          box_x=49 + 5, box_y=y_skill_base + 79 * i + 4,
                          box_width=60, box_height=60, radius=15, beta=beta)
        lv_color = (125, 210, 255) if (i < len(skill_boosted) and skill_boosted[i]) else (255, 255, 255)
        draw_figma_text_with_shadow(draw, text=f"Lv.{skill_levels[i]}", x=48,
                                    y=y_skill_base + 79 * i + 45, font=font_stats,
                                    align="center", font_size=20, box_width=68, fill_color=lv_color)

    constellation = card_data.get("constellation", 0)
    constellation_icons = card_data.get("constellationIcons", [])
    y_C_base = 139
    circle_size = 68
    for i in range(6):
        circle_x = 637
        circle_y = y_C_base + i * 76
        icon_name = constellation_icons[i] if i < len(constellation_icons) else ""

        if i >= constellation:
            draw_figma_circle(img, x=circle_x, y=circle_y, size=circle_size,
                              fill_color=(0, 0, 0, 180), outline_color=(80, 85, 95, 255), outline_width=2)
            icon_path = resolve_datas_path(f"static/assets/skills/{icon_name}.webp", beta)
            if os.path.exists(icon_path):
                icon_img = Image.open(icon_path).convert("RGBA")
                icon_img = icon_img.resize((60, 60), Image.Resampling.LANCZOS)
                alpha = icon_img.getchannel('A')
                alpha = alpha.point(lambda p: int(p * (45 / 255.0)))
                icon_img.putalpha(alpha)
                img.paste(icon_img, (circle_x + 5, circle_y + 5), icon_img)

            lock_w, lock_h = 24, 26
            lx = circle_x + (circle_size - lock_w) // 2
            ly = circle_y + (circle_size - lock_h) // 2 + 2
            lock_overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            draw_lock = ImageDraw.Draw(lock_overlay)
            draw_lock.arc([lx + 4, ly, lx + lock_w - 4, ly + 16], start=180, end=0,
                          fill=(255, 255, 255, 220), width=3)
            draw_lock.line([lx + 4, ly + 8, lx + 4, ly + 12], fill=(255, 255, 255, 220), width=3)
            draw_lock.line([lx + lock_w - 4, ly + 8, lx + lock_w - 4, ly + 12], fill=(255, 255, 255, 220), width=3)
            draw_lock.rounded_rectangle([lx, ly + 11, lx + lock_w, ly + lock_h], radius=4,
                                        fill=(20, 25, 35, 255), outline=(255, 255, 255, 220), width=2)
            draw_lock.ellipse([lx + 10, ly + 16, lx + 14, ly + 20], fill=(255, 255, 255, 220))
            img.paste(Image.alpha_composite(img.convert("RGBA"), lock_overlay).convert("RGB"))
        else:
            draw_figma_circle(img, x=circle_x, y=circle_y, size=circle_size,
                              fill_color=(0, 0, 0, 150), outline_color=base_color, outline_width=4)
            paste_figma_image(img, f"static/assets/skills/{icon_name}.webp",
                              box_x=circle_x + 5, box_y=circle_y + 5,
                              box_width=60, box_height=60, radius=15, beta=beta)

    weapon_icon = card_data.get("weaponIcon", "")
    weapon_name = card_data.get("weaponName", "")
    weapon_level = card_data.get("weaponLevel")
    weapon_affix = card_data.get("weaponAffix")

    paste_figma_image(img, f"static/assets/weapons/{weapon_icon}.webp",
                      box_x=1350, box_y=60, box_width=100, box_height=100, radius=15, beta=beta)
    draw_figma_box(img, x=1340, y=47, width=60, height=30, radius=2)
    draw_figma_text(draw, text=f"R{weapon_affix}", x=1357, y=48, font=font_stats, align="left", font_size=20)
    draw_figma_text(draw, text=weapon_name, x=1462, y=60, font=font_stats, align="left", font_size=23)
    draw_figma_text(draw, text=f"Lv.{weapon_level}", x=1462, y=90, font=font_stats, align="left", font_size=20)

    weapon_stats = card_data.get("weaponStats", [])
    if len(weapon_stats) >= 1:
        draw_figma_text(draw, text=weapon_stats[0].get("name", ""), x=1462, y=125,
                        font=font_stats_light, align="left", font_size=18)
        draw_figma_text(draw, text=weapon_stats[0].get("value", ""), x=1635, y=125,
                        font=font_stats_light, align="left", font_size=21)
    if len(weapon_stats) >= 2:
        draw_figma_text(draw, text=weapon_stats[1].get("name", ""), x=1462, y=155,
                        font=font_stats_light, align="left", font_size=18)
        draw_figma_text(draw, text=weapon_stats[1].get("value", ""), x=1635, y=155,
                        font=font_stats_light, align="left", font_size=21)

    main_stats = card_data.get("mainStats", [])
    base_y = 73
    max_y = 700
    row_gap = (max_y - base_y) // max(len(main_stats), 1)
    icon_size = 36
    icon_offset_y = 2

    for i, stat in enumerate(main_stats):
        current_y = base_y + (i * row_gap)
        label = stat.get("label", "")
        val = stat.get("val", "")
        icon_path = stat.get("icon", "")
        icon_path = resolve_datas_path(icon_path, beta)

        if icon_path and os.path.exists(icon_path):
            try:
                icon_img = Image.open(icon_path).convert("RGBA")
                icon_img = icon_img.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
                img.paste(icon_img, (840 - 60, current_y + icon_offset_y), icon_img)
            except Exception as e:
                print(f"[Error] Failed to paste status icon: {icon_path}. Reason: {e}")

        draw_figma_text(draw, text=label, x=840, y=current_y, font=font_stats, align="left")
        draw_figma_text(draw, text=str(val), x=870, y=current_y, font=font_stats,
                        align="right", box_width=450 - 60)

        if label in ["HP", "攻撃力", "防禦力"] and stat.get("base") and stat.get("val"):
            sub_y = current_y + 32
            base_val = stat.get("base", 0)
            try:
                add_val = int(stat.get("val", 0)) - int(base_val)
            except (TypeError, ValueError):
                add_val = 0
            green_text = f"+{add_val}"
            gray_text = str(base_val)
            try:
                calc_font = ImageFont.truetype(font_stats.path, 20) if hasattr(font_stats, "path") and font_stats.path else font_stats
            except Exception:
                calc_font = font_stats
            green_w = draw.textlength(green_text, font=calc_font)
            gray_w = draw.textlength(gray_text, font=calc_font)
            target_right_edge = 1260
            green_x = target_right_edge - green_w
            gray_x = green_x - 8 - gray_w
            draw_figma_text(draw, text=green_text, x=green_x, y=sub_y, font=font_stats,
                            font_size=20, fill_color=(0, 230, 115), align="left")
            draw_figma_text(draw, text=gray_text, x=gray_x, y=sub_y, font=font_stats,
                            font_size=20, fill_color=(160, 165, 175), align="left")

    artifacts = card_data.get("artifacts", [None] * 5)
    for x in ARTIFACT_X_LIST:
        draw_figma_box(img, x=x, y=738, width=314, height=399, radius=25)

    for i in range(5):
        box_x = ARTIFACT_X_LIST[i]
        artifact_data = artifacts[i] if i < len(artifacts) and artifacts[i] else None
        if not artifact_data:
            continue

        artifact_img_num = ARTIFACT_IMAGE_NUM[i]

        draw_figma_box(img, x=box_x + 14, y=754, width=90, height=90, radius=10)
        draw_figma_box(img, x=box_x + 230, y=795, width=70, height=40, radius=10)

        paste_figma_image(img, f"static/assets/artifacts/UI_RelicIcon_{artifact_data.get('set', '')}_{artifact_img_num}.webp",
                          box_x=box_x + 14, box_y=754, box_width=90, box_height=90, radius=15, beta=beta)

        main_data = artifact_data.get("main", {})
        draw_figma_text(draw, text=main_data.get("name", ""), x=box_x + 114, y=758,
                        font=font_stats, align="left")
        draw_figma_text(draw, text=main_data.get("value", ""), x=box_x + 114, y=792,
                        font=font_stats, align="left", font_size=30)
        draw_figma_text(draw, text=f"+{artifact_data.get('upgrade', 0)}", x=box_x + 237, y=793,
                        font=font_stats, align="left")

        substats = artifact_data.get("substats", [])
        y_base = 855
        for j in range(4):
            if j < len(substats):
                sub = substats[j]
                draw_figma_text(draw, text=sub.get("name", ""), x=box_x + 47, y=y_base + 50 * j,
                                font=font_stats, font_size=25, align="left")
                draw_figma_text(draw, text=sub.get("value", ""), x=box_x + 218, y=y_base + 50 * j,
                                font=font_stats, font_size=25, align="left")
                paste_figma_image(img, sub.get("icon", ""), box_x=box_x + 12, box_y=y_base + 50 * j,
                                  box_width=30, box_height=30, radius=5, beta=beta)

        draw_figma_line(img, x1=box_x + 27, y1=1065, x2=box_x + 287, y2=1065,
                        fill_color=(255, 255, 255, 50), width=1)
        draw_figma_text(draw, text="スコア", x=box_x + 142, y=1090, font=font_stats_light,
                        font_size=20, align="left")
        score_val = artifact_data.get("score", 0)
        draw_figma_text(draw, text=str(score_val), x=box_x + 207, y=1070, font=font_stats,
                        font_size=40, align="right")
        tier = artifact_data.get("tier", "B")
        paste_figma_image(img, f"static/assets/tiers/{tier}.png",
                          box_x=box_x + 27, box_y=1070, box_width=60, box_height=60, radius=15, beta=beta)

    set_bonuses = card_data.get("setBonuses", [])
    for s in set_bonuses:
        icon_path = s.get("icon", "")
        name = s.get("name", "")
        count = s.get("count", "")
        img_y = s.get("img_y", 271 - 4)
        text_y = s.get("text_y", 281)
        box_y = s.get("box_y", 279)
        paste_figma_image(img, icon_path, box_x=1360, box_y=img_y, box_width=60, box_height=60,
                          radius=15, beta=beta)
        draw_figma_text(draw, text=name, x=1435, y=text_y, font=font_stats, align="left", font_size=20)
        draw_figma_box(img, x=1610, y=box_y, width=35, height=28, radius=8, fill_color=(255, 255, 255, 40))
        draw_figma_text(draw, text=str(count), x=1623, y=text_y, font=font_stats,
                        align="center", font_size=18, box_width=35)

    score_sum = card_data.get("scoreSum", 0)
    tier_sum = card_data.get("tierSum", "B")
    calc_method = card_data.get("calcMethod", "crit")
    display_score_way = CALC_METHOD_LABEL.get(calc_method, calc_method)

    draw_figma_text(draw, text="総合スコア", x=1443, y=449, font=font_stats, align="left", font_size=30)
    draw_figma_text(draw, text=str(round(score_sum, 1)), x=1386, y=480, font=font_stats, align="left", font_size=90)
    draw_figma_line(img, x1=1380, y1=623, x2=1670, y2=623, fill_color=(255, 255, 255, 50), width=1)
    paste_figma_image(img, f"static/assets/tiers/{tier_sum}.png",
                      box_x=1620, box_y=400, box_width=80, box_height=80, radius=15, beta=beta)
    draw_figma_text(draw, text="計算方法", x=1350, y=642, font=font_stats, align="left", font_size=30)
    draw_figma_text_right(draw, text=display_score_way, x=1680, y=645, font=font_stats, align="right", font_size=35)

    img_io = io.BytesIO()
    img.save(img_io, 'PNG', quality=95)
    img_io.seek(0)
    return img_io.getvalue()