import json
import re
import sys

_COLOR_TAG_RE = re.compile(r"<color=#[0-9A-Fa-f]+>(.*?)</color>")


def _strip_color_tags(text: str) -> str:
    """ナノカの説明文に含まれる <color=#...>...</color> タグを除去して素の文字列にする。"""
    if not text:
        return ""
    return _COLOR_TAG_RE.sub(r"\1", text)

PROP_CONFIG = {
    "FIGHT_PROP_BASE_ATTACK": {
        "out_key": "atk",
        "curve_key": "atk"
    },
    "FIGHT_PROP_CRITICAL": {
        "out_key": "fight_prop_critical",
        "curve_key": "fight_prop_critical"
    },
    "FIGHT_PROP_CRITICAL_HURT": {
        "out_key": "fight_prop_critical_hurt",
        "curve_key": "crit_dmg"
    },
    "FIGHT_PROP_CHARGE_EFFICIENCY": {
        "out_key": "fight_prop_charge_efficiency",
        "curve_key": "charge_eff"
    },
    "FIGHT_PROP_ELEMENT_MASTERY": {
        "out_key": "fight_prop_element_mastery",
        "curve_key": "element_mastery"
    },
    "FIGHT_PROP_ATTACK_PERCENT": {
        "out_key": "fight_prop_attack_percent",
        "curve_key": "atk_per"
    },
    "FIGHT_PROP_DEFENSE_PERCENT": {
        "out_key": "fight_prop_defense_percent",
        "curve_key": "def_per"
    },
    "FIGHT_PROP_HP_PERCENT": {
        "out_key": "fight_prop_hp_percent",
        "curve_key": "hp_per"
    }
}

def _refinement_ja(data: dict) -> dict:
    """精錬効果（日本語のみ）を抽出する。refinement = {1..5: {name, desc, param_list}}。"""
    refinement = data.get("refinement") or {}
    out = {}
    for level in sorted(refinement.keys(), key=lambda x: int(x)):
        entry = refinement[level] or {}
        name = _strip_color_tags(entry.get("name") or "")
        desc = _strip_color_tags(entry.get("desc") or "")
        if not name and not desc:
            continue
        out[str(level)] = {"name": name, "desc": desc}
    return out


def transform_weapon(data: dict) -> dict:
    result = {
        "name": data.get("name"),
        "weapon_type": data.get("weapon_type"),
        "rarity": data.get("rarity"),
        "icon": data.get("icon")
    }

    asc_6 = data.get("ascension", {}).get("6", {})

    stats_modifier = {}

    weapon_props = data.get("weapon_prop", [])
    for prop in weapon_props:
        prop_type = prop.get("prop_type")
        init_value = prop.get("init_value")

        if prop_type in PROP_CONFIG:
            config = PROP_CONFIG[prop_type]
            out_key = config["out_key"]
            curve_key = config["curve_key"]

            stats_data = data.get("stats_modifier", {})
            curve_data = stats_data.get(curve_key, stats_data.get(out_key, {}))
            mult_90 = curve_data.get("levels", {}).get("90", 1.0)

            add_value = asc_6.get(prop_type, asc_6.get(prop_type.lower(), 0.0))

            final_value = init_value * mult_90 + add_value

            stats_modifier[out_key] = round(final_value, 4)

    result["stats_modifier"] = stats_modifier

    refinement = _refinement_ja(data)
    if refinement:
        result["refinement"] = refinement

    return result

def main():
    if len(sys.argv) > 1:
        filepath = sys.argv[1]
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            transformed = transform_weapon(data)
            print(json.dumps(transformed, ensure_ascii=False, indent=2))
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
    else:
        print("Usage: python from_nanoka.py <weapon_json_path>")

if __name__ == "__main__":
    main()