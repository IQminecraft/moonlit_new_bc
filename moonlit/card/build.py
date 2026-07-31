from __future__ import annotations

import json
import os
from collections import Counter
from typing import Any, Dict, Optional

from moonlit.config import BASE_DIR, CACHE_DIR
from moonlit.path import resolve_datas_path, resolve_list_path
from moonlit.stats.score import (
    score_calc,
    artifact_tier,
    total_tier,
    CALC_METHOD_PROP_ID,
    CALC_METHOD_LABEL,
)
from moonlit.stats.props import (
    formal_round,
    new_stat_totals,
    to_ratio_if_percent,
    apply_stat_bonus,
    get_stat_japanese,
    load_text_map,
)
from moonlit.stats.levels import get_char_level, resolve_display_skill_levels
from moonlit.showcase.normalize import normalize_avatar_id, match_avatar_id

ELEMENT_JA_MAP = {
    "Pyro": "炎", "Hydro": "水", "Anemo": "風", "Electro": "雷",
    "Dendro": "草", "Cryo": "氷", "Geo": "岩", "None": "無"
}
SLOT_TO_INDEX = {"4": 0, "2": 1, "5": 2, "1": 3, "3": 4}
SLOT_NAMES = ["花", "羽", "時計", "杯", "冠"]

_text_map = None


def _get_text_map():
    global _text_map
    if _text_map is None:
        _text_map = load_text_map()
    return _text_map


def _load_json(path: str) -> Optional[Dict]:
    """Load JSON from path, try utf-8 then cp932."""
    if not os.path.exists(path):
        return None
    for enc in ("utf-8", "cp932"):
        try:
            with open(path, "r", encoding=enc) as f:
                return json.load(f)
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None

def _build_weapon_stats_list(weapon_jsondata: dict, beta: str) -> list:
    """Build weapon_stats_list from fake_weapon JSON data."""
    keys_list = list(weapon_jsondata["stats_modifier"].keys())
    second_key = keys_list[1] if len(keys_list) > 1 else None
    stat_calc = weapon_jsondata["stats_modifier"].get(second_key, 0)
    if stat_calc < 1:
        stat_calc = round(stat_calc * 100, 1)
    else:
        stat_calc = round(stat_calc)
    return [
        {"appendPropId": "FIGHT_PROP_BASE_ATTACK", "statValue": weapon_jsondata["stats_modifier"]["atk"]},
        {"appendPropId": str(second_key).upper() if second_key else "", "statValue": stat_calc},
    ]


def _build_weapon_from_real(weapon_data: dict, beta: str) -> tuple:
    """Build weapon info from real showcase weapon data."""
    weapon_id = weapon_data["itemId"]
    weapon_json_path = resolve_datas_path(f"static/data/weapons/{weapon_id}.json", beta)
    weapon_jsondata = _load_json(weapon_json_path) or {}
    weapon_name = weapon_jsondata.get("name", "未知の武器")
    weapon_icon = resolve_datas_path(f"static/assets/weapons/{weapon_data['flat']['icon']}.webp", beta)
    weapon_level = weapon_data["weapon"]["level"]
    affix_map = weapon_data["weapon"].get("affixMap", {})
    weapon_affix = list(affix_map.values())[0] + 1 if affix_map else 1
    weapon_stats_list = weapon_data["flat"].get("weaponStats", [])
    return weapon_name, weapon_icon, weapon_level, weapon_affix, weapon_stats_list


def _build_main_stats_from_fight_prop(fight_prop: dict, element_type: str, element_ja: str) -> list:
    """Build mainStats from real fightPropMap."""
    all_buff_ids = ['30', '40', '41', '42', '43', '44', '45', '46']
    max_dmg_val = 0.0
    for b_id in all_buff_ids:
        val = fight_prop.get(b_id, 0.0)
        if val > max_dmg_val:
            max_dmg_val = val
    if max_dmg_val == 0.0:
        relic_buff_ids = ['50', '51', '52', '53', '54', '55', '56', '57']
        for r_id in relic_buff_ids:
            val = fight_prop.get(r_id, 0.0)
            if val > max_dmg_val:
                max_dmg_val = val
    dmg_buff_val = str(formal_round(max_dmg_val * 1000) / 10) + "%"

    return [
        {"label": "HP", "val": formal_round(fight_prop.get('2000', 1)), "base": formal_round(fight_prop.get('1', 1)), "icon": "static/assets/props/hp.png"},
        {"label": "攻撃力", "val": formal_round(fight_prop.get('2001', 1)), "base": formal_round(fight_prop.get('4', 1)), "icon": "static/assets/props/atk.png"},
        {"label": "防禦力", "val": formal_round(fight_prop.get('2002', 1)), "base": formal_round(fight_prop.get('7', 1)), "icon": "static/assets/props/def.png"},
        {"label": "元素熟知", "val": formal_round(fight_prop.get('28', 1)), "icon": "static/assets/props/em.png"},
        {"label": "会心率", "val": str(formal_round(fight_prop.get('20', 1) * 1000) / 10) + "%", "icon": "static/assets/props/rate.webp"},
        {"label": "会心ダメージ", "val": str(formal_round(fight_prop.get('22', 1) * 1000) / 10) + "%", "icon": "static/assets/props/dmg.webp"},
        {"label": "元素チャージ効率", "val": str(formal_round(fight_prop.get('23', 1) * 1000) / 10) + "%", "icon": "static/assets/props/er.png"},
        {"label": f"{element_ja}ダメバフ", "val": dmg_buff_val, "icon": f"static/assets/props/{element_type.lower()}.png"},
    ]


def _build_main_stats_calculated(chardatas: dict, element_type: str, element_ja: str,
                                  weapon_stats_list: list, raw_artifacts: list,
                                  fake_char: bool, fake_weapon: bool) -> list:
    """Build mainStats when fake_char or fake_weapon is active."""
    if fake_char:
        char_stats_mod = chardatas.get("stats_modifier", {}) or {}
        base_hp = chardatas.get("hp", char_stats_mod.get("hp", 1))
        base_atk = chardatas.get("atk", char_stats_mod.get("atk", 1))
        base_def = chardatas.get("def", char_stats_mod.get("def", 1))
        base_crit_rate = chardatas.get("crit_rate", 0.05)
        base_crit_dmg = chardatas.get("crit_dmg", 0.5)
        base_em = chardatas.get("elemental_mastery", 0.0)
    else:
        base_hp = 1
        base_atk = 1
        base_def = 1
        base_crit_rate = 0.05
        base_crit_dmg = 0.5
        base_em = 0.0
    base_er = 1.0

    stat_totals = new_stat_totals()
    char_stats_mod_for_bonus = chardatas.get("stats_modifier", {}) or {}

    extra_bonus = char_stats_mod_for_bonus.get("extra")
    if isinstance(extra_bonus, dict):
        for asc_key, asc_val in extra_bonus.items():
            apply_stat_bonus(stat_totals, asc_key, asc_val)
    elif isinstance(extra_bonus, list):
        for asc_entry in extra_bonus:
            for asc_key, asc_val in asc_entry.items():
                apply_stat_bonus(stat_totals, asc_key, asc_val)

    for asc_entry in char_stats_mod_for_bonus.get("ascension", []):
        for asc_key, asc_val in asc_entry.items():
            apply_stat_bonus(stat_totals, asc_key, asc_val)

    weapon_base_atk = 0.0
    for w_entry in weapon_stats_list:
        w_prop_id = w_entry.get("appendPropId", "")
        w_val = w_entry.get("statValue", 0.0)
        if w_prop_id.upper() in ("FIGHT_PROP_BASE_ATTACK", "FIGHT_PROP_ATTACK"):
            weapon_base_atk = w_val
        else:
            apply_stat_bonus(stat_totals, w_prop_id, to_ratio_if_percent(w_prop_id, w_val))

    for art_raw in raw_artifacts:
        art_flat = art_raw.get("flat", {})
        art_main = art_flat.get("reliquaryMainstat", {})
        art_main_id = art_main.get("mainPropId", "")
        apply_stat_bonus(stat_totals, art_main_id, to_ratio_if_percent(art_main_id, art_main.get("statValue", 0.0)))
        for art_sub in art_flat.get("reliquarySubstats", []):
            art_sub_id = art_sub.get("appendPropId", "")
            apply_stat_bonus(stat_totals, art_sub_id, to_ratio_if_percent(art_sub_id, art_sub.get("statValue", 0.0)))

    total_hp = base_hp * (1 + stat_totals["hp_percent"]) + stat_totals["hp_flat"]
    total_atk = (base_atk + weapon_base_atk) * (1 + stat_totals["atk_percent"]) + stat_totals["atk_flat"]
    total_def = base_def * (1 + stat_totals["def_percent"]) + stat_totals["def_flat"]
    total_em = base_em + stat_totals["em"]
    total_crit_rate = base_crit_rate + stat_totals["crit_rate"]
    total_crit_dmg = base_crit_dmg + stat_totals["crit_dmg"]
    total_er = base_er + stat_totals["energy_recharge"]

    dmg_buff_val = "0%"
    if element_type in ("Pyro", "Hydro", "Anemo", "Electro", "Dendro", "Geo", "Cryo"):
        buff_val = stat_totals["dmg_bonus_by_element"].get(element_type, 0.0)
        if buff_val > 0:
            dmg_buff_val = str(formal_round(buff_val * 1000) / 10) + "%"

    return [
        {"label": "HP", "val": formal_round(total_hp), "base": formal_round(base_hp), "icon": "static/assets/props/hp.png"},
        {"label": "攻撃力", "val": formal_round(total_atk), "base": formal_round(base_atk + weapon_base_atk), "icon": "static/assets/props/atk.png"},
        {"label": "防禦力", "val": formal_round(total_def), "base": formal_round(base_def), "icon": "static/assets/props/def.png"},
        {"label": "元素熟知", "val": formal_round(total_em), "icon": "static/assets/props/em.png"},
        {"label": "会心率", "val": str(formal_round(total_crit_rate * 1000) / 10) + "%", "icon": "static/assets/props/rate.webp"},
        {"label": "会心ダメージ", "val": str(formal_round(total_crit_dmg * 1000) / 10) + "%", "icon": "static/assets/props/dmg.webp"},
        {"label": "元素チャージ効率", "val": str(formal_round(total_er * 1000) / 10) + "%", "icon": "static/assets/props/er.png"},
        {"label": f"{element_ja}ダメバフ", "val": dmg_buff_val, "icon": f"static/assets/props/{element_type.lower()}.png"},
    ]


def _build_artifact_entry(art: dict, target_prop_id: str, calc_method: str, beta: str) -> Optional[dict]:
    flat = art.get("flat", {})
    reliquary = art.get("reliquary", {})
    if flat.get("itemType") != "ITEM_RELIQUARY":
        return None
    icon_name = flat.get("icon", "")
    if "_" not in icon_name:
        return None
    last_num = icon_name.split("_")[-1]
    if last_num not in SLOT_TO_INDEX:
        return None
    target_idx = SLOT_TO_INDEX[last_num]

    name_hash = str(flat.get("nameTextMapHash", ""))
    artifact_name = _get_text_map().get(name_hash, "未知の聖遺物")

    main_stat_raw = flat.get("reliquaryMainstat", {})
    main_prop_id = main_stat_raw.get("mainPropId", "")
    main_name = get_stat_japanese(main_prop_id, _get_text_map())
    main_val = main_stat_raw.get("statValue", 0)
    if "PERCENT" in main_prop_id or "CRITICAL" in main_prop_id or "CHARGE" in main_prop_id or "HURT" in main_prop_id:
        main_value_str = f"{main_val}%"
    else:
        main_value_str = f"{int(main_val):,}"

    crit_rate, crit_dmg, target_stat_val = 0.0, 0.0, 0.0
    substats_out = []
    for sub_data in flat.get("reliquarySubstats", []):
        sub_prop_id = sub_data.get("appendPropId", "")
        sub_name = get_stat_japanese(sub_prop_id, _get_text_map())
        sub_val = sub_data.get("statValue", 0)

        if sub_prop_id == "FIGHT_PROP_CRITICAL":
            crit_rate = sub_val
        elif sub_prop_id == "FIGHT_PROP_CRITICAL_HURT":
            crit_dmg = sub_val
        elif sub_prop_id == target_prop_id:
            target_stat_val = sub_val

        icon_file = "atk_per.png"
        if "CRITICAL" in sub_prop_id and "HURT" not in sub_prop_id:
            icon_file = "rate.webp"
        elif "HURT" in sub_prop_id:
            icon_file = "dmg.webp"
        elif "CHARGE" in sub_prop_id:
            icon_file = "er.png"
        elif "ELEMENT_MASTERY" in sub_prop_id:
            icon_file = "em.png"
        elif "HP" in sub_prop_id:
            icon_file = "hp_per.png" if "PERCENT" in sub_prop_id else "hp.png"
        elif "ATTACK" in sub_prop_id:
            icon_file = "atk_per.png" if "PERCENT" in sub_prop_id else "atk.png"
        elif "DEFENSE" in sub_prop_id:
            icon_file = "def_per.png" if "PERCENT" in sub_prop_id else "def.png"

        if "PERCENT" in sub_prop_id or "CRITICAL" in sub_prop_id or "CHARGE" in sub_prop_id or "HURT" in sub_prop_id:
            sub_value_str = f"{sub_val}%"
        else:
            sub_value_str = f"{int(sub_val)}"

        substats_out.append({
            "name": sub_name, "value": sub_value_str,
            "icon": resolve_datas_path(f"static/assets/props/{icon_file}", beta)
        })

    art_score = round(score_calc(stat=target_stat_val, critrate=crit_rate, critdmg=crit_dmg, method=calc_method), 1)
    art_t = artifact_tier(art_score, target_idx)

    return {
        "slot": SLOT_NAMES[target_idx],
        "set": str(flat.get("setId", "")),
        "name": artifact_name,
        "upgrade": reliquary.get("level", 1) - 1,
        "main": {"name": main_name, "value": main_value_str},
        "substats": substats_out,
        "score": art_score,
        "tier": art_t,
        "icon": resolve_datas_path(f"static/assets/artifacts/UI_RelicIcon_{flat.get('setId','')}_{icon_name.split('_')[-1]}.webp", beta)
    }


def build_card_model(uid: str, avatar_id: str, calc_method: str = "crit",
                     fake_char: Optional[str] = None, fake_weapon: Optional[str] = None,
                     beta: str = "false") -> dict:
 
    if beta != "true":
        beta = "false"

    json_path = os.path.join("static", "cache", f"showcase_{uid}.json")
    json_path = resolve_datas_path(json_path, beta)
    showcase_data = _load_json(json_path)
    if not showcase_data:
        raise FileNotFoundError(f"UID: {uid} のキャッシュデータが見つかりませんでした。")

    avatar_list = showcase_data.get("avatarInfoList")
    if not avatar_list and "playerInfo" in showcase_data:
        player_info = showcase_data["playerInfo"]
        avatar_list = player_info.get("showAvatarInfoList") or player_info.get("show_avatar_info_list")
    avatar_list = avatar_list or []

    target_avatar_info = None
    for avatar in avatar_list:
        if match_avatar_id(avatar, avatar_id):
            target_avatar_info = avatar
            break

    if not target_avatar_info:
        raise ValueError(f"Avatar ID {avatar_id} not found in showcase.")

    if fake_char:
        json_path2 = os.path.join("static", "data", "characters", f"{fake_char}.json")
        if not os.path.exists(json_path2) and beta == "true":
            json_path2 = os.path.join("static", "beta", "data", "characters", f"{fake_char}.json")
    else:
        json_path2 = os.path.join("static", "data", "characters", f"{avatar_id}.json")
    json_path2 = resolve_datas_path(json_path2, beta)

    chardatas = _load_json(json_path2)
    if not chardatas:
        base_avatar_id = str(avatar_id).split("-")[0]
        backup_path = resolve_datas_path(os.path.join("static", "data", "characters", f"{base_avatar_id}.json"), beta)
        chardatas = _load_json(backup_path)
        if not chardatas:
            raise FileNotFoundError(f"Character JSON file not found: {json_path2}")

    element_type = chardatas.get("element", "None")
    element_ja = ELEMENT_JA_MAP.get(element_type, "無")

    if fake_char:
        constellation = 0
    else:
        constellation = len(target_avatar_info.get("talentIdList", []))

    if fake_char:
        char_level = 90
    else:
        char_level = get_char_level(target_avatar_info)

    weapon_data = next((item for item in target_avatar_info.get("equipList", []) if "weapon" in item), None)
    weapon_name = "未知の武器"
    weapon_icon = ""
    weapon_level = None
    weapon_affix = None
    weapon_stats_list = []

    if fake_weapon:
        weapon_id = fake_weapon
        weapon_json_path = resolve_datas_path(f"static/data/weapons/{weapon_id}.json", beta)
        weapon_jsondata = _load_json(weapon_json_path)
        if not weapon_jsondata:
            raise FileNotFoundError(f"Weapon JSON file not found: {weapon_json_path}")
        weapon_level = 90
        weapon_affix = 1
        weapon_icon = resolve_datas_path(f"static/assets/weapons/{weapon_jsondata['icon']}.webp", beta)
        weapon_name = weapon_jsondata["name"]
        weapon_stats_list = _build_weapon_stats_list(weapon_jsondata, beta)
    elif weapon_data:
        weapon_name, weapon_icon, weapon_level, weapon_affix, weapon_stats_list = _build_weapon_from_real(weapon_data, beta)

    raw_artifacts = [item for item in target_avatar_info.get("equipList", []) if "reliquary" in item]

    if fake_char or fake_weapon:
        main_stats = _build_main_stats_calculated(chardatas, element_type, element_ja,
                                                    weapon_stats_list, raw_artifacts,
                                                    bool(fake_char), bool(fake_weapon))
    else:
        fight_prop = target_avatar_info.get('fightPropMap', {})
        main_stats = _build_main_stats_from_fight_prop(fight_prop, element_type, element_ja)

    for s in main_stats:
        s["icon"] = resolve_datas_path(s["icon"], beta)

    target_prop_id = CALC_METHOD_PROP_ID.get(calc_method, "")
    artifacts_out = [None, None, None, None, None]
    score_sum = 0

    for art in target_avatar_info.get("equipList", []):
        entry = _build_artifact_entry(art, target_prop_id, calc_method, beta)
        if entry is None:
            continue
        # Find slot index
        flat = art.get("flat", {})
        icon_name = flat.get("icon", "")
        if "_" not in icon_name:
            continue
        last_num = icon_name.split("_")[-1]
        if last_num not in SLOT_TO_INDEX:
            continue
        target_idx = SLOT_TO_INDEX[last_num]
        artifacts_out[target_idx] = entry
        score_sum += entry["score"]

    set_ids = [a["set"] for a in artifacts_out if a and a["set"] and a["set"] != "0"]
    set_counts = Counter(set_ids)
    active_sets = [(sid, cnt) for sid, cnt in set_counts.items() if cnt >= 2]

    def get_set_name(set_id_str):
        possible_paths = [
            "static/data/lists/artifacts.json",
            os.path.join(BASE_DIR, "static", "data", "lists", "artifacts.json"),
        ]
        for raw_path in possible_paths:
            path = resolve_list_path(raw_path, beta)
            if os.path.exists(path):
                art_json = _load_json(path)
                if art_json and set_id_str in art_json:
                    return art_json[set_id_str].get("janame", f"セット {set_id_str}")
        return f"セット {set_id_str}"

    set_bonuses = [{"name": get_set_name(sid), "count": cnt} for sid, cnt in active_sets]
    tier_sum = total_tier(score_sum)

    splash_path = ""
    try:
        icon_name = str(chardatas.get("icon", ""))
        if icon_name:
            splash_raw = f"static/assets/splash/{icon_name.replace('AvatarIcon', 'Gacha_AvatarImg')}.webp"
            splash_path = resolve_datas_path(splash_raw, beta)
    except Exception:
        splash_path = ""

    if fake_char:
        friendship_lv = 10
    else:
        friendship_lv = target_avatar_info.get("fetterInfo", {}).get("expLevel", 1)

    skill_icons = []
    skill_levels = [1, 1, 1]
    skill_boosted = [False, False, False]
    try:
        skills_meta = chardatas.get("skills") or []
        skill_levels, skill_boosted = resolve_display_skill_levels(target_avatar_info, fake_char=bool(fake_char))
        for i in range(min(3, len(skills_meta))):
            icon = skills_meta[i].get("icon", "")
            skill_icons.append(resolve_datas_path(f"static/assets/skills/{icon}.webp", beta) if icon else "")
        while len(skill_levels) < 3:
            skill_levels.append(1)
            skill_boosted.append(False)
    except Exception:
        skill_icons, skill_levels, skill_boosted = [], [1, 1, 1], [False, False, False]

    constellation_icons = []
    try:
        consts_meta = chardatas.get("constellations") or []
        for i in range(min(6, len(consts_meta))):
            icon = consts_meta[i].get("icon", "")
            constellation_icons.append(resolve_datas_path(f"static/assets/skills/{icon}.webp", beta) if icon else "")
    except Exception:
        constellation_icons = []

    weapon_stats_out = []
    for w_entry in weapon_stats_list:
        w_prop = w_entry.get("appendPropId", "")
        w_val = w_entry.get("statValue", 0)
        w_name = get_stat_japanese(w_prop, _get_text_map())
        if "PERCENT" in str(w_prop).upper() or "CRITICAL" in str(w_prop).upper() or "CHARGE" in str(w_prop).upper() or "HURT" in str(w_prop).upper():
            try:
                fv = float(w_val)
                w_val_str = f"{fv}%" if fv < 1000 else str(int(fv))
            except Exception:
                w_val_str = str(w_val)
        else:
            try:
                w_val_str = str(int(float(w_val)))
            except Exception:
                w_val_str = str(w_val)
        weapon_stats_out.append({"name": w_name, "value": w_val_str})

    display_score_way = CALC_METHOD_LABEL.get(calc_method, calc_method)

    return {
        "displayName": (chardatas.get("name", avatar_id) + "(swap)") if fake_char else chardatas.get("name", avatar_id),
        "element": element_type,
        "level": char_level,
        "friendship": friendship_lv,
        "constellation": constellation,
        "splash": splash_path,
        "skills": [{
            "icon": skill_icons[i] if i < len(skill_icons) else "",
            "level": skill_levels[i] if i < len(skill_levels) else 1,
            "boosted": bool(skill_boosted[i]) if i < len(skill_boosted) else False,
        } for i in range(3)],
        "constellationIcons": constellation_icons,
        "weaponName": weapon_name,
        "weaponIcon": weapon_icon,
        "weaponLevel": weapon_level,
        "weaponAffix": weapon_affix,
        "weaponStats": weapon_stats_out,
        "mainStats": main_stats,
        "artifacts": artifacts_out,
        "setBonuses": set_bonuses,
        "scoreSum": round(score_sum, 1),
        "tierSum": tier_sum,
        "calcMethod": calc_method,
        "calcMethodLabel": display_score_way,
    }


def build_card_image_model(uid: str, avatar_id: str, calc_method: str,
                           fake_char: Optional[str] = None, fake_weapon: Optional[str] = None,
                           beta: str = "false", bg_color: Optional[str] = None) -> dict:
    card_data = build_card_model(uid, avatar_id, calc_method, fake_char, fake_weapon, beta)

    target_json_path = f"static/data/characters/{fake_char or avatar_id}.json"
    target_json_path = resolve_datas_path(target_json_path, beta)
    chardatas = _load_json(target_json_path)
    if not chardatas:
        base_avatar_id = str(avatar_id).split("-")[0]
        backup_path = resolve_datas_path(f"static/data/characters/{base_avatar_id}.json", beta)
        chardatas = _load_json(backup_path) or {}

    element_type = card_data.get("element", "None")

    element_colors = {
        "Pyro": (0x90, 0x3B, 0x2A), "Hydro": (0x34, 0x45, 0x95),
        "Cryo": (0x57, 0x7F, 0xC7), "Dendro": (0x46, 0x6B, 0x63),
        "Geo": (0x6A, 0x67, 0x48), "Electro": (0x73, 0x4A, 0x8C),
        "Anemo": (0x12, 0x95, 0x88), "None": (0x4A, 0x55, 0x68),
    }
    element_base_rgb = element_colors.get(element_type, (0x4A, 0x55, 0x68))

    from moonlit.card.drawing import hex_to_rgb
    custom_rgb = hex_to_rgb(bg_color) if bg_color else None
    bg_base_rgb = custom_rgb if custom_rgb else element_base_rgb

    return {
        "card_data": card_data,
        "chardatas": chardatas,
        "bg_base_rgb": bg_base_rgb,
        "element_base_rgb": element_base_rgb,
        "element_type": element_type,
        "beta": beta,
        "fake_char": bool(fake_char),
        "fake_weapon": bool(fake_weapon),
    }