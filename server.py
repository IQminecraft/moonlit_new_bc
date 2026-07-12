from fastapi import FastAPI, Request, Response, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import uvicorn
import os
import sys
import asyncio
import io
import json
from typing import Dict, Any

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from locale import normalize
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import get_info_state

app = FastAPI()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)
"""
FONT_PATH = os.path.join(BASE_DIR, "font_fixed.ttf")
FONT_LIGHT_PATH = os.path.join(BASE_DIR, "font-light.ttf")"""

FONT_PATH = os.path.join(BASE_DIR, "font_fixed.ttf")
FONT_LIGHT_PATH = os.path.join(BASE_DIR, "font_light.ttf")

try:
    with open(".enka_py/assets/text_map.json", "r", encoding="utf-8") as f:
        # text_map.json が直下にない場合は os.path.join(BASE_DIR, "text_map.json") などにしてください
        text_map_data = json.load(f)
except Exception as e:
    print(f"[Warning] text_map.json の読み込みに失敗: {e}")
    text_map_data = {}


def get_stat_japanese(append_prop_id: str) -> str:
    """
    appendPropId を日本語に変換する関数（手動のfallback_mapを最優先で適用）
    """
    # 💡 1. 【最優先】自分で設定したカスタム表記（上書き用マッピング）を真っ先に探す
    fallback_map = {
        "FIGHT_PROP_BASE_ATTACK": "基礎攻撃力",
        "FIGHT_PROP_CRITICAL": "会心率",
        "FIGHT_PROP_CRITICAL_HURT": "会心ダメージ",
        "FIGHT_PROP_CHARGE_EFFICIENCY": "チャージ効率",
        "FIGHT_PROP_ATTACK_PERCENT": "攻撃力%",
        "FIGHT_PROP_HP_PERCENT": "HP%",
        "FIGHT_PROP_DEFENSE_PERCENT": "防御力%",
        "FIGHT_PROP_ELEMENT_MASTERY": "熟知"
    }

    if append_prop_id in fallback_map:
        return fallback_map[append_prop_id]

    # 2. fallback_map に登録がないステータス（元素ダメージバフなど）だけ、text_map.json から探す
    if append_prop_id in text_map_data:
        return text_map_data[append_prop_id]

    if "ja" in text_map_data and append_prop_id in text_map_data["ja"]:
        return text_map_data["ja"][append_prop_id]

    # 3. どこにもなければ、元のIDをそのまま返す
    return append_prop_id


def formal_round(val):
    return int(val + 0.5) if val >= 0 else int(val - 0.5)


# --------------------------------------------------------------------
# 🧪 fake_char / fake_weapon 用: ステータス再計算ヘルパー
# --------------------------------------------------------------------
def new_stat_totals():
    """キャラ/武器/聖遺物からの補正値を貯めていく集計用の入れ物"""
    return {
        "hp_flat": 0.0, "hp_percent": 0.0,
        "atk_flat": 0.0, "atk_percent": 0.0,
        "def_flat": 0.0, "def_percent": 0.0,
        "em": 0.0, "crit_rate": 0.0, "crit_dmg": 0.0,
        "energy_recharge": 0.0,
        "dmg_bonus_by_element": {},  # 例: {"Pyro": 0.15, "物理": 0.10}
    }


def to_ratio_if_percent(prop_id, value):
    """
    Enkaの「flat(digest)」データ（武器のサブステ・聖遺物のメイン/サブステ）は、
    %系ステータスが人間向けの百分率でそのまま入っている（例: 46.6 → 46.6%）。
    一方 fightPropMap やキャラJSONの ascension は比率のまま（例: 0.466）。
    このヘルパーで %系のキーだけ ÷100 して比率に揃えてから合算できるようにする。
    """
    if value is None:
        return 0.0
    key_upper = str(prop_id).upper()
    if "PERCENT" in key_upper or "CRITICAL" in key_upper or "CHARGE" in key_upper or "HURT" in key_upper:
        return value / 100.0
    return value


def apply_stat_bonus(totals, prop_id, value):
    """
    (prop_id, value) 形式のステータス補正を totals に加算する共通ヘルパー。
    prop_id は Enka形式（大文字, 例: "FIGHT_PROP_CRITICAL_HURT"）でも
    静的JSON形式（小文字, 例: "fight_prop_critical_hurt"）でもOK。
    """
    if not prop_id or value in (None, ""):
        return
    key = str(prop_id).upper()

    if key == "FIGHT_PROP_HP":
        totals["hp_flat"] += value
    elif key == "FIGHT_PROP_HP_PERCENT":
        totals["hp_percent"] += value
    elif key in ("FIGHT_PROP_ATTACK", "FIGHT_PROP_BASE_ATTACK"):
        totals["atk_flat"] += value
    elif key == "FIGHT_PROP_ATTACK_PERCENT":
        totals["atk_percent"] += value
    elif key == "FIGHT_PROP_DEFENSE":
        totals["def_flat"] += value
    elif key == "FIGHT_PROP_DEFENSE_PERCENT":
        totals["def_percent"] += value
    elif key == "FIGHT_PROP_ELEMENT_MASTERY":
        totals["em"] += value
    elif key == "FIGHT_PROP_CRITICAL":
        totals["crit_rate"] += value
    elif key == "FIGHT_PROP_CRITICAL_HURT":
        totals["crit_dmg"] += value
    elif key == "FIGHT_PROP_CHARGE_EFFICIENCY":
        totals["energy_recharge"] += value
    elif key.endswith("_DMG") or key.endswith("_ADD_HURT") or "DMG_BONUS" in key:
        # ⚠️ 元素/物理ダメージ%系。実データのキー名が違う場合はここを調整してください
        elem = None
        if "PYRO" in key or "FIRE" in key: elem = "Pyro"
        elif "HYDRO" in key or "WATER" in key: elem = "Hydro"
        elif "ANEMO" in key or "WIND" in key: elem = "Anemo"
        elif "ELECTRO" in key or "ELEC" in key: elem = "Electro"
        elif "DENDRO" in key or "GRASS" in key: elem = "Dendro"
        elif "CRYO" in key or "ICE" in key: elem = "Cryo"
        elif "GEO" in key or "ROCK" in key: elem = "Geo"
        elif "PHYSICAL" in key: elem = "物理"
        if elem:
            totals["dmg_bonus_by_element"][elem] = totals["dmg_bonus_by_element"].get(elem, 0.0) + value


# --------------------------------------------------------------------
# Figma互換 描画関数システム (存在しないファイルを表示する警告機能付き)
# --------------------------------------------------------------------
def draw_figma_text_right(draw, text, x, y, font, font_size=24, fill_color=(255, 255, 255), **kwargs):
    text_str = str(text)
    # 渡されたx座標を「右端の絶対的な壁」にして描画する
    draw.text((x, y), text_str, fill=fill_color, font=font, anchor="ra")


def draw_figma_box(img, x, y, width, height, radius=15, fill_color=(60, 64, 72, 180)):
    x1, y1 = x, y
    x2, y2 = x + width, y + height

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)
    draw_overlay.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=fill_color)

    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))


def draw_figma_text(draw, text, x, y, font, font_size=None, fill_color=(255, 255, 255), align="left", box_width=None):
    text_str = str(text)
    actual_font = font

    if font_size is not None:
        if isinstance(font, str):
            if os.path.exists(font):
                try:
                    actual_font = ImageFont.truetype(font, font_size)
                except Exception:
                    actual_font = ImageFont.load_default()
            else:
                print(f"[Warning] Font file not found: {font}")
                actual_font = ImageFont.load_default()
        elif hasattr(font, "path") and font.path:
            if os.path.exists(font.path):
                try:
                    actual_font = ImageFont.truetype(font.path, font_size)
                except Exception:
                    actual_font = font
            else:
                print(f"[Warning] Font path not found: {font.path}")
                actual_font = font
        else:
            if not isinstance(font, ImageFont.FreeTypeFont):
                actual_font = ImageFont.load_default()

    if align == "left":
        actual_x = x
    elif align == "right" and box_width:
        text_width = draw.textlength(text_str, font=actual_font)
        actual_x = x + box_width - text_width
    else:
        actual_x = x

    draw.text((actual_x, y), text_str, font=actual_font, fill=fill_color)


def draw_figma_text_with_shadow(draw, text, x, y, font, font_size=None, fill_color=(255, 255, 255), shadow_color=(0, 0, 0, 200), shadow_offset=(1.5, 1.5), align="left", box_width=None):
    text_str = str(text)
    actual_font = font

    if font_size:
        if hasattr(font, "path") and font.path:
            if os.path.exists(font.path):
                try:
                    actual_font = ImageFont.truetype(font.path, font_size)
                except Exception:
                    actual_font = font
            else:
                print(f"[Warning] Font path not found in shadow text: {font.path}")
                actual_font = font
        elif isinstance(font, str):
            if os.path.exists(font):
                try:
                    actual_font = ImageFont.truetype(font, font_size)
                except Exception:
                    actual_font = ImageFont.load_default()
            else:
                print(f"[Warning] Font file not found in shadow text: {font}")
                actual_font = ImageFont.load_default()
        else:
            if not isinstance(font, ImageFont.FreeTypeFont):
                actual_font = ImageFont.load_default()

    if align == "left":
        target_x = x
    elif align == "right" and box_width:
        text_width = draw.textlength(text_str, font=actual_font)
        target_x = x + box_width - text_width
    elif align == "center" and box_width:
        # 💡 中央揃えの計算を追加！
        # 「枠の左端(x) + 残りスペースの半分」の位置を文字の開始位置にします
        text_width = draw.textlength(text_str, font=actual_font)
        target_x = x + (box_width - text_width) / 2
    else:
        target_x = x

    try:
        left, top, right, bottom = draw.textbbox((0, 0), text_str, font=actual_font)
        text_w = right - left
        text_h = bottom - top
    except Exception:
        text_w = int(draw.textlength(text_str, font=actual_font))
        text_h = font_size if font_size else 40
        left, top = 0, 0

    pad = 60
    shadow_layer = Image.new("RGBA", (text_w + pad * 2, text_h + pad * 2), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(shadow_layer)

    sx = pad - left
    sy = pad - top
    s_draw.text((sx, sy), text_str, font=actual_font, fill=(0, 0, 0, 255))

    blurred_shadow = shadow_layer.filter(ImageFilter.GaussianBlur(3.0))

    r, g, b = shadow_color[0], shadow_color[1], shadow_color[2]
    thick_alpha = 270

    alpha_mask = blurred_shadow.split()[3].point(lambda p: int(p * (thick_alpha / 255.0)))
    final_shadow_piece = Image.new("RGBA", blurred_shadow.size, (r, g, b, 255))
    blurred_shadow = Image.composite(final_shadow_piece, Image.new("RGBA", blurred_shadow.size, (0, 0, 0, 0)), alpha_mask)

    ox, oy = shadow_offset[0], shadow_offset[1]
    paste_x = int(target_x - sx + ox)
    paste_y = int(y - sy + oy)

    draw._image.paste(blurred_shadow, (paste_x, paste_y), blurred_shadow)
    draw.text((target_x, y), text_str, font=actual_font, fill=fill_color)


def draw_figma_line(img, x1, y1, x2, y2, fill_color=(255, 255, 255, 50), width=1):
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)
    draw_overlay.line([(x1, y1), (x2, y2)], fill=fill_color, width=width)

    if img.mode != "RGBA":
        rgba_base = img.convert("RGBA")
        rgba_base.paste(overlay, (0, 0), overlay)
        img.paste(rgba_base.convert("RGB"))
    else:
        img.paste(overlay, (0, 0), overlay)


def draw_figma_circle(img, x, y, size, fill_color=(60, 64, 72, 180), outline_color=None, outline_width=0):
    x1, y1 = x, y
    x2, y2 = x + size, y + size

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_overlay = ImageDraw.Draw(overlay)

    # 💡 引数の指定に合わせて ellipse に outline と width を渡す
    # outline_color が None の場合は、Pillow の仕様で枠線は描画されません
    draw_overlay.ellipse(
        [x1, y1, x2, y2],
        fill=fill_color,
        outline=outline_color,
        width=outline_width
    )

    img.paste(Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB"))


def resolve_datas_path(path, beta="false"):
    """
    beta が "true" のとき、path (static/datas/... 配下) が存在しなければ
    static/BETA/... 配下も探索し、そちらが存在すればそのパスを返す。
    画像・JSONどちらのパス探索にも共通で使える汎用ヘルパー。
    ※ static/datas/lists/ 配下（リストファイル）はここでは扱わない。
       リストファイルは resolve_list_path を使うこと。
    """
    if beta == "true" and path and "static/datas" in path and not os.path.exists(path):
        beta_path = path.replace("static/datas", "static/BETA", 1)
        if os.path.exists(beta_path):
            return beta_path
    return path


def resolve_list_path(path, beta="false"):
    """
    static/datas/lists/... を参照するファイル専用のリゾルバ。
    beta が "true" の場合、static/datas 側に存在するかどうかに関わらず
    static/BETA/lists/... を優先して使用する（存在すれば問答無用でそちら）。
    static/BETA 側に無ければ通常の static/datas パスにフォールバックする。
    """
    if beta == "true" and path and "static/datas/lists" in path:
        beta_path = path.replace("static/datas/lists", "static/BETA/lists", 1)
        if os.path.exists(beta_path):
            return beta_path
    return path


def paste_figma_image(base_img, img_path, box_x, box_y, box_width, box_height, radius=15, beta="false"):
    img_path = resolve_datas_path(img_path, beta)
    if not img_path or not os.path.exists(img_path):
        return

    try:
        paste_img = Image.open(img_path).convert("RGBA")
        paste_img = paste_img.resize((box_width, box_height), Image.Resampling.LANCZOS)

        overlay = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
        mask = Image.new("L", (box_width, box_height), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle([0, 0, box_width, box_height], radius=radius, fill=255)

        overlay.paste(paste_img, (box_x, box_y), mask)
        base_img.paste(Image.alpha_composite(base_img.convert("RGBA"), overlay).convert("RGB"))
    except Exception as e:
        print(f"[Error] Failed to paste image: {img_path}. Reason: {e}")


def paste_mask_image(base_img, img_path, box_x, box_y, box_width, box_height, radius=15, zoom=1.0, beta="false"):
    img_path = resolve_datas_path(img_path, beta)
    if not img_path or not os.path.exists(img_path):

        return

    try:
        paste_img = Image.open(img_path).convert("RGBA")
        orig_w, orig_h = paste_img.size

        new_height = int(box_height * zoom)
        new_width = int(orig_w * (new_height / orig_h))

        paste_img = paste_img.resize((new_width, new_height), Image.Resampling.LANCZOS)

        overlay = Image.new("RGBA", base_img.size, (0, 0, 0, 0))
        canvas = Image.new("RGBA", (box_width, box_height), (0, 0, 0, 0))

        offset_x = (box_width - new_width) // 2
        offset_y = (box_height - new_height) // 2

        canvas.paste(paste_img, (offset_x, offset_y), paste_img)

        mask = Image.new("L", (box_width, box_height), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle([0, 0, box_width, box_height], radius=radius, fill=255)

        overlay.paste(canvas, (box_x, box_y), mask)
        base_img.paste(Image.alpha_composite(base_img.convert("RGBA"), overlay).convert("RGB"))
    except Exception as e:
        print(f"[Error] Failed to paste mask image: {img_path}. Reason: {e}")


def score_calc(stat, critrate, critdmg, method):
    scores = critrate * 2 + critdmg
    if method == "em":
        scores += stat * 0.25
    else:
        scores += stat
    return scores


# --------------------------------------------------------------------
# 1. トップページ（UID入力画面）
# --------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("artifacter.html", {"request": request, "lang": "ja"})


# --------------------------------------------------------------------
# 2. UIDを受け取ってキャラクター一覧画面（build_card.html）を表示する
# --------------------------------------------------------------------
@app.get("/fetch_uid", response_class=HTMLResponse)
async def fetch_uid(request: Request, uid: str, from_artifacter: bool = False, ver: str = "live"):
    print(f"[Info] Fetching characters via API for UID: {uid} (from_artifacter: {from_artifacter})")
    if ver != "beta":
        ver = "live"

    if not uid.isdigit():
        return HTMLResponse(content="ユーザーUIDが不正です。数字のみ入力してください。", status_code=400)

    uid_int = int(uid)
    json_path = os.path.join("static", "datas", "cache", f"showcase_{uid}.json")

    # 💡 1. artifacterから直接来た場合、またはキャッシュファイルがまだ存在しない場合のみAPI取得を行う
    if from_artifacter or not os.path.exists(json_path):
        success, message = await get_info_state.update_uid_data(uid_int)
        if not success:
            print(f"[Warning] API Fetch failed or warning: {message}")

    if not os.path.exists(json_path):
        raise HTTPException(status_code=404, detail=f"UID: {uid} のデータが見つかりませんでした。(APIエラーかつキャッシュなし)")

    # 多段エンコード読み込みで文字化け・JSONデコードエラーを防ぐ安全仕様
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            showcase_data = json.load(f)
    except (UnicodeDecodeError, json.JSONDecodeError):
        with open(json_path, 'r', encoding='cp932') as f:
            showcase_data = json.load(f)

    # 3. playerInfo -> showAvatarInfoList からキャラクターIDとレベル等を取り出す
    player_info = showcase_data.get("playerInfo", {})
    show_avatar_list = player_info.get("showAvatarInfoList", [])

    char_list = []
    for index, avatar in enumerate(show_avatar_list):
        current_avatar_id = str(avatar.get("avatarId"))
        if not current_avatar_id:
            continue

        # 主人公の4つのIDの時は後ろに -(そのキャラのenergyType) を追加する
        if current_avatar_id in ["10000005", "10000007", "10000117", "10000118"]:
            energy_type = avatar.get("energyType")
            if energy_type is not None:
                current_avatar_id = f"{current_avatar_id}-{energy_type}"
            else:
                current_avatar_id = f"{current_avatar_id}-4"

        json_path_char = f"static/datas/characters/{current_avatar_id}.json"

        if os.path.exists(json_path_char):
            try:
                with open(json_path_char, "r", encoding="utf-8") as f:
                    jsondata = json.load(f)
            except (UnicodeDecodeError, json.JSONDecodeError):
                with open(json_path_char, "r", encoding="cp932") as f:
                    jsondata = json.load(f)

            icon_suffix = str(jsondata["icon"])
            char_entry = {
                "id": current_avatar_id,  # ハイフン付きのID
                "icon": f"static/datas/assets/characters/{icon_suffix}.webp",
                "active": (index == 0)
            }
            char_list.append(char_entry)
        else:
            print(f"[Warning] キャラクターJSONが見つからないためスキップ: {json_path_char}")
            continue

    if not char_list:
        return HTMLResponse(content=f"UID: {uid} のゲーム内プロフィールで『キャラクター詳細を公開』がオンになっていないか、ショーケースが空です。", status_code=400)

    # build_card.htmlにデータを送り込んでレンダリング
    return templates.TemplateResponse("build_card.html", {
        "request": request,
        "uid": uid,
        "char_list": char_list,
        "ver": ver
    })


# --------------------------------------------------------------------
# 2.5. build_card.html の「再取得 + 再生成」ボタン用: Enka APIを叩き直してキャッシュを更新する
# --------------------------------------------------------------------
@app.post("/refresh_uid/{uid}")
async def refresh_uid(uid: str):
    if not uid.isdigit():
        raise HTTPException(status_code=400, detail="UIDが不正です。数字のみ入力してください。")

    uid_int = int(uid)
    print(f"[Info] Refreshing showcase data via Enka API for UID: {uid}")
    success, message = await get_info_state.update_uid_data(uid_int)

    if not success:
        print(f"[Warning] Refresh failed: {message}")
        raise HTTPException(status_code=502, detail=f"Enka APIの取得に失敗しました: {message}")

    return {"success": True, "message": message}



# --------------------------------------------------------------------
# 3. PIL ビルドカード画像生成エンドポイント
# --------------------------------------------------------------------
@app.get("/generate_card_image/{uid}/{avatar_id}/{calc_method}")
async def generate_card_image(uid: str, avatar_id: str, calc_method: str, fake_char: str = None, fake_weapon: str = None, beta: str = "false"):
    if beta != "true":
        beta = "false"
    cache_key = f"{uid}_{avatar_id}_{calc_method}"
    print(f"[Cache Miss] 初回生成のため、PILで気合を入れて画像を作ります...: UID:{uid} - CharID:{avatar_id} - Method:{calc_method}")

    # ================================================================
    # ① 取得 / 変数定義（データ収集・計算ロジック）
    #    ここではPILへの描画は一切行わず、必要な値をすべて揃えるだけ。
    # ================================================================

    # --- 1-1. Showcase JSONの読み込みと対象キャラの特定 ---

    target_avatar_info = None
    json_path = os.path.join("static", "datas", "cache", f"showcase_{uid}.json")
    json_path = resolve_datas_path(json_path, beta)

    if os.path.exists(json_path):
        # 多段エンコード読み込みでJSONデコードエラーを防止
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                showcase_data = json.load(f)
        except (UnicodeDecodeError, json.JSONDecodeError):
            with open(json_path, "r", encoding="cp932") as f:
                showcase_data = json.load(f)

        avatar_list = showcase_data.get("avatarInfoList")
        if not avatar_list and "playerInfo" in showcase_data:
            player_info = showcase_data["playerInfo"]
            avatar_list = player_info.get("showAvatarInfoList") or player_info.get("show_avatar_info_list")

        # 引数の avatar_id（例: "10000125" や "10000118-4"）に対応する生データを特定する
        if avatar_list:
            for avatar in avatar_list:
                raw_id = str(avatar.get("avatarId"))

                # 比較用のIDを生成する
                loop_avatar_id = raw_id
                if raw_id in ["10000005", "10000007", "10000117", "10000118"]:
                    energy_type = avatar.get("energyType")
                    if energy_type is not None:
                        loop_avatar_id = f"{raw_id}-{energy_type}"
                    else:
                        loop_avatar_id = f"{raw_id}-4"

                # 💡 両方を確実に str にして比較する（これで通常キャラも旅人も100%マッチします！）
                if str(loop_avatar_id) == str(avatar_id):
                    target_avatar_info = avatar
                    break

    # 該当キャラがshowcaseに見つからなかったら404エラーにする
    if not target_avatar_info:
        raise HTTPException(status_code=404, detail=f"Avatar ID {avatar_id} not found in showcase.")

    # --- 1-2. キャラクター固有JSONの読み込み ---
    print(f"[Debug] 読み込もうとしているファイル名: {avatar_id}.json")

    if fake_char:
        json_path2 = os.path.join("static", "datas", "characters", f"{fake_char}.json") #fake_chair
        if not os.path.exists(json_path2):
            if beta == "true":
                json_path2 = os.path.join("static", "BETA", "characters", f"{fake_char}.json") #fake_chair
    else:
        json_path2 = os.path.join("static", "datas", "characters", f"{avatar_id}.json")
    json_path2 = resolve_datas_path(json_path2, beta)

    if os.path.exists(json_path2):
        try:
            with open(json_path2, "r", encoding="utf-8") as f:
                chardatas = json.load(f)
        except (UnicodeDecodeError, json.JSONDecodeError):
            with open(json_path2, "r", encoding="cp932") as f:
                chardatas = json.load(f)
    else:
        # 💡 万が一旅人のファイル名がズレていた場合、即落ちせずに通常のID（ハイフンなし）を試す最後の砦
        base_avatar_id = str(avatar_id).split("-")[0]
        backup_path = os.path.join("static", "datas", "characters", f"{base_avatar_id}.json")
        backup_path = resolve_datas_path(backup_path, beta)
        if os.path.exists(backup_path):
            try:
                with open(backup_path, "r", encoding="utf-8") as f:
                    chardatas = json.load(f)
            except (UnicodeDecodeError, json.JSONDecodeError):
                with open(backup_path, "r", encoding="cp932") as f:
                    chardatas = json.load(f)
        else:
            raise HTTPException(status_code=404, detail=f"Character JSON file not found: {json_path2}")

    # --- 1-3. カードサイズ・元素カラー判定 ---
    card_width = 1741
    card_height = 1159

    # chardatas や元のデータから元素（Element）の文字列を取得します
    # ※お使いの chardatas の構造に合わせて調整してください。もし無ければ target_avatar_info の energyType などでも判定可能です。
    element_type = chardatas.get("element", "None")  # 例: "Pyr", "Hyd", "Anemo", "Elec", "Grass", "Cryo", "Rock"

    # 元素名とカラー（RGBA）のマッピング
    element_colors = {
        "Pyro": (240, 140, 140, 150),      # 🔴 炎: 淡いコーラルピンク
        "Hydro": (150, 195, 245, 150),     # 🔵 水: マイルドなスカイブルー
        "Anemo": (150, 230, 205, 150),     # 🟢 風: 淡いミントグリーン
        "Electro": (215, 175, 245, 150),    # 🟣 雷: 淡いラベンダー
        "Dendro": (190, 235, 150, 150),     # 🌿 草: 淡いパステルグリーン
        "Cryo": (195, 235, 245, 150),      # ❄️ 氷: 白っぽいペールブルー
        "Geo": (240, 215, 150, 150),        # 🟡 岩: 優しく淡いシフォンゴールド
        "None": (255, 255, 255, 255),
    }
    element_ja_map = {
        "Pyro": "炎元素",
        "Hydro": "水元素",
        "Anemo": "風元素",     # 🟢 風: 淡いミントグリーン
        "Electro": "雷元素",    # 🟣 雷: 淡いラベンダー
        "Dendro": "草元素",     # 🌿 草: 淡いパステルグリーン
        "Cryo": "氷元素",      # ❄️ 氷: 白っぽいペールブルー
        "Geo": "岩元素",
    }
    element_ja = element_ja_map.get(element_type, "なし")

    # 一致する元素がなかった場合のデフォルト色（今までの色など）
    base_color = element_colors.get(element_type, (121, 169, 239, 255))

    # --- 1-4. スプラッシュ画像パス ---
    splash = f"static/datas/assets/splash/{chardatas['icon'].replace('AvatarIcon', 'Gacha_AvatarImg')}.webp"
    splash = resolve_datas_path(splash, beta)

    # --- 1-5. テキスト情報（名前・レベル・好感度） ---
    char_name = chardatas["name"]

    # 💡 特定した target_avatar_info からキャラレベルを取得
    if fake_char:
        char_level = 90
    else:
        char_level = target_avatar_info.get('propMap', {}).get('4001', {}).get('val', 1)

    # 💡 好感度レベルを正しく取得
    if fake_char:
        friendship_lv = 10
    else:
        friendship_lv = target_avatar_info.get("fetterInfo", {}).get("expLevel", 1)

    # --- 1-6. 天賦スキル ---
    skill_map = target_avatar_info.get("skillLevelMap", {})
    skill_values = list(skill_map.values())
    if fake_char:
        normal, skill, burst = 9, 9, 9
    else:
        if len(skill_values) >= 3:
            normal, skill, burst = skill_values[0], skill_values[1], skill_values[2]
        else:
            normal, skill, burst = 1, 1, 1

    skill_level = [normal, skill, burst]
    skill_icon = [chardatas["skills"][0]["icon"], chardatas["skills"][1]["icon"], chardatas["skills"][2]["icon"]]

    # --- 1-7. 命ノ星座 ---
    y_C_base = 139  # 元の基準Y座標
    circle_size = 68

    # talentIdList の長さから凸数（0〜6）を取得
    if fake_char:
        constellation_releas_num = 0
    else:
        constellation_releas_num = len(target_avatar_info.get("talentIdList", []))

    # 先に6箇所分すべての星座アイコンファイル名を取得
    Constellation_icon = []
    for i in range(6):
        Constellation_icon.append(chardatas["constellations"][i]["icon"])

    # --- 1-8. 武器情報 ---
    # target_avatar_info["equipList"] から "weapon" キーを持っている辞書を1つだけ釣り上げる
    weapon_data = next((item for item in target_avatar_info.get("equipList", []) if "weapon" in item), None)

    # 武器の基本情報を取り出す
    if fake_weapon:
        weapon_id = fake_weapon
        weapon_json_path = resolve_datas_path(f"static/datas/weapons/{weapon_id}.json", beta)
        with open(weapon_json_path, "r", encoding="utf-8") as f:
            weapon_jsondata = json.load(f)
        weapon_level = 90
        weapon_affix = 1
        weapon_icon = weapon_jsondata["icon"]
        weapon_name = weapon_jsondata["name"]
        keys_list = list(weapon_jsondata["stats_modifier"].keys())
        try:
            second_key = keys_list[1]
        except:
            second_key = None
        stat_calc = weapon_jsondata["stats_modifier"][second_key]
        if stat_calc < 1:
            stat_calc = round(stat_calc*100,1)
        else:
            stat_calc = round(stat_calc)
        print(stat_calc) 
        weapon_stats_list = [
            {'appendPropId': 'FIGHT_PROP_BASE_ATTACK', 'statValue': weapon_jsondata["stats_modifier"]["atk"]}, 
            {'appendPropId': second_key.upper(), 'statValue': stat_calc}
        ]
        print()
    else:
        weapon_id = weapon_data["itemId"]
        weapon_json_path = resolve_datas_path(f"static/datas/weapons/{weapon_id}.json", beta)
        with open(weapon_json_path, "r", encoding="utf-8") as f:
            weapon_jsondata = json.load(f)
        weapon_icon = weapon_data["flat"]["icon"]
        weapon_level = weapon_data["weapon"]["level"]
        weapon_affix = list(weapon_data["weapon"].get("affixMap", {}).values())[0] + 1 if weapon_data["weapon"].get("affixMap") else 1
        weapon_name = weapon_jsondata.get("name", "未知の武器")
        weapon_stats_list = weapon_data["flat"].get("weaponStats", [])

    # 武器のサブステータス（最大2つ）を整形しておく

    weapon_stat1 = None
    if len(weapon_stats_list) >= 1:
        prop_id1 = weapon_stats_list[0]["appendPropId"]
        stat_name1 = get_stat_japanese(prop_id1)  # 日本語名に変換
        stat_val1 = weapon_stats_list[0]["statValue"]

        if "PERCENT" in prop_id1 or "CRITICAL" in prop_id1 or "CHARGE" in prop_id1:
            stat_val1_str = f"{stat_val1}%"
        else:
            stat_val1_str = f"{int(stat_val1)}"

        weapon_stat1 = (stat_name1, stat_val1_str)

    weapon_stat2 = None
    if len(weapon_stats_list) == 2:
        prop_id2 = weapon_stats_list[1]["appendPropId"]
        stat_name2 = get_stat_japanese(prop_id2)  # 日本語名に変換
        stat_val2 = weapon_stats_list[1]["statValue"]

        if "PERCENT" in prop_id2 or "CRITICAL" in prop_id2 or "CHARGE" in prop_id2 or "HURT" in prop_id2:
            stat_val2_str = f"{stat_val2}%"
        else:
            stat_val2_str = f"{int(stat_val2)}"

        weapon_stat2 = (stat_name2, stat_val2_str)

    # 💡 fakeモードのステータス再計算でも使うため、聖遺物の生データはここで先に抽出しておく
    raw_artifacts = [item for item in target_avatar_info.get("equipList", []) if "reliquary" in item]

    # --- 1-9. ステータス詳細一覧（HP・攻撃力・防御力など） ---
    # 💡 元素名に対応する正確な「fightPropMap」の文字列IDをマッピング
    if fake_char or fake_weapon:
        # ============================================================
        # 🧪 fakeモード: キャラ/武器/聖遺物の生データから最終ステータスを再計算する
        #   ・fake_char あり        → キャラJSON(chardatas)自身の基礎値を使用
        #   ・fake_char なし(武器のみ) → 実キャラのEnka初期ステ(base HP/ATK/DEF)+汎用初期値を使用
        #   ・武器は weapon_stats_list（実武器 or fake武器、どちらも同じ形式）を使用
        #   ・聖遺物は raw_artifacts（実データ）を使用
        # ============================================================
        if fake_char:
            # 💡 キャラJSONのスキーマが2種類あるため両対応する
            #   ・旧形式(ベータ等): hp/atk/def がトップレベル
            #   ・新形式(製品版)  : hp/atk/def が stats_modifier の中
            char_stats_mod = chardatas.get("stats_modifier", {}) or {}
            base_hp = chardatas.get("hp", char_stats_mod.get("hp", 1))
            base_atk = chardatas.get("atk", char_stats_mod.get("atk", 1))
            base_def = chardatas.get("def", char_stats_mod.get("def", 1))
            base_crit_rate = chardatas.get("crit_rate", 0.05)
            base_crit_dmg = chardatas.get("crit_dmg", 0.5)
            base_em = chardatas.get("elemental_mastery", 0.0)
        else:
            # 実キャラの「初期ステ」＝Enkaのbase系ID（武器・聖遺物補正を含まない値）
            base_hp = target_avatar_info.get('fightPropMap', {}).get('1', 1)
            base_atk = target_avatar_info.get('fightPropMap', {}).get('4', 1)
            base_def = target_avatar_info.get('fightPropMap', {}).get('7', 1)
            base_crit_rate = 0.05
            base_crit_dmg = 0.5
            base_em = 0.0
        base_er = 1.0  # 元素チャージ効率の基礎値（100%）は全キャラ共通

        stat_totals = new_stat_totals()

        # キャラクター自身の隠しステータス（突破ボーナスなど）を加算
        # ※ fake_char が無い場合も chardatas は実キャラ自身の静的JSONなので、そのまま使えます
        # 💡 キャラJSONのスキーマが2種類あるため両対応する
        #   ・旧形式(ベータ等): stats_modifier.ascension が [{"fight_prop_xxx": val}, ...] のリスト
        #   ・新形式(製品版)  : stats_modifier.extra が {"fight_prop_xxx": val} の単一辞書
        char_stats_mod_for_bonus = chardatas.get("stats_modifier", {}) or {}

        extra_bonus = char_stats_mod_for_bonus.get("extra")
        if isinstance(extra_bonus, dict):
            for asc_key, asc_val in extra_bonus.items():
                apply_stat_bonus(stat_totals, asc_key, asc_val)
        elif isinstance(extra_bonus, list):
            for asc_entry in extra_bonus:
                for asc_key, asc_val in asc_entry.items():
                    apply_stat_bonus(stat_totals, asc_key, asc_val)

        for asc_entry in char_stats_mod_for_bonus.get("ascension", []):
            for asc_key, asc_val in asc_entry.items():
                apply_stat_bonus(stat_totals, asc_key, asc_val)

        # 武器のステータスを加算（基礎攻撃力だけは特別扱い、サブステはtotalsへ）
        # ⚠️ weapon_stats_list の%系ステータス（会心率・会心ダメ・チャージ効率など）は
        #    表示用に「100倍した値」(例: 61.3 → 61.3%)で入っているため、計算に使う前に
        #    ÷100 して比率(0.613)に戻してから加算する（実武器・fake武器どちらも同じ形式）
        weapon_base_atk = 0.0
        for w_entry in weapon_stats_list:
            w_prop_id = w_entry.get("appendPropId", "")
            w_val = w_entry.get("statValue", 0.0)
            if w_prop_id.upper() in ("FIGHT_PROP_BASE_ATTACK", "FIGHT_PROP_ATTACK"):
                weapon_base_atk = w_val
            else:
                apply_stat_bonus(stat_totals, w_prop_id, to_ratio_if_percent(w_prop_id, w_val))

        # 聖遺物（メイン・サブ両方）のステータスを加算
        # ※ 武器と同様、reliquaryMainstat/reliquarySubstats の%系ステータスも
        #   百分率のまま入っているため to_ratio_if_percent() で比率に揃える
        for art_raw in raw_artifacts:
            art_flat = art_raw.get("flat", {})
            art_main = art_flat.get("reliquaryMainstat", {})
            art_main_id = art_main.get("mainPropId", "")
            apply_stat_bonus(stat_totals, art_main_id, to_ratio_if_percent(art_main_id, art_main.get("statValue", 0.0)))
            for art_sub in art_flat.get("reliquarySubstats", []):
                art_sub_id = art_sub.get("appendPropId", "")
                apply_stat_bonus(stat_totals, art_sub_id, to_ratio_if_percent(art_sub_id, art_sub.get("statValue", 0.0)))

        total_hp = base_hp * (1 + stat_totals["hp_percent"]) + stat_totals["hp_flat"]
        total_atk = (base_atk + weapon_base_atk) * (1 + stat_totals["atk_percent"]) + stat_totals["atk_flat"]
        total_def = base_def * (1 + stat_totals["def_percent"]) + stat_totals["def_flat"]
        total_em = base_em + stat_totals["em"]
        total_crit_rate = base_crit_rate + stat_totals["crit_rate"]
        total_crit_dmg = base_crit_dmg + stat_totals["crit_dmg"]
        total_er = base_er + stat_totals["energy_recharge"]

        # 元素/物理ダメージ%は、見つかった中で一番大きいものを採用（既存の実キャラ表示と同じ考え方）
# キャラの元素タイプを取得
        element_type = chardatas.get("element", "None")

        # 元素タイプとバフキーのマッピング（stat_totalsのキーと一致させる）
        element_buff_key_mapping = {
            "Pyro": "Pyro",
            "Hydro": "Hydro",
            "Anemo": "Anemo",
            "Electro": "Electro",
            "Dendro": "Dendro",
            "Geo": "Geo",
            "Cryo": "Cryo",
            # 物理は意図的に含めない
        }

        # 該当する元素のバフ値を取得
        dmg_buff_val = "0%"
        if element_type in element_buff_key_mapping:
            buff_key = element_buff_key_mapping[element_type]
            buff_val = stat_totals["dmg_bonus_by_element"].get(buff_key, 0.0)
            if buff_val > 0:
                dmg_buff_val = str(formal_round(buff_val * 1000) / 10) + "%"

        stats_mock = {
            "HP": {"val": formal_round(total_hp), "base": formal_round(base_hp), "add": "+" + str(formal_round(total_hp - base_hp)), "icon": "static/datas/assets/prot_icon/hp.png"},
            "攻撃力": {"val": formal_round(total_atk), "base": formal_round(base_atk + weapon_base_atk), "add": "+" + str(formal_round(total_atk - base_atk + weapon_base_atk)), "icon": "static/datas/assets/prot_icon/atk.png"},
            "防禦力": {"val": formal_round(total_def), "base": formal_round(base_def), "add": "+" + str(formal_round(total_def - base_def)), "icon": "static/datas/assets/prot_icon/def.png"},
            "元素熟知": {"val": formal_round(total_em), "icon": "static/datas/assets/prot_icon/EM.png"},
            "会心率": {"val": str(formal_round(total_crit_rate * 1000) / 10) + "%", "icon": "static/datas/assets/prot_icon/rate.webp"},
            "会心ダメージ": {"val": str(formal_round(total_crit_dmg * 1000) / 10) + "%", "icon": "static/datas/assets/prot_icon/dmg.webp"},
            "元素チャージ効率": {"val": str(formal_round(total_er * 1000) / 10) + "%", "icon": "static/datas/assets/prot_icon/ER.png"},
            f"{element_ja}ダメバフ": {"val": dmg_buff_val, "icon": f"static/datas/assets/prot_icon/{element_type}.png"},
        }
    else:
        # ============================================================
        # 🎯 通常モード（今まで通り）: EnkaのfightPropMapの最終値をそのまま表示
        # ============================================================
        # ─── 💡 元素バフ自動検索ロジック ───
        # EnkaのfightPropMapに存在する、可能性のあるすべてのダメバフIDのリスト
        # (30:炎, 40:水, 41:風, 42:雷, 43:草, 45:岩, 46:氷, 44:物理)
        all_buff_ids = ['30', '40', '41', '42', '43', '44', '45', '46']

        # キャラクターのデータ（あなたが元々お使いだった変数名に変えてください。例: target_avatar_info など）
        prop_map = target_avatar_info.get('fightPropMap', {})

        max_dmg_val = 0.0

        # 1つずつ部屋を覗いて、一番大きい数値（バフ）が入っているところを探す
        for b_id in all_buff_ids:
            val = prop_map.get(b_id, 0.0)
            if val > max_dmg_val:
                max_dmg_val = val

        # もし全部0だった場合は、最低限聖遺物の杯などのメインステータスが入る部屋（50〜57）もスキャンする
        if max_dmg_val == 0.0:
            relic_buff_ids = ['50', '51', '52', '53', '54', '55', '56', '57']
            for r_id in relic_buff_ids:
                val = prop_map.get(r_id, 0.0)
                if val > max_dmg_val:
                    max_dmg_val = val

        # 最終的に見つかった一番高いバフをパーセント表記に変換
        dmg_buff_val = str(formal_round(max_dmg_val * 1000) / 10) + "%"

        # 💡 元の正常に動いていたクォーテーション（'2000' や '1' など）は1ミリも変えずにそのままです！
        stats_mock = {
            "HP": {"val": formal_round(target_avatar_info.get('fightPropMap', {}).get('2000', 1)), "base": formal_round(target_avatar_info.get('fightPropMap', {}).get('1', 1)), "add": "+" + str(formal_round(target_avatar_info.get('fightPropMap', {}).get('2000', 1) - target_avatar_info.get('fightPropMap', {}).get('1', 1))), "icon": "static/datas/assets/prot_icon/hp.png"},
            "攻撃力": {"val": formal_round(target_avatar_info.get('fightPropMap', {}).get('2001', 1)), "base": formal_round(target_avatar_info.get('fightPropMap', {}).get('4', 1)), "add": "+" + str(formal_round(target_avatar_info.get('fightPropMap', {}).get('2001', 1) - target_avatar_info.get('fightPropMap', {}).get('4', 1))), "icon": "static/datas/assets/prot_icon/atk.png"},
            "防禦力": {"val": formal_round(target_avatar_info.get('fightPropMap', {}).get('2002', 1)), "base": formal_round(target_avatar_info.get('fightPropMap', {}).get('7', 1)), "add": "+" + str(formal_round(target_avatar_info.get('fightPropMap', {}).get('2002', 1) - target_avatar_info.get('fightPropMap', {}).get('7', 1))), "icon": "static/datas/assets/prot_icon/def.png"},
            "元素熟知": {"val": formal_round(target_avatar_info.get('fightPropMap', {}).get('28', 1)), "icon": "static/datas/assets/prot_icon/EM.png"},
            "会心率": {"val": str(formal_round(target_avatar_info.get('fightPropMap', {}).get('20', 1) * 1000) / 10) + "%", "icon": "static/datas/assets/prot_icon/rate.webp"},
            "会心ダメージ": {"val": str(formal_round(target_avatar_info.get('fightPropMap', {}).get('22', 1) * 1000) / 10) + "%", "icon": "static/datas/assets/prot_icon/dmg.webp"},
            "元素チャージ効率": {"val": str(formal_round(target_avatar_info.get('fightPropMap', {}).get('23', 1) * 1000) / 10) + "%", "icon": "static/datas/assets/prot_icon/ER.png"},
            # 💡 固定表記だった部分を、上で正確に取得した文字列「dmg_buff_val」に差し替え
            f"{element_ja}ダメバフ": {"val": dmg_buff_val, "icon": f"static/datas/assets/prot_icon/{element_type}.png"},
        }

    # --- 1-10. 聖遺物データ解析（スコア・ティア計算を含む） ---
    artifact_x_list = [33, 375, 718, 1061, 1404]

    # raw_artifacts は上（fakeステータス計算より前）で抽出済みのものを再利用する

    # _4:花(0), _2:羽(1), _5:時計(2), _1:杯(3), _3:冠(4)
    slot_to_index = {
        "4": 0,  # 花
        "2": 1,  # 羽
        "5": 2,  # 時計
        "1": 3,  # 杯
        "3": 4   # 冠
    }

    # 空データで初期化
    artifacts_mock = []
    for _ in range(5):
        artifacts_mock.append({
            "set": "0",
            "name": "未装備",
            "upgrade": 0,
            "Main": ["-", "-"],
            "stats": {i: ["static/datas/assets/prot_icon/atk_per.png", "-", "-"] for i in range(4)},
            "score": 0.0,
            "tier": "-",
            "icon": ""
        })

    # 計算方法（calc_method）に対応するEnkaのステータスIDをマッピング
    method_to_prop_id = {
        "atk": "FIGHT_PROP_ATTACK_PERCENT",
        "hp": "FIGHT_PROP_HP_PERCENT",
        "def": "FIGHT_PROP_DEFENSE_PERCENT",
        "em": "FIGHT_PROP_ELEMENT_MASTERY",
        "charge": "FIGHT_PROP_CHARGE_EFFICIENCY"
    }
    target_prop_id = method_to_prop_id.get(calc_method, "")

    # 装備されている聖遺物データを解析し上書き
    score_sum = 0
    for art in raw_artifacts:
        flat = art.get("flat", {})
        reliquary = art.get("reliquary", {})
        icon_name = flat.get("icon", "")

        if "_" not in icon_name:
            continue
        last_num = icon_name.split("_")[-1]
        if last_num not in slot_to_index:
            continue

        target_idx = slot_to_index[last_num]

        name_hash = str(flat.get("nameTextMapHash", ""))
        artifact_name = text_map_data.get(name_hash, "未知の聖遺物")

        main_stat_raw = flat.get("reliquaryMainstat", {})
        main_prop_id = main_stat_raw.get("mainPropId", "")
        main_name = get_stat_japanese(main_prop_id)
        main_val = main_stat_raw.get("statValue", 0)

        if "PERCENT" in main_prop_id or "CRITICAL" in main_prop_id or "CHARGE" in main_prop_id or "HURT" in main_prop_id:
            main_value_str = f"{main_val}%"
        else:
            main_value_str = f"{int(main_val):,}"

        # 💡 各聖遺物のスコア計算用の変数を初期化
        crit_rate = 0.0
        crit_dmg = 0.0
        target_stat_val = 0.0

        sub_stats_dict = {}
        sub_list = flat.get("reliquarySubstats", [])

        for idx in range(4):
            if idx < len(sub_list):
                sub_data = sub_list[idx]
                sub_prop_id = sub_data.get("appendPropId", "")
                sub_name = get_stat_japanese(sub_prop_id)
                sub_val = sub_data.get("statValue", 0)

                # 💡 スコア計算用に数値をプールする
                if sub_prop_id == "FIGHT_PROP_CRITICAL":
                    crit_rate = sub_val
                elif sub_prop_id == "FIGHT_PROP_CRITICAL_HURT":
                    crit_dmg = sub_val
                elif sub_prop_id == target_prop_id:
                    target_stat_val = sub_val

                icon_file = "atk_per.png"
                if "CRITICAL" in sub_prop_id and "HURT" not in sub_prop_id: icon_file = "rate.webp"
                elif "HURT" in sub_prop_id: icon_file = "dmg.webp"
                elif "CHARGE" in sub_prop_id: icon_file = "ER.png"
                elif "ELEMENT_MASTERY" in sub_prop_id: icon_file = "EM.png"
                elif "HP" in sub_prop_id: icon_file = "hp_per.png" if "PERCENT" in sub_prop_id else "hp.png"
                elif "ATTACK" in sub_prop_id: icon_file = "atk_per.png" if "PERCENT" in sub_prop_id else "atk.png"
                elif "DEFENSE" in sub_prop_id: icon_file = "def_per.png" if "PERCENT" in sub_prop_id else "def.png"

                sub_icon_path = f"static/datas/assets/prot_icon/{icon_file}"

                if "PERCENT" in sub_prop_id or "CRITICAL" in sub_prop_id or "CHARGE" in sub_prop_id or "HURT" in sub_prop_id:
                    sub_value_str = f"{sub_val}%"
                else:
                    sub_value_str = f"{int(sub_val)}"

                sub_stats_dict[idx] = [sub_icon_path, sub_name, sub_value_str]
            else:
                sub_stats_dict[idx] = ["static/datas/assets/prot_icon/atk_per.png", "-", "-"]

        # 💡 定義されている score_calc 関数を使ってリアルタイムに計算
        art_score = score_calc(stat=target_stat_val, critrate=crit_rate, critdmg=crit_dmg, method=calc_method)
        # 小数点第1位までに丸める
        art_score = round(art_score, 1)

        # 💡 部位（target_idx）ごとに完全に分けたティア判定
        # target_idx -> 0:花, 1:羽, 2:時計, 3:杯, 4:冠
        if target_idx in [0, 1]:  # 🌸 花・🪶 羽 の場合
            if art_score >= 50.0:
                art_tier = "SS"
            elif art_score >= 45.0:
                art_tier = "S"
            elif art_score >= 40.0:
                art_tier = "A"
            else:
                art_tier = "B"

        elif target_idx == 2:     # ⏳ 時計 の場合
            if art_score >= 45.0:
                art_tier = "SS"
            elif art_score >= 40.0:
                art_tier = "S"
            elif art_score >= 35.0:
                art_tier = "A"
            else:
                art_tier = "B"

        elif target_idx == 3:     # 🍷 杯 の場合
            if art_score >= 45.0:
                art_tier = "SS"
            elif art_score >= 40.0:
                art_tier = "S"
            elif art_score >= 37.0:
                art_tier = "A"
            else:
                art_tier = "B"

        elif target_idx == 4:     # 👑 冠 の場合
            if art_score >= 40.0:
                art_tier = "SS"
            elif art_score >= 35.0:
                art_tier = "S"
            elif art_score >= 30.0:
                art_tier = "A"
            else:
                art_tier = "B"
        else:
            art_tier = "B"

        artifacts_mock[target_idx] = {
            "set": str(flat.get("setId", "")),
            "name": artifact_name,
            "upgrade": reliquary.get("level", 1) - 1,
            "Main": [main_name, main_value_str],
            "stats": sub_stats_dict,
            "score": art_score,
            "tier": art_tier,   # 💡 新しい個別基準のティアが適用されます
            "icon": icon_name
        }
        score_sum += art_score

    if not artifacts_mock:
        artifacts_mock = [{
            "set": "15046", "upgrade": 0, "Main": ["-", "-"],
            "stats": {i: ["static/datas/assets/prot_icon/atk_per.png", "-", "-"] for i in range(4)},
            "score": 0.0, "tier": "B", "icon": ""
        }] * 5
    artifact_image_num = [4, 2, 5, 1, 3]

    # --- 1-11. 聖遺物セット効果判定（動的判定・個数反映） ---
    from collections import Counter

    set_ids = [art["set"] for art in artifacts_mock if art["set"] and art["set"] != "0"]
    set_counts = Counter(set_ids)
    active_sets = [(set_id, count) for set_id, count in set_counts.items() if count >= 2]

    def get_set_info(set_id_str):
        possible_paths = [
            "static/datas/lists/artifacts.json",
            os.path.join(BASE_DIR, "static", "datas", "lists", "artifacts.json"),
            "artifacts.json"
        ]
        for raw_path in possible_paths:
            path = resolve_list_path(raw_path, beta)
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f_art:
                        art_json = json.load(f_art)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    with open(path, "r", encoding="cp932") as f_art:
                        art_json = json.load(f_art)
                try:
                    if set_id_str in art_json:
                        name = art_json[set_id_str].get("janame", f"セット {set_id_str}")
                        icon_field = art_json[set_id_str].get("icon", "UI_RelicIcon_15046_4")
                        icon_path = resolve_datas_path(f"static/datas/assets/artifacts/{icon_field}.webp", beta)
                        return name, icon_path
                except Exception:
                    pass
        return f"セット {set_id_str}", resolve_datas_path("static/datas/assets/artifacts/UI_RelicIcon_15046_4.webp", beta)

    # 描画に必要な位置情報も含めて、表示すべきセットを事前にまとめておく
    sets_display = []

    # 2個以上が「1種類」だけ発動している場合
    if len(active_sets) == 1:
        set_id, count = active_sets[0]
        set_name, set_icon = get_set_info(set_id)
        sets_display.append({
            "icon": set_icon, "name": set_name, "count": str(count),
            "img_y": 271 - 4, "text_y": 281, "box_y": 279
        })

    # 2個以上が「2種類」発動している場合
    elif len(active_sets) >= 2:
        set_id1, count1 = active_sets[0]
        set_name1, set_icon1 = get_set_info(set_id1)
        sets_display.append({
            "icon": set_icon1, "name": set_name1, "count": str(count1),
            "img_y": 242 - 4, "text_y": 252, "box_y": 250
        })

        set_id2, count2 = active_sets[1]
        set_name2, set_icon2 = get_set_info(set_id2)
        sets_display.append({
            "icon": set_icon2, "name": set_name2, "count": str(count2),
            "img_y": 301 - 4, "text_y": 311, "box_y": 309
        })

    # --- 1-12. 総合スコア・ティア・計算方法表示 ---
    if score_sum < 180:
        tier_sum_score = "B"
    elif 180 <= score_sum and score_sum < 200:
        tier_sum_score = "A"
    elif 200 <= score_sum and score_sum < 220:
        tier_sum_score = "S"
    else:
        tier_sum_score = "SS"

    display_map = {
        "crit": "会心のみ",
        "atk": "攻撃力%",
        "hp": "HP%",
        "def": "DEF%",
        "em": "元素熟知",
        "charge": "チャージ効率"
    }
    display_score_way = display_map[calc_method]

    # ================================================================
    # ② 描画（PIL 描画処理）
    #    ここから先は①で用意した変数を使って描くだけ。
    # ================================================================

    # --- 2-0. キャンバス初期化・フォント読み込み ---
    img = Image.new("RGB", (card_width, card_height), base_color)
    draw = ImageDraw.Draw(img)

    if os.path.exists(FONT_PATH):
        try: font_stats = ImageFont.truetype(FONT_PATH, 28)
        except: font_stats = ImageFont.load_default()
    else: font_stats = ImageFont.load_default()

    if os.path.exists(FONT_LIGHT_PATH):
        try: font_stats_light = ImageFont.truetype(FONT_LIGHT_PATH, 28)
        except: font_stats_light = font_stats
    else: font_stats_light = font_stats

    # --- 2-1. レイアウト枠の描画 ---
    draw_figma_box(img, x=33, y=30, width=694, height=671)
    draw_figma_box(img, x=753, y=30, width=549, height=671)
    draw_figma_box(img, x=1332, y=30, width=386, height=164, radius=25)
    draw_figma_box(img, x=1332, y=231, width=386, height=121, radius=25)
    draw_figma_box(img, x=1332, y=389, width=386, height=312, radius=25)

    # --- 2-2. スプラッシュ画像の貼り付け ---
    paste_mask_image(img, splash, box_x=33, box_y=30, box_width=694, box_height=671, radius=15, zoom=1.1, beta=beta)

    # --- 2-3. テキスト情報（名前・レベル・好感度） ---
    draw_figma_text_with_shadow(draw, text=char_name, x=53, y=53, font=font_stats, font_size=50)
    draw_figma_text_with_shadow(draw, text=f"Lv.{char_level}", x=53, y=117, font=font_stats, font_size=30)
    draw_figma_text_with_shadow(draw, text=f"♥ {friendship_lv}", x=53, y=162, font=font_stats, font_size=30)

    # --- 2-4. 天賦スキルの描画 ---
    y_skill_base = 389
    for i in range(3):
        # サークルの中心X座標を計算する（x=49 で size=68 なので、中心は 49 + 34 = 83）
        circle_center_x = 49 + 34

        draw_figma_circle(img, x=49, y=y_skill_base + 79 * i, size=68, fill_color=(0, 0, 0, 150), outline_color=base_color, outline_width=4)
        paste_figma_image(img, f"static/datas/assets/skill_icon/{skill_icon[i]}.webp", box_x=49 + 5, box_y=y_skill_base + 79 * i + 4, box_width=60, box_height=60, radius=15, beta=beta)

        # 💡 基準Xを「サークルの中心（circle_center_x）」にし、align="center" を指定します。
        draw_figma_text_with_shadow(draw, text=f"Lv.{skill_level[i]}", x=48, y=y_skill_base + 79 * i + 45, font=font_stats, align="center", font_size=20, box_width=68)

    # --- 2-5. 命ノ星座の描画 ---
    for i in range(6):
        circle_x = 637
        circle_y = y_C_base + i * 76
        icon_name = Constellation_icon[i]

        # 🔒 i が解放数以上 = 「未解放」の星座スロットの場合
        if i >= constellation_releas_num:
            # 1. 未解放用の暗い背景とグレーの枠線を描画
            draw_figma_circle(
                img,
                x=circle_x,
                y=circle_y,
                size=circle_size,
                fill_color=(0, 0, 0, 180),          # 背景をより暗く
                outline_color=(80, 85, 95, 255),    # 枠線をダークグレーに
                outline_width=2
            )

            # 2. 星座アイコンを読み込んで「めっちゃ透明」にしてからペーストする
            icon_path = resolve_datas_path(f"static/datas/assets/skill_icon/{icon_name}.webp", beta)
            if os.path.exists(icon_path):
                # アイコンを読み込んでRGBA（透明度あり）に変換
                icon_img = Image.open(icon_path).convert("RGBA")
                icon_img = icon_img.resize((60, 60), Image.Resampling.LANCZOS)

                # 💡 ここで透明度を調整します
                # 255が通常。50にすると約20%の薄さ（めっちゃ透明）になります。
                # 好みに合わせて 30（さらに薄く）〜 70 くらいで調整してください。
                alpha_value = 45

                # 既存のアルファチャンネル（透明度）に、一律でさらに透明にする計算をかける
                alpha = icon_img.getchannel('A')
                alpha = alpha.point(lambda p: int(p * (alpha_value / 255.0)))
                icon_img.putalpha(alpha)

                # メイン画像に半透明で合成（マスクにもicon_img自身を指定することで透明度が維持されます）
                img.paste(icon_img, (circle_x + 5, circle_y + 5), icon_img)

            # 3. 真ん中にコードだけで鍵アイコンを描画（画像ファイル不要）
            # 鍵全体の基準座標を計算（円の中央付近）
            lock_w, lock_h = 24, 26
            lx = circle_x + (circle_size - lock_w) // 2
            ly = circle_y + (circle_size - lock_h) // 2 + 2  # 少し下に微調整

            # 透過Overlayの上に描画して重ねる
            lock_overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            draw_lock = ImageDraw.Draw(lock_overlay)

            # ① 鍵の「アーチ（上の半円パーツ）」を描く
            # arc([左, 上, 右, 下], 開始角度, 終了角度)
            draw_lock.arc([lx + 4, ly, lx + lock_w - 4, ly + 16], start=180, end=0, fill=(255, 255, 255, 220), width=3)
            # アーチの縦棒部分を少し下に伸ばす
            draw_lock.line([lx + 4, ly + 8, lx + 4, ly + 12], fill=(255, 255, 255, 220), width=3)
            draw_lock.line([lx + lock_w - 4, ly + 8, lx + lock_w - 4, ly + 12], fill=(255, 255, 255, 220), width=3)

            # ② 鍵の「ボディ（下の四角パーツ）」を描く
            # rounded_rectangle([左, 上, 右, 下], 角の丸み)
            draw_lock.rounded_rectangle(
                [lx, ly + 11, lx + lock_w, ly + lock_h],
                radius=4,
                fill=(20, 25, 35, 255),            # ボディの塗りつぶし（暗いグレー）
                outline=(255, 255, 255, 220),       # ボディの枠線（白）
                width=2
            )

            # ③ 鍵穴（中央のポッチ）を描く
            draw_lock.ellipse([lx + 10, ly + 16, lx + 14, ly + 20], fill=(255, 255, 255, 220))

            # メイン画像に合成
            img.paste(Image.alpha_composite(img.convert("RGBA"), lock_overlay).convert("RGB"))

        # ✨ 解放済みの星座スロットの場合（通常通り綺麗に描画）
        else:
            # 1. 鮮やかな元素色（base_color）の太枠で円を描画
            draw_figma_circle(
                img,
                x=circle_x,
                y=circle_y,
                size=circle_size,
                fill_color=(0, 0, 0, 150),
                outline_color=base_color,  # クッキリ鮮やかな枠線
                outline_width=4
            )
            # 2. 星座アイコンを綺麗に重ねる
            paste_figma_image(img, f"static/datas/assets/skill_icon/{icon_name}.webp", box_x=circle_x + 5, box_y=circle_y + 5, box_width=60, box_height=60, radius=15, beta=beta)

    # --- 2-6. 武器の描画 ---
    paste_figma_image(img, f"static/datas/assets/weapons/{weapon_icon}.webp", box_x=1350, box_y=60, box_width=100, box_height=100, radius=15, beta=beta)
    draw_figma_box(img, x=1340, y=47, width=60, height=30, radius=2)

    # 精錬ランク（R1 などの1を動的に反映）
    draw_figma_text(draw, text=f"R{weapon_affix}", x=1357, y=48, font=font_stats, align="left", font_size=20)
    draw_figma_text(draw, text=weapon_name, x=1462, y=60, font=font_stats, align="left", font_size=23)
    draw_figma_text(draw, text=f"Lv.{weapon_level}", x=1462, y=90, font=font_stats, align="left", font_size=20)

    if weapon_stat1:
        stat_name1, stat_val1_str = weapon_stat1
        draw_figma_text(draw, text=stat_name1, x=1462, y=125, font=font_stats_light, align="left", font_size=18)
        draw_figma_text(draw, text=stat_val1_str, x=1635, y=125, font=font_stats_light, align="left", font_size=21)

    if weapon_stat2:
        stat_name2, stat_val2_str = weapon_stat2
        draw_figma_text(draw, text=stat_name2, x=1462, y=155, font=font_stats_light, align="left", font_size=18)
        draw_figma_text(draw, text=stat_val2_str, x=1635, y=155, font=font_stats_light, align="left", font_size=21)

    # --- 2-7. ステータス詳細一覧の描画 ---
    base_y = 73
    max_y = 700
    row_gap = (max_y - base_y) // len(stats_mock)
    icon_size = 36
    icon_offset_y = 2

    for i, (n, data) in enumerate(stats_mock.items()):
        current_y = base_y + (i * row_gap)
        icon_path = resolve_datas_path(data["icon"], beta)
        icon_x = 840 - 60

        if icon_path and os.path.exists(icon_path):
            try:
                icon_img = Image.open(icon_path).convert("RGBA")
                icon_img = icon_img.resize((icon_size, icon_size), Image.Resampling.LANCZOS)
                img.paste(icon_img, (icon_x, current_y + icon_offset_y), icon_img)
            except Exception as e:
                print(f"[Error] Failed to paste status icon: {icon_path}. Reason: {e}")
        else:
            print(f"[Warning] Status icon not found: {icon_path}")

        draw_figma_text(draw, text=n, x=840, y=current_y, font=font_stats, align="left")
        draw_figma_text(draw, text=data["val"], x=870, y=current_y, font=font_stats, align="right", box_width=450 - 60)

        if n in ["HP", "攻撃力", "防禦力"] and data.get("base") and data.get("add"):
            sub_y = current_y + 32
            green_text = data["add"]
            gray_text = str(data["base"])

            try:
                calc_font = ImageFont.truetype(font_stats.path, 20) if hasattr(font_stats, "path") and font_stats.path else font_stats
            except:
                calc_font = font_stats

            green_w = draw.textlength(green_text, font=calc_font)
            gray_w = draw.textlength(gray_text, font=calc_font)

            # 💡 基準の右端を 870 から 1260 に変更！
            target_right_edge = 1260

            green_x = target_right_edge - green_w
            gray_x = green_x - 8 - gray_w

            # 緑色を描画
            draw_figma_text(draw, text=green_text, x=green_x, y=sub_y, font=font_stats, font_size=20, fill_color=(0, 230, 115), align="left")
            # 灰色を描画
            draw_figma_text(draw, text=gray_text, x=gray_x, y=sub_y, font=font_stats, font_size=20, fill_color=(160, 165, 175), align="left")

    # --- 2-8. 聖遺物スロット枠の描画 ---
    for x in artifact_x_list:
        draw_figma_box(img, x=x, y=738, width=314, height=399, radius=25)

    # --- 2-9. 聖遺物詳細の描画 ---
    for i in range(5):
        box_x = artifact_x_list[i]
        artifact_data = artifacts_mock[i]
        artifact_img_num = artifact_image_num[i]

        draw_figma_box(img, x=box_x + 14, y=754, width=90, height=90, radius=10)
        draw_figma_box(img, x=box_x + 230, y=795, width=70, height=40, radius=10)

        paste_figma_image(img, f"static/datas/assets/artifacts/UI_RelicIcon_{artifact_data['set']}_{artifact_img_num}.webp", box_x=box_x + 14, box_y=754, box_width=90, box_height=90, radius=15, beta=beta)

        draw_figma_text(draw, text=artifact_data["Main"][0], x=box_x + 114, y=758, font=font_stats, align="left")
        draw_figma_text(draw, text=artifact_data["Main"][1], x=box_x + 114, y=792, font=font_stats, align="left", font_size=30)
        draw_figma_text(draw, text=f"+{artifact_data['upgrade']}", x=box_x + 237, y=793, font=font_stats, align="left")

        y_base = 855
        for j in range(4):
            draw_figma_text(draw, text=artifact_data["stats"][j][1], x=box_x + 47, y=y_base + 50 * j, font=font_stats, font_size=25, align="left")
            draw_figma_text(draw, text=artifact_data["stats"][j][2], x=box_x + 218, y=y_base + 50 * j, font=font_stats, font_size=25, align="left")
            paste_figma_image(img, artifact_data["stats"][j][0], box_x=box_x + 12, box_y=y_base + 50 * j, box_width=30, box_height=30, radius=5, beta=beta)

        draw_figma_line(img, x1=box_x + 27, y1=1065, x2=box_x + 287, y2=1065, fill_color=(255, 255, 255, 50), width=1)
        draw_figma_text(draw, text="スコア", x=box_x + 142, y=1090, font=font_stats_light, font_size=20, align="left")
        draw_figma_text(draw, text=artifact_data["score"], x=box_x + 207, y=1070, font=font_stats, font_size=40, align="right")
        paste_figma_image(img, f"static/datas/assets/tiers/{artifact_data['tier']}.png", box_x=box_x + 27, box_y=1070, box_width=60, box_height=60, radius=15, beta=beta)

    # --- 2-10. 聖遺物セット効果の描画 ---
    for s in sets_display:
        paste_figma_image(img, s["icon"], box_x=1360, box_y=s["img_y"], box_width=60, box_height=60, radius=15, beta=beta)
        draw_figma_text(draw, text=s["name"], x=1435, y=s["text_y"], font=font_stats, align="left", font_size=20)
        draw_figma_box(img, x=1610, y=s["box_y"], width=35, height=28, radius=8, fill_color=(255, 255, 255, 40))
        draw_figma_text(draw, text=s["count"], x=1623, y=s["text_y"], font=font_stats, align="center", font_size=18, box_width=35)

    # --- 2-11. 総合スコア・ティアの描画 ---
    draw_figma_text(draw, text="総合スコア", x=1443, y=449, font=font_stats, align="left", font_size=30)
    draw_figma_text(draw, text=round(score_sum, 1), x=1386, y=480, font=font_stats, align="left", font_size=90)
    draw_figma_line(img, x1=1380, y1=623, x2=1670, y2=623, fill_color=(255, 255, 255, 50), width=1)
    paste_figma_image(img, f"static/datas/assets/tiers/{tier_sum_score}.png", box_x=1620, box_y=400, box_width=80, box_height=80, radius=15, beta=beta)

    # --- 2-12. 計算方法の描画 ---
    draw_figma_text(draw, text="計算方法", x=1350, y=642, font=font_stats, align="left", font_size=30)
    draw_figma_text_right(draw, text=display_score_way, x=1680, y=645, font=font_stats, align="right", font_size=35)

    # --- 2-13. 画像のレスポンス生成 ---
    img_io = io.BytesIO()
    img.save(img_io, 'PNG', quality=95)
    img_io.seek(0)
    return StreamingResponse(img_io, media_type="image/png")


if __name__ == "__main__":
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True)