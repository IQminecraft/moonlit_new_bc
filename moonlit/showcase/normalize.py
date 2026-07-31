from __future__ import annotations

TRAVELER_BASE_IDS = ("10000005", "10000007", "10000117", "10000118")


def normalize_avatar_id(raw_id, energy_type=None) -> str:
    rid = str(raw_id)
    if rid in TRAVELER_BASE_IDS:
        if energy_type is not None:
            return f"{rid}-{energy_type}"
        return f"{rid}-4"
    return rid


def match_avatar_id(avatar: dict, target_avatar_id: str) -> bool:
    raw_id = str(avatar.get("avatarId"))
    loop_id = normalize_avatar_id(raw_id, avatar.get("energyType"))
    return str(loop_id) == str(target_avatar_id)
