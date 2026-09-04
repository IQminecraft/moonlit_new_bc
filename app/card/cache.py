# -*- coding: utf-8 -*-
"""画像・フォント・レイヤのメモリキャッシュ（バイト予算制 LRU）。

旧設計は「件数上限」のみだったため、1枚 15MB 級のスプラッシュぼかしや
2400x1620 の背景が混在すると上限件数以内でも RSS が大きく膨らんだ。
ここでは各キャッシュが *バイト予算* で管理され、予算超過時は最古から
淘汰される。サーバーメモリ圧迫の天井を環境変数で明示的に制御できる。

- get() は常に「コピー」を返す（呼び出し側が自由に加工できる安全语义）。
- put() で渡した画像はキャッシュが所有する（以降変更しないこと）。
- 予算単価は 生バイト幅 x 高さ x バンド数 + 1KB の見積もり。
"""
import gc
import os
import time
import threading
from collections import OrderedDict
from PIL import Image, ImageDraw, ImageFont
# .env の読み込み（dotenv）は app.paths の import 副作用で行われる。
# このモジュールは環境変数を import 時に読むため、どの import 順でも
# .env が先に載るよう app.paths をここで参照しておく。
from app.paths import BASE_DIR  # noqa: F401

# ==========================================================
#  バイト予算制 LRU
# ==========================================================
_CACHE_LOCK = threading.Lock()

_OVERHEAD = 1024  # PIL オブジェクトのヘッダ等見積もり


def _estimate_bytes(value) -> int:
    """キャッシュエントリのメモリ見積もり。"""
    if isinstance(value, Image.Image):
        return value.width * value.height * max(1, len(value.getbands())) + _OVERHEAD
    return 4096  # 画像以外（フォント等）の名目サイズ


class BoundedImageLRU:
    """バイト予算 + 件数上限の LRU。スレッドセーフ。

    get() は格納値のコピーを返す（Image 以外はそのもの）。
    """

    def __init__(self, name: str, max_bytes: int, max_items: int):
        self.name = name
        self.max_bytes = max(1, int(max_bytes))
        self.max_items = max(1, int(max_items))
        self._data: "OrderedDict" = OrderedDict()  # key -> (value, size)
        self._bytes = 0
        self.hits = 0
        self.misses = 0

    def get(self, key, copy: bool = True):
        with _CACHE_LOCK:
            ent = self._data.get(key)
            if ent is None:
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            value = ent[0]
        if copy and isinstance(value, Image.Image):
            return value.copy()
        return value

    def put(self, key, value, size: int = None) -> None:
        if size is None:
            size = _estimate_bytes(value)
        if size > self.max_bytes:
            return  # 予算超えのエントリはキャッシュしない（1枚で全枠を食うのを防ぐ）
        with _CACHE_LOCK:
            old = self._data.pop(key, None)
            if old is not None:
                self._bytes -= old[1]
            self._data[key] = (value, size)
            self._bytes += size
            while (self._bytes > self.max_bytes or len(self._data) > self.max_items) and self._data:
                _, (_, evicted_size) = self._data.popitem(last=False)
                self._bytes -= evicted_size

    def clear(self) -> int:
        with _CACHE_LOCK:
            n = len(self._data)
            self._data.clear()
            self._bytes = 0
            return n

    def __len__(self) -> int:
        with _CACHE_LOCK:
            return len(self._data)

    def stats(self) -> dict:
        with _CACHE_LOCK:
            return {
                "items": len(self._data),
                "items_max": self.max_items,
                "bytes": self._bytes,
                "mb": round(self._bytes / (1024 * 1024), 1),
                "max_mb": round(self.max_bytes / (1024 * 1024), 1),
                "hits": self.hits,
                "misses": self.misses,
            }


def _env_mb(name: str, default_mb: float) -> int:
    try:
        return int(float(os.environ.get(name, str(default_mb))) * 1024 * 1024)
    except (TypeError, ValueError):
        return int(default_mb * 1024 * 1024)


# 各キャッシュのバイト予算（環境変数で調整可。合計の天井 ≈ これらの和）。
# 旧件数上限設計の最悪ケース（数百MB級）より厳しい既定値にしている。
IMAGE_CACHE = BoundedImageLRU("images", _env_mb("CACHE_IMAGES_MB", 64), max_items=128)
RESIZED_CACHE = BoundedImageLRU("resized", _env_mb("CACHE_RESIZED_MB", 64), max_items=256)
SPLASH_BLUR_CACHE = BoundedImageLRU("splash_blur", _env_mb("CACHE_SPLASH_BLUR_MB", 96), max_items=24)
REGION_BGS = BoundedImageLRU("region_bgs", _env_mb("CACHE_REGION_BGS_MB", 48), max_items=24)
# 描画レイヤ（ガラスボックス等の合成済みパネル・角丸マスク）のキャッシュ
LAYER_CACHE = BoundedImageLRU("layers", _env_mb("CACHE_LAYERS_MB", 64), max_items=256)
MASK_CACHE = BoundedImageLRU("masks", _env_mb("CACHE_MASKS_MB", 16), max_items=256)

_DYNAMIC_CACHES = (IMAGE_CACHE, RESIZED_CACHE, SPLASH_BLUR_CACHE, REGION_BGS, LAYER_CACHE, MASK_CACHE)

# プリビルド背景（起動時に生成。動的キャッシュとは別に永続保持。RGB で保持し
# 使用時に RGBA 変換することで、旧 RGBA 保持比でメモリを約 25% 削減）
_PREBUILT_BGS: dict = {}

_FONT_CACHE: "OrderedDict" = OrderedDict()
_FONT_CACHE_MAX = max(16, int(os.environ.get("FONT_CACHE_MAX", "256")))
_FONT_EXISTS: dict = {}

# ==========================================================
#  TTL リセット + バックグラウンド掃除（従来挙動を維持）
# ==========================================================
# 画像メモリキャッシュの寿命（秒）。CARD_CACHE_TTL_SEC 優先、なければ HOURS。
_CARD_CACHE_TTL_SEC = float(os.environ.get("CARD_CACHE_TTL_SEC", "300") or 300)
if "CARD_CACHE_TTL_SEC" not in os.environ and "CARD_CACHE_TTL_HOURS" in os.environ:
    _CARD_CACHE_TTL_SEC = max(60.0, float(os.environ.get("CARD_CACHE_TTL_HOURS", "1")) * 3600.0)
_CACHE_SWEEP_SEC = max(30.0, float(os.environ.get("CACHE_SWEEP_SEC", "300")))

_CARD_CACHE_LAST_RESET = time.time()


def _maybe_reset_image_caches(force: bool = False) -> bool:
    """TTL ごとに動的画像キャッシュを捨ててメモリを解放する。
    プリビルド背景・フォント・JSONキャッシュは残す。
    """
    global _CARD_CACHE_LAST_RESET
    now = time.time()
    if not force and (now - _CARD_CACHE_LAST_RESET) < _CARD_CACHE_TTL_SEC:
        return False
    counts = {c.name: c.clear() for c in _DYNAMIC_CACHES}
    _CARD_CACHE_LAST_RESET = now
    try:
        gc.collect()
    except Exception:
        pass
    summary = " ".join(f"{k}={v}" for k, v in counts.items())
    print(f"[Cache] reset: {summary} (ttl={_CARD_CACHE_TTL_SEC:.0f}s force={force})", flush=True)
    return True


def _cache_sweeper_loop() -> None:
    """リクエストが来なくても定期的に TTL クリアする（長時間放置での OOM 防止）。"""
    while True:
        try:
            time.sleep(_CACHE_SWEEP_SEC)
            _maybe_reset_image_caches(force=False)
        except Exception as e:
            print(f"[Cache] sweeper error: {e}", flush=True)


_CACHE_SWEEPER_STARTED = False


def _ensure_cache_sweeper() -> None:
    global _CACHE_SWEEPER_STARTED
    if _CACHE_SWEEPER_STARTED:
        return
    _CACHE_SWEEPER_STARTED = True
    threading.Thread(target=_cache_sweeper_loop, name="cache-sweeper", daemon=True).start()
    budget = " ".join(f"{c.name}<={c.stats()['max_mb']:.0f}MB" for c in _DYNAMIC_CACHES)
    print(
        f"[OK] Cache LRU (byte budget): {budget} "
        f"ttl={_CARD_CACHE_TTL_SEC:.0f}s sweep={_CACHE_SWEEP_SEC:.0f}s",
        flush=True,
    )


_ensure_cache_sweeper()


# ==========================================================
#  公開 API
# ==========================================================
def _font_exists(path: str) -> bool:
    """フォントファイル存在チェックのキャッシュ（生成1枚で数百回呼ばれるため）。"""
    hit = _FONT_EXISTS.get(path)
    if hit is None:
        hit = os.path.exists(path)
        _FONT_EXISTS[path] = hit
    return hit


def get_cached_font(path: str, size: int):
    """ImageFont.truetype の結果を (path, size) でキャッシュして返す。"""
    if not path or not _font_exists(path):
        return ImageFont.load_default()
    key = (path, size)
    with _CACHE_LOCK:
        font = _FONT_CACHE.get(key)
        if font is not None:
            _FONT_CACHE.move_to_end(key)
            return font
    try:
        font = ImageFont.truetype(path, size)
    except Exception as e:
        print(f"[Warning] Failed to load font {path} size={size}: {e}")
        return ImageFont.load_default()
    with _CACHE_LOCK:
        _FONT_CACHE[key] = font
        _FONT_CACHE.move_to_end(key)
        while len(_FONT_CACHE) > _FONT_CACHE_MAX:
            _FONT_CACHE.popitem(last=False)
    return font


def get_cached_image(path: str):
    """RGBA 変換済み画像を LRU キャッシュして返す（戻り値はコピー）。"""
    if not path:
        return None
    hit = IMAGE_CACHE.get(path)
    if hit is not None:
        return hit
    if not os.path.exists(path):
        return None
    try:
        img = Image.open(path).convert("RGBA")
    except Exception as e:
        print(f"[Warning] Failed to cache image {path}: {e}")
        return None
    IMAGE_CACHE.put(path, img)
    return img.copy()


def get_resized_image(path: str, size: tuple):
    """指定サイズにリサイズ済み画像を LRU キャッシュして返す（戻り値はコピー）。"""
    if not path:
        return None
    key = (path, size)
    hit = RESIZED_CACHE.get(key)
    if hit is not None:
        return hit
    img = get_cached_image(path)
    if img is None:
        return None
    resized = img.resize(size, get_resize_filter(size))
    RESIZED_CACHE.put(key, resized)
    return resized.copy()


def get_resize_filter(target_size):
    """BICUBIC 固定（Pillow-SIMD では LANCZOS より BICUBIC が高速）。"""
    return Image.Resampling.BICUBIC


def get_rounded_mask(w: int, h: int, radius: int):
    """角丸矩形の "L" モードマスクをキャッシュして返す（共有・変更禁止）。"""
    from app.card.draw import safe_rounded_rectangle  # 循環参照回避のため関数内 import
    w, h, radius = max(1, int(w)), max(1, int(h)), max(0, int(radius))
    key = (w, h, radius)
    hit = MASK_CACHE.get(key, copy=False)
    if hit is not None:
        return hit
    mask = Image.new("L", (w, h), 0)
    safe_rounded_rectangle(ImageDraw.Draw(mask), [0, 0, w - 1, h - 1], radius=radius, fill=255)
    MASK_CACHE.put(key, mask)
    return mask


def cache_stats() -> dict:
    """server_stats / admin 用の集計。旧キー(images/resized/splash_blur 等)も維持。"""
    s = {c.name: c.stats() for c in _DYNAMIC_CACHES}
    with _CACHE_LOCK:
        fonts = len(_FONT_CACHE)
    return {
        "images": s["images"]["items"],
        "images_max": s["images"]["items_max"],
        "resized": s["resized"]["items"],
        "resized_max": s["resized"]["items_max"],
        "splash_blur": s["splash_blur"]["items"],
        "splash_blur_max": s["splash_blur"]["items_max"],
        "region_bgs": s["region_bgs"]["items"],
        "region_bgs_max": s["region_bgs"]["items_max"],
        "layers": s["layers"]["items"],
        "masks": s["masks"]["items"],
        "fonts": fonts,
        "ttl_sec": round(_CARD_CACHE_TTL_SEC, 1),
        "last_reset_ago_sec": round(time.time() - _CARD_CACHE_LAST_RESET, 1),
        "budget": s,
    }
