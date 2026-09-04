# -*- coding: utf-8 -*-
"""元素共鳴バフの選択・反映（単体カード用）。

編成（team）カードが一時的に無効化されているため、元素共鳴は編成から
自動判定できなくなった。代わりに単体カード側で「どの元素共鳴が有効か」を
手動選択（最大2つ）し、最終ステータスへ反映・カード下端の隙間にバッジ表示する。

ステータスに反映できる共鳴（RESO_STAT_AFFECTING）のみ選択肢として表示する。
表示名のみの共鳴（迅速の風/強権の雷/不動の岩/交錯の護り）はカードの
ステータス項目に効かないため対象外。
"""
from app.card.stats import apply_stat_bonus, to_ratio_if_percent

# ---------------------------------------------------------------------------
# ステータスに反映可能な元素共鳴（原神の共鳴効果より）。
#   key → { elem, name, prop: FIGHT_PROP_*, value, label }
#   value は「編集・保存の単位」で percent 系は %（ex: 25）、flat 系はそのまま数値。
#   to_ratio_if_percent が percent 系を率（0.25）へ変換する。
# ---------------------------------------------------------------------------
RESONANCE_TYPES = {
    "pyro":   {"elem": "Pyro",   "name": "熱誠の炎", "name_en": "Fervent Flames",    "prop": "FIGHT_PROP_ATTACK_PERCENT",  "value": 25, "label": "攻撃力+25%", "label_en": "ATK +25%"},
    "hydro":  {"elem": "Hydro",  "name": "治療の水", "name_en": "Soothing Waters",   "prop": "FIGHT_PROP_HP_PERCENT",      "value": 25, "label": "HP上限+25%", "label_en": "HP +25%"},
    "cryo":   {"elem": "Cryo",   "name": "粉砕の氷", "name_en": "Shattering Ice",    "prop": "FIGHT_PROP_CRITICAL",         "value": 15, "label": "会心率+15%", "label_en": "CRIT Rate +15%"},
    "dendro": {"elem": "Dendro", "name": "蔓生の草", "name_en": "Sprawling Greenery", "prop": "FIGHT_PROP_ELEMENT_MASTERY", "value": 50, "label": "元素熟知+50", "label_en": "Elemental Mastery +50"},
}

# 同時に有効にできる共鳴の上限（原神の仕様と同じ2つ）
MAX_RESONANCES = 2


def parse_resonance_param(raw) -> list:
    """クエリパラメータ（"pyro,dendro" など）→ 有効な共鳴キーのリスト。

    重複は除去し最大 MAX_RESONANCES 件。未知のキーは無視。リスト入力も許容。
    """
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        parts = [str(p) for p in raw]
    else:
        parts = str(raw).split(",")
    keys = []
    for part in parts:
        k = str(part).strip().lower()
        if k in RESONANCE_TYPES and k not in keys:
            keys.append(k)
        if len(keys) >= MAX_RESONANCES:
            break
    return keys


def apply_resonance_buffs(stat_totals, resonance) -> None:
    """選択された共鳴バフを stat_totals へ反映する。

    resonance: "pyro,dendro"（str）またはキーのリスト。
    """
    for key in parse_resonance_param(resonance):
        info = RESONANCE_TYPES.get(key)
        if not info:
            continue
        apply_stat_bonus(stat_totals, info["prop"], to_ratio_if_percent(info["prop"], info["value"]))


def resonance_badges(resonance, lang: str = "ja") -> list:
    """カード下端の隙間に表示する共鳴バッジ [{kind, text, elem, label}] を返す。"""
    badges = []
    for key in parse_resonance_param(resonance):
        info = RESONANCE_TYPES.get(key)
        if not info:
            continue
        if lang == "en":
            _text, _label = info.get("name_en") or info["name"], info.get("label_en") or info["label"]
        else:
            _text, _label = info["name"], info["label"]
        badges.append({
            "kind": "resonance",
            "text": _text,
            "elem": info["elem"],
            "label": _label,
        })
    return badges


def resonance_catalog(lang: str = "ja") -> list:
    """UI（選択肢）用のカタログ [{key, elem, name, label}] を返す。"""
    out = []
    for key, info in RESONANCE_TYPES.items():
        if lang == "en":
            _name, _label = info.get("name_en") or info["name"], info.get("label_en") or info["label"]
        else:
            _name, _label = info["name"], info["label"]
        out.append({"key": key, "elem": info["elem"], "name": _name, "label": _label})
    return out
