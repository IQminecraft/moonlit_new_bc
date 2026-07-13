from flask import Flask, render_template, request
import requests
import json
import os
import gachabase_changelog
from convert import *

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/nanoka_get_live')
def get_livedata():
    manifest = requests.get("https://static.nanoka.cc/manifest.json")
    manifest_data = manifest.json()
    live = manifest_data["gi"]["live"]

    live_character_r = requests.get(f"https://static.nanoka.cc/gi/{live}/character.json")
    live_character_r = live_character_r.json()
    live_character = list(live_character_r.keys())

    live_weapon_r = requests.get(f"https://static.nanoka.cc/gi/{live}/weapon.json")
    live_weapon_r = live_weapon_r.json()
    live_weapon = list(live_weapon_r.keys())

    live_artifact_r = requests.get(f"https://static.nanoka.cc/gi/{live}/artifact.json")
    live_artifact_r= live_artifact_r.json()
    live_artifact = list(live_artifact_r.keys())
    
    for m in live_character:
        url = f"https://static.nanoka.cc/gi/{live}/ja/character/{m}.json"
        r = requests.get(url)
        converted = characters.from_nanoka(r.json())
        char_path = os.path.join("..","static", "datas", "characters", f"{m}.json")
        with open(char_path, "w", encoding="utf-8") as f:
            json.dump(converted, f, indent=2, ensure_ascii=False)
    """
    live_weapon_r = requests.get(f"https://static.nanoka.cc/gi/{live}/weapon.json")
    live_weapon_r = live_weapon_r.json()
    live_weapon = list(live_weapon_r.keys())
    for m in live_weapon:
        url = f"https://static.nanoka.cc/gi/{live}/ja/weapon/{m}.json"
        r = requests.get(url)
        converted = weapons.from_nanoka(r.json())
        weapon_path = os.path.join("..","static", "datas", "weapons", f"{m}.json")
        with open(weapon_path, "w", encoding="utf-8") as f:
            json.dump(converted, f, indent=2, ensure_ascii=False)
# --- 3. 一覧（リスト）データの処理 ---
    
    char_list_url = f"https://static.nanoka.cc/gi/{live}/character.json"
    weapon_list_url = f"https://static.nanoka.cc/gi/{live}/weapon.json"
    artifact_list_url = f"https://static.nanoka.cc/gi/{live}/artifact.json"

    characters_path = os.path.join("..", "static", "datas", "lists", "characters.json")
    weapons_path = os.path.join("..", "static", "datas", "lists", "weapons.json")
    artifacts_path = os.path.join("..", "static", "datas", "lists", "artifacts.json")
    
    # ここでインポートしたモジュール名（weapons, artifacts）と被らない別名にする
    chars_data = characters_list.from_nanoka(requests.get(char_list_url).json())
    weapons_data = weapons_list.from_nanoka(requests.get(weapon_list_url).json())
    artifacts_data = artifacts.from_nanoka(requests.get(artifact_list_url).json())
    
    with open(characters_path, "w", encoding="utf-8") as f:
        json.dump(chars_data, f, indent=2, ensure_ascii=False)

    with open(weapons_path, "w", encoding="utf-8") as f:
        json.dump(weapons_data, f, indent=2, ensure_ascii=False)
    
    with open(artifacts_path, "w", encoding="utf-8") as f:
        json.dump(artifacts_data, f, indent=2, ensure_ascii=False)
    #キャラアバﾀー取得
    for charid in live_character:
        
        with open(f"../static/datas/characters/{charid}.json", "r", encoding="utf-8") as f:
            avatar_id = json.load(f)["icon"]
        #アバター
        
        r = requests.get(f"https://static.nanoka.cc/assets/gi/{avatar_id}.webp")
        img_dir = os.path.join("..", "static", "datas", "assets", "characters")
        img_path = os.path.join(img_dir, f"{avatar_id}.webp")
        
        with open(img_path, "wb") as f:
            f.write(r.content) # テキストではなくバイナリを書き込む
        #スプラッシュ
        splashid = avatar_id.replace("AvatarIcon", "Gacha_AvatarImg")
        r1 = requests.get(f"https://static.nanoka.cc/assets/gi/{splashid}.webp")
        img_dir1 = os.path.join("..", "static", "datas", "assets", "splash")
        img_path1 = os.path.join(img_dir1, f"{splashid}.webp")
        with open(img_path1, "wb") as f:
            f.write(r1.content) # テキストではなくバイナリを書き込む
    
        #聖遺物
    for artifactid in live_artifact:
        artifactjson = os.path.join("..","static","datas","lists","artifacts.json")
        with open(artifactjson, "r", encoding="utf-8") as f:
            avatar_id2 = json.load(f)[artifactid]["icon"]
        for i in range(5):
            avatar_new = avatar_id2.replace("_4",f"_{i+1}")
            r = requests.get(f"https://static.nanoka.cc/assets/gi/{avatar_new}.webp")
            img_dir2 = os.path.join("..", "static", "datas", "assets", "artifacts")
            img_path2 = os.path.join(img_dir2, f"{avatar_new}.webp")
            with open(img_path2, "wb") as f:
                f.write(r.content) # テキストではなくバイナリを書き込む

    
        #武器アイコン
    for weaponid in live_weapon:
        try:
            weaponjson = os.path.join("..","static","datas","weapons",f"{weaponid}.json")
            with open(weaponjson, "r", encoding="utf-8") as f:
                avatar_id1 = json.load(f)["icon"]
            r = requests.get(f"https://static.nanoka.cc/assets/gi/{avatar_id1}.webp")
            img_dir2 = os.path.join("..", "static", "datas", "assets", "weapons")
            img_path2 = os.path.join(img_dir2, f"{avatar_id1}.webp")
            with open(img_path2, "wb") as f:
                f.write(r.content) # テキストではなくバイナリを書き込む
        except:
            pass
    """
        


    return "Live data updated successfully"





@app.route('/lunaris_get_data')
def lunaris_get_data():
    manifest = requests.get("https://api.lunaris.moe/data/version.json")
    manifest_data = manifest.json()
    live = max([x for x in manifest_data["versions"] if '.0' in x], key=str)
    beta = manifest_data["version"]
    print(f"live : {live}, beta : {beta}")

    # weapon
    live_weapon_r, beta_weapon_r = requests.get(f"https://api.lunaris.moe/data/{live}/weaponlist.json"), requests.get(f"https://api.lunaris.moe/data/{beta}/weaponlist.json")
    live_weapon_r, beta_weapon_r = live_weapon_r.json(), beta_weapon_r.json()
    live_weapon, beta_weapon = list(live_weapon_r.keys()), list(beta_weapon_r.keys())
    added_weapon_list = list(set(beta_weapon) - set(live_weapon))

    # character
    live_character_r, beta_character_r = requests.get(f"https://api.lunaris.moe/data/{live}/charlist.json"), requests.get(f"https://api.lunaris.moe/data/{beta}/charlist.json")
    live_character_r, beta_character_r = live_character_r.json(), beta_character_r.json()
    live_character, beta_character = list(live_character_r.keys()), list(beta_character_r.keys())
    added_character_list = list(set(beta_character) - set(live_character))

    # articaft
    live_artifact_r, beta_artifact_r = requests.get(f"https://api.lunaris.moe/data/{live}/artifactlist.json"), requests.get(f"https://api.lunaris.moe/data/{beta}/artifactlist.json")
    live_artifact_r, beta_artifact_r = live_artifact_r.json(), beta_artifact_r.json()
    live_artifact, beta_artifact = list(live_artifact_r.keys()), list(beta_artifact_r.keys())
    added_artifact_list = list(set(beta_artifact) - set(live_artifact))

    char_dir = os.path.join("..", "static", "BETA", "characters")
    weapon_dir = os.path.join("..", "static", "BETA", "weapons")
    list_dir = os.path.join("..", "static", "BETA", "lists")

    # --- 1. 個別キャラクター詳細の変換（詳細用の characters モジュールを使用） ---
    for char_id in added_character_list:
        url = f"https://api.lunaris.moe/data/{beta}/en/char/{char_id}.json"
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            save_path = os.path.join(char_dir, f"{char_id}.json")
            
            # 【修正】_listなしの「characters」に修正
            converted = characters.from_lunaris(response.json())
            
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(converted, f, indent=2, ensure_ascii=False)
        except Exception as e:
            pass

    # --- 2. 個別武器詳細の変換（詳細用の weapons モジュールを使用） ---
    for weapon_id in added_weapon_list:
        # URLの先頭の不要な空白スペースも削っておきました
        url = f"https://api.lunaris.moe/data/{beta}/en/weapon/{weapon_id}.json"
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            save_path = os.path.join(weapon_dir, f"{weapon_id}.json")
            
            # 【修正】_listなしの「weapons」に修正
            converted = weapons.from_lunaris(response.json())
            
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(converted, f, indent=2, ensure_ascii=False)
        except Exception as e:
            pass

    # --- 3. 一覧（リスト）データの一括変換 ---
    MODULE_MAP = {
        "characters.json": characters_list,  # リスト用
        "weapons.json": weapons_list,        # リスト用
        "artifacts.json": artifacts          # 単体用
    }

    list_urls = {
        "characters.json": f"https://api.lunaris.moe/data/{beta}/charlist.json",
        "weapons.json": f"https://api.lunaris.moe/data/{beta}/weaponlist.json",
        "artifacts.json": f"https://api.lunaris.moe/data/{beta}/artifactlist.json"
    }

    for filename, url in list_urls.items():
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            raw_data = response.json()
            
            target_module = MODULE_MAP.get(filename)
            if target_module:
                converted = target_module.from_lunaris(raw_data)
            else:
                converted = raw_data

            save_path = os.path.join(list_dir, filename)
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(converted, f, indent=2, ensure_ascii=False)
                
        except Exception as e:
            pass

    return str(added_weapon_list)+"\n"+str(added_character_list)+"\n"+str(added_artifact_list)

def download_character_images(char_id, mode: "live" or "beta"):
    # 1. モードに応じて、読み込むJSONの場所と画像の保存先を切り替える
    if mode == "live":
        json_path = f"../static/datas/characters/{char_id}.json"
        img_dir = os.path.join("..", "static", "datas", "assets", "skill_icon")
    elif mode == "beta":
        json_path = f"../static/BETA/characters/{char_id}.json"
        img_dir = os.path.join("..", "static", "BETA", "assets", "skill_icon") # 必要に応じてパスは調整してください
    else:
        print("エラー: mode には 'live' または 'beta' を指定してください。")
        return

    os.makedirs(img_dir, exist_ok=True)

    # JSONファイルを開いてデータを読み込む
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"エラー: {json_path} が見つかりません。")
        return
    except Exception as e:
        print(f"JSON読み込みエラー: {e}")
        return

    # 2. JSONからすべてのアイコン名（画像名）を抽出
    icon_names = []

    if "icon" in data and data["icon"]:
        icon_names.append(data["icon"])

    if "skills" in data and isinstance(data["skills"], list):
        for skill in data["skills"]:
            if "icon" in skill and skill["icon"]:
                icon_names.append(skill["icon"])

    if "passives" in data and isinstance(data["passives"], list):
        for passive in data["passives"]:
            if "icon" in passive and passive["icon"]:
                icon_names.append(passive["icon"])

    if "constellations" in data and isinstance(data["constellations"], list):
        for constellation in data["constellations"]:
            if "icon" in constellation and constellation["icon"]:
                icon_names.append(constellation["icon"])

    # 重複排除
    icon_names = list(set(icon_names))
    print(f"【{mode.upper()} - {data.get('name', char_id)}】から {len(icon_names)} 個のアイコンを検出しました。")

    # 3. 画像のダウンロードと保存
    for icon_name in icon_names:
        url = f"https://static.nanoka.cc/assets/gi/{icon_name}.webp"
        img_path = os.path.join(img_dir, f"{icon_name}.webp")

        if os.path.exists(img_path):
            continue

        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()

            with open(img_path, "wb") as f:
                f.write(r.content)
            print(f"保存成功: {icon_name}.webp")

        except Exception as e:
            print(f"ダウンロード失敗 ({icon_name}): {e}")

@app.route('/nanoka_get_data')
def nanoka_get_data():
    manifest = requests.get("https://static.nanoka.cc/manifest.json")
    manifest_data = manifest.json()
    live = manifest_data["gi"]["live"]
    beta = manifest_data["gi"]["latest"]
    print(f"live : {live}, beta : {beta}")
    # weapon
    live_weapon_r, beta_weapon_r = requests.get(f"https://static.nanoka.cc/gi/{live}/weapon.json"), requests.get(f"https://static.nanoka.cc/gi/{beta}/weapon.json")
    live_weapon_r, beta_weapon_r = live_weapon_r.json(), beta_weapon_r.json()
    live_weapon, beta_weapon = list(live_weapon_r.keys()), list(beta_weapon_r.keys())
    added_weapon_list = list(set(beta_weapon) - set(live_weapon))

    # character
    live_character_r, beta_character_r = requests.get(f"https://static.nanoka.cc/gi/{live}/character.json"), requests.get(f"https://static.nanoka.cc/gi/{beta}/character.json")
    live_character_r, beta_character_r = live_character_r.json(), beta_character_r.json()
    live_character, beta_character = list(live_character_r.keys()), list(beta_character_r.keys())
    added_character_list = list(set(beta_character) - set(live_character))

    # artifact
    live_artifact_r, beta_artifact_r = requests.get(f"https://static.nanoka.cc/gi/{live}/artifact.json"), requests.get(f"https://static.nanoka.cc/gi/{beta}/artifact.json")
    live_artifact_r, beta_artifact_r = live_artifact_r.json(), beta_artifact_r.json()
    live_artifact, beta_artifact = list(live_artifact_r.keys()), list(beta_artifact_r.keys())
    added_artifact_list = list(set(beta_artifact) - set(live_artifact))

    char_dir = os.path.join("..", "static", "BETA", "characters")
    weapon_dir = os.path.join("..", "static", "BETA", "weapons")
    list_dir = os.path.join("..", "static", "BETA", "lists")
    
    # --- 1. 個別キャラクター詳細（_listなしの「characters」を使用） ---
    for char_id in added_character_list:
        url = f"https://static.nanoka.cc/gi/{beta}/ja/character/{char_id}.json"
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            save_path = os.path.join(char_dir, f"{char_id}.json")
            
            # フォルダ名を「characters」にして、末尾を「.from_nanoka」で呼び出す
            converted = characters.from_nanoka(response.json())
            
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(converted, f, indent=2, ensure_ascii=False)
        except Exception as e:
            pass
    
    # --- 2. 個別武器詳細（_listなしの「weapons」を使用） ---
    for weapon_id in added_weapon_list:
        url = f"https://static.nanoka.cc/gi/{beta}/ja/weapon/{weapon_id}.json"
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            save_path = os.path.join(weapon_dir, f"{weapon_id}.json")
            
            # フォルダ名を「weapons」にして、末尾を「.from_nanoka」で呼び出す
            converted = weapons.from_nanoka(response.json())
            
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(converted, f, indent=2, ensure_ascii=False)
        except Exception as e:
            pass
    
    # --- 3. 一覧（リスト）データの一括処理 ---
    MODULE_MAP = {
        "characters.json": characters_list,
        "weapons.json": weapons_list,
        "artifacts.json": artifacts
    }

    list_urls = {
        "characters.json": f"https://static.nanoka.cc/gi/{beta}/character.json",
        "weapons.json": f"https://static.nanoka.cc/gi/{beta}/weapon.json",
        "artifacts.json": f"https://static.nanoka.cc/gi/{beta}/artifact.json"
    }

    for filename, url in list_urls.items():
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            raw_data = response.json()
            
            target_module = MODULE_MAP.get(filename)
            if target_module:
                # 末尾を「.from_nanoka」で呼び出す
                converted = target_module.from_nanoka(raw_data)
            else:
                converted = raw_data

            save_path = os.path.join(list_dir, filename)
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(converted, f, indent=2, ensure_ascii=False)
                
        except Exception as e:
            pass
        
    
    """
    for charid in added_character_list:
        #アイコン
        with open(f"../static/BETA/characters/{charid}.json", "r", encoding="utf-8") as f:
            avatar_id = json.load(f)["icon"]
        
        r = requests.get(f"https://static.nanoka.cc/assets/gi/{avatar_id}.webp")
        img_dir = os.path.join("..", "static", "BETA", "assets", "characters")
        img_path = os.path.join(img_dir, f"{avatar_id}.webp")
        with open(img_path, "wb") as f:
            f.write(r.content) # テキストではなくバイナリを書き込む
        
        #スプラッシュ
        splashid = avatar_id.replace("AvatarIcon", "Gacha_AvatarImg")
        r1 = requests.get(f"https://static.nanoka.cc/assets/gi/{splashid}.webp")
        img_dir1 = os.path.join("..", "static", "BETA", "assets", "splash")
        img_path1 = os.path.join(img_dir1, f"{splashid}.webp")
        with open(img_path1, "wb") as f:
            f.write(r1.content) # テキストではなくバイナリを書き込む
        #スキル/天賦/凸 アイコン
        download_character_images(charid, "beta")
        

    #武器アイコン
    for weaponid in added_weapon_list:
        weaponjson = os.path.join("..","static","BETA","weapons",f"{weaponid}.json")
        with open(weaponjson, "r", encoding="utf-8") as f:
            avatar_id1 = json.load(f)["icon"]
        r = requests.get(f"https://static.nanoka.cc/assets/gi/{avatar_id1}.webp")
        img_dir2 = os.path.join("..", "static", "BETA", "assets", "weapons")
        img_path2 = os.path.join(img_dir2, f"{avatar_id1}.webp")
        with open(img_path2, "wb") as f:
            f.write(r.content) # テキストではなくバイナリを書き込む
        
    
    #聖遺物
    
    for artifactid in added_artifact_list:
        artifactjson = os.path.join("..","static","BETA","lists","artifacts.json")
        with open(artifactjson, "r", encoding="utf-8") as f:
            avatar_id2 = json.load(f)[artifactid]["icon"]
        for i in range(5):
            avatar_new = avatar_id2.replace("_4",f"_{i+1}")
            r = requests.get(f"https://static.nanoka.cc/assets/gi/{avatar_new}.webp")
            img_dir2 = os.path.join("..", "static", "BETA", "assets", "artifacts")
            img_path2 = os.path.join(img_dir2, f"{avatar_new}.webp")
            with open(img_path2, "wb") as f:
                f.write(r.content) # テキストではなくバイナリを書き込む
    """
            


    return str(added_weapon_list)+"\n"+str(added_character_list)+"\n"+str(added_artifact_list)


@app.route('/gachabase_get_data')
def gachabase_get_data():

    changelog_data = gachabase_changelog.main()

    
    chars = changelog_data["characters"]
    weapons = changelog_data["weapons"]
    artifacts = changelog_data["artifacts"]

    added_character_list = []
    for c in chars:
        if c["status"] == "new":
            added_character_list.append(str(c["id"]))

    added_weapon_list = []
    for c in weapons:
        if c["status"] == "new":
            added_weapon_list.append(str(c["id"]))

    added_artifact_list = []
    for c in artifacts:
        if c["status"] == "new":
            added_artifact_list.append(str(c["id"]))
    
    return str(added_weapon_list)+"\n"+str(added_character_list)+"\n"+str(added_artifact_list)

if __name__ == '__main__':
    app.run(debug=True, port=8001)