# -*- coding: utf-8 -*-
"""聖遺物 2セット効果バフの選択・判定・反映。

- 反映可能なバフ種(BUFF_TYPES) は admin で選択する。
- detect_2set_buff(): nanoka の 2セット効果 desc.ja から自動判定（候補提示用）。
- 選択は static/data/setting/artifact_2set_buffs.json に保存（live/beta 共通設定）。
"""
import os
import re
import json
from collections import Counter

from app.paths import BASE_DIR, STATIC_DIR
from app.core.jsonio import write_json_atomic
from app.card.stats import apply_stat_bonus, to_ratio_if_percent

# 2set バフ保存ファイル（live/beta 共通の admin 設定）
SET_BUFFS_PATH = os.path.join(STATIC_DIR, "data", "setting", "artifact_2set_buffs.json")

# ---------------------------------------------------------------------------
# 反映可能なバフ種
#   type → { prop: FIGHT_PROP_*, label: 表示ラベル, jatype: 日本語種別(admin用) }
#   値は「編集・保存の単位」で、percent 系は %（ex: 18）, flat 系はそのまま数値。
# ---------------------------------------------------------------------------
BUFF_TYPES = {
    "atk_p": {"prop": "FIGHT_PROP_ATTACK_PERCENT", "label": "攻撃力+{v}%", "jatype": "攻撃力%"},
    "hp_p": {"prop": "FIGHT_PROP_HP_PERCENT", "label": "HP+{v}%", "jatype": "HP%"},
    "def_p": {"prop": "FIGHT_PROP_DEFENSE_PERCENT", "label": "防御力+{v}%", "jatype": "防御力%"},
    "hp_flat": {"prop": "FIGHT_PROP_HP", "label": "HP上限+{v}", "jatype": "HP(固定)"},
    "def_flat": {"prop": "FIGHT_PROP_DEFENSE", "label": "防御力+{v}", "jatype": "防御力(固定)"},
    "em": {"prop": "FIGHT_PROP_ELEMENT_MASTERY", "label": "元素熟知+{v}", "jatype": "元素熟知"},
    "crit_rate": {"prop": "FIGHT_PROP_CRITICAL", "label": "会心率+{v}%", "jatype": "会心率"},
    "crit_dmg": {"prop": "FIGHT_PROP_CRITICAL_HURT", "label": "会心ダメージ+{v}%", "jatype": "会心ダメージ"},
    "er": {"prop": "FIGHT_PROP_CHARGE_EFFICIENCY", "label": "元素チャージ効率+{v}%", "jatype": "チャージ効率"},
    "pyro_dmg": {"prop": "FIGHT_PROP_FIRE_ADD_HURT", "label": "炎元素ダメージ+{v}%", "jatype": "炎元素ダメージ%"},
    "hydro_dmg": {"prop": "FIGHT_PROP_WATER_ADD_HURT", "label": "水元素ダメージ+{v}%", "jatype": "水元素ダメージ%"},
    "anemo_dmg": {"prop": "FIGHT_PROP_WIND_ADD_HURT", "label": "風元素ダメージ+{v}%", "jatype": "風元素ダメージ%"},
    "electro_dmg": {"prop": "FIGHT_PROP_ELEC_ADD_HURT", "label": "雷元素ダメージ+{v}%", "jatype": "雷元素ダメージ%"},
    "dendro_dmg": {"prop": "FIGHT_PROP_GRASS_ADD_HURT", "label": "草元素ダメージ+{v}%", "jatype": "草元素ダメージ%"},
    "cryo_dmg": {"prop": "FIGHT_PROP_ICE_ADD_HURT", "label": "氷元素ダメージ+{v}%", "jatype": "氷元素ダメージ%"},
    "geo_dmg": {"prop": "FIGHT_PROP_ROCK_ADD_HURT", "label": "岩元素ダメージ+{v}%", "jatype": "岩元素ダメージ%"},
    "phys_dmg": {"prop": "FIGHT_PROP_PHYSICAL_ADD_HURT", "label": "物理ダメージ+{v}%", "jatype": "物理ダメージ%"},
}

# desc.ja からの自動判定（上から順に最初の一致を採用）
_DETECT_PATTERNS = [
    (r"攻撃力\+(\d+(?:\.\d+)?)%", "atk_p"),
    (r"防御力\+(\d+(?:\.\d+)?)%", "def_p"),
    (r"防御力\+(\d+(?:\.\d+)?)", "def_flat"),
    (r"HP上限\+(\d+(?:\.\d+)?)", "hp_flat"),
    (r"HP\+(\d+(?:\.\d+)?)%", "hp_p"),
    (r"会心率\+(\d+(?:\.\d+)?)%", "crit_rate"),
    (r"会心ダメージ\+(\d+(?:\.\d+)?)%", "crit_dmg"),
    (r"元素熟知\+(\d+(?:\.\d+)?)", "em"),
    (r"元素チャージ効率\+(\d+(?:\.\d+)?)%", "er"),
    (r"炎元素ダメージ\+(\d+(?:\.\d+)?)%", "pyro_dmg"),
    (r"水元素ダメージ\+(\d+(?:\.\d+)?)%", "hydro_dmg"),
    (r"風元素ダメージ\+(\d+(?:\.\d+)?)%", "anemo_dmg"),
    (r"雷元素ダメージ\+(\d+(?:\.\d+)?)%", "electro_dmg"),
    (r"草元素ダメージ\+(\d+(?:\.\d+)?)%", "dendro_dmg"),
    (r"氷元素ダメージ\+(\d+(?:\.\d+)?)%", "cryo_dmg"),
    (r"岩元素ダメージ\+(\d+(?:\.\d+)?)%", "geo_dmg"),
    (r"物理ダメージ\+(\d+(?:\.\d+)?)%", "phys_dmg"),
]


def detect_2set_buff(desc_ja: str):
    """2セット効果 desc.ja から反映可能なバフを自動判定する。無ければ None。"""
    if not desc_ja:
        return None
    for pattern, type_key in _DETECT_PATTERNS:
        m = re.search(pattern, desc_ja)
        if m:
            try:
                value = int(float(m.group(1)))
            except (TypeError, ValueError):
                value = 0
            if value <= 0:
                return None
            return {"type": type_key, "value": value}
    return None


def buff_label(type_key: str, value) -> str:
    info = BUFF_TYPES.get(type_key)
    if not info:
        return ""
    try:
        return info["label"].format(v=int(value))
    except (TypeError, ValueError):
        return ""


def load_2set_buff_map() -> dict:
    """保存済みの 2set バフ選択マップ {set_id: {type, value}} を返す。"""
    if not os.path.exists(SET_BUFFS_PATH):
        return {}
    try:
        with open(SET_BUFFS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        result = {}
        for sid, entry in data.items():
            if isinstance(entry, dict) and entry.get("type") in BUFF_TYPES:
                result[str(sid)] = {"type": entry["type"], "value": int(entry.get("value", 0))}
        return result
    except Exception:
        return {}


def save_2set_buff_map(buff_map: dict) -> None:
    """選択マップを保存する。入力は {set_id: {type, value} | null}。"""
    cleaned = {}
    for sid, entry in buff_map.items():
        sid = str(sid).strip()
        if not sid:
            continue
        if not isinstance(entry, dict) or not entry.get("type"):
            continue
        type_key = str(entry.get("type", ""))
        if type_key not in BUFF_TYPES:
            continue
        try:
            value = int(float(entry.get("value", 0)))
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            cleaned[sid] = {"type": type_key, "value": value}
    os.makedirs(os.path.dirname(SET_BUFFS_PATH), exist_ok=True)
    write_json_atomic(SET_BUFFS_PATH, cleaned)
    return cleaned


def apply_2set_buffs(stat_totals, set_ids, beta="false") -> None:
    """アクティブなセットID一覧のうち、選択済み 2set バフを stat_totals へ反映する。"""
    if not set_ids:
        return
    counts = Counter(set_ids)
    active_ids = {sid for sid, cnt in counts.items() if cnt >= 2 and str(sid) not in ("0", "")}
    if not active_ids:
        return
    buff_map = load_2set_buff_map()
    for sid in active_ids:
        entry = buff_map.get(str(sid))
        if not entry:
            continue
        info = BUFF_TYPES.get(entry["type"])
        if not info:
            continue
        apply_stat_bonus(stat_totals, info["prop"], to_ratio_if_percent(info["prop"], entry["value"]))


def set_buff_label(set_id: str) -> str:
    """セットIDに対応する選択済み 2set バフの表示ラベル。無ければ ""。"""
    entry = load_2set_buff_map().get(str(set_id))
    if not entry:
        return ""
    return buff_label(entry.get("type", ""), entry.get("value", 0))