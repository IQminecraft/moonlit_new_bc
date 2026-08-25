# -*- coding: utf-8 -*-
"""キャラ最終ステータスの手動計算を一元化するヘルパー。

差し替え（fake）時・実キャラ（enka fightPropMap）時のどちらも、
同一の手動計算でステータスを導出する。サブオプ値は
ReliquaryAffixExcelConfigData の合算値を使い、最後にフォーマット側で
「小数点3位四捨五入 → 小数2位表示」する構成は共通。
"""
import json
import os
from collections import Counter

from app.paths import STATIC_DIR
from app.card.stats import (
    new_stat_totals, apply_stat_bonus, to_ratio_if_percent,
    sum_affix_substat_values,
)
from app.card.set_buffs import apply_2set_buffs


# ----------------------------------------------------------------
# 固有元素熟知（admin 管理）
# レベル・突破段階に依存せず、特定のキャラに常に付与される元素熟知。
# 保存先: static/data/setting/innate_em.json
# 形式: {"characters": {"<charId>": <number>}}
# ----------------------------------------------------------------
INNATE_EM_PATH = os.path.join(STATIC_DIR, "data", "setting", "innate_em.json")
_innate_em_cache = {"map": {}, "mtime": None}


def load_innate_em_map() -> dict:
    """mtime キャッシュ付きで固有元素熟知マップ {charId: float} を読み込む。"""
    if not os.path.exists(INNATE_EM_PATH):
        _innate_em_cache["map"] = {}
        _innate_em_cache["mtime"] = None
        return {}
    try:
        mtime = os.path.getmtime(INNATE_EM_PATH)
        if _innate_em_cache["mtime"] == mtime:
            return _innate_em_cache["map"]
        with open(INNATE_EM_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            chars = data.get("characters")
            raw_map = chars if isinstance(chars, dict) else data
        else:
            raw_map = {}
        cleaned = {}
        for key, val in raw_map.items():
            try:
                v = float(val)
            except (TypeError, ValueError):
                continue
            if v > 0:
                cleaned[str(key)] = v
        _innate_em_cache["map"] = cleaned
        _innate_em_cache["mtime"] = mtime
        return _innate_em_cache["map"]
    except Exception:
        return _innate_em_cache["map"] or {}


def get_innate_em(char_id) -> float:
    """キャラの固有元素熟知を返す。未設定は 0.0。"""
    if char_id is None:
        return 0.0
    try:
        return float(load_innate_em_map().get(str(char_id), 0.0))
    except (TypeError, ValueError):
        return 0.0


def get_char_base_stats(chardatas, level):
    """キャラクター JSON の base_stats からレベル別の基礎ステータスを返す。

    保存されているのは Lv1～100 の HP/ATK/DEF。レベルに該当するエントリが
    無い場合は Lv90 の値（stats_modifier）にフォールバックする。
    Returns:
        {"hp": float, "atk": float, "def": float} | None
    """
    if not isinstance(chardatas, dict):
        return None
    base_stats = chardatas.get("base_stats")
    if isinstance(base_stats, dict):
        entry = base_stats.get(str(level))
        if isinstance(entry, dict) and (entry.get("hp") is not None
                                        or entry.get("atk") is not None
                                        or entry.get("def") is not None):
            return {
                "hp": float(entry.get("hp") or 0),
                "atk": float(entry.get("atk") or 0),
                "def": float(entry.get("def") or 0),
            }
    return None


def compute_manual_totals(*, base_hp, base_atk, base_def, base_crit_rate, base_crit_dmg,
                          base_em, weapon_stats_list, raw_artifacts, chardatas,
                          element_type, beta="false", weapon_base_included_in_base_atk=False):
    """手動計算で最終ステータスを導出する。

    base_atk の解釈:
      - weapon_base_included_in_base_atk=True  … 実キャラ(fightPropMap['4'])等、
        base_atk に武器基礎攻撃力が既に含まれる場合。
      - False … 差し替え(characters/xxx.json の atk)等、base_atk が素の
        キャラ基礎攻撃力のみの場合。武器の BASE_ATTACK を別途加算する。

    Returns:
        {
          "hp":  {"val": .., "base": ..},
          "atk":  {"val": .., "base": ..},
          "def": {"val": .., "base": ..},
          "em": {..}, "crit_rate": {..}, "crit_dmg": {..},
          "er": {..}, "dmg_buff": {"val": 0.0(率), "base": 0.0},
        }
        ※ dmg_buff.val は率（0.0～）なので表示時 ×100 で "%"。
    """
    stat_totals = new_stat_totals()

    # 固有元素熟知（admin 管理）: レベル・突破に依存しない基礎熟知として
    # base_em に合算し、最終ステータス（total_em）へ反映する。
    innate_em = get_innate_em(chardatas.get("id") if isinstance(chardatas, dict) else None)
    if innate_em:
        base_em = base_em + innate_em

    char_stats_mod = chardatas.get("stats_modifier", {}) or {} if isinstance(chardatas, dict) else {}
    extra_bonus = char_stats_mod.get("extra")
    if isinstance(extra_bonus, dict):
        for asc_key, asc_val in extra_bonus.items():
            apply_stat_bonus(stat_totals, asc_key, asc_val)
    elif isinstance(extra_bonus, list):
        for asc_entry in extra_bonus:
            for asc_key, asc_val in asc_entry.items():
                apply_stat_bonus(stat_totals, asc_key, asc_val)

    for asc_entry in char_stats_mod.get("ascension", []):
        for asc_key, asc_val in asc_entry.items():
            apply_stat_bonus(stat_totals, asc_key, asc_val)

    weapon_base_atk = 0.0
    for w_entry in weapon_stats_list or []:
        w_prop_id = w_entry.get("appendPropId", "")
        w_val = w_entry.get("statValue", 0.0)
        if w_prop_id.upper() in ("FIGHT_PROP_BASE_ATTACK", "FIGHT_PROP_ATTACK"):
            if not weapon_base_included_in_base_atk:
                weapon_base_atk = w_val
            continue
        apply_stat_bonus(stat_totals, w_prop_id, to_ratio_if_percent(w_prop_id, w_val))

    for art_raw in raw_artifacts or []:
        art_flat = art_raw.get("flat", {})
        art_main = art_flat.get("reliquaryMainstat", {})
        art_main_id = art_main.get("mainPropId", "")
        apply_stat_bonus(stat_totals, art_main_id, to_ratio_if_percent(art_main_id, art_main.get("statValue", 0.0)))
        art_sub_sums = sum_affix_substat_values(art_raw.get("reliquary", {}).get("appendPropIdList"))
        for art_sub in art_flat.get("reliquarySubstats", []):
            art_sub_id = art_sub.get("appendPropId", "")
            art_sub_val = art_sub_sums.get(art_sub_id, art_sub.get("statValue", 0.0))
            apply_stat_bonus(stat_totals, art_sub_id, to_ratio_if_percent(art_sub_id, art_sub_val))

    set_ids = [a.get("flat", {}).get("setId", "") for a in raw_artifacts or []]
    apply_2set_buffs(stat_totals, set_ids, beta)

    base_atk_eff = base_atk + weapon_base_atk
    total_hp = base_hp * (1 + stat_totals["hp_percent"]) + stat_totals["hp_flat"]
    total_atk = base_atk_eff * (1 + stat_totals["atk_percent"]) + stat_totals["atk_flat"]
    total_def = base_def * (1 + stat_totals["def_percent"]) + stat_totals["def_flat"]
    total_em = base_em + stat_totals["em"]
    total_crit_rate = base_crit_rate + stat_totals["crit_rate"]
    total_crit_dmg = base_crit_dmg + stat_totals["crit_dmg"]
    total_er = 1.0 + stat_totals["energy_recharge"]

    buff_val = stat_totals["dmg_bonus_by_element"].get(element_type, 0.0)

    return {
        "hp": {"val": total_hp, "base": base_hp},
        "atk": {"val": total_atk, "base": base_atk_eff},
        "def": {"val": total_def, "base": base_def},
        "em": {"val": total_em, "base": base_em},
        "crit_rate": {"val": total_crit_rate, "base": base_crit_rate},
        "crit_dmg": {"val": total_crit_dmg, "base": base_crit_dmg},
        "er": {"val": total_er, "base": 1.0},
        "dmg_buff": {"val": buff_val, "base": 0.0},
    }