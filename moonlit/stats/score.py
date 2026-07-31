from __future__ import annotations

CALC_METHOD_PROP_ID = {
    "atk": "FIGHT_PROP_ATTACK_PERCENT",
    "hp": "FIGHT_PROP_HP_PERCENT",
    "def": "FIGHT_PROP_DEFENSE_PERCENT",
    "em": "FIGHT_PROP_ELEMENT_MASTERY",
    "charge": "FIGHT_PROP_CHARGE_EFFICIENCY",
}

CALC_METHOD_LABEL = {
    "crit": "会心のみ",
    "atk": "攻撃力%",
    "hp": "HP%",
    "def": "DEF%",
    "em": "元素熟知",
    "charge": "チャージ効率",
}


def score_calc(stat, critrate, critdmg, method):
    scores = critrate * 2 + critdmg
    if method == "em":
        scores += stat * 0.25
    else:
        scores += stat
    return scores


def artifact_tier(art_score: float, slot_index: int) -> str:
    if slot_index in (0, 1):
        if art_score >= 50.0:
            return "SS"
        if art_score >= 45.0:
            return "S"
        if art_score >= 40.0:
            return "A"
        return "B"
    if slot_index == 2:
        if art_score >= 45.0:
            return "SS"
        if art_score >= 40.0:
            return "S"
        if art_score >= 35.0:
            return "A"
        return "B"
    if slot_index == 3:
        if art_score >= 45.0:
            return "SS"
        if art_score >= 40.0:
            return "S"
        if art_score >= 37.0:
            return "A"
        return "B"
    if art_score >= 40.0:
        return "SS"
    if art_score >= 35.0:
        return "S"
    if art_score >= 30.0:
        return "A"
    return "B"


def total_tier(score_sum: float) -> str:
    if score_sum < 180:
        return "B"
    if score_sum < 200:
        return "A"
    if score_sum < 220:
        return "S"
    return "SS"
