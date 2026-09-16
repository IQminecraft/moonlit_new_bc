import asyncio
import json
from enka import GenshinClient
import os

from app.paths import STATIC_DIR, BASE_DIR
from app.core.jsonio import write_json_atomic

try:
    import board_generator
    HAS_BOARD_GENERATOR = True
except ImportError:
    HAS_BOARD_GENERATOR = False

# ------------------------------------------------------------
#  サーバー分類（UID先頭 digit）
#  原神の UID は 9 桁で、先頭の数字がサーバーを表す:
#    1 / 2 = CN 天空島（中国公式サーバー）
#    5     = CN Bilibiliサーバー
#    6, 7  = America / 8 = Europe / 9 = Asia, TW, HK, MO
#  （enka ライブラリの CN_UID_PREFIXES = ("1", "2", "5") と同じ分類）
#  Enka.network は CN サーバーの UID を引けないため、CN の場合のみ
#  MicroGG API (Enka互換JSON) を使う。
# ------------------------------------------------------------
_CN_UID_PREFIXES = ("1", "2", "5")
_CN_API_URL = "https://profile.microgg.cn/gi/{}"


def is_cn_uid(uid) -> bool:
    """UID が中国サーバー（天空島 / Bilibili）のものかを判定する。

    CN は 9 桁で先頭が 1/2/5。10 桁の拡張 UID（例: 1812256644 = Asia サーバー）
    があるため桁数も見る（enka ライブラリの is_hsr_cn_uid と同じ対策）。
    """
    s = str(uid)
    return len(s) == 9 and s.startswith(_CN_UID_PREFIXES)


async def update_uid_data(uid: int):
    """
    UID のサーバーを分類し、CN なら MicroGG API、それ以外は Enka.network から
    ショーケースデータを取得して JSON に保存する。
    """
    if is_cn_uid(uid):
        return await _update_uid_data_cn(uid)

    async with GenshinClient(lang="ja") as client:
        try:
            data = await client.fetch_showcase(uid, raw=True)
        except Exception as e:
            save_dir = os.path.join(STATIC_DIR, "cache")
            json_filename = os.path.join(save_dir, f"showcase_{str(uid)}.json")
            if os.path.exists(json_filename):
                return True, "API error. Using cached data."
            return False, str(e)

        return _save_showcase(uid, data)


async def _update_uid_data_cn(uid: int):
    """CN サーバーUID: MicroGG API (https://profile.microgg.cn/gi/{uid}) から取得する。

    レスポンスは Enka 互換の JSON（playerInfo / avatarInfoList / showAvatarInfoList）。
    """
    try:
        import httpx  # enka の依存で導入済み
        async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "Mozilla/5.0"}) as client:
            res = await client.get(_CN_API_URL.format(uid))
            res.raise_for_status()
            raw = res.json()
    except Exception as e:
        save_dir = os.path.join(STATIC_DIR, "cache")
        json_filename = os.path.join(save_dir, f"showcase_{str(uid)}.json")
        if os.path.exists(json_filename):
            return True, "API error. Using cached data."
        return False, str(e)

    if not isinstance(raw, dict) or "playerInfo" not in raw:
        save_dir = os.path.join(STATIC_DIR, "cache")
        json_filename = os.path.join(save_dir, f"showcase_{str(uid)}.json")
        if os.path.exists(json_filename):
            return True, "API error. Using cached data."
        return False, "CN API returned unexpected response"

    return _save_showcase(uid, raw)


# 名前カード・プロフアイコンのマップ（enka assets から）
try:
    with open(os.path.join(BASE_DIR, 'external', 'enka_py', 'assets', 'namecards.json'), 'r', encoding='utf-8') as f:
        _NAMECARDS = json.load(f)
except Exception as e:
    print(f'[Info] namecards.json load failed: {e}')
    _NAMECARDS = {}

try:
    with open(os.path.join(BASE_DIR, 'external', 'enka_py', 'assets', 'pfps.json'), 'r', encoding='utf-8') as f:
        _PFPS = json.load(f)
except Exception as e:
    print(f'[Info] pfps.json load failed: {e}')
    _PFPS = {}

_NAMECARD_DIR = os.path.join(STATIC_DIR, 'assets', 'namecards')


def _get_namecard_icon_name(name_card_id):
    """nameCardId からネームカードのアイコン名 (UI_NameCardPic_xxx_P) を返す。"""
    entry = _NAMECARDS.get(str(name_card_id))
    if entry and entry.get('icon'):
        return entry['icon']
    return None


def _get_pfp_icon_name(profile_picture):
    """profilePicture からプロフアイコン名 (UI_AvatarIcon_xxx) を返す。"""
    if not profile_picture:
        return None
    # Costumes/Activity など id ベースのマップ
    for key in ('id', 'avatarId', 'profilePictureAvatarId'):
        v = profile_picture.get(key)
        if v is not None:
            entry = _PFPS.get(str(v))
            if entry and entry.get('iconPath'):
                return entry['iconPath'].replace('_Circle', '')
    return None


def _save_namecard_image(icon_name):
    """ネームカード画像を static/assets/namecards/ にキャッシュする（未所持時のみ DL）。"""
    if not icon_name:
        return None
    os.makedirs(_NAMECARD_DIR, exist_ok=True)
    path = os.path.join(_NAMECARD_DIR, f'{icon_name}.png')
    if os.path.exists(path):
        return path
    try:
        import requests
        url = f'https://enka.network/ui/{icon_name}.png'
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        with open(path, 'wb') as f:
            f.write(r.content)
        return path
    except Exception as e:
        print(f'[Info] namecard download failed {icon_name}: {e}')
        return None


def _save_showcase(uid: int, data):
    """取得済みデータをクリーンして cache/showcase_{uid}.json に保存する（Enka/CN 共通）。"""
    save_dir = os.path.join(STATIC_DIR, "cache")
    if not os.path.exists(save_dir):
        os.makedirs(save_dir)

    json_filename = os.path.join(save_dir, f"showcase_{str(uid)}.json")

    # 部分データガード: Enka/MicroGG が avatarInfoList 無しのレスポンス（詳細取得の
    # 一時的な失敗など）を返しても、既存の良いキャッシュを上書きして壊さない。
    # （壊れると char_list が空になり全機能が 400 になるため）
    if not (data.get("avatarInfoList") or []) and os.path.exists(json_filename):
        try:
            with open(json_filename, "r", encoding="utf-8") as f:
                old = json.load(f)
            if old.get("avatarInfoList"):
                return True, "API returned partial data. Keeping existing cache."
        except Exception:
            pass

    # Enka/MicroGG からのレスポンスは一切加工せず生データのまま保存する。
    # （旧 clean_showcase_data のキー削除は廃止。読み取り側が使わないキーが
    #   混ざっても動作に影響はないため）
    # + プロフアイコン・ネームカードの表示用パスを付与する
    # ネームカード・プロフアイコンIDを _meta に保存（ローカルDL済み画像と対応）
    player_info = data.get('playerInfo') or {}
    data['_meta'] = data.get('_meta') or {}
    if player_info.get('nameCardId'):
        data['_meta']['nameCardId'] = player_info['nameCardId']
    pp = player_info.get('profilePicture') or {}
    if pp.get('id'):
        data['_meta']['pfpId'] = pp['id']
    write_json_atomic(json_filename, data)

    if HAS_BOARD_GENERATOR:
        output_img = f"board_{uid}.png"
        try:
            board_generator.generate_board(json_filename, "template.png", output_img)
        except Exception:
            pass

    return True, "Success"

def clean_showcase_data(data):
    """互換用の no-op。かつては未使用キーを削除していたが、現在は生データを
    そのまま保存する方針のため何も加工しない。"""
    return data


def clean_showcase(file_path):
    if not os.path.exists(file_path):
        return

    with open(file_path, 'r', encoding='utf-8') as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError:
            return

    cleaned_data = clean_showcase_data(data)

    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(cleaned_data, f, indent=4, ensure_ascii=False)

if __name__ == "__main__":
    UID = 1812256644
    asyncio.run(update_uid_data(UID))
