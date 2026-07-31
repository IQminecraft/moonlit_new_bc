from __future__ import annotations

import os
import random
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from moonlit.config import FONT_PATH, FONT_LIGHT_PATH
from moonlit.path import resolve_datas_path


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


def create_card_background(width, height, base_rgb, splash_path=None):
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
    bg = small.resize((width, height), Image.Resampling.LANCZOS).convert("RGBA")

    particles = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(particles)
    rng = random.Random(hash(base_rgb) & 0xFFFFFFFF)
    bright = _shade_rgb(base_rgb, 1.55)
    for _ in range(140):
        x = rng.randint(0, width - 1)
        y = rng.randint(0, height - 1)
        rad = rng.choice([1, 1, 1, 2, 2, 3])
        alpha = rng.randint(18, 48)
        pdraw.ellipse([x - rad, y - rad, x + rad, y + rad], fill=(bright[0], bright[1], bright[2], alpha))
    for _ in range(25):
        x = rng.randint(0, width - 1)
        y = rng.randint(0, height - 1)
        rad = rng.randint(4, 10)
        alpha = rng.randint(8, 20)
        pdraw.ellipse([x - rad, y - rad, x + rad, y + rad], fill=(bright[0], bright[1], bright[2], alpha))
    bg = Image.alpha_composite(bg, particles)

    if splash_path and os.path.exists(splash_path):
        try:
            splash_img = Image.open(splash_path).convert("RGBA")
            scale = max(width / splash_img.width, height / splash_img.height) * 1.35
            nw = int(splash_img.width * scale)
            nh = int(splash_img.height * scale)
            splash_img = splash_img.resize((nw, nh), Image.Resampling.LANCZOS)
            layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            ox = (width - nw) // 2
            oy = (height - nh) // 2
            layer.paste(splash_img, (ox, oy), splash_img)
            layer = layer.filter(ImageFilter.GaussianBlur(radius=48))
            r, g, b, a = layer.split()
            a = a.point(lambda p: int(p * 0.09))
            layer = Image.merge("RGBA", (r, g, b, a))
            bg = Image.alpha_composite(bg, layer)
        except Exception as e:
            print(f"[Warning] splash blur background failed: {e}")

    return bg.convert("RGB")


def draw_figma_text_right(draw, text, x, y, font, font_size=24, fill_color=(255, 255, 255), **kwargs):
    draw.text((x, y), str(text), fill=fill_color, font=font, anchor="ra")


def draw_figma_box(img, x, y, width, height, radius=15, fill_color=(60, 64, 72, 125),
                   outline_color=(140, 145, 155, 90), outline_width=1,
                   shadow=True, shadow_offset=(6, 6), shadow_blur=8, shadow_alpha=70):
    x1, y1 = x, y
    x2, y2 = x + width, y + height
    base_rgba = img.convert("RGBA")

    if shadow:
        shadow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(shadow_layer)
        ox, oy = shadow_offset
        sdraw.rounded_rectangle([x1 + ox, y1 + oy, x2 + ox, y2 + oy], radius=radius,
                                fill=(0, 0, 0, shadow_alpha))
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=shadow_blur))
        base_rgba = Image.alpha_composite(base_rgba, shadow_layer)

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)
    outline_kwargs = {}
    if outline_color and outline_width > 0:
        outline_kwargs = {"outline": outline_color, "width": outline_width}
    draw_overlay.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=fill_color, **outline_kwargs)
    base_rgba = Image.alpha_composite(base_rgba, overlay)
    img.paste(base_rgba.convert("RGB"))


def draw_figma_text(draw, text, x, y, font, font_size=None, fill_color=(255, 255, 255),
                    align="left", box_width=None):
    text_str = str(text)
    actual_font = _resolve_font(font, font_size)
    if align == "left":
        actual_x = x
    elif align == "right" and box_width:
        actual_x = x + box_width - draw.textlength(text_str, font=actual_font)
    else:
        actual_x = x
    draw.text((actual_x, y), text_str, font=actual_font, fill=fill_color)


def draw_figma_text_with_shadow(draw, text, x, y, font, font_size=None, fill_color=(255, 255, 255),
                                shadow_color=(0, 0, 0, 200), shadow_offset=(1.5, 1.5),
                                align="left", box_width=None):
    text_str = str(text)
    actual_font = _resolve_font(font, font_size)

    if align == "left":
        target_x = x
    elif align == "right" and box_width:
        target_x = x + box_width - draw.textlength(text_str, font=actual_font)
    elif align == "center" and box_width:
        target_x = x + (box_width - draw.textlength(text_str, font=actual_font)) / 2
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
    r, g, b = shadow_color[:3]
    thick_alpha = 270
    alpha_mask = blurred_shadow.split()[3].point(lambda p: int(p * (thick_alpha / 255.0)))
    final_shadow_piece = Image.new("RGBA", blurred_shadow.size, (r, g, b, 255))
    blurred_shadow = Image.composite(final_shadow_piece, Image.new("RGBA", blurred_shadow.size, (0, 0, 0, 0)), alpha_mask)
    ox, oy = shadow_offset
    paste_x = int(target_x - sx + ox)
    paste_y = int(y - sy + oy)
    draw._image.paste(blurred_shadow, (paste_x, paste_y), blurred_shadow)
    draw.text((target_x, y), text_str, font=actual_font, fill=fill_color)


def draw_figma_line(img, x1, y1, x2, y2, fill_color=(255, 255, 255, 50), width=1):
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)
    draw_overlay.line([(x1, y1), (x2, y2)], fill=fill_color, width=width)
    if img.mode != "RGBA":
        rgba_base = img.convert("RGBA")
        rgba_base.paste(overlay, (0, 0), overlay)
        img.paste(rgba_base.convert("RGB"))
    else:
        img.paste(overlay, (0, 0), overlay)


def draw_figma_circle(img, x, y, size, fill_color=(60, 64, 72, 125), outline_color=None, outline_width=0):
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)
    draw_overlay.ellipse([x, y, x + size, y + size], fill=fill_color, outline=outline_color, width=outline_width)
    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))


def paste_figma_image(base_img, img_path, box_x, box_y, box_width, box_height, radius=15, beta="false"):
    img_path = resolve_datas_path(img_path, beta)
    if not img_path or not os.path.exists(img_path):
        return
    try:
        paste_img = Image.open(img_path).convert("RGBA")
        paste_img = paste_img.resize((box_width, box_height), Image.Resampling.LANCZOS)
        overlay = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
        mask = Image.new("L", (box_width, box_height), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle([0, 0, box_width, box_height], radius=radius, fill=255)
        overlay.paste(paste_img, (box_x, box_y), mask)
        base_img.paste(Image.alpha_composite(base_img.convert("RGBA"), overlay).convert("RGB"))
    except Exception as e:
        print(f"[Error] Failed to paste image: {img_path}. Reason: {e}")


def paste_mask_image(base_img, img_path, box_x, box_y, box_width, box_height, radius=15, zoom=1.0, beta="false"):
    img_path = resolve_datas_path(img_path, beta)
    if not img_path or not os.path.exists(img_path):
        return
    try:
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
    except Exception as e:
        print(f"[Error] Failed to paste mask image: {img_path}. Reason: {e}")


def load_fonts():
    font_stats = None
    font_light = None
    if os.path.exists(FONT_PATH):
        try:
            font_stats = ImageFont.truetype(FONT_PATH, 28)
        except Exception:
            font_stats = ImageFont.load_default()
    else:
        font_stats = ImageFont.load_default()
    if os.path.exists(FONT_LIGHT_PATH):
        try:
            font_light = ImageFont.truetype(FONT_LIGHT_PATH, 28)
        except Exception:
            font_light = font_stats
    else:
        font_light = font_stats
    return font_stats, font_light


def _resolve_font(font, font_size):
    if font_size is None:
        return font
    if isinstance(font, str):
        if os.path.exists(font):
            try:
                return ImageFont.truetype(font, font_size)
            except Exception:
                return ImageFont.load_default()
        return ImageFont.load_default()
    if hasattr(font, "path") and font.path:
        if os.path.exists(font.path):
            try:
                return ImageFont.truetype(font.path, font_size)
            except Exception:
                return font
        return font
    if not isinstance(font, ImageFont.FreeTypeFont):
        return ImageFont.load_default()
    return font