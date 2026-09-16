import os
import json
from collections import Counter
from fastapi import HTTPException
from app.paths import BASE_DIR, STATIC_DIR
from app.card.stats import (
    get_stat_label, get_text_map_name, get_char_level,
    score_calc,
    sum_affix_substat_values, is_percent_prop, format_base_value, format_decimal_value,
    artifact_substat_rolls,
)
from app.card.special import (
    SPECIAL_ELEMENT_CHARACTERS, build_special_energy_hint_map,
    resolve_special_avatar_id, resolve_datas_path, resolve_list_path,
    resolve_display_skill_levels, resolve_costume_icon, resolve_costume_splash, _special_raw_id,
    _NO_CONSTELLATION_CHARS, _NO_FRIENDSHIP_CHARS,
)
from app.card.region import build_region_info, find_regions_for_character
from app.card.set_buffs import set_buff_label
from app.card.resonance import resonance_badges, parse_resonance_param
from app.card.stat_calc import compute_manual_totals
from app.card.calc_method import resolve_calc_method, get_default_calc_method
from app.card.scorecard_splash import get_scorecard_splash_offset
from app.card.growth import build_growth_panel, build_growth_from_fake
from app.card.jsoncache import load_json_cached


def _load_json_auto(path: str) -> dict:
    """UTF-8 → cp932 の順で JSON を読み込む（mtime 無効化のメモリキャッシュ付き）。

    注意: 戻り値はキャッシュされた共有オブジェクト。呼び出し側は変更しないこと
    （生成系の呼び出しはすべて読み取り専用のため、そのまま共有して安全）。
    """
    return load_json_cached(path)


def _build_char_list_from_showcase(showcase_data: dict, beta: str) -> list:
    """showcase JSON からサムネイル用キャラ一覧を構築する（fetch_uid / refresh_uid / char_list API 共通）。

    avatarInfoList（武器・聖遺物などの詳細データ）に存在するキャラのみを一覧に出す。
    avatarInfoList が無い（ゲーム内で「キャラクター詳細を公開」がオフ等）場合は
    空リストを返し、呼び出し側のエラー表示に委ねる。
    """
    player_info = showcase_data.get("playerInfo", {})
    show_avatar_list = player_info.get("showAvatarInfoList", [])

    avatar_info_list = showcase_data.get("avatarInfoList") or []
    if not avatar_info_list:
        # 詳細データ無し（詳細非公開 or ショーケース空）→ カード生成できるキャラが居ない
        return []
    detailed_raw_ids = {str(a.get("avatarId")) for a in avatar_info_list if a.get("avatarId") is not None}

    char_list = []
    for avatar in show_avatar_list:
        raw_id = str(avatar.get("avatarId"))
        if not raw_id:
            continue

        # 詳細データ（avatarInfoList）に無いキャラはカード生成できないためスキップ
        if raw_id not in detailed_raw_ids:
            continue

        current_avatar_id = raw_id
        if current_avatar_id in SPECIAL_ELEMENT_CHARACTERS:
            current_avatar_id = resolve_special_avatar_id(avatar, beta)

        json_path_char = resolve_datas_path(f"static/data/characters/{current_avatar_id}.json", beta)

        if os.path.exists(json_path_char):
            jsondata = _load_json_auto(json_path_char)
            icon_suffix = str(jsondata["icon"])
            icon_path = resolve_datas_path(f"static/assets/characters/{icon_suffix}.webp", beta)
            # コスチューム装備中（costumeId あり）はコスチュームアイコンをサムネイルに使用
            costume_icon = resolve_costume_icon(jsondata, avatar)
            if costume_icon:
                costume_path = resolve_datas_path(f"static/assets/characters/{costume_icon}.webp", beta)
                if os.path.exists(costume_path):
                    icon_path = costume_path
            char_entry = {
                "id": current_avatar_id,
                "name": jsondata.get("name", ""),
                "name_en": jsondata.get("en_name", ""),
                "element": jsondata.get("element", ""),
                "icon": icon_path,
                "active": (len(char_list) == 0)
            }
            char_list.append(char_entry)
        else:
            print(f"[Warning] キャラクターJSONが見つからないためスキップ: {json_path_char}")
            continue

    return char_list


def _get_card_data_sync(uid: str, avatar_id: str, calc_method: str = "crit", fake_char: str = None, fake_weapon: str = None, beta: str = "false", growth: str = "false", base_prec: str = "0", resonance: str = None, lang: str = "ja"):
    if beta != "true":
        beta = "false"
    # 表示言語（"ja" / "en"）。カード画像生成は常に ja のまま。
    lang = "en" if str(lang or "").lower() == "en" else "ja"
    growth = "true" if str(growth or "") == "true" else "false"
    base_prec = str(base_prec or "0")
    if base_prec not in ("0", "2", "4"):
        base_prec = "0"

    # 計算方式が未指定/不正ならキャラ毎デフォルト（admin 設定）→ "crit" に解決する。
    calc_method = resolve_calc_method(calc_method, avatar_id)

    json_path = os.path.join(STATIC_DIR, "cache", f"showcase_{uid}.json")
    json_path = resolve_datas_path(json_path, beta)
    if not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail=f"UID: {uid} のキャッシュデータが見つかりませんでした。")

    showcase_data = _load_json_auto(json_path)

    avatar_list = showcase_data.get("avatarInfoList")
    if not avatar_list and "playerInfo" in showcase_data:
        player_info = showcase_data["playerInfo"]
        avatar_list = player_info.get("showAvatarInfoList") or player_info.get("show_avatar_info_list")
    avatar_list = avatar_list or []
    energy_hint_map = build_special_energy_hint_map(showcase_data)

    target_avatar_info = None
    for avatar in avatar_list:
        raw_id = str(avatar.get("avatarId"))
        loop_avatar_id = raw_id
        if raw_id in SPECIAL_ELEMENT_CHARACTERS:
            loop_avatar_id = resolve_special_avatar_id(avatar, beta, energy_hint_map.get(raw_id))
        if str(loop_avatar_id) == str(avatar_id):
            target_avatar_info = avatar
            break

    if not target_avatar_info:
        raise HTTPException(status_code=404, detail=f"Avatar ID {avatar_id} not found in showcase.")

    if fake_char:
        json_path2 = os.path.join(STATIC_DIR, "data", "characters", f"{fake_char}.json")
        if not os.path.exists(json_path2):
            if beta == "true":
                json_path2 = os.path.join(STATIC_DIR, "beta", "data", "characters", f"{fake_char}.json")
    else:
        json_path2 = os.path.join(STATIC_DIR, "data", "characters", f"{avatar_id}.json")
    json_path2 = resolve_datas_path(json_path2, beta)

    if os.path.exists(json_path2):
        chardatas = _load_json_auto(json_path2)
    else:
        base_avatar_id = str(avatar_id).split("-")[0]
        backup_path = os.path.join(STATIC_DIR, "data", "characters", f"{base_avatar_id}.json")
        backup_path = resolve_datas_path(backup_path, beta)
        if os.path.exists(backup_path):
            chardatas = _load_json_auto(backup_path)
        else:
            raise HTTPException(status_code=404, detail=f"Character JSON file not found: {json_path2}")

    element_type = chardatas.get("element", "None")
    element_ja_map = {
        "Pyro": "炎", "Hydro": "水", "Anemo": "風", "Electro": "雷",
        "Dendro": "草", "Cryo": "氷", "Geo": "岩", "None": "無"
    }
    element_ja = element_ja_map.get(element_type, "無")

    # 言語別ラベル（card_data API の表示名用。画像生成は常に ja）
    if lang == "en":
        _lb_atk, _lb_def, _lb_em = "ATK", "DEF", "Elemental Mastery"
        _lb_cr, _lb_cd, _lb_er = "CRIT Rate", "CRIT DMG", "Energy Recharge"
        element_dmg_label = f"{element_type} DMG Bonus" if element_type != "None" else "DMG Bonus"
        slot_names = ["Flower", "Plume", "Sands", "Goblet", "Circlet"]
    else:
        _lb_atk, _lb_def, _lb_em = "攻撃力", "防御力", "元素熟知"
        _lb_cr, _lb_cd, _lb_er = "会心率", "会心ダメージ", "チャージ効率"
        element_dmg_label = f"{element_ja}ダメバフ"
        slot_names = ["花", "羽", "時計", "杯", "冠"]

    raw_special_id = _special_raw_id(avatar_id)
    if fake_char or raw_special_id in _NO_CONSTELLATION_CHARS:
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
    weapon_id = None

    if fake_weapon:
        weapon_id = fake_weapon
        weapon_json_path = resolve_datas_path(f"static/data/weapons/{weapon_id}.json", beta)
        if not os.path.exists(weapon_json_path):
            raise HTTPException(status_code=404, detail=f"Weapon JSON file not found: {weapon_json_path}")
        weapon_jsondata = _load_json_auto(weapon_json_path)
        # レアリティ1・2の武器はLv70（3以上はLv90）
        weapon_level = 70 if int(weapon_jsondata.get("rarity", 3)) in (1, 2) else 90
        weapon_affix = 1
        weapon_icon = resolve_datas_path(f"static/assets/weapons/{weapon_jsondata.get('icon', '')}.webp", beta)
        weapon_name = weapon_jsondata.get("name", "未知の武器")
        # サブオプション（会心率・チャージ効率など）は武器によって存在しないため、
        # 無い場合はエントリごとパスする（描画側も None でスキップされる）
        stats_modifier = weapon_jsondata.get("stats_modifier") or {}
        weapon_stats_list = [{'appendPropId': 'FIGHT_PROP_BASE_ATTACK', 'statValue': stats_modifier.get("atk", 0.0)}]
        sub_keys = [k for k in stats_modifier.keys() if k != "atk"]
        if sub_keys:
            second_key = sub_keys[0]
            stat_calc = stats_modifier[second_key]
            if stat_calc < 1:
                stat_calc = round(stat_calc * 100, 1)
            else:
                stat_calc = round(stat_calc)
            weapon_stats_list.append({'appendPropId': second_key.upper(), 'statValue': stat_calc})
    elif weapon_data:
        weapon_id = weapon_data["itemId"]
        weapon_json_path = resolve_datas_path(f"static/data/weapons/{weapon_id}.json", beta)
        if os.path.exists(weapon_json_path):
            try:
                weapon_jsondata = _load_json_auto(weapon_json_path)
                weapon_name = weapon_jsondata.get("name", "未知の武器")
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        weapon_icon = resolve_datas_path(f"static/assets/weapons/{weapon_data['flat']['icon']}.webp", beta)
        weapon_level = weapon_data["weapon"]["level"]
        weapon_affix = list(weapon_data["weapon"].get("affixMap", {}).values())[0] + 1 if weapon_data["weapon"].get("affixMap") else 1
        weapon_stats_list = weapon_data["flat"].get("weaponStats", [])

    # 英語モード時は武器名を lists/weapons.json の enName で置き換える
    # （武器詳細 JSON には日本語 name しか無いため）
    if lang == "en" and weapon_id:
        try:
            _wp_list_path = resolve_list_path("static/data/lists/weapons.json", beta)
            if os.path.exists(_wp_list_path):
                _wp_entry = (_load_json_auto(_wp_list_path) or {}).get(str(weapon_id)) or {}
                weapon_name = _wp_entry.get("enName") or weapon_name
        except Exception:
            pass

    raw_artifacts = [item for item in target_avatar_info.get("equipList", []) if "reliquary" in item]

    if fake_char or fake_weapon:
        if fake_char:
            char_stats_mod = chardatas.get("stats_modifier", {}) or {}
            base_hp = chardatas.get("hp", char_stats_mod.get("hp", 1))
            base_atk = chardatas.get("atk", char_stats_mod.get("atk", 1))
            base_def = chardatas.get("def", char_stats_mod.get("def", 1))
            base_crit_rate = chardatas.get("crit_rate", 0.05)
            base_crit_dmg = chardatas.get("crit_dmg", 0.5)
            base_em = chardatas.get("elemental_mastery", 0.0)
            # 差し替えキャラの基礎攻撃力には武器基礎攻撃力が含まれないため加算する
            weapon_base_included = False
        else:
            base_hp = target_avatar_info.get('fightPropMap', {}).get('1', 1)
            base_atk = target_avatar_info.get('fightPropMap', {}).get('4', 1)
            base_def = target_avatar_info.get('fightPropMap', {}).get('7', 1)
            base_crit_rate = 0.05
            base_crit_dmg = 0.5
            base_em = 0.0
            # 差し替え武器の基礎攻撃力を別途加算する
            weapon_base_included = False

        totals = compute_manual_totals(
            base_hp=base_hp,
            base_atk=base_atk,
            base_def=base_def,
            base_crit_rate=base_crit_rate,
            base_crit_dmg=base_crit_dmg,
            base_em=base_em,
            weapon_stats_list=weapon_stats_list,
            raw_artifacts=raw_artifacts,
            chardatas=chardatas,
            element_type=element_type,
            beta=beta,
            weapon_base_included_in_base_atk=weapon_base_included,
            resonance=resonance,
        )

        dmg_buff_val = "0%"
        if element_type in ("Pyro", "Hydro", "Anemo", "Electro", "Dendro", "Geo", "Cryo"):
            buff_val = totals["dmg_buff"]["val"]
            if buff_val > 0:
                dmg_buff_val = f"{format_decimal_value(buff_val * 100, base_prec)}%"

        main_stats = [
            {"label": "HP", "val": format_base_value(totals["hp"]["val"], base_prec), "base": format_base_value(totals["hp"]["base"], base_prec), "icon": "static/assets/props/hp.png"},
            {"label": _lb_atk, "val": format_base_value(totals["atk"]["val"], base_prec), "base": format_base_value(totals["atk"]["base"], base_prec), "icon": "static/assets/props/atk.png"},
            {"label": _lb_def, "val": format_base_value(totals["def"]["val"], base_prec), "base": format_base_value(totals["def"]["base"], base_prec), "icon": "static/assets/props/def.png"},
            {"label": _lb_em, "val": format_base_value(totals["em"]["val"], base_prec), "icon": "static/assets/props/em.png"},
            {"label": _lb_cr, "val": f"{format_decimal_value(totals['crit_rate']['val'] * 100, base_prec)}%", "icon": "static/assets/props/rate.webp"},
            {"label": _lb_cd, "val": f"{format_decimal_value(totals['crit_dmg']['val'] * 100, base_prec)}%", "icon": "static/assets/props/dmg.webp"},
            {"label": _lb_er, "val": f"{format_decimal_value(totals['er']['val'] * 100, base_prec)}%", "icon": "static/assets/props/er.png"},
            {"label": element_dmg_label, "val": dmg_buff_val, "icon": f"static/assets/props/{element_type.lower()}.png"},
        ]
    else:
        fight_prop = target_avatar_info.get('fightPropMap', {})

        # 実キャラも差し替えと同一の手動計算でステータスを導出する。
        # fightPropMap['4'](基礎攻撃力) には武器基礎攻撃力が既に含まれるため、
        # weapon_base_included_in_base_atk=True で二重加算を防ぐ。
        # 共鳴なしの実キャラは fightPropMap の最終値を EM/ER に採用（丸め境界対策）。
        _has_resonance = bool(parse_resonance_param(resonance))
        totals = compute_manual_totals(
            base_hp=fight_prop.get('1', 1),
            base_atk=fight_prop.get('4', 1),
            base_def=fight_prop.get('7', 1),
            base_crit_rate=0.05,
            base_crit_dmg=0.5,
            base_em=0.0,
            weapon_stats_list=weapon_stats_list,
            raw_artifacts=raw_artifacts,
            chardatas=chardatas,
            element_type=element_type,
            beta=beta,
            weapon_base_included_in_base_atk=True,
            resonance=resonance,
            authoritative_em=None if _has_resonance else fight_prop.get('28'),
            authoritative_er=None if _has_resonance else fight_prop.get('23'),
        )

        dmg_buff_val = "0%"
        if totals["dmg_buff"]["val"] > 0:
            dmg_buff_val = f"{format_decimal_value(totals['dmg_buff']['val'] * 100, base_prec)}%"

        main_stats = [
            {"label": "HP", "val": format_base_value(totals["hp"]["val"], base_prec), "base": format_base_value(totals["hp"]["base"], base_prec), "icon": "static/assets/props/hp.png"},
            {"label": _lb_atk, "val": format_base_value(totals["atk"]["val"], base_prec), "base": format_base_value(totals["atk"]["base"], base_prec), "icon": "static/assets/props/atk.png"},
            {"label": _lb_def, "val": format_base_value(totals["def"]["val"], base_prec), "base": format_base_value(totals["def"]["base"], base_prec), "icon": "static/assets/props/def.png"},
            {"label": _lb_em, "val": format_base_value(totals["em"]["val"], base_prec), "icon": "static/assets/props/em.png"},
            {"label": _lb_cr, "val": f"{format_decimal_value(totals['crit_rate']['val'] * 100, base_prec)}%", "icon": "static/assets/props/rate.webp"},
            {"label": _lb_cd, "val": f"{format_decimal_value(totals['crit_dmg']['val'] * 100, base_prec)}%", "icon": "static/assets/props/dmg.webp"},
            {"label": _lb_er, "val": f"{format_decimal_value(totals['er']['val'] * 100, base_prec)}%", "icon": "static/assets/props/er.png"},
            {"label": element_dmg_label, "val": dmg_buff_val, "icon": f"static/assets/props/{element_type.lower()}.png"},
        ]

    for s in main_stats:
        s["icon"] = resolve_datas_path(s["icon"], beta)

    slot_to_index = {"4": 0, "2": 1, "5": 2, "1": 3, "3": 4}
    method_to_prop_id = {
        "atk": "FIGHT_PROP_ATTACK_PERCENT",
        "hp": "FIGHT_PROP_HP_PERCENT",
        "def": "FIGHT_PROP_DEFENSE_PERCENT",
        "em": "FIGHT_PROP_ELEMENT_MASTERY",
        "charge": "FIGHT_PROP_CHARGE_EFFICIENCY"
    }
    target_prop_id = method_to_prop_id.get(calc_method, "")

    artifacts_out = [None, None, None, None, None]
    score_sum = 0

    for art in target_avatar_info.get("equipList", []):
        flat = art.get("flat", {})
        reliquary = art.get("reliquary", {})
        if flat.get("itemType") != "ITEM_RELIQUARY":
            continue
        icon_name = flat.get("icon", "")
        if "_" not in icon_name:
            continue
        last_num = icon_name.split("_")[-1]
        if last_num not in slot_to_index:
            continue
        target_idx = slot_to_index[last_num]

        name_hash = str(flat.get("nameTextMapHash", ""))
        artifact_name = get_text_map_name(
            name_hash, lang,
            "未知の聖遺物" if lang == "ja" else "Unknown Artifact",
        )

        main_stat_raw = flat.get("reliquaryMainstat", {})
        main_prop_id = main_stat_raw.get("mainPropId", "")
        main_name = get_stat_label(main_prop_id, lang)
        main_val = main_stat_raw.get("statValue", 0)
        if "PERCENT" in main_prop_id or "CRITICAL" in main_prop_id or "CHARGE" in main_prop_id or "HURT" in main_prop_id:
            main_value_str = f"{format_decimal_value(main_val, base_prec)}%"
        else:
            main_value_str = format_decimal_value(main_val, base_prec)

        crit_rate, crit_dmg, target_stat_val = 0.0, 0.0, 0.0
        substats_out = []
        sub_sums = sum_affix_substat_values(reliquary.get("appendPropIdList"))
        sub_rolls = artifact_substat_rolls(reliquary)
        for sub_data in flat.get("reliquarySubstats", []):
            sub_prop_id = sub_data.get("appendPropId", "")
            sub_name = get_stat_label(sub_prop_id, lang)
            sub_val = sub_sums.get(sub_prop_id, sub_data.get("statValue", 0))

            if sub_prop_id == "FIGHT_PROP_CRITICAL":
                crit_rate = sub_val
            elif sub_prop_id == "FIGHT_PROP_CRITICAL_HURT":
                crit_dmg = sub_val
            elif sub_prop_id == target_prop_id:
                target_stat_val = sub_val

            icon_file = "atk_per.png"
            if "CRITICAL" in sub_prop_id and "HURT" not in sub_prop_id: icon_file = "rate.webp"
            elif "HURT" in sub_prop_id: icon_file = "dmg.webp"
            elif "CHARGE" in sub_prop_id: icon_file = "er.png"
            elif "ELEMENT_MASTERY" in sub_prop_id: icon_file = "em.png"
            elif "HP" in sub_prop_id: icon_file = "hp_per.png" if "PERCENT" in sub_prop_id else "hp.png"
            elif "ATTACK" in sub_prop_id: icon_file = "atk_per.png" if "PERCENT" in sub_prop_id else "atk.png"
            elif "DEFENSE" in sub_prop_id: icon_file = "def_per.png" if "PERCENT" in sub_prop_id else "def.png"

            if is_percent_prop(sub_prop_id):
                sub_value_str = f"{format_decimal_value(sub_val, base_prec)}%"
            else:
                sub_value_str = format_decimal_value(sub_val, base_prec)

            substats_out.append({
                "name": sub_name, "value": sub_value_str,
                "icon": resolve_datas_path(f"static/assets/props/{icon_file}", beta),
                "rolls": (sub_rolls.get(sub_prop_id) or {}).get("tiers", [])
            })

        art_score = round(score_calc(stat=target_stat_val, critrate=crit_rate, critdmg=crit_dmg, method=calc_method), 1)

        if target_idx in [0, 1]:
            art_tier = "SS" if art_score >= 50.0 else "S" if art_score >= 45.0 else "A" if art_score >= 40.0 else "B"
        elif target_idx == 2:
            art_tier = "SS" if art_score >= 45.0 else "S" if art_score >= 40.0 else "A" if art_score >= 35.0 else "B"
        elif target_idx == 3:
            art_tier = "SS" if art_score >= 45.0 else "S" if art_score >= 40.0 else "A" if art_score >= 37.0 else "B"
        else:
            art_tier = "SS" if art_score >= 40.0 else "S" if art_score >= 35.0 else "A" if art_score >= 30.0 else "B"

        artifacts_out[target_idx] = {
            "slot": slot_names[target_idx],
            "set": str(flat.get("setId", "")),
            "name": artifact_name,
            "upgrade": reliquary.get("level", 1) - 1,
            "main": {"name": main_name, "value": main_value_str},
            "substats": substats_out,
            "score": round(art_score, 1),
            "tier": art_tier,
            "icon": resolve_datas_path(f"static/assets/artifacts/UI_RelicIcon_{flat.get('setId','')}_{icon_name.split('_')[-1]}.webp", beta)
        }
        score_sum += art_score

    set_ids = [a["set"] for a in artifacts_out if a and a["set"] and a["set"] != "0"]
    set_counts = Counter(set_ids)
    active_sets = [(sid, cnt) for sid, cnt in set_counts.items() if cnt >= 2]

    def get_set_info(set_id_str):
        possible_paths = [
            "static/data/lists/artifacts.json",
            os.path.join(BASE_DIR, "static", "data", "lists", "artifacts.json"),
        ]
        for raw_path in possible_paths:
            path = resolve_list_path(raw_path, beta)
            if os.path.exists(path):
                art_json = _load_json_auto(path)
                if set_id_str in art_json:
                    _entry = art_json[set_id_str]
                    # 英語モード時は enname を優先（無ければ janame にフォールバック）
                    if lang == "en":
                        _set_name = _entry.get("enname") or _entry.get("janame") or f"Set {set_id_str}"
                    else:
                        _set_name = _entry.get("janame", f"セット {set_id_str}")
                    return (_set_name, _entry.get("icon"))
        if lang == "en":
            return f"Set {set_id_str}", None
        return f"セット {set_id_str}", None

    set_bonuses = []
    for sid, cnt in active_sets:
        _set_name, _set_icon = get_set_info(sid)
        set_bonuses.append({
            "name": _set_name,
            "count": cnt,
            "icon": (resolve_datas_path(f"static/assets/artifacts/{_set_icon}.webp", beta) if _set_icon else ""),
            "id": str(sid),
            "buff": set_buff_label(sid, lang),
        })

    # 育成モード: 右側パネル用データ（card_data API → HTML/glass 表示に使用）
    growth_panel = {}
    if growth == "true":
        if fake_char or fake_weapon:
            growth_panel = build_growth_from_fake(
                calc_method,
                base_hp=base_hp,
                base_atk=base_atk,
                base_def=base_def,
                weapon_affix=weapon_affix,
                weapon_jsondata=weapon_jsondata if "weapon_jsondata" in locals() else None,
                raw_artifacts=raw_artifacts,
                set_bonuses=set_bonuses,
                beta=beta,
                lang=lang,
            )
        else:
            growth_panel = build_growth_panel(
                calc_method,
                base_hp=fight_prop.get("1", 0),
                base_atk=fight_prop.get("4", 0),
                base_def=fight_prop.get("7", 0),
                weapon_affix=weapon_affix,
                weapon_refinement=(weapon_jsondata.get("refinement") or {} if "weapon_jsondata" in locals() and weapon_jsondata else {}),
                raw_artifacts=raw_artifacts,
                set_bonuses=set_bonuses,
                beta=beta,
                lang=lang,
            )

    if score_sum < 180:
        tier_sum_score = "B"
    elif score_sum < 200:
        tier_sum_score = "A"
    elif score_sum < 220:
        tier_sum_score = "S"
    else:
        tier_sum_score = "SS"

    splash_path = ""
    costume_id = None
    try:
        icon_name = str(chardatas.get("icon", ""))
        # 展示データの costumeId があればコスチュームスプラッシュ（UI_Costume_*）を使用
        splash_path = resolve_costume_splash(chardatas, target_avatar_info, beta)
        if not splash_path and icon_name:
            splash_raw = f"static/assets/splash/{icon_name.replace('AvatarIcon', 'Gacha_AvatarImg')}.webp"
            splash_path = resolve_datas_path(splash_raw, beta)
        # 実際にコスチュームが適用された場合のみ costumeId を付与（オフセットキー用）
        _raw_costume_id = (target_avatar_info or {}).get("costumeId")
        if _raw_costume_id is not None and resolve_costume_icon(chardatas, target_avatar_info):
            costume_id = int(_raw_costume_id)
    except Exception:
        splash_path = ""

    if fake_char:
        friendship_lv = 10
    elif raw_special_id in _NO_FRIENDSHIP_CHARS:
        friendship_lv = None
    else:
        friendship_lv = target_avatar_info.get("fetterInfo", {}).get("expLevel", 1)

    skill_icons = []
    skill_levels = []
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
    # 命の星座は「表示中キャラ」（差し替えがあれば差し替え先）の有無に従う
    if _special_raw_id(fake_char or avatar_id) not in _NO_CONSTELLATION_CHARS:
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
        w_name = get_stat_label(w_prop, lang)
        if "PERCENT" in str(w_prop).upper() or "CRITICAL" in str(w_prop).upper() or "CHARGE" in str(w_prop).upper() or "HURT" in str(w_prop).upper():
            try:
                w_val_str = f"{format_decimal_value(float(w_val), base_prec)}%"
            except Exception:
                w_val_str = str(w_val)
        else:
            try:
                w_val_str = str(int(float(w_val)))
            except Exception:
                w_val_str = str(w_val)
        weapon_stats_out.append({"name": w_name, "value": w_val_str})

    if lang == "en":
        # HTML/スコアカード等の計算方法表示。ボックス幅が狭い場所に描かれるため短縮形
        display_map = {
            "crit": "CRIT only",
            "atk": "ATK%",
            "hp": "HP%",
            "def": "DEF%",
            "em": "EM",
            "charge": "ER",
        }
    else:
        display_map = {
            "crit": "会心のみ",
            "atk": "攻撃力%",
            "hp": "HP%",
            "def": "防御%",
            "em": "元素熟知",
            "charge": "チャージ効率",
        }
    display_score_way = display_map.get(calc_method, calc_method)

    # HTML カードテーマ（cinema / scorecard 提案デザイン）用の追加フィールド
    rarity_val = 5 if str(chardatas.get("rarity", "")) == "QUALITY_ORANGE" else 4
    if lang == "en":
        weapon_type_ja_map = {
            "WEAPON_SWORD_ONE_HAND": "Sword",
            "WEAPON_CLAYMORE": "Claymore",
            "WEAPON_POLE": "Polearm",
            "WEAPON_CATALYST": "Catalyst",
            "WEAPON_BOW": "Bow",
        }
    else:
        weapon_type_ja_map = {
            "WEAPON_SWORD_ONE_HAND": "片手剣",
            "WEAPON_CLAYMORE": "両手剣",
            "WEAPON_POLE": "長柄武器",
            "WEAPON_CATALYST": "法器",
            "WEAPON_BOW": "弓",
        }
    weapon_type_ja = weapon_type_ja_map.get(str(chardatas.get("weapon", "")), "")
    char_icon_path = ""
    try:
        _costume_icon = resolve_costume_icon(chardatas, target_avatar_info)
        _face_icon_name = _costume_icon or str(chardatas.get("icon", ""))
        if _face_icon_name:
            char_icon_path = resolve_datas_path(f"static/assets/characters/{_face_icon_name}.webp", beta)
    except Exception:
        char_icon_path = ""
    try:
        crit_value = round(totals["crit_rate"]["val"] * 100 + totals["crit_dmg"]["val"] * 100, 1)
    except Exception:
        crit_value = None

    _display_name = chardatas.get("name", avatar_id)
    if lang == "en":
        # キャラ詳細 JSON の en_name を優先（無ければ日本語名にフォールバック）
        _display_name = chardatas.get("en_name") or _display_name
    return {
        "displayName": (_display_name + "(swap)") if fake_char else _display_name,
        "element": element_type,
        "level": char_level,
        "friendship": friendship_lv,
        "constellation": constellation,
        "splash": splash_path,
        "costumeId": costume_id,
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
        "resonanceBadges": resonance_badges(resonance, lang),
        "scoreSum": round(score_sum, 1),
        "tierSum": tier_sum_score,
        "calcMethod": calc_method,
        "calcMethodLabel": display_score_way,
        "defaultCalcMethod": get_default_calc_method(_special_raw_id(fake_char or avatar_id)),
        "rarity": rarity_val,
        "weaponType": weapon_type_ja,
        "charIcon": char_icon_path,
        "critValue": crit_value,
        "growth": growth_panel,
        "regions": build_region_info(find_regions_for_character(_special_raw_id(fake_char or avatar_id), element_type)),
        # SCORECARD テーマ用スプラッシュオフセット（admin 管理・未設定は null）
        # キャラJSON には id フィールドが無いため、ベースID（例: 10000002）で引く。
        # admin の保存キーもベースID（str(char_id).split("-")[0]）なので一致する。
        "splashOffset": get_scorecard_splash_offset(_special_raw_id(fake_char or avatar_id)),
    }
