import os
import json
from app.paths import BASE_DIR

try:
    with open(os.path.join(BASE_DIR, "external", "enka_py", "assets", "text_map.json"), "r", encoding="utf-8") as f:
        text_map_data = json.load(f)
except Exception as e:
    print(f"[Warning] text_map.json の読み込みに失敗: {e}")
    text_map_data = {}


def get_stat_japanese(append_prop_id: str) -> str:
    fallback_map = {
        "FIGHT_PROP_BASE_ATTACK": "基礎攻撃力",
        "FIGHT_PROP_CRITICAL": "会心率",
        "FIGHT_PROP_CRITICAL_HURT": "会心ダメージ",
        "FIGHT_PROP_CHARGE_EFFICIENCY": "チャージ効率",
        "FIGHT_PROP_ATTACK_PERCENT": "攻撃力%",
        "FIGHT_PROP_HP_PERCENT": "HP%",
        "FIGHT_PROP_DEFENSE_PERCENT": "防御力%",
        "FIGHT_PROP_ELEMENT_MASTERY": "熟知"
    }
    if append_prop_id in fallback_map:
        return fallback_map[append_prop_id]
    if append_prop_id in text_map_data:
        return text_map_data[append_prop_id]
    if "ja" in text_map_data and append_prop_id in text_map_data["ja"]:
        return text_map_data["ja"][append_prop_id]
    return append_prop_id


def formal_round(val):
    return int(val + 0.5) if val >= 0 else int(val - 0.5)


def get_char_level(avatar_info):
    if not avatar_info:
        return 1
    prop_map = avatar_info.get("propMap") or {}
    level_entry = prop_map.get("4001") or prop_map.get(4001)
    if isinstance(level_entry, dict):
        raw = level_entry.get("val")
        if raw is None:
            raw = level_entry.get("ival")
        if raw is not None and str(raw).strip() != "":
            try:
                return int(float(str(raw)))
            except (TypeError, ValueError):
                pass
    top_level = avatar_info.get("level")
    if top_level is not None and str(top_level).strip() != "":
        try:
            return int(float(str(top_level)))
        except (TypeError, ValueError):
            pass
    return 1


def new_stat_totals():
    return {
        "hp_flat": 0.0, "hp_percent": 0.0,
        "atk_flat": 0.0, "atk_percent": 0.0,
        "def_flat": 0.0, "def_percent": 0.0,
        "em": 0.0, "crit_rate": 0.0, "crit_dmg": 0.0,
        "energy_recharge": 0.0,
        "dmg_bonus_by_element": {},
    }


def to_ratio_if_percent(prop_id, value):
    if value is None:
        return 0.0
    key_upper = str(prop_id).upper()
    if "PERCENT" in key_upper or "CRITICAL" in key_upper or "CHARGE" in key_upper or "HURT" in key_upper:
        return value / 100.0
    return value


def is_percent_prop(prop_id):
    return to_ratio_if_percent(prop_id, 1.0) < 1.0


def format_substat_value(value):
    """サブオプの表示用フォーマット。小数点3位で四捨五入して
    小数第2位まで表示（不要な trailing zero は削除）。"""
    r = round(value + 1e-9, 2)
    if abs(r - round(r)) < 1e-9:
        return f"{int(round(r)):,}"
    s = f"{r:,.2f}"
    if s.endswith("0"):
        s = s[:-1]
    return s


def format_base_value(value, prec=2):
    """基礎ステータス（基礎HP/攻撃/防御）の表示用フォーマット。
    prec=0 で整数、prec=2 で小数第2位、prec=4 で小数第4位まで表示。
    prec=2 は format_substat_value と同じ表示になる。"""
    prec = str(prec or "2")
    if prec not in ("0", "2", "4"):
        prec = "2"
    if prec == "0":
        return f"{int(round(float(value) + 1e-9)):,}"
    if prec == "4":
        r = round(float(value) + 1e-9, 4)
        if abs(r - round(r)) < 1e-9:
            return f"{int(round(r)):,}"
        s = f"{r:,.4f}".rstrip("0").rstrip(".")
        return s
    return format_substat_value(value)


def format_var_base_add(value, base, prec=2):
    """val / base / add の表示用フォーマットをまとめて返す。
    prec=0 の場合 val と base を整数に四捨五入し、
    add は丸め後の val から base を引いた値にして base + add == val が表示上成り立つようにする。
    それ以外は format_substat_value（小数第2位）と同じ。"""
    prec = str(prec or "2")
    if prec not in ("0", "2", "4"):
        prec = "2"
    v = float(value) + 1e-9
    b = float(base) + 1e-9
    if prec == "0":
        rv = int(round(v))
        rb = int(round(b))
        return f"{rv:,}", f"{rb:,}", f"{rv - rb:,}"
    if prec == "4":
        rv = round(v, 4)
        rb = round(b, 4)
        add = round(rv - rb + 1e-9, 4)

        def _fmt(x):
            if abs(x - round(x)) < 1e-9:
                return f"{int(round(x)):,}"
            s = f"{x:,.4f}".rstrip("0").rstrip(".")
            return s

        return _fmt(rv), _fmt(rb), _fmt(add)
    s_v = format_substat_value(v)
    s_b = format_substat_value(b)
    return s_v, s_b, format_substat_value(round(v - b, 2))


def format_decimal_value(value, prec="0"):
    """% やサブステータス値の表示用フォーマット。
    prec='0'（標準）は小数第1位まで、prec='2'（小数第2位）は小数第2位まで表示。"""
    prec = str(prec or "0")
    if prec not in ("0", "2", "4"):
        prec = "0"
    if prec == "2":
        return format_substat_value(value)
    if prec == "4":
        r = round(float(value) + 1e-9, 4)
        if abs(r - round(r)) < 1e-9:
            return f"{int(round(r)):,}"
        s = f"{r:,.4f}".rstrip("0").rstrip(".")
        return s
    r = round(float(value) + 1e-9, 1)
    if abs(r - round(r)) < 1e-9:
        return f"{int(round(r)):,}"
    s = f"{r:,.1f}".rstrip("0").rstrip(".")
    return s


def apply_stat_bonus(totals, prop_id, value):
    if not prop_id or value in (None, ""):
        return
    key = str(prop_id).upper()
    if key == "FIGHT_PROP_HP":
        totals["hp_flat"] += value
    elif key == "FIGHT_PROP_HP_PERCENT":
        totals["hp_percent"] += value
    elif key in ("FIGHT_PROP_ATTACK", "FIGHT_PROP_BASE_ATTACK"):
        totals["atk_flat"] += value
    elif key == "FIGHT_PROP_ATTACK_PERCENT":
        totals["atk_percent"] += value
    elif key == "FIGHT_PROP_DEFENSE":
        totals["def_flat"] += value
    elif key == "FIGHT_PROP_DEFENSE_PERCENT":
        totals["def_percent"] += value
    elif key == "FIGHT_PROP_ELEMENT_MASTERY":
        totals["em"] += value
    elif key == "FIGHT_PROP_CRITICAL":
        totals["crit_rate"] += value
    elif key == "FIGHT_PROP_CRITICAL_HURT":
        totals["crit_dmg"] += value
    elif key == "FIGHT_PROP_CHARGE_EFFICIENCY":
        totals["energy_recharge"] += value
    elif key.endswith("_DMG") or key.endswith("_ADD_HURT") or "DMG_BONUS" in key:
        elem = None
        if "PYRO" in key or "FIRE" in key: elem = "Pyro"
        elif "HYDRO" in key or "WATER" in key: elem = "Hydro"
        elif "ANEMO" in key or "WIND" in key: elem = "Anemo"
        elif "ELECTRO" in key or "ELEC" in key: elem = "Electro"
        elif "DENDRO" in key or "GRASS" in key: elem = "Dendro"
        elif "CRYO" in key or "ICE" in key: elem = "Cryo"
        elif "GEO" in key or "ROCK" in key: elem = "Geo"
        elif "PHYSICAL" in key: elem = "物理"
        if elem:
            totals["dmg_bonus_by_element"][elem] = totals["dmg_bonus_by_element"].get(elem, 0.0) + value


def score_calc(stat, critrate, critdmg, method):
    scores = critrate * 2 + critdmg
    if method == "em":
        scores += stat * 0.25
    else:
        scores += stat
    return scores


def _load_reliquary_affix_map():
    """ReliquaryAffixExcelConfigData.json を読み、Id → レコード のマップを返す。"""
    path = os.path.join(BASE_DIR, "static", "data", "ReliquaryAffixExcelConfigData.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            entries = json.load(f)
        return {e["Id"]: e for e in entries}
    except Exception as e:
        print(f"[Warning] ReliquaryAffixExcelConfigData.json の読み込みに失敗: {e}")
        return {}


_RELIQUARY_AFFIX_MAP = None


def get_reliquary_affix_map():
    global _RELIQUARY_AFFIX_MAP
    if _RELIQUARY_AFFIX_MAP is None:
        _RELIQUARY_AFFIX_MAP = _load_reliquary_affix_map()
    return _RELIQUARY_AFFIX_MAP


_AFFIX_TIER_SCALE_CACHE = {}


def _affix_tier_scale(affix_map, depot_id, prop_type):
    """指定 Depot + PropType の伸び値タイア一覧（昇順）を返す（e.g. 5★HP: [209.13, 239, 268.88, 298.75]）。"""
    key = (depot_id, prop_type)
    if key not in _AFFIX_TIER_SCALE_CACHE:
        vals = sorted({
            round(float(e.get("PropValue", 0.0)), 5)
            for e in affix_map.values()
            if e.get("DepotId") == depot_id and e.get("PropType") == prop_type
        })
        _AFFIX_TIER_SCALE_CACHE[key] = vals
    return _AFFIX_TIER_SCALE_CACHE[key]


def artifact_substat_rolls(reliquary):
    """appendPropIdList からサブステ毎のロール情報を返す。

    appendPropIdList は「ロール1回 = affix 1件」を保持するため、同一 PropType の
    出現回数がそのサブステのロール回数＝初期値 + 強化回数となる。
    強化のみのタイア一覧（値が小さい順に 0=青 / 1=黄緑 / 2=黄 / 3=赤）を
    {'rolls_all': [{tier, value}, ...], 'upgrades': [tier, ...]} の形で返す。

    戻り値: {prop_type: {'rolls_all': [...], 'upgrades': [...]}}
    """
    app_ids = reliquary.get("appendPropIdList") or []
    result = {}
    if not app_ids:
        return result
    affix_map = get_reliquary_affix_map()
    entries = []
    for aid in app_ids:
        e = affix_map.get(aid)
        if e:
            entries.append(e)
    if not entries:
        return result
    depot_ids = {e.get("DepotId") for e in entries if e.get("DepotId")}
    depot_id = next(iter(depot_ids)) if depot_ids else None

    from collections import OrderedDict
    per_prop = OrderedDict()
    for e in entries:
        pt = e.get("PropType")
        if not pt:
            continue
        per_prop.setdefault(pt, []).append(round(float(e.get("PropValue", 0.0)), 5))

    for pt, raw_values in per_prop.items():
        scale = _affix_tier_scale(affix_map, depot_id, pt) if depot_id else []
        rolls_all = []
        for v in raw_values:
            tier = 0
            if scale:
                tier = min(range(len(scale)), key=lambda i: abs(scale[i] - v))
            rolls_all.append({"tier": tier, "value": v})
        result[pt] = {
            "rolls_all": rolls_all,
            "tiers": [r["tier"] for r in rolls_all],  # 初期値+強化 全ロール
            "upgrades": [r["tier"] for r in rolls_all[1:]],  # 先頭 = 初期値
        }
    return result


def sum_affix_substat_values(app_id_list):
    """enka の reliquary.appendPropIdList の各 affix ID に対応する値を
    PropType ごとに合計し、表示単位の辞書を返す（％系は ×100）。

    戻り値: {FIGHT_PROP_*: 合計値}。対応するデータが無ければ空 dict。
    """
    if not app_id_list:
        return {}
    affix_map = get_reliquary_affix_map()
    sums = {}
    for aid in app_id_list:
        entry = affix_map.get(aid)
        if not entry:
            continue
        prop_type = entry.get("PropType")
        if not prop_type:
            continue
        value = entry.get("PropValue", 0.0)
        if "PERCENT" in prop_type or "CRITICAL" in prop_type or "CHARGE" in prop_type or "HURT" in prop_type:
            value = value * 100.0
        sums[prop_type] = sums.get(prop_type, 0.0) + value
    return sums
