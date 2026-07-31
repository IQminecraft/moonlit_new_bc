import asyncio
import json
from enka import GenshinClient
import os

try:
    import board_generator  # type: ignore[import-not-found]
    HAS_BOARD_GENERATOR = True
except ImportError:
    HAS_BOARD_GENERATOR = False

async def update_uid_data(uid: int):
    async with GenshinClient(lang="ja") as client:
        try:
            data = await client.fetch_showcase(uid, raw=True)
        except Exception as e:
            save_dir = os.path.join("static", "cache")
            json_filename = os.path.join(save_dir, f"showcase_{str(uid)}.json")
            if os.path.exists(json_filename):
                return True, "API error. Using cached data."
            return False, str(e)

        save_dir = os.path.join("static", "cache")
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)
        
        json_filename = os.path.join(save_dir, f"showcase_{str(uid)}.json")
        
        cleaned_data = clean_showcase_data(data)
        
        with open(json_filename, "w", encoding="utf-8") as f:
            json.dump(cleaned_data, f, ensure_ascii=False, indent=2)
        
        if HAS_BOARD_GENERATOR:
            output_img = f"board_{uid}.png"
            try:
                board_generator.generate_board(json_filename, "template.png", output_img)
                pass
            except Exception as e:
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


def clean_showcase(file_path):
    if not os.path.exists(file_path):
        return

    with open(file_path, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            return

    cleaned_data = clean_showcase_data(data)

    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(cleaned_data, f, indent=4, ensure_ascii=False)

if __name__ == "__main__":
    UID = 1812256644
    asyncio.run(update_uid_data(UID))
