from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from moonlit.config import TEXT_MAP_PATH

_text_map_cache: Optional[Dict[str, Any]] = None


def load_text_map() -> Dict[str, Any]:
    global _text_map_cache
    if _text_map_cache is not None:
        return _text_map_cache
    try:
        with open(TEXT_MAP_PATH, "r", encoding="utf-8") as f:
            _text_map_cache = json.load(f)
    except Exception as e:
        print(f"text_map.json の読み込みに失敗しました: {e}")
        _text_map_cache = {}
    return _text_map_cache


def formal_round(val):
    return int(val + 0.5) if val >= 0 else int(val - 0.5)


def new_stat_totals():
    return {
        "hp_flat": 0.0, "hp_percent": 0.0,
        "atk_flat": 0.0, "atk_percent": 0.0,
        "def_flat": 0.0, "def_percent": 0.0,
        "em": 0.0, "crit_rate": 0.0, "crit_dmg": 0.0,
        "energy_recharge": 0.0,
        "dmg_bonus_by_element": {},
    }


def to_ratio_if_percent(prop_id, value):
    if value is None:
        return 0.0
    key_upper = str(prop_id).upper()
    if "PERCENT" in key_upper or "CRITICAL" in key_upper or "CHARGE" in key_upper or "HURT" in key_upper:
        return value / 100.0
    return value


def apply_stat_bonus(totals, prop_id, value):
    if not prop_id or value in (None, ""):
        return
    key = str(prop_id).upper()

    if key == "FIGHT_PROP_HP":
        totals["hp_flat"] += value
    elif key == "FIGHT_PROP_HP_PERCENT":
        totals["hp_percent"] += value
    elif key in ("FIGHT_PROP_ATTACK", "FIGHT_PROP_BASE_ATTACK"):
        totals["atk_flat"] += value
    elif key == "FIGHT_PROP_ATTACK_PERCENT":
        totals["atk_percent"] += value
    elif key == "FIGHT_PROP_DEFENSE":
        totals["def_flat"] += value
    elif key == "FIGHT_PROP_DEFENSE_PERCENT":
        totals["def_percent"] += value
    elif key == "FIGHT_PROP_ELEMENT_MASTERY":
        totals["em"] += value
    elif key == "FIGHT_PROP_CRITICAL":
        totals["crit_rate"] += value
    elif key == "FIGHT_PROP_CRITICAL_HURT":
        totals["crit_dmg"] += value
    elif key == "FIGHT_PROP_CHARGE_EFFICIENCY":
        totals["energy_recharge"] += value
    elif key.endswith("_DMG") or key.endswith("_ADD_HURT") or "DMG_BONUS" in key:
        elem = None
        if "PYRO" in key or "FIRE" in key:
            elem = "Pyro"
        elif "HYDRO" in key or "WATER" in key:
            elem = "Hydro"
        elif "ANEMO" in key or "WIND" in key:
            elem = "Anemo"
        elif "ELECTRO" in key or "ELEC" in key:
            elem = "Electro"
        elif "DENDRO" in key or "GRASS" in key:
            elem = "Dendro"
        elif "CRYO" in key or "ICE" in key:
            elem = "Cryo"
        elif "GEO" in key or "ROCK" in key:
            elem = "Geo"
        elif "PHYSICAL" in key:
            elem = "物理"
        if elem:
            totals["dmg_bonus_by_element"][elem] = totals["dmg_bonus_by_element"].get(elem, 0.0) + value


def get_stat_japanese(append_prop_id: str, text_map_data: Optional[Dict] = None) -> str:
    fallback_map = {
        "FIGHT_PROP_BASE_ATTACK": "基礎攻撃力",
        "FIGHT_PROP_CRITICAL": "会心率",
        "FIGHT_PROP_CRITICAL_HURT": "会心ダメージ",
        "FIGHT_PROP_CHARGE_EFFICIENCY": "チャージ効率",
        "FIGHT_PROP_ATTACK_PERCENT": "攻撃力%",
        "FIGHT_PROP_HP_PERCENT": "HP%",
        "FIGHT_PROP_DEFENSE_PERCENT": "防御力%",
        "FIGHT_PROP_ELEMENT_MASTERY": "熟知"
    }

    if append_prop_id in fallback_map:
        return fallback_map[append_prop_id]

    if text_map_data is None:
        text_map_data = load_text_map()

    if append_prop_id in text_map_data:
        return text_map_data[append_prop_id]

    if "ja" in text_map_data and append_prop_id in text_map_data["ja"]:
        return text_map_data["ja"][append_prop_id]

    return append_prop_id
