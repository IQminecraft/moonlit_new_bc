# -*- coding: utf-8 -*-
"""キャラ毎のデフォルト計算方式（calc_method）管理。

- カード生成時のスコア計算方式（calc_method）は通常クライアントが明示的に渡す。
- 未選択（空）の場合は、admin がキャラ毎に設定したデフォルト方式を使い、
  それも無ければ "crit" にフォールバックする。
- 保存先: static/data/setting/default_calc_method.json
  形式: {"characters": {"<ベースキャラID>": "<calc_method>"}}
"""
import os
import json

from app.paths import STATIC_DIR
from app.core.jsonio import write_json_atomic

# 有効な計算方式（build_card.html のセレクト選択肢と一致）
VALID_CALC_METHODS = ("crit", "atk", "hp", "def", "em", "charge")
DEFAULT_CALC_METHOD = "crit"

DEFAULT_CALC_METHOD_PATH = os.path.join(STATIC_DIR, "data", "setting", "default_calc_method.json")

_cache = {"map": {}, "mtime": None}


def _base_char_id(char_id) -> str:
    """コスト/元素の添字（例: 10000005-2）を外したベースIDを返す。"""
    return str(char_id).split("-")[0]


def load_default_calc_method_map() -> dict:
    """mtime キャッシュ付きでデフォルト計算方式マップ {ベースID: method} を読み込む。"""
    if not os.path.exists(DEFAULT_CALC_METHOD_PATH):
        _cache["map"] = {}
        _cache["mtime"] = None
        return {}
    try:
        mtime = os.path.getmtime(DEFAULT_CALC_METHOD_PATH)
        if _cache["mtime"] == mtime:
            return _cache["map"]
        with open(DEFAULT_CALC_METHOD_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            chars = data.get("characters")
            raw_map = chars if isinstance(chars, dict) else data
        else:
            raw_map = {}
        cleaned = {}
        for key, val in raw_map.items():
            method = str(val or "").strip()
            if method in VALID_CALC_METHODS:
                cleaned[_base_char_id(key)] = method
        _cache["map"] = cleaned
        _cache["mtime"] = mtime
        return _cache["map"]
    except Exception:
        return _cache["map"] or {}


def save_default_calc_method_map(char_map: dict) -> dict:
    """デフォルト計算方式マップを保存する。入力は {charId: method | null}。"""
    cleaned = {}
    for char_id, method in (char_map or {}).items():
        base = _base_char_id(char_id)
        if not base:
            continue
        m = str(method or "").strip()
        if m in VALID_CALC_METHODS:
            cleaned[base] = m
    os.makedirs(os.path.dirname(DEFAULT_CALC_METHOD_PATH), exist_ok=True)
    write_json_atomic(DEFAULT_CALC_METHOD_PATH, {"characters": cleaned})
    _cache["map"] = cleaned
    _cache["mtime"] = os.path.getmtime(DEFAULT_CALC_METHOD_PATH)
    return cleaned


def get_default_calc_method(char_id) -> str:
    """キャラのデフォルト計算方式を返す。未設定は None。"""
    if char_id is None:
        return None
    return load_default_calc_method_map().get(_base_char_id(char_id))


def resolve_calc_method(calc_method, char_id) -> str:
    """calc_method を解決する。

    有効な方式が明示されていればそれを、空/不正ならキャラ毎デフォルト、
    それも無ければ DEFAULT_CALC_METHOD("crit") を返す。
    """
    cm = str(calc_method or "").strip()
    if cm in VALID_CALC_METHODS:
        return cm
    d = get_default_calc_method(char_id)
    return d or DEFAULT_CALC_METHOD
