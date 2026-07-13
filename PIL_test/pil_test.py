from locale import normalize
from PIL import Image, ImageDraw, ImageFont, ImageFilter


def draw_figma_box(
    img, x, y, width, height, radius=15, fill_color=(60, 64, 72, 180)
):
    x1 = x
    y1 = y
    x2 = x + width
    y2 = y + height

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)

    draw_overlay.rounded_rectangle(
        [x1, y1, x2, y2], radius=radius, fill=fill_color
    )

    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))

def draw_figma_text(
    draw, text, x, y, font, font_size=None, fill_color=(255, 255, 255), align="left", box_width=None
):

    text_str = str(text)

    actual_font = font
    if font_size is not None:
        if isinstance(font, str):
            try:
                actual_font = ImageFont.truetype(font, font_size)
            except Exception:
                actual_font = font
        elif hasattr(font, "path") and font.path:
            try:
                actual_font = ImageFont.truetype(font.path, font_size)
            except Exception:
                actual_font = font

    if align == "left":
        actual_x = x
    elif align == "right" and box_width:
        text_width = draw.textlength(text_str, font=actual_font)
        actual_x = x + box_width - text_width
    else:
        actual_x = x

    draw.text((actual_x, y), text_str, font=actual_font, fill=fill_color)
    
def draw_figma_text_with_shadow(
    draw, text, x, y, font, font_size=None, fill_color=(255, 255, 255), shadow_color=(0, 0, 0, 200), shadow_offset=(2, 2), align="left", box_width=None
):
    from PIL import Image, ImageDraw, ImageFont, ImageFilter

    text_str = str(text)
    
    if font_size and hasattr(font, "path") and font.path:
        try:
            actual_font = ImageFont.truetype(font.path, font_size)
        except Exception:
            actual_font = font
    else:
        actual_font = font

    if align == "left":
        target_x = x
    elif align == "right" and box_width:
        text_width = draw.textlength(text_str, font=actual_font)
        target_x = x + box_width - text_width
    else:
        target_x = x

    try:
        left, top, right, bottom = draw.textbbox((0, 0), text_str, font=actual_font)
        text_w = right - left
        text_h = bottom - top
    except Exception:
        text_w = int(draw.textlength(text_str, font=actual_font))
        text_h = font_size if font_size else 40
        left, top = 0, 0

    pad = 60
    shadow_layer = Image.new("RGBA", (text_w + pad * 2, text_h + pad * 2), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(shadow_layer)

    sx = pad - left
    sy = pad - top
    s_draw.text((sx, sy), text_str, font=actual_font, fill=(0, 0, 0, 255))

    blurred_shadow = shadow_layer.filter(ImageFilter.GaussianBlur(3.0))

    r, g, b = shadow_color[0], shadow_color[1], shadow_color[2]
    thick_alpha = 270
    
    alpha_mask = blurred_shadow.split()[3].point(lambda p: int(p * (thick_alpha / 255.0)))
    final_shadow_piece = Image.new("RGBA", blurred_shadow.size, (r, g, b, 255))
    blurred_shadow = Image.composite(final_shadow_piece, Image.new("RGBA", blurred_shadow.size, (0, 0, 0, 0)), alpha_mask)

    ox, oy = shadow_offset[0], shadow_offset[1]
    paste_x = int(target_x - sx + ox)
    paste_y = int(y - sy + oy)

    draw._image.paste(blurred_shadow, (paste_x, paste_y), blurred_shadow)

    draw.text((target_x, y), text_str, font=actual_font, fill=fill_color)

def draw_figma_line(
    img, x1, y1, x2, y2, fill_color=(255, 255, 255, 50), width=1
):
    from PIL import Image, ImageDraw

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)

    draw_overlay.line([(x1, y1), (x2, y2)], fill=fill_color, width=width)

    if img.mode != "RGBA":
        rgba_base = img.convert("RGBA")
        rgba_base.paste(overlay, (0, 0), overlay)
        img.paste(rgba_base.convert("RGB"))
    else:
        img.paste(overlay, (0, 0), overlay)

def draw_figma_circle(
    img, x, y, size, fill_color=(60, 64, 72, 180)
):
    x1 = x
    y1 = y
    x2 = x + size
    y2 = y + size

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)

    draw_overlay.ellipse([x1, y1, x2, y2], fill=fill_color)

    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))

def paste_figma_image(base_img, img_path, box_x, box_y, box_width, box_height, radius=15):
    from PIL import Image, ImageDraw

    paste_img = Image.open(img_path).convert("RGBA")
    paste_img = paste_img.resize((box_width, box_height), Image.Resampling.LANCZOS)
    
    overlay = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
    
    mask = Image.new("L", (box_width, box_height), 0)
    mask_draw = ImageDraw.Draw(mask)
    
    mask_draw.rounded_rectangle([0, 0, box_width, box_height], radius=radius, fill=255)
    
    overlay.paste(paste_img, (box_x, box_y), mask)
    
    base_img.paste(Image.alpha_composite(base_img.convert("RGBA"), overlay).convert("RGB"))

def paste_mask_image(base_img, img_path, box_x, box_y, box_width, box_height, radius=15, zoom=1.0):
    paste_img = Image.open(img_path).convert("RGBA")
    orig_w, orig_h = paste_img.size
    
    new_height = int(box_height * zoom)
    new_width = int(orig_w * (new_height / orig_h))
    
    paste_img = paste_img.resize((new_width, new_height), Image.Resampling.LANCZOS)
    
    overlay = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
    
    canvas = Image.new("RGBA", (box_width, box_height), (0, 0, 0, 0))
    
    offset_x = (box_width - new_width) // 2
    offset_y = (box_height - new_height) // 2
    
    canvas.paste(paste_img, (offset_x, offset_y), paste_img)
    
    mask = Image.new("L", (box_width, box_height), 0)
    mask_draw = ImageDraw.Draw(mask)
    
    mask_draw.rounded_rectangle([0, 0, box_width, box_height], radius=radius, fill=255)
    
    overlay.paste(canvas, (box_x, box_y), mask)
    
    base_img.paste(Image.alpha_composite(base_img.convert("RGBA"), overlay).convert("RGB"))

def create_base_card():
    card_width = 1741
    card_height = 1159
    base_color = (121, 169, 239,100)

    img = Image.new("RGB", (card_width, card_height), base_color)
    draw = ImageDraw.Draw(img)

    draw_figma_box(img, x=33, y=30, width=694, height=671)

    draw_figma_box(img, x=753, y=30, width=549, height=671)

    draw_figma_box(img, x=1332, y=30, width=386, height=164,radius=25)
    draw_figma_box(img, x=1332, y=231, width=386, height=121,radius=25)
    draw_figma_box(img, x=1332, y=389, width=386, height=312,radius=25)


    font_stats = ImageFont.truetype("../fonts/font.ttf", 28)
    font_stats_light = ImageFont.truetype("../fonts/font_light.ttf", 28)

    paste_mask_image(   
        img, 
        "../static/assets/splash/UI_Gacha_AvatarImg_Columbina.webp", 
        box_x=33, 
        box_y=30, 
        box_width=694, 
        box_height=671, 
        radius=15,
        zoom=1.1
    )

    draw_figma_text_with_shadow(
        draw,
        text="コロンビーナ",
        x=53,
        y=53,
        font=font_stats,
        font_size=50,
        align="left",
        shadow_color=(0, 0, 0, 200),
        shadow_offset=(1.5, 1.5)
    )

    draw_figma_text_with_shadow(
        draw,
        text="Lv.90",
        x=53,
        y=117,
        font=font_stats,
        font_size=30,
        align="left",
        shadow_color=(0, 0, 0, 200),
        shadow_offset=(1.5, 1.5) 
    )
    
    draw_figma_text_with_shadow(
        draw,
        text="♥10",
        x=53,
        y=162,
        font=font_stats,
        font_size=30,
        align="left",
        shadow_color=(0, 0, 0, 200),
        shadow_offset=(1.5, 1.5)
    )

    normal = 10
    skill = 10
    burst = 10
    skill_level = [normal, skill, burst]
    skill_icon = [
        "Skill_A_Catalyst_MD",
        "Skill_S_Columbina_01",
        "Skill_E_Columbina_01"
    ]
    y_skill_base = 389
    for i in range(3):
        draw_figma_circle(img, x=49, y=y_skill_base + 79*i, size=68, fill_color=(0, 0, 0, 150))

        paste_figma_image(   
            img, 
            f"../static/assets/skills/{skill_icon[i]}.webp", 
            box_x=49+5, 
            box_y=y_skill_base + 79*i+4, 
            box_width=60,
            box_height=60,
            radius=15,
        )
        
        draw_figma_text_with_shadow(draw, text=f"Lv.{str(skill_level[i])}", x=55, y=y_skill_base + 79*i+45, font=font_stats, align="left", font_size=20)
    y_C_base = 139
    Constellation = 6
    Constellation_icon = ["UI_Talent_S_Columbina_01","UI_Talent_S_Columbina_02","UI_Talent_U_Columbina_01","UI_Talent_S_Columbina_03","UI_Talent_U_Columbina_02","UI_Talent_S_Columbina_04"]
    for i in range(6):
        draw_figma_circle(img, x=637, y=y_C_base + i*76, size=68, fill_color=(0, 0, 0, 150))
        paste_figma_image(   
            img, 
            f"../static/assets/skills/{Constellation_icon[i]}.webp", 
            box_x=637+5, 
            box_y=y_C_base + i*76+5, 
            box_width=60,
            box_height=60,
            radius=15,
        )
    
    paste_figma_image(   
        img, 
        f"../static/assets/weapons/UI_EquipIcon_Catalyst_Brisingamen.webp", 
        box_x=1350, 
        box_y=60, 
        box_width=100, 
        box_height=100,  
        radius=15,
        
    )
    draw_figma_box(img, x=1340, y=47, width=60, height=30,radius=2)
    draw_figma_text(
            draw,
            text="R1",
            x=1357,
            y=48,
            font=font_stats,
            align="left",
            font_size=20,

        )
    draw_figma_text(
            draw,
            text="帳の夜曲",
            x=1462,
            y=53,
            font=font_stats,
            align="left",
            font_size=35,

        )
    draw_figma_text(
            draw,
            text="Lv.90",
            x=1635,
            y=70,
            font=font_stats,
            align="left",
            font_size=20
        )
    draw_figma_text(
            draw,
            text="基礎攻撃力",
            x=1462,
            y=109,
            font=font_stats_light,
            align="left",
            font_size=21
        )
    draw_figma_text(
            draw,
            text="542",
            x=1635,
            y=109,
            font=font_stats_light,
            align="left",
            font_size=21
        )
    draw_figma_text(
            draw,
            text="会心率",
            x=1462,
            y=148,
            font=font_stats_light,
            align="left",
            font_size=21
        )
    draw_figma_text(
            draw,
            text="11%",
            x=1635,
            y=148,
            font=font_stats_light,
            align="left",
            font_size=21
        )
    
    m=2
    if m==2:
        draw_figma_text(
            draw,
            text="影に沈む幻",
            x=1450,
            y=242,
            font=font_stats,
            align="left",
        )
        draw_figma_text(
            draw,
            text="2",
            x=1600,
            y=301,
            font=font_stats,
            align="left",
        )  
        draw_figma_text(
            draw,
            text="影に沈む幻",
            x=1450,
            y=301,
            font=font_stats,
            align="left",
        )   
        paste_figma_image(   
            img, 
            f"../static/assets/artifacts/UI_RelicIcon_15046_4.webp", 
            box_x=1360, 
            box_y=242-4, 
            box_width=60,
            box_height=60,
            radius=15,
        )
        paste_figma_image(   
            img, 
            f"../static/assets/artifacts/UI_RelicIcon_15046_4.webp", 
            
            box_x=1360, 
            box_y=301-4, 
            box_width=60,
            box_height=60,
            radius=15,
        )
    else:
        draw_figma_text(
            draw,
            text="影に沈む幻",
            x=1450,
            y=268,
            font=font_stats,
            align="left",
        )   
        paste_figma_image(   
            img, 
            f"../static/assets/artifacts/UI_RelicIcon_15046_4.webp", 
            box_x=1330,
            box_y=240, 
            box_width=120,
            box_height=120,
            radius=15,
        )

    draw_figma_text(
            draw,
            text="総合スコア",
            x=1443,
            y=449,
            font=font_stats,
            align="left",
            font_size=30
        )   
    draw_figma_text(
            draw,
            text="200.5",
            x=1386,
            y=480,
            font=font_stats,
            align="left",
            font_size=90
        )   
    draw_figma_line(
        img, 
        x1=1380, 
        y1=623, 
        x2=1670, 
        y2=623, 
        fill_color=(255, 255, 255, 50),
        width=1
    )
    draw_figma_text(
            draw,
            text="計算方法",
            x=1350,
            y=642,
            font=font_stats,
            align="left",
            font_size=30
    )   
    draw_figma_text(
            draw,
            text="HP%",
            x=1620,
            y=638,
            font=font_stats,
            align="right",
            font_size=35
    )   
    

    stats = {
            "HP": {"val": "16,897", "base": "11,669", "add": "+5,228", "icon": "../static/assets/props/hp.png"},
            "攻撃力": {"val": "2,807", "base": "834", "add": "+1,973", "icon": "../static/assets/props/atk.png"},
            "防禦力": {"val": "818", "base": "664", "add": "+154", "icon": "../static/assets/props/def.png"},
            "元素熟知": {"val": "44", "icon": "../static/assets/props/em.png"},
            "会心率": {"val": "56.3%", "icon": "../static/assets/props/rate.webp"},
            "会心ダメージ": {"val": "165.0%", "icon": "../static/assets/props/dmg.webp"},
            "元素チャージ効率": {"val": "100.0%", "icon": "../static/assets/props/er.png"},
            "水元素ダメバフ": {"val": "0.0%", "icon": "../static/assets/props/hydro.png"},
    }

    base_y = 73
    max_y = 700
    row_gap = (max_y - base_y) // len(stats)

    icon_size = 36
    icon_offset_y = 2

    for i, (n, data) in enumerate(stats.items()):
        current_y = base_y + (i * row_gap)

        icon_path = data["icon"]
        icon_x = 840 - 60

        icon_img = Image.open(icon_path).convert("RGBA")
        icon_img = icon_img.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
        img.paste(icon_img, (icon_x, current_y + icon_offset_y), icon_img)

        draw_figma_text(
            draw,
            text=n,
            x=840,
            y=current_y,
            font=font_stats,
            align="left",
        )

        draw_figma_text(
            draw,
            text=data["val"],
            x=870,
            y=current_y,
            font=font_stats,
            align="right",
            box_width=450 - 60,
        )

        if n in ["HP", "攻撃力", "防禦力"] and data.get("base") and data.get("add"):
            sub_y = current_y + 32
            
            green_text = data["add"]
            draw_figma_text(
                draw,
                text=green_text,
                x=870,
                y=sub_y,
                font=font_stats,
                font_size=20,
                fill_color=(0, 230, 115),
                align="right",
                box_width=450 - 60,
            )

            green_w = draw.textlength(green_text, font=ImageFont.truetype(font_stats.path, 20))
            gray_x_offset = (450 - 60) - int(green_w) - 8  # 緑の幅 ＋ 8pxの間隔
            
            draw_figma_text(
                draw,
                text=data["base"],
                x=870,
                y=sub_y,
                font=font_stats,
                font_size=20,
                fill_color=(160, 165, 175),
                align="right",
                box_width=gray_x_offset,
            )
    artifact_x_list = [33, 375, 718, 1061, 1404]

    for x in artifact_x_list:
        draw_figma_box(img, x=x, y=738, width=314, height=399, radius=25)

    artifacts = [
            {
                "set": "15046",
                "upgrade": 20,
                "Main": ["HP", "4780"],
                "stats": { 
                    0: ["../static/assets/props/atk_per.png", "ステータス", "10.0%"],
                    1: ["../static/assets/props/atk_per.png", "ステータス", "10.0%"],
                    2: ["../static/assets/props/atk_per.png", "ステータス", "10.0%"],
                    3: ["../static/assets/props/atk_per.png", "ステータス", "10.0%"]
                },
                "score": 60.1,
                "tier": "SS"
            },
            {
                "set": "15046",
                "upgrade": 20,
                "Main": ["攻撃力", "258"],
                "stats": { 
                    0: ["../static/assets/props/atk_per.png", "ステータス", "10.0%"],
                    1: ["../static/assets/props/atk_per.png", "ステータス", "10.0%"],
                    2: ["../static/assets/props/atk_per.png", "ステータス", "10.0%"],
                    3: ["../static/assets/props/atk_per.png", "ステータス", "10.0%"]
                },
                "score": 60.1,
                "tier": "SS"
            },
            {
                "set": "15046",
                "upgrade": 20,
                "Main": ["攻撃力%", "46.6%"],
                "stats": { 
                    0: ["../static/assets/props/atk_per.png", "ステータス", "10.0%"],
                    1: ["../static/assets/props/atk_per.png", "ステータス", "10.0%"],
                    2: ["../static/assets/props/atk_per.png", "ステータス", "10.0%"],
                    3: ["../static/assets/props/atk_per.png", "ステータス", "10.0%"]
                },
                "score": 60.1,
                "tier": "SS",
            },
            {
                "set": "15046",
                "upgrade": 20,
                "Main": ["攻撃力%", "46.6%"],
                "stats": { 
                    0: ["../static/assets/props/atk_per.png", "ステータス", "10.0%"],
                    1: ["../static/assets/props/atk_per.png", "ステータス", "10.0%"],
                    2: ["../static/assets/props/atk_per.png", "ステータス", "10.0%"],
                    3: ["../static/assets/props/atk_per.png", "ステータス", "10.0%"]
                },
                "score": 60.1,
                "tier": "SS"
            },
            {
                "set": "15046",
                "upgrade": 20,
                "Main": ["会心ダメージ", "62.2%"],
                "stats": { 
                    0: ["../static/assets/props/atk_per.png", "ステータス", "10.0%"],
                    1: ["../static/assets/props/atk_per.png", "ステータス", "10.0%"],
                    2: ["../static/assets/props/atk_per.png", "ステータス", "10.0%"],
                    3: ["../static/assets/props/atk_per.png", "ステータス", "10.0%"]
                },
                "score": 60.1,
                "tier": "SS"
            }
    ]
    for i in range(5):  
        box_x = artifact_x_list[i]
        artifact_data = artifacts[i]
        artifact_image_num = [4,2,5,1,3]
        artifact_img_num = artifact_image_num[i]

        draw_figma_box(img, x=box_x + 14, y=754, width=90, height=90, radius=10)
        draw_figma_box(img, x=box_x + 230, y=795, width=70, height=40, radius=10)
        
        paste_figma_image(   
            img, 
            f"../static/assets/artifacts/UI_RelicIcon_{artifact_data['set']}_{artifact_img_num}.webp", 
            box_x=box_x + 14,
            box_y=754, 
            box_width=90,   
            box_height=90,  
            radius=15,
        )
        draw_figma_text(
            draw,
            text=artifact_data["Main"][0],
            x=box_x + 114,
            y=758,
            font=font_stats,
            align="left",
        )
        draw_figma_text(
            draw,
            text=artifact_data["Main"][1],
            x=box_x + 114,
            y=792,
            font=font_stats,
            align="left",
            font_size=30
        )
        draw_figma_text(
            draw,
            text=f"+{artifact_data['upgrade']}",
            x=box_x + 237,
            y=793,
            font=font_stats,
            align="left",
        )
        
        y_base = 855
        for j in range(4):
            draw_figma_text(
                draw,
                text=artifact_data["stats"][j][1],
                x=box_x + 47,
                y=y_base + 50 * j, 
                font=font_stats,
                font_size=25,
                align="left",
            )
            draw_figma_text(
                draw,
                text=artifact_data["stats"][j][2],
                x=box_x + 218,
                y=y_base + 50 * j, 
                font=font_stats,
                font_size=25,
                align="left",
            )
            paste_figma_image(   
                img, 
                artifact_data["stats"][j][0], 
                box_x=box_x + 12,
                box_y=y_base + 50 * j, 
                box_width=30, 
                box_height=30, 
                radius=5,
            )
            
        draw_figma_line(
            img, 
            x1=box_x + 27, 
            y1=1065, 
            x2=box_x + 287, 
            y2=1065, 
            fill_color=(255, 255, 255, 50),  
            width=1
        )
        draw_figma_text(
                draw,
                text="スコア",
                x=box_x + 142,
                y=1090, 
                font=font_stats_light,
                font_size=20,
                align="left",
        )
        draw_figma_text(
                draw,
                text=artifact_data["score"],
                x=box_x + 207,
                y=1070, 
                font=font_stats,
                font_size=40,
                align="right",
        )
        paste_figma_image(   
            img, 
            f"../static/assets/tiers/{artifact_data['tier']}.png", 
            box_x=box_x + 27,
            box_y=1070, 
            box_width=60,   
            box_height=60,  
            radius=15,
        )
    img.save("build_card_preview.png")


if __name__ == "__main__":
    create_base_card()