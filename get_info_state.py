import asyncio
import json
from enka import GenshinClient
import os

try:
    import board_generator
    HAS_BOARD_GENERATOR = True
except ImportError:
    HAS_BOARD_GENERATOR = False
    #print("Warning: board_generator.py not found. Image generation will be skipped.")

async def update_uid_data(uid: int):
    """
    Fetches showcase data from Enka.network, cleans it, and saves it to a JSON file.
    """
    async with GenshinClient(lang="ja") as client:
        try:
            data = await client.fetch_showcase(uid, raw=True)
        except Exception as e:
            #print(f"Error fetching data from Enka for UID {uid}: {e}")
            # Fallback: check if cache already exists
            save_dir = os.path.join("static", "datas", "cache")
            json_filename = os.path.join(save_dir, f"showcase_{str(uid)}.json")
            if os.path.exists(json_filename):
                #print(f"Fallback: Using existing cache for UID {uid}")
                return True, "API error. Using cached data."
            return False, str(e)

        #print("現在の保存先:", os.getcwd())
        # Ensure the directory exists (optional, depends on where we want to save)
        # For now, keep the original behavior of saving to current directory or a specific one
        save_dir = os.path.join("static", "datas", "cache")
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)
        
        json_filename = os.path.join(save_dir, f"showcase_{str(uid)}.json")
        
        # Clean the data before saving (as per original logic)
        cleaned_data = clean_showcase_data(data)
        
        with open(json_filename, "w", encoding="utf-8") as f:
            json.dump(cleaned_data, f, ensure_ascii=False, indent=2)
            #print(f"Saved cleaned data to {json_filename}")
        
        # Generate image if board_generator is available
        if HAS_BOARD_GENERATOR:
            output_img = f"board_{uid}.png"
            try:
                board_generator.generate_board(json_filename, "template.png", output_img)
                #print(f"Generated board: {output_img}")
                pass
            except Exception as e:
                #print(f"Error generating board: {e}")
                pass
        
        return True, "Success"

def clean_showcase_data(data):
    """
    Cleans the showcase data by removing unnecessary keys.
    Returns the modified data object.
    """
    # Keys to remove from playerInfo
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
    
    # Remove specifying keys from avatarInfoList
    avatar_keys_to_remove = [
        "propMap", "fightPropMap", "skillDepotId",
        "inherentProudSkillList"
    ]

    if "avatarInfoList" in data:
        for avatar in data["avatarInfoList"]:
            # Remove keys from avatar object (Keeping propMap and fightPropMap for build cards)
            for key in avatar_keys_to_remove:
                if key in avatar and key not in ["propMap", "fightPropMap"]:
                    del avatar[key]
            
            # Deep clean equipList
            if "equipList" in avatar:
                for equip in avatar["equipList"]:
                    # Clean reliquary
                    if "reliquary" in equip:
                        if "appendPropIdList" in equip["reliquary"]:
                            del equip["reliquary"]["appendPropIdList"]
                    
                    # Clean flat
                    # We keep nameTextMapHash and icon as they are useful for translations and UI
                    pass
    return data

# Keep cleaning function for backward compatibility if needed, 
# but we refactored it to return data instead of modifying in place from file.
def clean_showcase(file_path):
    if not os.path.exists(file_path):
        #print(f"Error: {file_path} not found.")
        return

    with open(file_path, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            #print(f"Error decoding JSON: {e}")
            return

    cleaned_data = clean_showcase_data(data)

    # Save the modified data back to the same file
    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(cleaned_data, f, indent=4, ensure_ascii=False)
        #print(f"\nSuccessfully saved cleaned data to {file_path}")

if __name__ == "__main__":
    UID = 1812256644  # Default UID for testing
    asyncio.run(update_uid_data(UID))
