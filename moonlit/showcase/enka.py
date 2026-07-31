from __future__ import annotations

import asyncio
import json
import os
from enka import GenshinClient

from moonlit.config import CACHE_DIR

try:
    import board_generator
    HAS_BOARD_GENERATOR = True
except ImportError:
    HAS_BOARD_GENERATOR = False


async def update_uid_data(uid: int):
    async with GenshinClient(lang="ja") as client:
        try:
            data = await client.fetch_showcase(uid, raw=True)
        except Exception as e:
            json_filename = os.path.join(CACHE_DIR, f"showcase_{str(uid)}.json")
            if os.path.exists(json_filename):
                return True, "API error. Using cached data."
            return False, str(e)

        if not os.path.exists(CACHE_DIR):
            os.makedirs(CACHE_DIR)

        json_filename = os.path.join(CACHE_DIR, f"showcase_{str(uid)}.json")

        cleaned_data = clean_showcase_data(data)

        with open(json_filename, "w", encoding="utf-8") as f:
            json.dump(cleaned_data, f, ensure_ascii=False, indent=2)

        if HAS_BOARD_GENERATOR:
            output_img = f"board_{uid}.png"
            try:
                board_generator.generate_board(json_filename, "template.png", output_img)
            except Exception:
                pass

        return True, "Success"


def clean_showcase_data(data):
    keys_to_remove = [
        "worldLevel", "nameCardId", "finishAchievementNum", "towerFloorIndex",
        "towerLevelIndex", "showNameCardIdList", "theaterActIndex", "theaterModeIndex",
        "theaterStarIndex", "isShowAvatarTalent", "fetterCount", "towerStarIndex",
        "stygianIndex", "stygianSeconds", "stygianId"
    ]

    if "playerInfo" in data:
        player_info = data["playerInfo"]
        for key in keys_to_remove:
            if key in player_info:
                del player_info[key]

    avatar_keys_to_remove = [
        "skillDepotId",
        "inherentProudSkillList",
    ]

    if "avatarInfoList" in data:
        for avatar in data["avatarInfoList"]:
            for key in avatar_keys_to_remove:
                if key in avatar:
                    del avatar[key]

            if "equipList" in avatar:
                for equip in avatar["equipList"]:
                    if "reliquary" in equip:
                        if "appendPropIdList" in equip["reliquary"]:
                            del equip["reliquary"]["appendPropIdList"]
    return data