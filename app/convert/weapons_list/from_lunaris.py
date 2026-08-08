import json
import sys

def simplify_weapons(data: dict) -> dict:
    result = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        result[key] = {
            "enName": value.get("enName"),
            "qualityType": value.get("qualityType"),
            "weaponType": value.get("weaponType")
        }
    return result

def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            raw = json.load(f)
    else:
        raw = json.load(sys.stdin)

    out = simplify_weapons(raw)

    if len(sys.argv) > 2:
        with open(sys.argv[2], "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
    else:
        json.dump(out, sys.stdout, indent=2, ensure_ascii=False)

if __name__ == "__main__":
    main()