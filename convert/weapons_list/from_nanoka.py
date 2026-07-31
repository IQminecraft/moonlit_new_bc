import json
import sys

RANK_TO_QUALITY = {
    1: "QUALITY_GREEN",
    2: "QUALITY_GREEN",
    3: "QUALITY_BLUE",
    4: "QUALITY_PURPLE",
    5: "QUALITY_ORANGE",
}

def simplify_weapon(data: dict) -> dict:
    result = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            continue

        en = value.get("en", "")
        ja = value.get("ja", "")
        rank = value.get("rank")
        weapon_type = value.get("type", "")

        quality = RANK_TO_QUALITY.get(rank, "")

        result[key] = {
            "enName": en,
            "jaName": ja,
            "qualityType": quality,
            "weaponType": weapon_type
        }
    return result

def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            raw = json.load(f)
    else:
        raw = json.load(sys.stdin)

    out = simplify_weapon(raw)

    if len(sys.argv) > 2:
        with open(sys.argv[2], "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
    else:
        json.dump(out, sys.stdout, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    main()