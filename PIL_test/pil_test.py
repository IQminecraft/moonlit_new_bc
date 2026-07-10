from locale import normalize
from PIL import Image, ImageDraw, ImageFont, ImageFilter


# 【神関数】Figmaの数値をそのまま入れるだけで四角を描く関数
def draw_figma_box(
    img, x, y, width, height, radius=15, fill_color=(60, 64, 72, 180)
):
    """透明度に対応した角丸四角形を描く関数

    - fill_color の4つ目の数字（例: 180）で透明度を調整します
    - 引数の「draw」を「img」に変更しています
    """
    x1 = x
    y1 = y
    x2 = x + width
    y2 = y + height

    # 【重要】透明なレイヤーを1枚作って、そこに四角を描いてから重ね合わせる
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)

    # 4つの数字（RGBA）で角丸四角を描く
    draw_overlay.rounded_rectangle(
        [x1, y1, x2, y2], radius=radius, fill=fill_color
    )

    # 元の画像（img）に半透明レイヤーをガッちゃんこする
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))

def draw_figma_text(
    draw, text, x, y, font, font_size=None, fill_color=(255, 255, 255), align="left", box_width=None
):
    """Figmaの座標に文字を入れる関数（サイズ指定対応・完全版）

    - align="left"  : 左寄せ
    - align="right" : 右寄せ (box_width を指定する)
    - font_size     : 未設定（None）なら今のまま。数値を指定したらそのサイズに自動変更
    """
    text_str = str(text)

    # 1. font_size が指定されている場合のフォント切り替え処理
    actual_font = font
    if font_size is not None:
        # font自体が文字列（パス）で渡された場合、またはオブジェクトが path 属性を持っている場合
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

    # 2. 描画位置（X座標）の計算
    if align == "left":
        actual_x = x
    elif align == "right" and box_width:
        text_width = draw.textlength(text_str, font=actual_font)
        actual_x = x + box_width - text_width
    else:
        actual_x = x

    # 3. 描画
    draw.text((actual_x, y), text_str, font=actual_font, fill=fill_color)
    
def draw_figma_text_with_shadow(
    draw, text, x, y, font, font_size=None, fill_color=(255, 255, 255), shadow_color=(0, 0, 0, 200), shadow_offset=(2, 2), align="left", box_width=None
):
    """
    文字を最前線にクッキリ残し、
    後ろのブラー影をガッツリ濃く（濃刻）して視認性を最強にした完成版
    """
    from PIL import Image, ImageDraw, ImageFont, ImageFilter

    text_str = str(text)
    
    # 1. フォントサイズの適用
    if font_size and hasattr(font, "path") and font.path:
        try:
            actual_font = ImageFont.truetype(font.path, font_size)
        except Exception:
            actual_font = font
    else:
        actual_font = font

    # 2. 描画位置（X座標）の計算
    if align == "left":
        target_x = x
    elif align == "right" and box_width:
        text_width = draw.textlength(text_str, font=actual_font)
        target_x = x + box_width - text_width
    else:
        target_x = x

    # --- 完全に滑らかなブラー影を作るロジック ---
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

    # ブラーの滑らかさ（3.0）
    blurred_shadow = shadow_layer.filter(ImageFilter.GaussianBlur(3.0))

    # 【ここを改良】影の不透明度を 50 → 180 に一気に引き上げて濃くしました！
    r, g, b = shadow_color[0], shadow_color[1], shadow_color[2]
    thick_alpha = 270
    
    alpha_mask = blurred_shadow.split()[3].point(lambda p: int(p * (thick_alpha / 255.0)))
    final_shadow_piece = Image.new("RGBA", blurred_shadow.size, (r, g, b, 255))
    blurred_shadow = Image.composite(final_shadow_piece, Image.new("RGBA", blurred_shadow.size, (0, 0, 0, 0)), alpha_mask)

    ox, oy = shadow_offset[0], shadow_offset[1]
    paste_x = int(target_x - sx + ox)
    paste_y = int(y - sy + oy)

    # 安全に透過ペースト
    draw._image.paste(blurred_shadow, (paste_x, paste_y), blurred_shadow)

    # 3. 最後にメインの美しい白文字を最前線に重ね書き
    draw.text((target_x, y), text_str, font=actual_font, fill=fill_color)

def draw_figma_line(
    img, x1, y1, x2, y2, fill_color=(255, 255, 255, 50), width=1
):
    """Figmaの座標のまま、透明度指定つきの直線（区切り線）を描く関数

    - x1, y1     : 線のスタート地点の座標
    - x2, y2     : 線のゴール地点の座標
    - fill_color : (R, G, B, Alpha) で指定。4つ目の数字（0〜255）で透明度を調整
    - width      : 線の太さ（ピクセル）。基本は 1 のままでOKです
    """
    from PIL import Image, ImageDraw

    # 透明度に対応するため、一度透明なレイヤーに線を描いてから合成する
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)

    # 直線を描画
    draw_overlay.line([(x1, y1), (x2, y2)], fill=fill_color, width=width)

    # 元の画像（img）に透過合成（ガッちゃんこ）
    if img.mode != "RGBA":
        rgba_base = img.convert("RGBA")
        rgba_base.paste(overlay, (0, 0), overlay)
        img.paste(rgba_base.convert("RGB"))
    else:
        img.paste(overlay, (0, 0), overlay)

def draw_figma_circle(
    img, x, y, size, fill_color=(60, 64, 72, 180)
):
    """Figmaの座標のまま正円（またはアイコンの土台など）を透明度対応で描く関数

    - size : 円の直径（幅と高さが同じ正円になります）
    - fill_color : (R, G, B, Alpha) で指定。4つ目の数字で透明度を調整
    """
    x1 = x
    y1 = y
    x2 = x + size
    y2 = y + size

    # 透明なレイヤーを1枚作って、そこに円を描いてから重ね合わせる
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)

    # 指定された正方形の枠いっぱいに収まる正円を描く
    draw_overlay.ellipse([x1, y1, x2, y2], fill=fill_color)

    # 元の画像（img）に半透明レイヤーを透過合成（ガッちゃんこ）する
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))

def paste_figma_image(base_img, img_path, box_x, box_y, box_width, box_height, radius=15):
    """
    Figmaの指定サイズ(box_width, box_height)ぴったりに画像をリサイズし、
    角丸に型抜きして指定座標(box_x, box_y)に配置する、アイコン・汎用画像向けの関数
    """
    from PIL import Image, ImageDraw

    # 1. 画像を読み込んで、Figmaで指定された枠のサイズに【ぴったりリサイズ】する
    paste_img = Image.open(img_path).convert("RGBA")
    paste_img = paste_img.resize((box_width, box_height), Image.Resampling.LANCZOS)
    
    # 土台と同じ大きさの透明レイヤーを1枚作る
    overlay = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
    
    # 指定された「ボックスのサイズ」で透明なマスク用レイヤーを作成
    mask = Image.new("L", (box_width, box_height), 0)
    mask_draw = ImageDraw.Draw(mask)
    
    # マスク用レイヤーに、ボックスと同じ形の「白い角丸四角形」を描く
    mask_draw.rounded_rectangle([0, 0, box_width, box_height], radius=radius, fill=255)
    
    # 透明レイヤー（overlay）の指定座標に、角丸マスクを適用して画像を貼り付け
    overlay.paste(paste_img, (box_x, box_y), mask)
    
    # 元の画像（base_img）に、透過を維持したまま合成
    base_img.paste(Image.alpha_composite(base_img.convert("RGBA"), overlay).convert("RGB"))

def paste_mask_image(base_img, img_path, box_x, box_y, box_width, box_height, radius=15, zoom=1.0):
    """
    画像の高さをベースに拡大・縮小し、指定した角丸ボックスの枠内だけを表示する関数
    - zoom: 拡大倍率（1.0がぴったり。1.2にすると20%拡大して上下の透過を外に逃がせます）
    """
    # 1. 配置したい画像を原寸のまま読み込む（RGBAモード）
    paste_img = Image.open(img_path).convert("RGBA")
    orig_w, orig_h = paste_img.size
    
    # 上下の長さ（box_height）に、指定されたzoom倍率を掛け合わせて新しいサイズを計算
    new_height = int(box_height * zoom)
    new_width = int(orig_w * (new_height / orig_h))
    
    # 計算したサイズに画像を拡大・縮小
    paste_img = paste_img.resize((new_width, new_height), Image.Resampling.LANCZOS)
    
    # 【バグ対策】土台（base_img）と同じ大きさの透明レイヤーを1枚作る
    overlay = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
    
    # ボックスと同じサイズのキャンバスを作成
    canvas = Image.new("RGBA", (box_width, box_height), (0, 0, 0, 0))
    
    # 左右・上下が均等にはみ出す（中央寄せになる）ように座標を計算
    offset_x = (box_width - new_width) // 2
    offset_y = (box_height - new_height) // 2  # zoomが1.0より大きいとマイナスになり、上下が外に飛び出します
    
    # 画像自身の透明度（paste_img）を維持してキャンバスに貼り付け
    canvas.paste(paste_img, (offset_x, offset_y), paste_img)
    
    # 指定された「ボックスのサイズ」で透明なマスク用レイヤーを作成
    mask = Image.new("L", (box_width, box_height), 0)
    mask_draw = ImageDraw.Draw(mask)
    
    # マスク用レイヤーに、ボックスと同じ形の「白い角丸四角形」を描く
    mask_draw.rounded_rectangle([0, 0, box_width, box_height], radius=radius, fill=255)
    
    # 透明レイヤー（overlay）の指定座標に、角丸マスクを適用して画像を貼り付け
    overlay.paste(canvas, (box_x, box_y), mask)
    
    # 【重要】元の画像（base_img）に、透過を維持したままガッちゃんこする
    base_img.paste(Image.alpha_composite(base_img.convert("RGBA"), overlay).convert("RGB"))

def create_base_card():
    # 指定のサイズで土台を作成
    card_width = 1741
    card_height = 1159
    base_color = (121, 169, 239,100)  # 背景（薄いグレー）

    img = Image.new("RGB", (card_width, card_height), base_color)
    draw = ImageDraw.Draw(img)

    # =========================================================
    # ここにFigmaの [X, Y, Width, Height] をそのまま入れていく！
    # =========================================================
    draw_figma_box(img, x=33, y=30, width=694, height=671)

    draw_figma_box(img, x=753, y=30, width=549, height=671)

    draw_figma_box(img, x=1332, y=30, width=386, height=164,radius=25)
    draw_figma_box(img, x=1332, y=231, width=386, height=121,radius=25)
    draw_figma_box(img, x=1332, y=389, width=386, height=312,radius=25)

# 1. まずフォントを準備（サイズ28）

    font_stats = ImageFont.truetype("../font.ttf", 28)
    font_stats_light = ImageFont.truetype("../font-light.ttf", 28)

    #スプラッシュ
    paste_mask_image(   
        img, 
        "../static/datas/assets/splash/UI_Gacha_AvatarImg_Columbina.webp", 
        box_x=33, 
        box_y=30, 
        box_width=694, 
        box_height=671, 
        radius=15,
        zoom=1.1  # 【ここを追加】1.1〜1.4あたりでキャラが一番綺麗に収まる倍率を調整してください
    )

    #タイトル
    draw_figma_text_with_shadow(
        draw,
        text="コロンビーナ",
        x=53,
        y=53,
        font=font_stats,         # 外で作ったオブジェクトをそのまま入れてOK
        font_size=50,            # 好きなサイズをここで指定（10だとかなり小さいです！）
        align="left",
        shadow_color=(0, 0, 0, 200),  # 影の色と透明度
        shadow_offset=(1.5, 1.5)          # 影のずらし幅(X, Y)
    )
    #レベル
    draw_figma_text_with_shadow(
        draw,
        text="Lv.90",
        x=53,
        y=117,
        font=font_stats,         # 外で作ったオブジェクトをそのまま入れてOK
        font_size=30,            # 好きなサイズをここで指定（10だとかなり小さいです！）
        align="left",
        shadow_color=(0, 0, 0, 200),  # 影の色と透明度
        shadow_offset=(1.5, 1.5)          # 影のずらし幅(X, Y)
    )
    #好感度
    draw_figma_text_with_shadow(
        draw,
        text="♥10",
        x=53,
        y=162,
        font=font_stats,         # 外で作ったオブジェクトをそのまま入れてOK
        font_size=30,            # 好きなサイズをここで指定（10だとかなり小さいです！）
        align="left",
        shadow_color=(0, 0, 0, 200),  # 影の色と透明度
        shadow_offset=(1.5, 1.5)          # 影のずらし幅(X, Y)
    )
#通常スキル爆発
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
        # 1. 後ろの黒い円（直径68）
        draw_figma_circle(img, x=49, y=y_skill_base + 79*i, size=68, fill_color=(0, 0, 0, 150))
        
        # 2. 【変更点】スキルアイコン（新関数 paste_figma_image を使用！）
        # ※ もしFigma上でアイコンの大きさが横68×縦68なら、box_width=68, box_height=68 に書き換えてね！
        paste_figma_image(   
            img, 
            f"../static/datas/assets/skill_icon/{skill_icon[i]}.webp", 
            box_x=49+5, 
            box_y=y_skill_base + 79*i+4, 
            box_width=60,   # ← ここをFigmaの実際のアイコンの幅（例: 68）にすると円にぴったり重なります
            box_height=60,  # ← ここをFigmaの実際のアイコンの高さ（例: 68）に
            radius=15,
        )
        
        # 3. 手前に文字（Lv.10）
        draw_figma_text_with_shadow(draw, text=f"Lv.{str(skill_level[i])}", x=55, y=y_skill_base + 79*i+45, font=font_stats, align="left", font_size=20)
    #星座
    y_C_base = 139
    Constellation = 6
    Constellation_icon = ["UI_Talent_S_Columbina_01","UI_Talent_S_Columbina_02","UI_Talent_U_Columbina_01","UI_Talent_S_Columbina_03","UI_Talent_U_Columbina_02","UI_Talent_S_Columbina_04"]
    for i in range(6):
        draw_figma_circle(img, x=637, y=y_C_base + i*76, size=68, fill_color=(0, 0, 0, 150))
        paste_figma_image(   
            img, 
            f"../static/datas/assets/skill_icon/{Constellation_icon[i]}.webp", 
            box_x=637+5, 
            box_y=y_C_base + i*76+5, 
            box_width=60,   # ← ここをFigmaの実際のアイコンの幅（例: 68）にすると円にぴったり重なります
            box_height=60,  # ← ここをFigmaの実際のアイコンの高さ（例: 68）に
            radius=15,
        )
    #武器
    paste_figma_image(   
        img, 
        f"../static/datas/assets/weapons/UI_EquipIcon_Catalyst_Brisingamen.webp", 
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
    #セット効果
    #2セット2セット (1)
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
            f"../static/datas/assets/artifacts/UI_RelicIcon_15046_4.webp", 
            box_x=1360, 
            box_y=242-4, 
            box_width=60,   # ← ここをFigmaの実際のアイコンの幅（例: 68）にすると円にぴったり重なります
            box_height=60,  # ← ここをFigmaの実際のアイコンの高さ（例: 68）に
            radius=15,
        )
        paste_figma_image(   
            img, 
            f"../static/datas/assets/artifacts/UI_RelicIcon_15046_4.webp", 
            
            box_x=1360, 
            box_y=301-4, 
            box_width=60,   # ← ここをFigmaの実際のアイコンの幅（例: 68）にすると円にぴったり重なります
            box_height=60,  # ← ここをFigmaの実際のアイコンの高さ（例: 68）に
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
            f"../static/datas/assets/artifacts/UI_RelicIcon_15046_4.webp", 
            box_x=1330,
            box_y=240, 
            box_width=120,   # ← ここをFigmaの実際のアイコンの幅（例: 68）にすると円にぴったり重なります
            box_height=120,  # ← ここをFigmaの実際のアイコンの高さ（例: 68）に
            radius=15,
        )
    #スコア
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
        fill_color=(255, 255, 255, 50),  # ← 最後の「50」をいじると線の薄さを変えられます！
        width=1
    )
    #計算方法
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
    

    # ステータス------------------------
    stats = {
            "HP": {"val": "16,897", "base": "11,669", "add": "+5,228", "icon": "../static/datas/assets/prot_icon/hp.png"},
            "攻撃力": {"val": "2,807", "base": "834", "add": "+1,973", "icon": "../static/datas/assets/prot_icon/atk.png"},
            "防禦力": {"val": "818", "base": "664", "add": "+154", "icon": "../static/datas/assets/prot_icon/def.png"},
            "元素熟知": {"val": "44", "icon": "../static/datas/assets/prot_icon/EM.png"},
            "会心率": {"val": "56.3%", "icon": "../static/datas/assets/prot_icon/rate.webp"},
            "会心ダメージ": {"val": "165.0%", "icon": "../static/datas/assets/prot_icon/dmg.webp"},
            "元素チャージ効率": {"val": "100.0%", "icon": "../static/datas/assets/prot_icon/ER.png"},
            "水元素ダメバフ": {"val": "0.0%", "icon": "../static/datas/assets/prot_icon/hydro.png"},
    }

    base_y = 73
    max_y = 700
    row_gap = (max_y - base_y) // len(stats)

    # アイコンのサイズ（文字の高さに合わせる）
    icon_size = 36
    icon_offset_y = 2  # Y軸の微調整用

    # i は 0, 1, 2...（順番のインデックス）、n は "HP"などのキー、data はその中身
    for i, (n, data) in enumerate(stats.items()):
        current_y = base_y + (i * row_gap)

        icon_path = data["icon"]
        icon_x = 840 - 60

        # アイコンを貼り付け
        icon_img = Image.open(icon_path).convert("RGBA")
        icon_img = icon_img.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
        img.paste(icon_img, (icon_x, current_y + icon_offset_y), icon_img)

        # ① 左寄せ：ステータス名
        draw_figma_text(
            draw,
            text=n,
            x=840,
            y=current_y,
            font=font_stats,
            align="left",
        )

        # ② 右寄せ：メインの合計数値（白）
        draw_figma_text(
            draw,
            text=data["val"],
            x=870,
            y=current_y,
            font=font_stats,
            align="right",
            box_width=450 - 60,
        )

        # ③ 下段の基礎値(灰色) ＋ 追加値(緑色) の描画
        # 指定された3つのステータス（HP、攻撃力、防禦力）で、かつデータが存在する場合のみ描画する
        if n in ["HP", "攻撃力", "防禦力"] and data.get("base") and data.get("add"):
            sub_y = current_y + 32  # メイン文字の少し下（32px）に配置
            
            # 追加値（緑文字）の描画
            green_text = data["add"]
            draw_figma_text(
                draw,
                text=green_text,
                x=870,
                y=sub_y,
                font=font_stats,
                font_size=20,                    # 小さめサイズ
                fill_color=(0, 230, 115),        # ゲーム風の黄緑色
                align="right",
                box_width=450 - 60,
            )
            
            # 基礎値（灰色文字）の描画
            # 緑文字の横幅を測ってその分だけ左にずらす
            green_w = draw.textlength(green_text, font=ImageFont.truetype(font_stats.path, 20))
            gray_x_offset = (450 - 60) - int(green_w) - 8  # 緑の幅 ＋ 8pxの間隔
            
            draw_figma_text(
                draw,
                text=data["base"],
                x=870,
                y=sub_y,
                font=font_stats,
                font_size=20,                    # 小さめサイズ
                fill_color=(160, 165, 175),      # スタイリッシュな灰色
                align="right",
                box_width=gray_x_offset,         # 計算した位置にピッタリ右寄せ
            )
    artifact_x_list = [33, 375, 718, 1061, 1404]

    for x in artifact_x_list:
        draw_figma_box(img, x=x, y=738, width=314, height=399, radius=25)
    # =========================================================
    #聖遺物 1部位
    artifacts = [
            {
                "set": "15046",
                "upgrade": 20,
                "Main": ["HP", "4780"],
                "stats": { 
                    0: ["../static/datas/assets/prot_icon/atk_per.png", "ステータス", "10.0%"],
                    1: ["../static/datas/assets/prot_icon/atk_per.png", "ステータス", "10.0%"],
                    2: ["../static/datas/assets/prot_icon/atk_per.png", "ステータス", "10.0%"],
                    3: ["../static/datas/assets/prot_icon/atk_per.png", "ステータス", "10.0%"]
                },
                "score": 60.1,
                "tier": "SS"
            },
            {
                "set": "15046",
                "upgrade": 20,
                "Main": ["攻撃力", "258"],
                "stats": { 
                    0: ["../static/datas/assets/prot_icon/atk_per.png", "ステータス", "10.0%"],
                    1: ["../static/datas/assets/prot_icon/atk_per.png", "ステータス", "10.0%"],
                    2: ["../static/datas/assets/prot_icon/atk_per.png", "ステータス", "10.0%"],
                    3: ["../static/datas/assets/prot_icon/atk_per.png", "ステータス", "10.0%"]
                },
                "score": 60.1,
                "tier": "SS"
            },
            {
                "set": "15046",
                "upgrade": 20,
                "Main": ["攻撃力%", "46.6%"],
                "stats": { 
                    0: ["../static/datas/assets/prot_icon/atk_per.png", "ステータス", "10.0%"],
                    1: ["../static/datas/assets/prot_icon/atk_per.png", "ステータス", "10.0%"],
                    2: ["../static/datas/assets/prot_icon/atk_per.png", "ステータス", "10.0%"],
                    3: ["../static/datas/assets/prot_icon/atk_per.png", "ステータス", "10.0%"]
                },
                "score": 60.1,
                "tier": "SS",
            },
            {
                "set": "15046",
                "upgrade": 20,
                "Main": ["攻撃力%", "46.6%"],
                "stats": { 
                    0: ["../static/datas/assets/prot_icon/atk_per.png", "ステータス", "10.0%"],
                    1: ["../static/datas/assets/prot_icon/atk_per.png", "ステータス", "10.0%"],
                    2: ["../static/datas/assets/prot_icon/atk_per.png", "ステータス", "10.0%"],
                    3: ["../static/datas/assets/prot_icon/atk_per.png", "ステータス", "10.0%"]
                },
                "score": 60.1,
                "tier": "SS"
            },
            {
                "set": "15046",
                "upgrade": 20,
                "Main": ["会心ダメージ", "62.2%"],
                "stats": { 
                    0: ["../static/datas/assets/prot_icon/atk_per.png", "ステータス", "10.0%"],
                    1: ["../static/datas/assets/prot_icon/atk_per.png", "ステータス", "10.0%"],
                    2: ["../static/datas/assets/prot_icon/atk_per.png", "ステータス", "10.0%"],
                    3: ["../static/datas/assets/prot_icon/atk_per.png", "ステータス", "10.0%"]
                },
                "score": 60.1,
                "tier": "SS"
            }
    ]
    for i in range(5):  
        # 基準となるボックスの左端X座標を取得
        box_x = artifact_x_list[i]
        artifact_data = artifacts[i]
        artifact_image_num = [4,2,5,1,3]
        artifact_img_num = artifact_image_num[i]

        # 各パーツの位置を box_x からの相対座標（元の配置のズレ幅）に修正
        draw_figma_box(img, x=box_x + 14, y=754, width=90, height=90, radius=10)
        draw_figma_box(img, x=box_x + 230, y=795, width=70, height=40, radius=10)
        
        paste_figma_image(   
            img, 
            f"../static/datas/assets/artifacts/UI_RelicIcon_{artifact_data['set']}_{artifact_img_num}.webp", 
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
        for j in range(4):  # 内側のループ変数を j に変更（外側と被らないように安全策）
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
            f"../static/datas/assets/tiers/{artifact_data['tier']}.png", 
            box_x=box_x + 27,
            box_y=1070, 
            box_width=60,   
            box_height=60,  
            radius=15,
        )
    # 画面にパッと表示
    # 保存
    img.save("build_card_preview.png")


if __name__ == "__main__":
    create_base_card()