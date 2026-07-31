from __future__ import annotations

import json
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from moonlit.config import TEMPLATES_DIR, CACHE_DIR
from moonlit.path import resolve_datas_path
from moonlit.showcase.enka import update_uid_data

router = APIRouter()
templates = Jinja2Templates(directory=TEMPLATES_DIR)


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("artifacter.html", {"request": request, "lang": "ja"})


@router.get("/fetch_uid", response_class=HTMLResponse)
async def fetch_uid(request: Request, uid: str, from_artifacter: bool = False, ver: str = "live"):
    if ver != "beta":
        ver = "live"

    if not uid.isdigit():
        return HTMLResponse(content="ユーザーUIDが不正です。数字のみ入力してください。", status_code=400)

    beta = "true" if ver == "beta" else "false"

    uid_int = int(uid)
    json_path = os.path.join("static", "cache", f"showcase_{uid}.json")

    if from_artifacter or not os.path.exists(json_path):
        success, message = await update_uid_data(uid_int)
        if not success:
            print(f"[Warning] API Fetch failed or warning: {message}")

    if not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail=f"UID: {uid} のデータが見つかりませんでした。")

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            showcase_data = json.load(f)
    except (UnicodeDecodeError, json.JSONDecodeError):
        with open(json_path, 'r', encoding='cp932') as f:
            showcase_data = json.load(f)

    player_info = showcase_data.get("playerInfo", {})
    show_avatar_list = player_info.get("showAvatarInfoList", [])

    from moonlit.showcase.normalize import normalize_avatar_id

    char_list = []
    for index, avatar in enumerate(show_avatar_list):
        current_avatar_id = str(avatar.get("avatarId"))
        if not current_avatar_id:
            continue

        current_avatar_id = normalize_avatar_id(current_avatar_id, avatar.get("energyType"))

        json_path_char = f"static/data/characters/{current_avatar_id}.json"
        json_path_char = resolve_datas_path(json_path_char, beta)

        if os.path.exists(json_path_char):
            try:
                with open(json_path_char, "r", encoding="utf-8") as f:
                    jsondata = json.load(f)
            except (UnicodeDecodeError, json.JSONDecodeError):
                with open(json_path_char, "r", encoding="cp932") as f:
                    jsondata = json.load(f)

            icon_suffix = str(jsondata["icon"])
            icon_path = resolve_datas_path(f"static/assets/characters/{icon_suffix}.webp", beta)
            char_entry = {
                "id": current_avatar_id,
                "icon": icon_path,
                "active": (index == 0)
            }
            char_list.append(char_entry)
        else:
            print(f"[Warning] キャラクターJSONが見つからないためスキップ: {json_path_char}")
            continue

    if not char_list:
        return HTMLResponse(
            content=f"UID: {uid} のゲーム内プロフィールで『キャラクター詳細を公開』がオンになっていないか、ショーケースが空です。",
            status_code=400,
        )

    return templates.TemplateResponse("build_card.html", {
        "request": request,
        "uid": uid,
        "char_list": char_list,
        "ver": ver
    })


@router.post("/refresh_uid/{uid}")
async def refresh_uid(uid: str):
    if not uid.isdigit():
        raise HTTPException(status_code=400, detail="UIDが不正です。数字のみ入力してください。")

    uid_int = int(uid)
    print(f"[Info] Refreshing showcase data via Enka API for UID: {uid}")
    success, message = await update_uid_data(uid_int)

    if not success:
        print(f"[Warning] Refresh failed: {message}")
        raise HTTPException(status_code=502, detail=f"Enka APIの取得に失敗しました: {message}")

    return {"success": True, "message": message}


@router.get("/serverup", response_class=HTMLResponse)
@router.post("/serverup", response_class=HTMLResponse)
@router.head("/serverup", response_class=HTMLResponse)
async def serverup(request: Request):
    return HTMLResponse(content="Success to access")