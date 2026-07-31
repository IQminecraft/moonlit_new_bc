import json
import sys

def simplify_artifact(data):
    result = {}
    for key, val in data.items():
        if "enName" in val and "qualityType" in val:
            result[key] = {
                "enName": val["enName"],
                "qualityType": val["qualityType"],
                "icon": val["setIcon"]
            }
    return result

if __name__ == "__main__":
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'r', encoding='utf-8') as f:
            raw = json.load(f)
    else:
        raw = json.load(sys.stdin)
    out = simplify_artifact(raw)
    if len(sys.argv) > 2:
        with open(sys.argv[2], 'w', encoding='utf-8') as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
    else:
        json.dump(out, sys.stdout, indent=2, ensure_ascii=False)