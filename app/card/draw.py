import os
import threading
from PIL import Image, ImageDraw, ImageChops
from app.paths import SX as _BASE_SX, SY as _BASE_SY
from app.card.cache import get_cached_font, get_cached_image, get_resized_image
from app.card.special import resolve_datas_path


# ==============================================================
#  描画スケール (SX/SY) はモジュールグローバルではなくスレッドローカル。
#  従来は app.card.draw のモジュール属性を _DrawCtx が差し替えており、
#  カード生成プール(並列スレッド)で単体カード(A設計)と編成カード(チーム設計)が
#  同時実行されると、片方のスレッドの描画が他方のスケールで行われて
#  位置・サイズが崩れるデータレースがあった。 threading.local にすることで
#  各スレッドが自分のスケールを持ち、差し替えが他スレッドへ漏れない。
# ==============================================================
_scale_state = threading.local()


def _sx() -> float:
    return getattr(_scale_state, "sx", _BASE_SX)


def _sy() -> float:
    return getattr(_scale_state, "sy", _BASE_SY)


class figma_draw_scale:
    """現在のスレッド限定で描画スケールを差し替えるコンテキストマネージャ。

    使用例::
        with figma_draw_scale(SX_TEAM, SY_TEAM):
            ... draw_figma_* ...
    """
    def __init__(self, sx: float, sy: float):
        self._sx = sx
        self._sy = sy
        self._prev = None

    def __enter__(self):
        self._prev = (_sx(), _sy())
        _scale_state.sx = self._sx
        _scale_state.sy = self._sy
        return self

    def __exit__(self, exc_type, exc, tb):
        prev_sx, prev_sy = self._prev
        _scale_state.sx = prev_sx
        _scale_state.sy = prev_sy
        return False


def draw_figma_text_right(draw, text, x, y, font, font_size=24, fill_color=(255, 255, 255), stroke_width=0, stroke_fill=None, shadow=False, **kwargs):
    text_str = str(text)
    # 設計座標 → キャンバス座標
    if shadow:
        draw.text((x * _sx() + 2 * _sx(), y * _sy() + 2 * _sy()), text_str, fill=(0, 0, 0, 160), font=font, anchor="ra")
    if stroke_width > 0:
        sf = stroke_fill if stroke_fill is not None else fill_color
        draw.text((x * _sx(), y * _sy()), text_str, fill=fill_color, font=font, anchor="ra",
                  stroke_width=max(1, round(stroke_width * _sy())), stroke_fill=sf)
    else:
        draw.text((x * _sx(), y * _sy()), text_str, fill=fill_color, font=font, anchor="ra")


def draw_figma_box(img, x, y, width, height, radius=15, fill_color=(60, 64, 72, 125),
                   outline_color=(140, 145, 155, 90), outline_width=1, shadow=True,
                   shadow_offset=(6, 6), shadow_blur=8, shadow_alpha=70):
    x1, y1 = x * _sx(), y * _sy()
    x2, y2 = x1 + width * _sx(), y1 + height * _sy()
    radius = radius * _sy()
    ox, oy = (shadow_offset[0] * _sx(), shadow_offset[1] * _sy()) if shadow else (0, 0)
    outline_width = max(1, round(outline_width * _sy())) if (outline_color and outline_width > 0) else 0

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
        scaled_size = max(1, round(font_size * _sy()))
        path = getattr(font, "path", None)
        if path and os.path.exists(path):
            actual_font = get_cached_font(path, scaled_size)
        elif isinstance(font, str) and os.path.exists(font):
            actual_font = get_cached_font(font, scaled_size)

    x_s = x * _sx()
    bw_s = box_width * _sx() if box_width else None
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
        draw.text((actual_x + 2 * _sx(), y * _sy() + 2 * _sy()), text_str, font=actual_font, fill=(0, 0, 0, 160))
    if stroke_width > 0:
        sf = stroke_fill if stroke_fill is not None else fill_color
        draw.text((actual_x, y * _sy()), text_str, font=actual_font, fill=fill_color,
                  stroke_width=max(1, round(stroke_width * _sy())), stroke_fill=sf)
    else:
        draw.text((actual_x, y * _sy()), text_str, font=actual_font, fill=fill_color)


def draw_figma_text_with_shadow(draw, text, x, y, font, font_size=None, fill_color=(255, 255, 255),
                                shadow_color=(0, 0, 0, 160), shadow_offset=(2, 2),
                                align="left", box_width=None):
    """影のみ（アウトラインなし）。先に影を描き、その上に本文を重ねる。"""
    text_str = str(text)
    actual_font = font

    if font_size is not None:
        scaled_size = max(1, round(font_size * _sy()))
        path = getattr(font, "path", None)
        if path and os.path.exists(path):
            actual_font = get_cached_font(path, scaled_size)
        elif isinstance(font, str) and os.path.exists(font):
            actual_font = get_cached_font(font, scaled_size)

    x_s = x * _sx()
    bw_s = box_width * _sx() if box_width else None
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

    sox, soy = shadow_offset[0] * _sx(), shadow_offset[1] * _sy()
    y_s = y * _sy()
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
    x1, y1, x2, y2 = x1 * _sx(), y1 * _sy(), x2 * _sx(), y2 * _sy()
    width = max(1, round(width * _sy()))
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
    x, y = x * _sx(), y * _sy()
    size = size * _sy()
    outline_width = max(1, round(outline_width * _sy())) if outline_width else 0
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


def draw_figma_dot(img, x, y, size, ratio=2.2, fill_color=(60, 64, 72, 125), outline_color=None, outline_width=0, corners=None):
    """横長の丸角ドット。size は高さ、ratio は幅の倍率（丸1.5個分=横長）。y は上端。
    corners: (左上, 右上, 右下, 左下) の丸める有無。None なら全角丸。"""
    x, y = x * _sx(), y * _sy()
    size = size * _sy()
    dot_w = max(1, round(size * ratio))
    outline_width = max(1, round(outline_width * _sy())) if outline_width else 0
    canvas_w, canvas_h = img.size
    margin = max(1, outline_width) + 1
    layer_x1 = int(max(0, x - margin))
    layer_y1 = int(max(0, y - margin))
    layer_x2 = int(min(canvas_w, x + dot_w + margin))
    layer_y2 = int(min(canvas_h, y + size + margin))
    if layer_x2 <= layer_x1 or layer_y2 <= layer_y1:
        return
    overlay = Image.new("RGBA", (layer_x2 - layer_x1, layer_y2 - layer_y1), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    _dot_radius = max(1, min(round(min(size, dot_w) / 2), max(1, round(size) // 2 - 1)))
    draw.rounded_rectangle(
        [x - layer_x1, y - layer_y1, x + dot_w - layer_x1, y + size - layer_y1],
        radius=_dot_radius,
        fill=fill_color,
        outline=outline_color,
        corners=corners,
        width=outline_width,
    )
    img.alpha_composite(overlay, dest=(layer_x1, layer_y1))


def paste_figma_image(base_img, img_path, box_x, box_y, box_width, box_height, radius=15, beta="false"):
    img_path = resolve_datas_path(img_path, beta)
    if not img_path or not os.path.exists(img_path):
        return
    try:
        bx, by = int(round(box_x * _sx())), int(round(box_y * _sy()))
        bw, bh = max(1, round(box_width * _sx())), max(1, round(box_height * _sy()))
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
            mask_draw.rounded_rectangle([0, 0, bw, bh], radius=max(1, round(radius * _sy())), fill=255)

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
        bw, bh = max(1, round(box_width * _sx())), max(1, round(box_height * _sy()))
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
        mask_draw.rounded_rectangle([0, 0, bw, bh], radius=max(1, round(radius * _sy())), fill=255)

        r, g, b, a = canvas.split()
        combined_alpha = ImageChops.multiply(a, corner_mask)
        canvas = Image.merge("RGBA", (r, g, b, combined_alpha))

        base_img.paste(canvas, (int(round(box_x * _sx())), int(round(box_y * _sy()))), canvas)
    except Exception as e:
        print(f"[Error] Failed to paste mask image: {img_path}. Reason: {e}")