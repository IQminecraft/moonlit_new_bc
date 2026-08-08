import os
import json

# ==========================================================
#  特別枠キャラクター（旅人 / ドール）
#  これらの avatarId は元素ごとに「id-元素id」のキャラJSONを持つ
#  （例: 10000005-2.json = 炎）。Enka の showcase データには元素の
#  フィールドが無いため、skillLevelMap のスキルIDから元素を推定し、
#  元素→添字の対応は {id}-{n}.json の "element" フィールドを見て決める。
# ==========================================================
SPECIAL_ELEMENT_CHARACTERS = {"10000005", "10000007", "10000117", "10000118"}

# スキルID → 元素名。旅人は通常/元素スキル/元素爆発、
# ドールは元素ごとに異なる元素爆発（11175X）で判別できる。
_SPECIAL_SKILL_ELEMENT_MAP = {
    # 旅人 通常攻撃（10054X=男 / 10055X=女）
    "100540": "Anemo", "100550": "Anemo",  # 元素未変更（風と同じ技セット）
    "100541": "Pyro",  "100551": "Pyro",
    "100542": "Hydro", "100552": "Hydro",
    "100543": "Anemo", "100553": "Anemo",
    "100545": "Geo",   "100555": "Geo",
    "100546": "Electro", "100556": "Electro",
    "100547": "Dendro", "100557": "Dendro",
    # 旅人 元素スキル / 元素爆発
    "10067": "Anemo", "10068": "Anemo",
    "10097": "Pyro",  "10098": "Pyro",
    "10087": "Hydro", "10088": "Hydro",
    "10077": "Geo",   "10078": "Geo",
    "10602": "Electro", "10605": "Electro",
    "10117": "Dendro", "10118": "Dendro",
    # ドール 元素爆発（11175X）
    "111751": "Pyro",   # 元素未変更時もこれを使用
    "111752": "Hydro",
    "111753": "Electro",
    "111754": "Cryo",
    "111755": "Anemo",
    "111756": "Geo",
    "111757": "Dendro",
}

_SPECIAL_SUFFIX_CACHE = {}


def _get_special_element_suffix_map(raw_id, beta="false"):
    """{id}-{添字}.json の "element" フィールドを読み、{元素名: 添字} マップを作る。

    対応はハードコードせずJSONの中身から決めるため、betaディレクトリに
    追加された元素（例: 氷の -5）にも自動対応する。同じ添字はlive側の
    定義を優先し、beta側で上書きしない。
    """
    cache_key = (raw_id, beta)
    cached = _SPECIAL_SUFFIX_CACHE.get(cache_key)
    if cached is not None:
        return cached

    dirs = [os.path.join("static", "data", "characters")]
    if beta == "true":
        dirs.append(os.path.join("static", "beta", "data", "characters"))

    element_to_suffix = {}
    seen_suffixes = set()
    prefix = f"{raw_id}-"
    for char_dir in dirs:
        if not os.path.isdir(char_dir):
            continue
        for fn in os.listdir(char_dir):
            if not (fn.startswith(prefix) and fn.endswith(".json")):
                continue
            suffix = fn[len(prefix):-len(".json")]
            if suffix in seen_suffixes:
                continue
            try:
                with open(os.path.join(char_dir, fn), "r", encoding="utf-8") as f:
                    element = json.load(f).get("element")
            except Exception:
                element = None
            if element:
                seen_suffixes.add(suffix)
                element_to_suffix[element] = suffix

    _SPECIAL_SUFFIX_CACHE[cache_key] = element_to_suffix
    return element_to_suffix


def build_special_energy_hint_map(showcase_data):
    """showAvatarInfoList の energyType を {avatarId: energyType} で収集する。

    avatarInfoList 側には energyType が無いため、特別枠キャラの元素を
    両リストで一致させるためにこのヒントを使う。
    """
    player_info = showcase_data.get("playerInfo") or {}
    show_list = player_info.get("showAvatarInfoList") or player_info.get("show_avatar_info_list") or []
    hint_map = {}
    for entry in show_list:
        raw_id = entry.get("avatarId")
        energy_type = entry.get("energyType")
        if raw_id is not None and energy_type is not None:
            hint_map[str(raw_id)] = energy_type
    return hint_map


# ==========================================================
#  energyType → JSON添字
#  旅人(10000005/10000007) と ドール(10000117/10000118) で
#  JSON の添字体系が異なるため、別マップを使う。
#
#  Enka energyType: 1炎 2水 3草 4雷 5氷 7風 8岩
#
#  旅人 JSON添字: 2炎 3水 8草 7雷 5氷 4風 6岩
#  ドール JSON添字: 2炎 3水 8草 4雷 5氷 6風 7岩
# ==========================================================
_TRAVELER_CHARS = {"10000005", "10000007"}
_DOLL_CHARS = {"10000117", "10000118"}

_ENERGY_TYPE_TO_JSON_SUFFIX_TRAVELER = {
    1: "2",  # Pyro  炎
    2: "3",  # Hydro 水
    3: "8",  # Dendro 草
    4: "7",  # Electro 雷
    5: "5",  # Cryo  氷
    7: "4",  # Anemo 風
    8: "6",  # Geo   岩
}
_ENERGY_TYPE_TO_JSON_SUFFIX_DOLL = {
    1: "2",  # Pyro  炎
    2: "3",  # Hydro 水
    3: "8",  # Dendro 草
    4: "4",  # Electro 雷
    5: "5",  # Cryo  氷
    7: "6",  # Anemo 風
    8: "7",  # Geo   岩
}
_ELEMENT_TO_JSON_SUFFIX_TRAVELER = {
    "Pyro": "2", "Hydro": "3", "Dendro": "8", "Electro": "7",
    "Cryo": "5", "Anemo": "4", "Geo": "6",
}
_ELEMENT_TO_JSON_SUFFIX_DOLL = {
    "Pyro": "2", "Hydro": "3", "Dendro": "8", "Electro": "4",
    "Cryo": "5", "Anemo": "6", "Geo": "7",
}

# 命の星座を表示しない特別枠（ドール）
_NO_CONSTELLATION_CHARS = {"10000117", "10000118"}
# 好感度を表示しない特別枠（旅人 / ドール）
_NO_FRIENDSHIP_CHARS = {"10000005", "10000007", "10000117", "10000118"}

# fightPropMap の元素ダメバフID（enka_py の FightPropType 採番）
# 30=物理 40=炎 41=雷 42=水 43=草 44=風 45=岩 46=氷
# （50番台は元素耐性 SUB_HURT のためダメバフとしては参照しない）
_ELEMENT_DMG_BUFF_ID = {
    "Pyro": "40", "Electro": "41", "Hydro": "42", "Dendro": "43",
    "Anemo": "44", "Geo": "45", "Cryo": "46", "None": "30",
}


def _special_raw_id(avatar_id) -> str:
    return str(avatar_id).split("-")[0]


def _energy_suffix_map_for(raw_id: str) -> dict:
    if raw_id in _DOLL_CHARS:
        return _ENERGY_TYPE_TO_JSON_SUFFIX_DOLL
    return _ENERGY_TYPE_TO_JSON_SUFFIX_TRAVELER


def _element_suffix_map_for(raw_id: str) -> dict:
    if raw_id in _DOLL_CHARS:
        return _ELEMENT_TO_JSON_SUFFIX_DOLL
    return _ELEMENT_TO_JSON_SUFFIX_TRAVELER


def resolve_special_avatar_id(avatar, beta="false", energy_type_hint=None):
    """特別枠キャラの現在元素を showcase から推定し 'id-添字' を返す。

    energyType は Enka 値なので、キャラ種別ごとの JSON 添字へ変換する。
    ドール例: energyType=8(岩) → 添字 7 → 10000117-7.json
    旅人例: energyType=8(岩) → 添字 6 → 10000005-6.json
    """
    raw_id = str(avatar.get("avatarId"))
    if raw_id not in SPECIAL_ELEMENT_CHARACTERS:
        return raw_id

    energy_type = avatar.get("energyType")
    if energy_type is None:
        energy_type = energy_type_hint

    if energy_type is not None:
        try:
            et = int(energy_type)
        except (TypeError, ValueError):
            et = None
        suffix_map = _energy_suffix_map_for(raw_id)
        if et is not None and et in suffix_map:
            result = f"{raw_id}-{suffix_map[et]}"
            print(f"[SpecialResolve] {raw_id} energyType={et} → {result}")
            return result

    elements = [
        _SPECIAL_SKILL_ELEMENT_MAP[str(skill_id)]
        for skill_id in (avatar.get("skillLevelMap") or {})
        if str(skill_id) in _SPECIAL_SKILL_ELEMENT_MAP
    ]
    if elements:
        element = max(set(elements), key=elements.count)
        # JSON の element フィールドから添字を取る（無ければ固定表）
        suffix = _get_special_element_suffix_map(raw_id, beta).get(element)
        if suffix is None:
            suffix = _element_suffix_map_for(raw_id).get(element)
        if suffix is not None:
            result = f"{raw_id}-{suffix}"
            print(f"[SpecialResolve] {raw_id} skill→{element} → {result}")
            return result

    # フォールバック: 旅人は風(-4)、ドールも風だが添字が異なる(-6)
    fallback = "6" if raw_id in _DOLL_CHARS else "4"
    print(f"[SpecialResolve] {raw_id} fallback → {raw_id}-{fallback}")
    return f"{raw_id}-{fallback}"


def resolve_datas_path(path, beta="false"):
    if beta != "true" or not path or os.path.exists(path):
        return path
    candidates = []
    if "static/assets" in path or "static\\assets" in path:
        candidates.append(
            path.replace("static/assets", "static/beta/assets", 1)
                .replace("static\\assets", "static\\beta\\assets", 1)
        )
    elif "static/data" in path or "static\\data" in path:
        candidates.append(
            path.replace("static/data", "static/beta/data", 1)
                .replace("static\\data", "static\\beta\\data", 1)
        )
    for beta_path in candidates:
        if os.path.exists(beta_path):
            return beta_path
    return path


def resolve_list_path(path, beta="false"):
    if beta == "true" and path and ("static/data/lists" in path or "static\\data\\lists" in path):
        beta_path = (
            path.replace("static/data/lists", "static/beta/data/lists", 1)
                .replace("static\\data\\lists", "static\\beta\\data\\lists", 1)
        )
        if os.path.exists(beta_path):
            return beta_path
    return path


def resolve_costume_icon(chardatas: dict, avatar_info: dict) -> str:
    """展示データの costumeId に対応するコスチューム icon を返す（該当なしは ""）。"""
    try:
        costume_id = (avatar_info or {}).get("costumeId")
        if costume_id is None:
            return ""
        for c in chardatas.get("costume") or []:
            if isinstance(c, dict) and str(c.get("id")) == str(costume_id):
                return str(c.get("icon") or "")
    except Exception:
        pass
    return ""


def resolve_costume_splash(chardatas: dict, avatar_info: dict, beta="false") -> str:
    """costumeId 対応のコスチュームスプラッシュ（UI_Costume_*）の既存パスを返す。
    スプラッシュが無ければ既存のコスチュームアイコン（assets/characters）へフォールバック。
    どちらも無ければ ""（呼び出し側で通常スプラッシュにフォールバック）。"""
    try:
        costume_icon = resolve_costume_icon(chardatas, avatar_info)
        if not costume_icon:
            return ""
        splash_name = str(costume_icon).replace("AvatarIcon", "Costume")
        candidates = [
            f"static/assets/splash/{splash_name}.webp",
            f"static/assets/characters/{costume_icon}.webp",
        ]
        if beta == "true":
            # assets は live 優先、beta はフォールバック（resolve_datas_path と同順序）
            candidates.append(f"static/beta/assets/splash/{splash_name}.webp")
            candidates.append(f"static/beta/assets/characters/{costume_icon}.webp")
        for p in candidates:
            if os.path.exists(p):
                return p
    except Exception:
        pass
    return ""


def resolve_display_skill_levels(avatar_info, fake_char=False):
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
