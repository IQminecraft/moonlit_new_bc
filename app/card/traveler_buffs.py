# -*- coding: utf-8 -*-
"""旅人(10000005/10000007)専用の追加バフ。"""

from app.card.stats import apply_stat_bonus, to_ratio_if_percent

TRAVELER_BUFF_IDS = {"10000005", "10000007"}

CANNED_ATK = 3.0
SKIRK_HP = 50.0
SKIRK_ATK = 7.0
SKIRK_EM = 15.0

# 上から時計回り: 風, 岩, 雷, 草, 水, 炎, 氷
HEX_NODES = (
    {"key": "anemo", "elem": "Anemo", "label": "会心率+10%", "prop": "FIGHT_PROP_CRITICAL", "value": 10},
    {"key": "geo", "elem": "Geo", "label": "防御力+20%", "prop": "FIGHT_PROP_DEFENSE_PERCENT", "value": 20},
    {"key": "electro", "elem": "Electro", "label": "元素チャージ効率+20%", "prop": "FIGHT_PROP_CHARGE_EFFICIENCY", "value": 20},
    {"key": "dendro", "elem": "Dendro", "label": "元素熟知+60", "prop": "FIGHT_PROP_ELEMENT_MASTERY", "value": 60},
    {"key": "hydro", "elem": "Hydro", "label": "HP+20%", "prop": "FIGHT_PROP_HP_PERCENT", "value": 20},
    {"key": "pyro", "elem": "Pyro", "label": "攻撃力+20%", "prop": "FIGHT_PROP_ATTACK_PERCENT", "value": 20},
    {"key": "cryo", "elem": "Cryo", "label": "会心ダメ+20%", "prop": "FIGHT_PROP_CRITICAL_HURT", "value": 20},
)
HEX_KEYS = {n["key"] for n in HEX_NODES}


def traveler_base_id(char_id) -> str:
    return str(char_id or "").split("-")[0]


def is_traveler_id(char_id) -> bool:
    return traveler_base_id(char_id) in TRAVELER_BUFF_IDS


def displayed_char_id(avatar_id, fake_char=None) -> str:
    """カード上の表示キャラ。差し替え中は差し替え先。"""
    if fake_char:
        return str(fake_char)
    return str(avatar_id or "")


def traveler_buffs_for(avatar_id, fake_char=None, traveler_buffs=None):
    """表示中が旅人のときだけバフを通す。差し替え先が他人なら無視。"""
    if not is_traveler_id(displayed_char_id(avatar_id, fake_char)):
        return None
    return traveler_buffs


def parse_traveler_buffs(raw):
    """クエリ traveler_buffs=canned,skirk,anemo,... → 正規化セット。

    未指定はデフォルト全部ON。空文字は全部OFF。
    """
    if raw is None:
        return set(HEX_KEYS)
    if isinstance(raw, (list, tuple)):
        parts = [str(p) for p in raw]
    else:
        parts = str(raw).split(",")
    keys = set()
    for part in parts:
        k = str(part).strip().lower()
        if k in ("canned", "skirk") or k in HEX_KEYS:
            keys.add(k)
    return keys


def apply_traveler_base_bonus(base_hp, base_atk, base_em, char_id, traveler_buffs):
    if not is_traveler_id(char_id):
        return float(base_hp or 0), float(base_atk or 0), float(base_em or 0)
    keys = parse_traveler_buffs(traveler_buffs)
    hp = float(base_hp or 0)
    atk = float(base_atk or 0)
    em = float(base_em or 0)
    if "canned" in keys:
        atk += CANNED_ATK
    if "skirk" in keys:
        hp += SKIRK_HP
        atk += SKIRK_ATK
        em += SKIRK_EM
    return hp, atk, em


def apply_traveler_hex_buffs(stat_totals, char_id, traveler_buffs) -> None:
    if not is_traveler_id(char_id):
        return
    keys = parse_traveler_buffs(traveler_buffs)
    for node in HEX_NODES:
        if node["key"] not in keys:
            continue
        apply_stat_bonus(stat_totals, node["prop"], to_ratio_if_percent(node["prop"], node["value"]))
