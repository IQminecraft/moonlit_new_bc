"""Character level and skill display level helpers."""
from __future__ import annotations


def get_char_level(avatar_info):
    """
    キャラレベルを取得する（90キャップなし）。
    propMap の 4001 (val / ival) を優先し、無ければ avatar 直下の level を使う。
    """
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


def resolve_display_skill_levels(avatar_info, fake_char=False):
    """
    天賦レベル（通常/スキル/爆発）を返す。
    命の星座による補正（proudSkillExtraLevelMap / C3→スキル+3, C5→爆発+3）を反映する。
    Returns: (levels: list[int], boosted: list[bool])
    """
    if fake_char:
        return [9, 9, 9], [False, False, False]

    skill_map = (avatar_info or {}).get("skillLevelMap") or {}
    base_vals = list(skill_map.values())
    levels = []
    for i in range(3):
        try:
            levels.append(int(base_vals[i]) if i < len(base_vals) else 1)
        except (TypeError, ValueError):
            levels.append(1)

    constellation = len((avatar_info or {}).get("talentIdList") or [])
    extra_map = (avatar_info or {}).get("proudSkillExtraLevelMap") or {}
    extra_amounts = []
    for v in extra_map.values():
        try:
            iv = int(v)
            if iv > 0:
                extra_amounts.append(iv)
        except (TypeError, ValueError):
            pass

    e_boost = 0
    q_boost = 0
    if constellation >= 3:
        e_boost = extra_amounts[0] if len(extra_amounts) >= 1 else 3
    if constellation >= 5:
        q_boost = extra_amounts[1] if len(extra_amounts) >= 2 else 3

    boosted = [False, False, False]
    if e_boost:
        levels[1] = levels[1] + e_boost
        boosted[1] = True
    if q_boost:
        levels[2] = levels[2] + q_boost
        boosted[2] = True

    return levels, boosted
