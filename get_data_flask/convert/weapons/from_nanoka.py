import json
import sys

def transform_weapon(data: dict) -> dict:
    # ---------- 基本情報 ----------
    result = {
        "name": data.get("name"),
        "weapon_type": data.get("weapon_type"),
        "rarity": data.get("rarity"),
        "icon": data.get("icon")
    }

    # ---------- weapon_prop から init_value を取得 ----------
    weapon_prop = data.get("weapon_prop", [])
    base_atk = None
    base_crit = None
    for prop in weapon_prop:
        if prop.get("prop_type") == "FIGHT_PROP_BASE_ATTACK":
            base_atk = prop.get("init_value")
        elif prop.get("prop_type") == "FIGHT_PROP_CRITICAL":
            base_crit = prop.get("init_value")

    # ---------- stats_modifier からレベル90の倍率を取得 ----------
    stats = data.get("stats_modifier", {})
    atk_levels = stats.get("atk", {}).get("levels", {})
    crit_levels = stats.get("fight_prop_critical", {}).get("levels", {})
    atk_mult_90 = atk_levels.get("90", 1.0)
    crit_mult_90 = crit_levels.get("90", 1.0)

    # ---------- ascension の最終突破ボーナスを取得 ----------
    ascension = data.get("ascension", {})
    asc_6 = ascension.get("6", {})
    add_atk = asc_6.get("fight_prop_base_attack", 0.0)

    # ---------- 計算 ----------
    stats_modifier = {}
    if base_atk is not None:
        stats_modifier["atk"] = base_atk * atk_mult_90 + add_atk
    if base_crit is not None:
        stats_modifier["fight_prop_critical"] = base_crit * crit_mult_90

    result["stats_modifier"] = stats_modifier
    return result


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)

    out = transform_weapon(data)

    if len(sys.argv) > 2:
        with open(sys.argv[2], "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
    else:
        json.dump(out, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()