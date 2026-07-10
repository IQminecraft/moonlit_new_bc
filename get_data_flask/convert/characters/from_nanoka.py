import json
import sys

def transform_character(data: dict) -> dict:
    # ---------- 基本情報 ----------
    top_keys = [
        "name", "desc", "weapon", "rarity", "element", "icon",
        "crit_rate", "crit_dmg", "elemental_mastery"
    ]
    new_data = {k: data.get(k) for k in top_keys if k in data}

    # ---------- stats_modifier から必要データを取得 ----------
    stats = data.get("stats_modifier", {})
    base_hp = data.get("base_hp", 0)
    base_atk = data.get("base_atk", 0)
    base_def = data.get("base_def", 0)

    hp_mult = stats.get("hp", {}).get("90", 1.0)
    atk_mult = stats.get("atk", {}).get("90", 1.0)
    def_mult = stats.get("def", {}).get("90", 1.0)

    asc_list = stats.get("ascension", [])
    if asc_list:
        last_asc = asc_list[-1]
        add_hp = last_asc.get("fight_prop_base_hp", 0)
        add_atk = last_asc.get("fight_prop_base_attack", 0)
        add_def = last_asc.get("fight_prop_base_defense", 0)
        crit_hurt = last_asc.get("fight_prop_critical_hurt", 0)
    else:
        add_hp = add_atk = add_def = crit_hurt = 0

    raw_hp = base_hp * hp_mult + add_hp
    raw_atk = base_atk * atk_mult + add_atk
    raw_def = base_def * def_mult + add_def

    new_data["hp"] = round(raw_hp)
    new_data["atk"] = round(raw_atk)
    new_data["def"] = round(raw_def)

    new_data["stats_modifier"] = {
        "ascension": [{"fight_prop_critical_hurt": crit_hurt}] if asc_list else []
    }

    # ---------- skills（IDを削除してアイコンのみ） ----------
    skills = data.get("skills", [])
    new_skills = []
    for sk in skills:
        promote = sk.get("promote", {})
        icon = None
        if "0" in promote and "icon" in promote["0"]:
            icon = promote["0"]["icon"]
        elif "icon" in sk:
            icon = sk["icon"]
        if icon:
            new_skills.append({"icon": icon})
    new_data["skills"] = new_skills

    # ---------- passives（IDを削除してアイコンのみ） ----------
    passives = data.get("passives", [])
    new_passives = [
        {"icon": p["icon"]}
        for p in passives if "icon" in p
    ]
    new_data["passives"] = new_passives

    # ---------- constellations（IDを削除してアイコンのみ） ----------
    cons = data.get("constellations", [])
    new_cons = [
        {"icon": c["icon"]}
        for c in cons if "icon" in c
    ]
    new_data["constellations"] = new_cons

    return new_data


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            raw = json.load(f)
    else:
        raw = json.load(sys.stdin)

    result = transform_character(raw)

    if len(sys.argv) > 2:
        with open(sys.argv[2], "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
    else:
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()