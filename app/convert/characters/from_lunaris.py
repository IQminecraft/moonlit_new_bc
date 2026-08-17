import json
import sys

def parse_percent(v):
    if v is None:
        return 0.0
    if isinstance(v, str):
        v = v.replace("%", "")
    return float(v) / 100.0

def convert(data):
    result = {}
    info = data.get("info", {})

    result["name"] = data.get("name") or info.get("name")
    result["desc"] = data.get("desc") or info.get("description", "")
    result["weapon"] = data.get("weapon") or info.get("weapon")
    result["rarity"] = data.get("rarity") or info.get("rarity")
    result["element"] = data.get("element") or info.get("element")
    result["icon"] = data.get("icon") or data.get("icons", {}).get("forward", "")

    attrs = info.get("attributes", [])
    lv1 = next((a for a in attrs if a.get("level") == 1), None)
    lv90 = next((a for a in attrs if a.get("level") == 90), None)

    old_base_hp = data.get("base_hp")
    old_base_atk = data.get("base_atk")
    old_base_def = data.get("base_def")

    if lv90:
        result["hp"] = lv90.get("hp")
        result["atk"] = lv90.get("atk")
        result["def"] = lv90.get("def")
    elif lv1:
        result["hp"] = lv1.get("hp")
        result["atk"] = lv1.get("atk")
        result["def"] = lv1.get("def")
    else:
        result["hp"] = old_base_hp
        result["atk"] = old_base_atk
        result["def"] = old_base_def

    result["crit_rate"] = data.get("crit_rate", 0.05)
    result["crit_dmg"] = data.get("crit_dmg", 0.5)
    result["elemental_mastery"] = data.get("elemental_mastery", 0)

    stats_mod = {}
    old_stats = data.get("stats_modifier", {})

    if old_stats:
        asc_list = old_stats.get("ascension", [])
        new_asc = []
        for asc in asc_list:
            filtered = {}
            if "fight_prop_critical_hurt" in asc:
                filtered["fight_prop_critical_hurt"] = asc["fight_prop_critical_hurt"]
            if filtered:
                new_asc.append(filtered)
        stats_mod["ascension"] = new_asc

    elif lv1 and lv90:
        stats_mod["ascension"] = [{
            "fight_prop_critical_hurt": parse_percent(lv90.get("CRIT DMG%", 0))
        }]

    else:
        stats_mod["ascension"] = []

    result["stats_modifier"] = stats_mod

    # レベル1～100の基礎ステータス（HP/ATK/DEF）を保存する。
    # info.attributes はレベル別リスト。欠落レベルは前後のエントリで補間する。
    attrs_by_level = {}
    if isinstance(attrs, list):
        for a in attrs:
            lvl = a.get("level")
            if isinstance(lvl, (int, float)) and (a.get("hp") is not None
                                                  or a.get("atk") is not None
                                                  or a.get("def") is not None):
                attrs_by_level[int(lvl)] = a
    attrs_levels = sorted(attrs_by_level.keys())
    base_stats = {}
    for lvl in range(1, 101):
        a = attrs_by_level.get(lvl)
        if a is None and attrs_levels:
            near = min(attrs_levels, key=lambda x: abs(x - lvl))
            a = attrs_by_level[near]
        base_stats[str(lvl)] = {
            "hp": round(float(a.get("hp") or 0), 4),
            "atk": round(float(a.get("atk") or 0), 4),
            "def": round(float(a.get("def") or 0), 4),
        }
    result["base_stats"] = base_stats

    def pick_icons(src):
        out = []
        if isinstance(src, list):
            for item in src:
                if "icon" in item:
                    out.append({"icon": item["icon"]})
        elif isinstance(src, dict):
            for key, val in src.items():
                if "icon" in val:
                    out.append({"icon": val["icon"]})
        return out

    result["skills"] = pick_icons(data.get("skills", []))
    result["passives"] = pick_icons(data.get("passives", []))
    result["constellations"] = pick_icons(data.get("constellations", []))

    return result


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            raw = json.load(f)
    else:
        raw = json.load(sys.stdin)

    out = convert(raw)

    if len(sys.argv) > 2:
        with open(sys.argv[2], "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
    else:
        json.dump(out, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()