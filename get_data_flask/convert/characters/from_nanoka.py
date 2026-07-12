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
    
    # 既存の基本ステータス
    base_hp = data.get("base_hp", 0)
    base_atk = data.get("base_atk", 0)
    base_def = data.get("base_def", 0)

    # 90レベル時の乗算倍率
    hp_mult = stats.get("hp", {}).get("90", 1.0)
    atk_mult = stats.get("atk", {}).get("90", 1.0)
    def_mult = stats.get("def", {}).get("90", 1.0)

    # 突破ステータスの抽出 (最大突破値を使用)
    asc_list = stats.get("ascension", [])
    
    # 計算式に基づいたステータス算出
    final_hp = (hp_mult * base_hp)
    final_atk = (atk_mult * base_atk)
    final_def = (def_mult * base_def)
    
    extra_stats = {}

    if asc_list:
        last_asc = asc_list[-1]
        
        # 突破固定値の加算
        final_hp += last_asc.get("fight_prop_base_hp", 0)
        final_atk += last_asc.get("fight_prop_base_attack", 0)
        final_def += last_asc.get("fight_prop_base_defense", 0)
        
        # HP/ATK/DEF 以外のキーを自動抽出
        ignored_keys = {"fight_prop_base_hp", "fight_prop_base_attack", "fight_prop_base_defense"}
        for key, value in last_asc.items():
            if key not in ignored_keys and value != 0:
                extra_stats[key] = value
    new_data["stats_modifier"] = {
            "hp": final_hp,
            "atk": final_atk,
            "def": final_def,
            "extra": extra_stats  # 突破ステータスの4つ目以降をここに格納
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