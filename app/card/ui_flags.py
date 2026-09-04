# -*- coding: utf-8 -*-
"""UI 表示フラグ設定（admin 管理）。

保存先: static/data/setting/ui_flags.json
形式: {"show_team_abyss_buttons": true, "show_status_view_setting": false}

show_team_abyss_buttons:
    build_card.html の「選択中」バーにある編成スロット管理ボタンと
    幽境モードボタンの表示/非表示（連動）。
    非表示時は生成/再取得ボタンがバーの右端に配置される。
    ファイル未作成時は既定値（true = 表示）。

show_status_view_setting:
    build_card.html の設定モーダルにある「ステータス表示」トグルの
    表示/非表示。非表示時はステータス表示（html ビュー）へ切り替えられず、
    保存済みの表示モードが html の場合はビルドカード（glass）に強制される。
    ファイル未作成時は既定値（false = 非表示）。

show_score_history:
    build_card.html の「スコア履歴」グラフ（閲覧スコアの折れ線グラフ）の
    表示/非表示。非表示時はスコアの記録も行わない。
    ファイル未作成時は既定値（true = 表示）。
"""
import json
import os

from app.paths import STATIC_DIR
from app.core.jsonio import write_json_atomic

UI_FLAGS_PATH = os.path.join(STATIC_DIR, "data", "setting", "ui_flags.json")

UI_FLAG_DEFAULTS = {
    "show_team_abyss_buttons": True,
    "show_status_view_setting": False,
    "show_score_history": True,
}

_ui_flags_cache = {"flags": dict(UI_FLAG_DEFAULTS), "mtime": None}


def load_ui_flags() -> dict:
    """mtime キャッシュ付きでフラグ一覧を読み込む（ファイル未作成時は既定値）。"""
    if not os.path.exists(UI_FLAGS_PATH):
        _ui_flags_cache["flags"] = dict(UI_FLAG_DEFAULTS)
        _ui_flags_cache["mtime"] = None
        return dict(UI_FLAG_DEFAULTS)
    try:
        mtime = os.path.getmtime(UI_FLAGS_PATH)
        if _ui_flags_cache["mtime"] == mtime:
            return dict(_ui_flags_cache["flags"])
        with open(UI_FLAGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        flags = dict(UI_FLAG_DEFAULTS)
        if isinstance(data, dict):
            for key in UI_FLAG_DEFAULTS:
                if isinstance(data.get(key), bool):
                    flags[key] = data[key]
        _ui_flags_cache["flags"] = flags
        _ui_flags_cache["mtime"] = mtime
        return dict(flags)
    except Exception:
        return dict(_ui_flags_cache["flags"])


def save_ui_flags(flags: dict) -> dict:
    """フラグを正規化して保存し、保存後の内容を返す。"""
    cleaned = {}
    for key, default in UI_FLAG_DEFAULTS.items():
        val = flags.get(key, default)
        cleaned[key] = bool(val) if isinstance(val, (bool, int)) else default
    os.makedirs(os.path.dirname(UI_FLAGS_PATH), exist_ok=True)
    write_json_atomic(UI_FLAGS_PATH, cleaned)
    _ui_flags_cache["flags"] = dict(cleaned)
    try:
        _ui_flags_cache["mtime"] = os.path.getmtime(UI_FLAGS_PATH)
    except OSError:
        _ui_flags_cache["mtime"] = None
    return cleaned
