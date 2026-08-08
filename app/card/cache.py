import os
import time
from collections import OrderedDict
from PIL import Image, ImageFont
from app.paths import STATIC_DIR

# ==========================================================
#  画像キャッシュ（無制限 dict だと RSS が単調増加して OOM する）
#  - 各エントリは RGBA ビットマップ。splash blur 1枚 ≈ 2400*1620*4 ≒ 15MB
#  - LRU 上限 + TTL 全クリア + バックグラウンド定期掃除
# ==========================================================
_IMAGE_CACHE: "OrderedDict" = OrderedDict()
_FONT_CACHE: dict = {}
_SPLASH_BLUR_CACHE: "OrderedDict" = OrderedDict()
_RESIZED_CACHE: "OrderedDict" = OrderedDict()
_PREBUILT_BGS: dict = {}
_REGION_BGS: "OrderedDict" = OrderedDict()
_REGION_MAP_CACHE: dict | None = None
_ADMIN_CATALOG_CACHE: dict = {}
_CACHE_LOCK = __import__("threading").Lock()

# 件数上限（環境変数で調整可）
_CACHE_MAX_IMAGES = max(8, int(os.environ.get("CACHE_MAX_IMAGES", "64")))
_CACHE_MAX_RESIZED = max(16, int(os.environ.get("CACHE_MAX_RESIZED", "96")))
# splash blur は特に大きいので厳しめ
_CACHE_MAX_SPLASH_BLUR = max(2, int(os.environ.get("CACHE_MAX_SPLASH_BLUR", "12")))
_CACHE_MAX_REGION_BGS = max(4, int(os.environ.get("CACHE_MAX_REGION_BGS", "16")))

# 画像メモリキャッシュの寿命（秒）。CARD_CACHE_TTL_SEC 優先、なければ HOURS。
# デフォルト 5 分。Blob/オブジェクトストレージとは無関係（プロセス内 PIL キャッシュのみ）。
# デフォルト 300 秒 = 5 分
_CARD_CACHE_TTL_SEC = float(os.environ.get("CARD_CACHE_TTL_SEC", "300") or 300)
if "CARD_CACHE_TTL_SEC" not in os.environ and "CARD_CACHE_TTL_HOURS" in os.environ:
    _CARD_CACHE_TTL_SEC = max(60.0, float(os.environ.get("CARD_CACHE_TTL_HOURS", "1")) * 3600.0)
# バックグラウンド掃除の間隔（デフォルトも 5 分）
_CACHE_SWEEP_SEC = max(30.0, float(os.environ.get("CACHE_SWEEP_SEC", "300")))

_CARD_CACHE_LAST_RESET = time.time()


def _lru_set(cache: OrderedDict, key, value, max_size: int) -> None:
    """上限付き LRU 挿入。超過分は最古から捨てる。"""
    with _CACHE_LOCK:
        if key in cache:
            cache.move_to_end(key)
            cache[key] = value
        else:
            cache[key] = value
            cache.move_to_end(key)
        while len(cache) > max_size:
            cache.popitem(last=False)


def _lru_get(cache: OrderedDict, key):
    with _CACHE_LOCK:
        if key not in cache:
            return None
        cache.move_to_end(key)
        return cache[key]


def _maybe_reset_image_caches(force: bool = False) -> bool:
    """TTL ごとに動的画像キャッシュを捨ててメモリを解放する。
    プリビルド背景・フォントは残す。
    """
    global _CARD_CACHE_LAST_RESET
    now = time.time()
    if not force and (now - _CARD_CACHE_LAST_RESET) < _CARD_CACHE_TTL_SEC:
        return False
    with _CACHE_LOCK:
        n_img = len(_IMAGE_CACHE)
        n_rsz = len(_RESIZED_CACHE)
        n_blur = len(_SPLASH_BLUR_CACHE)
        n_reg = len(_REGION_BGS)
        _IMAGE_CACHE.clear()
        _RESIZED_CACHE.clear()
        _SPLASH_BLUR_CACHE.clear()
        _REGION_BGS.clear()
    _CARD_CACHE_LAST_RESET = now
    try:
        import gc
        gc.collect()
    except Exception:
        pass
    print(
        f"[Cache] reset: images={n_img} resized={n_rsz} "
        f"splash_blur={n_blur} region_bgs={n_reg} "
        f"(ttl={_CARD_CACHE_TTL_SEC:.0f}s force={force})",
        flush=True,
    )
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
    __import__("threading").Thread(
        target=_cache_sweeper_loop, name="cache-sweeper", daemon=True
    ).start()
    print(
        f"[OK] Cache LRU: images<={_CACHE_MAX_IMAGES} resized<={_CACHE_MAX_RESIZED} "
        f"splash_blur<={_CACHE_MAX_SPLASH_BLUR} region_bgs<={_CACHE_MAX_REGION_BGS} "
        f"ttl={_CARD_CACHE_TTL_SEC:.0f}s sweep={_CACHE_SWEEP_SEC:.0f}s",
        flush=True,
    )


_ensure_cache_sweeper()


def get_cached_font(path: str, size: int):
    """OPTIMIZED: Cache ImageFont.truetype results by (path, size)."""
    if not path or not os.path.exists(path):
        return ImageFont.load_default()
    key = (path, size)
    if key not in _FONT_CACHE:
        try:
            _FONT_CACHE[key] = ImageFont.truetype(path, size)
        except Exception as e:
            print(f"[Warning] Failed to load font {path} size={size}: {e}")
            _FONT_CACHE[key] = ImageFont.load_default()
    return _FONT_CACHE[key]


def get_cached_image(path: str):
    """Cache Image.open().convert('RGBA') with LRU. Returns a copy."""
    if not path:
        return None
    hit = _lru_get(_IMAGE_CACHE, path)
    if hit is not None:
        return hit.copy()
    if not os.path.exists(path):
        return None
    try:
        img = Image.open(path).convert("RGBA")
    except Exception as e:
        print(f"[Warning] Failed to cache image {path}: {e}")
        return None
    _lru_set(_IMAGE_CACHE, path, img, _CACHE_MAX_IMAGES)
    return img.copy()

def get_resized_image(path: str, size: tuple):
    """指定サイズにリサイズ済み画像を LRU キャッシュして返す。"""
    if not path:
        return None
    key = (path, size)
    hit = _lru_get(_RESIZED_CACHE, key)
    if hit is not None:
        return hit.copy()
    img = get_cached_image(path)
    if img is None:
        return None
    filt = get_resize_filter(size)
    resized = img.resize(size, filt)
    _lru_set(_RESIZED_CACHE, key, resized, _CACHE_MAX_RESIZED)
    return resized.copy()

def get_resize_filter(target_size):
    """
    OPTIMIZED: BILINEAR for small images.
    Pillow-SIMD note: BICUBIC is generally preferred over LANCZOS for better SIMD performance.
    """
    w, h = target_size if isinstance(target_size, (tuple, list)) else (target_size, target_size)
    """
    if max(w, h) < 100:
        return Image.Resampling.BILINEAR
    """
    return Image.Resampling.BICUBIC
