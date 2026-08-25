import json
import sys

def transform_character(data: dict) -> dict:
    top_keys = [
        "name", "en_name", "desc", "weapon", "rarity", "element", "icon",
        "crit_rate", "crit_dmg", "elemental_mastery"
    ]
    new_data = {k: data.get(k) for k in top_keys if k in data}

    stats = data.get("stats_modifier", {})
    
    base_hp = data.get("base_hp", 0)
    base_atk = data.get("base_atk", 0)
    base_def = data.get("base_def", 0)

    hp_mult = stats.get("hp", {}).get("90", 1.0)
    atk_mult = stats.get("atk", {}).get("90", 1.0)
    def_mult = stats.get("def", {}).get("90", 1.0)

    asc_list = stats.get("ascension", [])
    
    final_hp = (hp_mult * base_hp)
    final_atk = (atk_mult * base_atk)
    final_def = (def_mult * base_def)
    
    extra_stats = {}

    if asc_list:
        last_asc = asc_list[-1]
        
        final_hp += last_asc.get("fight_prop_base_hp", 0)
        final_atk += last_asc.get("fight_prop_base_attack", 0)
        final_def += last_asc.get("fight_prop_base_defense", 0)
        
        ignored_keys = {"fight_prop_base_hp", "fight_prop_base_attack", "fight_prop_base_defense"}
        for key, value in last_asc.items():
            if key not in ignored_keys and value != 0:
                extra_stats[key] = value
    new_data["stats_modifier"] = {
            "hp": final_hp,
            "atk": final_atk,
            "def": final_def,
            "extra": extra_stats
        }

    # レベル1～100の基礎ステータス（HP/ATK/DEF）を保存する。
    # 計算式: base_stat(L) = base * mult[L] + ascension_bonus(phase(L))
    # phase はレベル閾値(20/40/50/60/70/80)で切り替わる。
    hp_mults = stats.get("hp", {})
    atk_mults = stats.get("atk", {})
    def_mults = stats.get("def", {})

    def _phase_index(level: int) -> int:
        if level >= 80:
            return 6
        if level >= 70:
            return 5
        if level >= 60:
            return 4
        if level >= 50:
            return 3
        if level >= 40:
            return 2
        if level >= 20:
            return 1
        return 0

    def _mult(mults: dict, level: int) -> float:
        return float(mults.get(str(level), mults.get("90", 1.0)))

    base_stats = {}
    for lvl in range(1, 101):
        pi = _phase_index(lvl)
        asc = asc_list[pi - 1] if 1 <= pi <= len(asc_list) else {}
        base_stats[str(lvl)] = {
            "hp": round(base_hp * _mult(hp_mults, lvl) + asc.get("fight_prop_base_hp", 0), 4),
            "atk": round(base_atk * _mult(atk_mults, lvl) + asc.get("fight_prop_base_attack", 0), 4),
            "def": round(base_def * _mult(def_mults, lvl) + asc.get("fight_prop_base_defense", 0), 4),
        }
    new_data["base_stats"] = base_stats

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

    passives = data.get("passives", [])
    new_passives = [
        {"icon": p["icon"]}
        for p in passives if "icon" in p
    ]
    new_data["passives"] = new_passives

    cons = data.get("constellations", [])
    new_cons = [
        {"icon": c["icon"]}
        for c in cons if "icon" in c
    ]
    new_data["constellations"] = new_cons

    # コスチューム: chara_info.costume から id / icon / quality のみ保持
    costumes = data.get("chara_info", {}).get("costume") or []
    new_costumes = []
    for c in costumes:
        if not isinstance(c, dict) or c.get("id") is None:
            continue
        new_costumes.append({
            "id": c.get("id"),
            "icon": c.get("icon", ""),
            "quality": c.get("quality", 0),
        })
    new_data["costume"] = new_costumes

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