import json
import sys

def quality_from_rank(ranks: list) -> str:
    """rank 配列の最大値から qualityType 文字列を返す"""
    max_rank = max(ranks) if ranks else 0
    mapping = {
        5: "QUALITY_ORANGE",
        4: "QUALITY_PURPLE",
        3: "QUALITY_BLUE",
        2: "QUALITY_GREEN",
        1: "QUALITY_WHITE"
    }
    return mapping.get(max_rank, "QUALITY_WHITE")

def _set_effect(set_obj: dict, suffix: str):
    """set オブジェクトから 2セット(末尾0) / 4セット(末尾1) の name/desc(ja) を返す。"""
    for key in set_obj:
        if str(key).endswith(suffix):
            entry = set_obj[key] or {}
            name_obj = entry.get("name", {}) or {}
            desc_obj = entry.get("desc", {}) or {}
            return {
                "ja": (name_obj.get("ja") or ""),
                "desc_ja": (desc_obj.get("ja") or ""),
                "desc_en": (desc_obj.get("en") or ""),
            }
    return None


def convert_artifact(data: dict) -> dict:
    result = {}
    for set_id, set_data in data.items():
        # rank を取得
        rank = set_data.get("rank", [])
        if not rank:
            continue
        quality = quality_from_rank(rank)
        icon = set_data.get("icon", "")
        # set オブジェクトを取得
        set_obj = set_data.get("set", {})
        if not set_obj:
            continue

        # 最初のキー（2セット効果）からセット名を取得（従来互換）
        first_key = next(iter(set_obj))
        first_set = set_obj[first_key]
        name_obj = first_set.get("name", {})

        enname = name_obj.get("en", "")
        janame = name_obj.get("ja", "")

        set2 = _set_effect(set_obj, "0")
        set4 = _set_effect(set_obj, "1")

        # 結果に追加（空の名前はスキップ可能だが、今回はそのまま残す）
        entry = {
            "janame": janame,
            "enname": enname,
            "quality": quality,
            "icon": icon,
        }
        # 2セット/4セット効果の説明文（ja）を保存（admin のバフ選択・判定に使用）
        if set2 and set2["desc_ja"]:
            entry["set2_desc_ja"] = set2["desc_ja"]
            if set2["desc_en"]:
                entry["set2_desc_en"] = set2["desc_en"]
        if set4 and set4["desc_ja"]:
            entry["set4_desc_ja"] = set4["desc_ja"]
        result[set_id] = entry

    return result


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)

    out = convert_artifact(data)

    if len(sys.argv) > 2:
        with open(sys.argv[2], "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
    else:
        json.dump(out, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()