import os
import threading
from PIL import Image, ImageDraw, ImageChops, ImageFilter
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


# ==============================================================
#  ライトモード描画テーマ（スレッドローカル）。
#  figma_draw_theme(light=True) 内では、draw_figma_* 系関数の
#  「色未指定時の既定色」がライト系（暗い文字 / 白系パネル）に切り替わる。
#  明示的に色が渡された描画は影響しない。
# ==============================================================
_theme_state = threading.local()

_DEFAULT = object()

_LIGHT_COLORS = {
    "text": (17, 24, 39, 255),
    "shadow": (255, 255, 255, 180),
    "box_fill": (255, 255, 255, 160),
    "box_outline": (15, 23, 42, 38),
    "glass_fill_top": (255, 255, 255, 170),
    "glass_fill_bottom": (241, 245, 249, 190),
    "glass_border_top": (15, 23, 42, 45),
    "glass_border_bottom": (15, 23, 42, 30),
    "line": (15, 23, 42, 40),
    "icon_container": (15, 23, 42, 70),
}


class figma_draw_theme:
    """現在のスレッド限定で描画既定色を切り替えるコンテキストマネージャ。

    使用例::
        with figma_draw_theme(light=True):
            ... draw_figma_* ...
    """
    def __init__(self, light=True):
        self._light = bool(light)
        self._prev = None

    def __enter__(self):
        self._prev = getattr(_theme_state, "light", False)
        _theme_state.light = self._light
        return self

    def __exit__(self, exc_type, exc, tb):
        _theme_state.light = self._prev
        return False


def _is_light_theme() -> bool:
    return bool(getattr(_theme_state, "light", False))


def _theme_color(key, dark_value):
    """ライトモード中はライト既定色、それ以外は従来既定色を返す。"""
    if _is_light_theme():
        return _LIGHT_COLORS.get(key, dark_value)
    return dark_value


def safe_rounded_rectangle(draw, xy, radius=0, **kwargs):
    """座標を正規化してから rounded_rectangle を呼ぶ安全ラッパー。

    PIL/Pillow-SIMD の rounded_rectangle は y1 < y0（または x1 < x0）で
    ValueError("y1 must be greater than or equal to y0") を送出し、そのまま
    500 になる。ここで反転した座標は入れ替え、radius は短辺の半分以下に
    クランプする（Pillow 12 相当の挙動に統一）。Pillow-SIMD 9.5 系には
    radius クランプがないため、版差や異常入力でも必ず描画が成功する。
    """
    if isinstance(xy[0], (list, tuple)):
        (x0, y0), (x1, y1) = xy
    else:
        x0, y0, x1, y1 = xy
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    if radius:
        radius = max(0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
    return draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, **kwargs)


def draw_figma_text_right(draw, text, x, y, font, font_size=24, fill_color=_DEFAULT, stroke_width=0, stroke_fill=None, shadow=False, **kwargs):
    if fill_color is _DEFAULT:
        fill_color = _theme_color("text", (255, 255, 255))
    text_str = str(text)
    # 設計座標 → キャンバス座標
    if shadow:
        draw.text((x * _sx() + 2 * _sx(), y * _sy() + 2 * _sy()), text_str, fill=_theme_color("shadow", (0, 0, 0, 160)), font=font, anchor="ra")
    if stroke_width > 0:
        sf = stroke_fill if stroke_fill is not None else fill_color
        draw.text((x * _sx(), y * _sy()), text_str, fill=fill_color, font=font, anchor="ra",
                  stroke_width=max(1, round(stroke_width * _sy())), stroke_fill=sf)
    else:
        draw.text((x * _sx(), y * _sy()), text_str, fill=fill_color, font=font, anchor="ra")


def draw_figma_box(img, x, y, width, height, radius=15, fill_color=None,
                   outline_color=None, outline_width=1, shadow=True,
                   shadow_offset=(6, 6), shadow_blur=8, shadow_alpha=70):
    if fill_color is None:
        fill_color = _theme_color("box_fill", (60, 64, 72, 125))
    if outline_color is None:
        outline_color = _theme_color("box_outline", (140, 145, 155, 90))
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
        safe_rounded_rectangle(
            draw,
            [x1 + ox + dx, y1 + oy + dy, x2 + ox + dx, y2 + oy + dy],
            radius=radius,
            fill=(0, 0, 0, shadow_alpha),
        )

    outline_kwargs = {}
    if outline_color and outline_width > 0:
        outline_kwargs = {"outline": outline_color, "width": outline_width}
    safe_rounded_rectangle(draw, [x1 + dx, y1 + dy, x2 + dx, y2 + dy], radius=radius, fill=fill_color, **outline_kwargs)

    img.alpha_composite(overlay, dest=(layer_x1, layer_y1))


def _composite_clipped(img, layer, dx, dy):
    """レイヤーをキャンバス範囲にクリップして合成する（はみ出し分の ValueError 防止）。"""
    iw, ih = img.size
    if dx < 0 or dy < 0:
        cx0, cy0 = max(0, -dx), max(0, -dy)
        if cx0 >= layer.width or cy0 >= layer.height:
            return
        layer = layer.crop((cx0, cy0, layer.width, layer.height))
        dx, dy = max(0, dx), max(0, dy)
    over_x = (dx + layer.width) - iw
    over_y = (dy + layer.height) - ih
    if over_x > 0 or over_y > 0:
        cw = layer.width - max(0, over_x)
        ch = layer.height - max(0, over_y)
        if cw <= 0 or ch <= 0:
            return
        layer = layer.crop((0, 0, cw, ch))
    img.alpha_composite(layer, dest=(dx, dy))


def _vertical_gradient_rgba(w, h, top, bottom):
    """上端 top -> 下端 bottom の RGBA 縦グラデーション画像を作る。"""
    w, h = max(1, int(w)), max(1, int(h))
    strip = Image.new("RGBA", (1, h), (0, 0, 0, 0))
    px = strip.load()
    denom = max(1, h - 1)
    for yy in range(h):
        t = yy / denom
        px[0, yy] = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(4))
    return strip.resize((w, h), Image.Resampling.BILINEAR)


def draw_figma_glass_box(img, x, y, width, height, radius=25,
                         fill_top=None, fill_bottom=None,
                         border_top=None, border_bottom=None,
                         highlight_alpha=0, shadow=True, shadow_alpha=28,
                         border_width=1.5):
    """モダンなフラットガラスのパネルを描く（draw_figma_box の強化版）。

    - ほぼ均一の半透明塗り（ごく弱い縦グラデで奥行き感だけ残す）
    - 細く均一な白系枠線
    - 上端ハイライト（ガラスの反射）は既定で無効
    - 控えめで締まった影（弱アルファ + 小ぼかし）

    座標・サイズは設計座標（draw_figma_box と同じ）。
    """
    if fill_top is None:
        fill_top = _theme_color("glass_fill_top", (22, 30, 52, 72))
    if fill_bottom is None:
        fill_bottom = _theme_color("glass_fill_bottom", (16, 22, 40, 80))
    if border_top is None:
        border_top = _theme_color("glass_border_top", (255, 255, 255, 40))
    if border_bottom is None:
        border_bottom = _theme_color("glass_border_bottom", (255, 255, 255, 30))
    x1, y1 = x * _sx(), y * _sy()
    w = int(round(width * _sx()))
    h = int(round(height * _sy()))
    r = max(1, round(radius * _sy()))
    X0, Y0 = int(round(x1)), int(round(y1))
    if w <= 2 or h <= 2:
        return
    r = min(r, w // 2, h // 2)

    # ---- 1) 控えめで締まった影 ----
    if shadow:
        blur_r = max(2, round(3 * _sy()))
        pad = blur_r * 2
        off_y = int(round(3 * _sy()))
        sw, sh = w + pad * 2, h + pad * 2
        lay = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        ld = ImageDraw.Draw(lay)
        safe_rounded_rectangle(ld, [pad, pad + off_y, pad + w, pad + off_y + h],
                               radius=r, fill=(0, 0, 0, shadow_alpha))
        lay = lay.filter(ImageFilter.GaussianBlur(radius=blur_r))
        _composite_clipped(img, lay, X0 - pad, Y0 - pad)

    # ---- 2) 縦グラデ塗り + 角丸マスク ----
    grad = _vertical_gradient_rgba(w, h, fill_top, fill_bottom)
    mask = Image.new("L", (w, h), 0)
    safe_rounded_rectangle(ImageDraw.Draw(mask), [0, 0, w - 1, h - 1], radius=r, fill=255)
    grad.putalpha(ImageChops.multiply(grad.getchannel("A"), mask))
    _composite_clipped(img, grad, X0, Y0)

    # ---- 3) グラデ枠線（外角丸 - 内角丸 のマスクに白グラデを流す） ----
    bw = max(1, round(border_width * _sy()))
    if min(w, h) > bw * 2 + 2:
        omask = Image.new("L", (w, h), 0)
        od = ImageDraw.Draw(omask)
        safe_rounded_rectangle(od, [0, 0, w - 1, h - 1], radius=r, fill=255)
        safe_rounded_rectangle(od, [bw, bw, w - 1 - bw, h - 1 - bw],
                               radius=max(1, r - bw), fill=0)
        blayer = _vertical_gradient_rgba(w, h, border_top, border_bottom)
        blayer.putalpha(ImageChops.multiply(blayer.getchannel("A"), omask))
        _composite_clipped(img, blayer, X0, Y0)

    # ---- 4) 上端ハイライト（反射） ----
    if highlight_alpha > 0:
        hh = max(2, int(h * 0.10))
        hl = _vertical_gradient_rgba(w, hh, (255, 255, 255, highlight_alpha), (255, 255, 255, 0))
        hmask = mask.crop((0, 0, w, hh))
        hl.putalpha(ImageChops.multiply(hl.getchannel("A"), hmask))
        _composite_clipped(img, hl, X0, Y0)


def draw_figma_text(draw, text, x, y, font, font_size=None, fill_color=_DEFAULT, align="left", box_width=None, stroke_width=0, stroke_fill=None, shadow=False):
    if fill_color is _DEFAULT:
        fill_color = _theme_color("text", (255, 255, 255))
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
    if align == "right":
        # box_width 未指定時は x を右端として扱う（右揃え）
        text_width = draw.textlength(text_str, font=actual_font)
        actual_x = x_s + (bw_s or 0) - text_width
    elif align == "center" and bw_s:
        text_width = draw.textlength(text_str, font=actual_font)
        actual_x = x_s + (bw_s - text_width) / 2
    else:
        actual_x = x_s

    if shadow:
        draw.text((actual_x + 2 * _sx(), y * _sy() + 2 * _sy()), text_str, font=actual_font, fill=_theme_color("shadow", (0, 0, 0, 160)))
    if stroke_width > 0:
        sf = stroke_fill if stroke_fill is not None else fill_color
        draw.text((actual_x, y * _sy()), text_str, font=actual_font, fill=fill_color,
                  stroke_width=max(1, round(stroke_width * _sy())), stroke_fill=sf)
    else:
        draw.text((actual_x, y * _sy()), text_str, font=actual_font, fill=fill_color)


def draw_figma_text_with_shadow(draw, text, x, y, font, font_size=None, fill_color=_DEFAULT,
                                shadow_color=None, shadow_offset=(2, 2),
                                align="left", box_width=None):
    """影のみ（アウトラインなし）。先に影を描き、その上に本文を重ねる。"""
    if fill_color is _DEFAULT:
        fill_color = _theme_color("text", (255, 255, 255))
    if shadow_color is None:
        shadow_color = _theme_color("shadow", (0, 0, 0, 160))
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
    if align == "right":
        # box_width 未指定時は x を右端として扱う（右揃え）
        text_width = draw.textlength(text_str, font=actual_font)
        target_x = x_s + (bw_s or 0) - text_width
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

def draw_figma_line(img, x1, y1, x2, y2, fill_color=None, width=1):
    if fill_color is None:
        fill_color = _theme_color("line", (255, 255, 255, 50))
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


def draw_figma_circle(img, x, y, size, fill_color=None, outline_color=None, outline_width=0):
    if fill_color is None:
        fill_color = _theme_color("icon_container", (60, 64, 72, 125))
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


def draw_figma_dot(img, x, y, size, ratio=2.2, fill_color=None, outline_color=None, outline_width=0, corners=None):
    """横長の丸角ドット。size は高さ、ratio は幅の倍率（丸1.5個分=横長）。y は上端。
    corners: (左上, 右上, 右下, 左下) の丸める有無。None なら全角丸。"""
    if fill_color is None:
        fill_color = _theme_color("icon_container", (60, 64, 72, 125))
    if size <= 0:
        return
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
    safe_rounded_rectangle(
        draw,
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
            safe_rounded_rectangle(mask_draw, [0, 0, bw, bh], radius=max(1, round(radius * _sy())), fill=255)

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
        safe_rounded_rectangle(mask_draw, [0, 0, bw, bh], radius=max(1, round(radius * _sy())), fill=255)

        r, g, b, a = canvas.split()
        combined_alpha = ImageChops.multiply(a, corner_mask)
        canvas = Image.merge("RGBA", (r, g, b, combined_alpha))

        base_img.paste(canvas, (int(round(box_x * _sx())), int(round(box_y * _sy()))), canvas)
    except Exception as e:
        print(f"[Error] Failed to paste mask image: {img_path}. Reason: {e}")