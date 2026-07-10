import json
import sys

# 【超重要】内部のプロパティ名、初期値(weapon_prop)、レベル倍率(stats_modifier)の紐付けマップ
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
        "curve_key": "crit_dmg"  # :bulb: 真言の匣など、json側が crit_dmg になっているケースに対応
    },
    "FIGHT_PROP_CHARGE_EFFICIENCY": {
        "out_key": "fight_prop_charge_efficiency",
        "curve_key": "charge_eff"  # :bulb: チャージ効率のキー揺れ対策
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

def transform_weapon(data: dict) -> dict:
    result = {
        "name": data.get("name"),
        "weapon_type": data.get("weapon_type"),
        "rarity": data.get("rarity"),
        "icon": data.get("icon")
    }

    # 最終突破(段階6)のボーナスデータを取得
    asc_6 = data.get("ascension", {}).get("6", {})

    stats_modifier = {}

    # 各プロパティごとに計算
    weapon_props = data.get("weapon_prop", [])
    for prop in weapon_props:
        prop_type = prop.get("prop_type")
        init_value = prop.get("init_value")

        if prop_type in PROP_CONFIG:
            config = PROP_CONFIG[prop_type]
            out_key = config["out_key"]
            curve_key = config["curve_key"]

            # 1. stats_modifier からレベル90の倍率を取得 (揺れに対応するため複数のキーをチェック)
            stats_data = data.get("stats_modifier", {})
            curve_data = stats_data.get(curve_key, stats_data.get(out_key, {}))
            mult_90 = curve_data.get("levels", {}).get("90", 1.0)

            # 2. ascension から突破ボーナス値を取得 (大文字・小文字両対応)
            add_value = asc_6.get(prop_type, asc_6.get(prop_type.lower(), 0.0))

            # 3. レベル90時点の最終値を計算
            # 基礎攻撃力以外（会心など）の突破ボーナスは通常0ですが、一応足し算の式にしています
            final_value = init_value * mult_90 + add_value

            # 小数点以下の丸め処理 (会心系はパーセントで見やすくするため、ここでは生の小数点データ)
            # 例: 0.192 * 1.9845 = 0.381024 -> 38.1% (初期値が19.2%の場合)
            # ただし真言の匣の初期値 0.192 はLv.1時点で、突破を重ねて最終的に 88.2% (0.882) になります。
            stats_modifier[out_key] = round(final_value, 4)

    result["stats_modifier"] = stats_modifier
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