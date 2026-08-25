# -*- coding: utf-8 -*-
"""SCORECARD テーマ用スプラッシュオフセット設定（admin 管理）。

保存先: static/data/setting/scorecard_splash_offsets.json
形式: {"offsets": {"<charId>": {"x": -100〜100, "y": -100〜100}}}

チームカード用オフセット（team_image._load_splash_offsets）と同じ規約:
幅/高さに対する割合で、0% = 中央、-100% = 左端/上端、100% = 右端/下端。
フロントエンド（build_card.html）が background-position の % へ変換する。
未設定のキャラはテーマ CSS の既定位置（center 18%）のまま。
"""
import json
import os

from app.paths import STATIC_DIR

SCORECARD_SPLASH_OFFSETS_PATH = os.path.join(STATIC_DIR, "data", "setting", "scorecard_splash_offsets.json")
_scorecard_offsets_cache = {"map": {}, "mtime": None}


def load_scorecard_splash_offsets() -> dict:
    """mtime キャッシュ付きでオフセット一覧を読み込む（ファイル未作成時は {}）。"""
    if not os.path.exists(SCORECARD_SPLASH_OFFSETS_PATH):
        _scorecard_offsets_cache["map"] = {}
        _scorecard_offsets_cache["mtime"] = None
        return {}
    try:
        mtime = os.path.getmtime(SCORECARD_SPLASH_OFFSETS_PATH)
        if _scorecard_offsets_cache["mtime"] == mtime:
            return _scorecard_offsets_cache["map"]
        with open(SCORECARD_SPLASH_OFFSETS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        offsets = data.get("offsets") if isinstance(data, dict) else {}
        _scorecard_offsets_cache["map"] = offsets if isinstance(offsets, dict) else {}
        _scorecard_offsets_cache["mtime"] = mtime
        return _scorecard_offsets_cache["map"] or {}
    except Exception:
        return _scorecard_offsets_cache["map"] or {}


def get_scorecard_splash_offset(char_id):
    """キャラ別オフセット {"x", "y"} を返す。未設定/不正値なら None。"""
    raw = (load_scorecard_splash_offsets().get(str(char_id)) or {})
    if not isinstance(raw, dict):
        return None
    try:
        return {"x": float(raw.get("x", 0)), "y": float(raw.get("y", 0))}
    except (TypeError, ValueError):
        return None
