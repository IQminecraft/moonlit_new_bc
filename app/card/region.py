import os
from app.paths import STATIC_DIR
from app.card.jsoncache import load_json_cached
from app.card.bg import region_image_path

# 旅人（10000005/10000007）は地域マップで管理せず、元素ごとに背景地域を固定する。
# admin からの割り当ては無効（POST 時にスキップ・ロード時に除去）。
_TRAVELER_BASE_IDS = (10000005, 10000007)
_TRAVELER_ELEMENT_REGIONS = {
    "Anemo": "mondstadt",
    "Geo": "liyue",
    "Electro": "inazuma",
    "Dendro": "sumeru",
    "Hydro": "fontaine",
    "Pyro": "natlan",
    "Cryo": "snezhnaya",
}

# characters.json の解析結果キャッシュ（clear_region_map_cache で無効化）
_REGION_MAP_CACHE = None


def _load_region_map():
    """static/assets/characters/characters.json を {地域名: [キャラ数値ID]} として読む。"""
    global _REGION_MAP_CACHE
    if _REGION_MAP_CACHE is None:
        region_map = {}
        path = os.path.join(STATIC_DIR, "assets", "characters", "characters.json")
        if os.path.exists(path):
            try:
                data = load_json_cached(path)
                if isinstance(data, dict):
                    for region, ids in data.items():
                        if not isinstance(ids, list):
                            continue
                        cleaned = []
                        for cid in ids:
                            try:
                                cleaned.append(int(cid))
                            except (TypeError, ValueError):
                                continue
                        # 旅人は元素連動のためマップに保持しない
                        region_map[str(region)] = [cid for cid in cleaned if cid not in _TRAVELER_BASE_IDS]
            except Exception as e:
                print(f"[Warning] region map load failed: {e}")
        _REGION_MAP_CACHE = region_map
    return _REGION_MAP_CACHE


def clear_region_map_cache():
    global _REGION_MAP_CACHE
    _REGION_MAP_CACHE = None


def find_regions_for_character(base_id, element=None):
    """キャラ（ベース数値ID）が所属する地域リストをJSONのキー順で返す。

    旅人（10000005/10000007）は地域マップを使わず、元素ごとの固定地域を返す（admin編集不可）。
    """
    try:
        base_int = int(str(base_id).split("-")[0])
    except (TypeError, ValueError):
        return []
    if base_int in _TRAVELER_BASE_IDS and element:
        mapped = _TRAVELER_ELEMENT_REGIONS.get(str(element))
        return [mapped] if mapped else []
    return [region for region, ids in _load_region_map().items() if base_int in ids]


def build_region_info(regions):
    """フロントエンド向けの地域情報 [{name, image}] を作る。image は背景画像がある時のみ。"""
    info = []
    for region in regions:
        info.append({
            "name": region,
            "image": f"static/assets/states/{region}.png" if region_image_path(region) else None,
        })
    return info
