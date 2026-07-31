import json
import sys

def quality_to_rarity(q):
    """qualityType を rarity 数値に変換"""
    mapping = {
        "QUALITY_PURPLE": 4,
        "QUALITY_ORANGE": 5,
        "QUALITY_BLUE": 3,
        "QUALITY_GREEN": 2,
        "QUALITY_WHITE": 1
    }
    return mapping.get(q, 0)

def convert_weapon(data):
    result = {}

    result["name"] = data.get("name")
    result["weapon_type"] = data.get("weaponType")
    result["rarity"] = quality_to_rarity(data.get("qualityType"))
    result["icon"] = data.get("weaponIcon")

    stats = data.get("stats", {})
    lv90 = stats.get("90", {})
    atk = lv90.get("atk")
    crit_rate = lv90.get("CRIT Rate%")

    if atk is not None and crit_rate is not None:
        result["stats_modifier"] = {
            "atk": atk,
            "fight_prop_critical": crit_rate / 100.0
        }
    else:
        result["stats_modifier"] = {}

    return result


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)

    out = convert_weapon(data)

    if len(sys.argv) > 2:
        with open(sys.argv[2], "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
    else:
        json.dump(out, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()