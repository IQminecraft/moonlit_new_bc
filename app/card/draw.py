import os
from PIL import Image, ImageDraw, ImageChops
from app.paths import SX, SY
from app.card.cache import get_cached_font, get_cached_image, get_resized_image
from app.card.special import resolve_datas_path


def draw_figma_text_right(draw, text, x, y, font, font_size=24, fill_color=(255, 255, 255), stroke_width=0, stroke_fill=None, shadow=False, **kwargs):
    text_str = str(text)
    # 設計座標 → キャンバス座標
    if shadow:
        draw.text((x * SX + 2 * SX, y * SY + 2 * SY), text_str, fill=(0, 0, 0, 160), font=font, anchor="ra")
    if stroke_width > 0:
        sf = stroke_fill if stroke_fill is not None else fill_color
        draw.text((x * SX, y * SY), text_str, fill=fill_color, font=font, anchor="ra",
                  stroke_width=max(1, round(stroke_width * SY)), stroke_fill=sf)
    else:
        draw.text((x * SX, y * SY), text_str, fill=fill_color, font=font, anchor="ra")


def draw_figma_box(img, x, y, width, height, radius=15, fill_color=(60, 64, 72, 125),
                   outline_color=(140, 145, 155, 90), outline_width=1, shadow=True,
                   shadow_offset=(6, 6), shadow_blur=8, shadow_alpha=70):
    x1, y1 = x * SX, y * SY
    x2, y2 = x1 + width * SX, y1 + height * SY
    radius = radius * SY
    ox, oy = (shadow_offset[0] * SX, shadow_offset[1] * SY) if shadow else (0, 0)
    outline_width = max(1, round(outline_width * SY)) if (outline_color and outline_width > 0) else 0

    canvas_w, canvas_h = img.size
    margin = max(1, outline_width)
    layer_x1 = int(max(0, min(x1, x1 + ox) - margin))
    layer_y1 = int(max(0, min(y1, y1 + oy) - margin))
    layer_x2 = int(min(canvas_w, max(x2, x2 + ox) + margin + 1))
    layer_y2 = int(min(canvas_h, max(y2, y2 + oy) + margin + 1))
    if layer_x2 <= layer_x1 or layer_y2 <= layer_y1:
        return

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


def draw_figma_text(draw, text, x, y, font, font_size=None, fill_color=(255, 255, 255), align="left", box_width=None, stroke_width=0, stroke_fill=None, shadow=False):
    text_str = str(text)
    actual_font = font

    if font_size is not None:
        scaled_size = max(1, round(font_size * SY))
        path = getattr(font, "path", None)
        if path and os.path.exists(path):
            actual_font = get_cached_font(path, scaled_size)
        elif isinstance(font, str) and os.path.exists(font):
            actual_font = get_cached_font(font, scaled_size)

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

    if shadow:
        draw.text((actual_x + 2 * SX, y * SY + 2 * SY), text_str, font=actual_font, fill=(0, 0, 0, 160))
    if stroke_width > 0:
        sf = stroke_fill if stroke_fill is not None else fill_color
        draw.text((actual_x, y * SY), text_str, font=actual_font, fill=fill_color,
                  stroke_width=max(1, round(stroke_width * SY)), stroke_fill=sf)
    else:
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

    sox, soy = shadow_offset[0] * SX, shadow_offset[1] * SY
    y_s = y * SY
    draw.text(
        (target_x + sox, y_s + soy),
        text_str,
        font=actual_font,
        fill=shadow_color,
    )
    draw.text(
        (target_x, y_s),
        text_str,
        font=actual_font,
        fill=fill_color,
    )

def draw_figma_line(img, x1, y1, x2, y2, fill_color=(255, 255, 255, 50), width=1):
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


def paste_figma_image(base_img, img_path, box_x, box_y, box_width, box_height, radius=15, beta="false"):
    img_path = resolve_datas_path(img_path, beta)
    if not img_path or not os.path.exists(img_path):
        return
    try:
        bx, by = int(round(box_x * SX)), int(round(box_y * SY))
        bw, bh = max(1, round(box_width * SX)), max(1, round(box_height * SY))
        paste_img = get_resized_image(img_path, (bw, bh))
        if paste_img is None:
            return

        if paste_img.mode != "RGBA":
            paste_img = paste_img.convert("RGBA")

        if max(box_width, box_height) < 50:
            base_img.paste(paste_img, (bx, by), paste_img)
        else:
            corner_mask = Image.new("L", (bw, bh), 0)
            mask_draw = ImageDraw.Draw(corner_mask)
            mask_draw.rounded_rectangle([0, 0, bw, bh], radius=max(1, round(radius * SY)), fill=255)

            r, g, b, a = paste_img.split()
            combined_alpha = ImageChops.multiply(a, corner_mask)
            paste_img = Image.merge("RGBA", (r, g, b, combined_alpha))

            base_img.paste(paste_img, (bx, by), paste_img)
    except Exception as e:
        print(f"[Error] Failed to paste image: {img_path}. Reason: {e}")


def paste_mask_image(base_img, img_path, box_x, box_y, box_width, box_height, radius=15, zoom=1.0, beta="false", offset=None):
    img_path = resolve_datas_path(img_path, beta)
    if not img_path or not os.path.exists(img_path):
        return
    try:
        paste_img = get_cached_image(img_path)
        if paste_img is None:
            return
        orig_w, orig_h = paste_img.size
        bw, bh = max(1, round(box_width * SX)), max(1, round(box_height * SY))
        # ボックスをカバーするスケールに zoom を掛け、中心を維持したまま拡大
        cover_scale = max(bw / orig_w, bh / orig_h) * zoom
        new_width = max(1, int(orig_w * cover_scale))
        new_height = max(1, int(orig_h * cover_scale))
        paste_img = get_resized_image(img_path, (new_width, new_height))
        if paste_img is None:
            return

        if paste_img.mode != "RGBA":
            paste_img = paste_img.convert("RGBA")

        # offset: (ox, oy) は余白スペースに対する割合(-100〜100%)。0=中心。
        # 100% で余白スペース目一杯に画像中心がずれる
        offset = offset or (0.0, 0.0)
        try:
            ox_pct = max(-100.0, min(100.0, float(offset[0]))) / 100.0
            oy_pct = max(-100.0, min(100.0, float(offset[1]))) / 100.0
        except (TypeError, ValueError, IndexError):
            ox_pct = oy_pct = 0.0

        canvas = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        offset_x = (bw - new_width) // 2 + int((new_width - bw) // 2 * ox_pct)
        offset_y = (bh - new_height) // 2 + int((new_height - bh) // 2 * oy_pct)
        canvas.paste(paste_img, (offset_x, offset_y), paste_img)

        corner_mask = Image.new("L", (bw, bh), 0)
        mask_draw = ImageDraw.Draw(corner_mask)
        mask_draw.rounded_rectangle([0, 0, bw, bh], radius=max(1, round(radius * SY)), fill=255)

        r, g, b, a = canvas.split()
        combined_alpha = ImageChops.multiply(a, corner_mask)
        canvas = Image.merge("RGBA", (r, g, b, combined_alpha))

        base_img.paste(canvas, (int(round(box_x * SX)), int(round(box_y * SY))), canvas)
    except Exception as e:
        print(f"[Error] Failed to paste mask image: {img_path}. Reason: {e}")
