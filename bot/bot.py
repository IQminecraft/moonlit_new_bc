# -*- coding: utf-8 -*-
"""
new_bc Discord bot（moonlit_new_bc 組み込み版）
================================================
new_bc (moonlit_new_bc) の Web API を叩いてビルドカード画像を生成する Discord bot。
Admin パネル（/admin → Bot管理タブ）から子プロセスとして起動/停止/再起動される。
単体でも `python bot/bot.py` で起動できる。

必要な環境変数（プロジェクト直下の .env から読み込む）:
    NEWBC_BOT_TOKEN   Discord bot トークン（必須、DISCORD_BOT_TOKEN でも可）
    NEW_BC_API_URL    new_bc API のベースURL（既定: http://127.0.0.1:PORT）
    GUILD_ID          開発用にコマンド同期を単一ギルドに限定したい場合（任意）

Admin パネル連携用に bot/bot_status.json へ状態を定期書き込みする。
"""
import io
import json
import os
import signal
import sys
import time
from datetime import datetime

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

# プロジェクトルート（bot/ の1階層上）の .env を読み込む
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BOT_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_BASE_DIR, ".env"))

BOT_TOKEN = (os.environ.get("NEWBC_BOT_TOKEN") or os.environ.get("DISCORD_BOT_TOKEN") or "").strip()
_API_PORT = (os.environ.get("PORT") or "8000").strip()
API_BASE = (os.environ.get("NEW_BC_API_URL") or f"http://127.0.0.1:{_API_PORT}").rstrip("/")
GUILD_ID = (os.environ.get("GUILD_ID") or "").strip()

# Admin パネル参照用の状態ファイル
STATUS_PATH = os.path.join(_BOT_DIR, "bot_status.json")

# 画像生成は数秒～数十秒かかることがあるため長めのタイムアウト
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=180)

VALID_CALC_METHODS = ("crit", "atk", "hp", "def", "em", "charge")
DEFAULT_CALC_METHOD = "crit"

# Discord の添付ファイル上限（通常 8MiB）。超えそうな場合は警告する
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

# Embed 配色
SELECT_COLOR = 0x5B8DEF   # キャラ選択プロンプト（青）
ERROR_COLOR = 0xE74C3C    # エラー（赤）

# 元素カラー（build_card.html の ELEMENT_COLORS と同一）
ELEMENT_COLORS = {
    "Pyro": 0xFF6B4A,
    "Hydro": 0x4AA4FF,
    "Anemo": 0x74E0C5,
    "Electro": 0xC97BFF,
    "Dendro": 0xA0E05A,
    "Cryo": 0x9AD4FF,
    "Geo": 0xF0C65A,
    "None": 0xAAAAAA,
}
DEFAULT_ELEMENT_COLOR = 0xAAAAAA

_STARTED_AT = datetime.now().isoformat(timespec="seconds")
_LAST_READY_AT = None       # 最後に on_ready を迎えた時刻
_last_error = None          # 最後に発生したコマンドエラー {at, where, error}
_synced = None              # スラッシュコマンド同期 成功/失敗/未実施(None)


def element_color(element: str) -> int:
    """元素名から Embed 用の色を返す。"""
    return ELEMENT_COLORS.get(str(element or "").strip(), DEFAULT_ELEMENT_COLOR)


def error_embed(message: str) -> discord.Embed:
    """エラー表示用の赤い Embed を返す。"""
    return discord.Embed(title="エラー", description=message, color=ERROR_COLOR)


# ------------------------------------------------------------------
#  Admin パネル用ステータス書き出し
# ------------------------------------------------------------------
def _write_status(connected: bool) -> None:
    """bot_status.json を原子的に更新する（Admin パネルから参照される）。"""
    try:
        data = {
            "pid": os.getpid(),
            "connected": connected,
            "user": None,
            "user_id": None,
            "latency_ms": None,
            "guild_count": 0,
            "command_count": len(bot.tree.get_commands()),
            "discord_py": discord.__version__,
            "api_base": API_BASE,
            "guild_id_set": bool(GUILD_ID),
            "started_at": _STARTED_AT,
            "last_ready_at": None,
            "synced": _synced,
            "last_error": _last_error,
        }
        if bot.user is not None:
            data["user"] = str(bot.user)
            data["user_id"] = str(bot.user.id)
            data["latency_ms"] = round(bot.latency * 1000, 1)
            data["guild_count"] = len(bot.guilds)
            data["last_ready_at"] = _LAST_READY_AT
        tmp = STATUS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATUS_PATH)
    except Exception as e:
        print(f"[Warn] ステータスファイルの書き込みに失敗しました: {e}")


def _record_error(where: str, error: BaseException) -> None:
    """エラーをコンソールとステータスに記録する。"""
    global _last_error
    _last_error = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "where": where,
        "error": f"{type(error).__name__}: {error}",
    }
    _write_status(bot.is_ready())


intents = discord.Intents.default()
# jishaku（!jsk）はプレフィックスコマンドのためメッセージ内容の読み取りが必要。
# ※ Discord Developer Portal → Bot → Privileged Gateway Intents の
#   「MESSAGE CONTENT INTENT」も有効にする必要がある。
intents.message_content = True


class _NewBCBot(commands.Bot):
    async def setup_hook(self):
        # jishaku（デバッグ用拡張。`!jsk` コマンド群）。インストールされていれば読み込む。
        # jishaku 標準のオーナー制限（bot.is_owner）に加えて、
        # ADMIN_DISCORD_USERID に書かれた管理者も使えるようにする。
        try:
            await self.load_extension("jishaku")

            from admin_panel import is_admin_user  # bot/bot.py は bot/ を sys.path[0] にして起動される

            _orig_cog_check = None
            for cog in self.cogs.values():
                if type(cog).__module__.startswith("jishaku") and hasattr(cog, "cog_check"):
                    _orig_cog_check = cog.cog_check

                    async def _admin_or_owner(self, ctx, _orig=_orig_cog_check):
                        if is_admin_user(ctx.author.id):
                            return True
                        return await _orig(ctx)

                    # discord.py は cog_check を「Cog のメソッド」として扱う
                    # （_get_overridden_method が __func__ を参照する）。
                    # Cog サブクラスを動的に作って型レベルで差し替えるのが確実。
                    import types
                    cog_cls = type(cog)
                    cog_cls.cog_check = _admin_or_owner
                    break

            jsk = self.get_command("jsk")
            if jsk is not None:
                print("[OK] jishaku loaded (!jsk ... / botオーナー または ADMIN_DISCORD_USERID の管理者のみ使用可)")
            else:
                print("[Warn] jishaku を読み込んだが jsk コマンドが見つかりません")
        except ImportError:
            print("[Info] jishaku 未インストールのため !jsk は無効（pip install jishaku で有効化）")
        except Exception as e:
            print(f"[Warn] jishaku のロードに失敗しました: {e}")

        # /admin（adminパネル操作。許可ユーザーは .env の ADMIN_DISCORD_USERID のみ）
        try:
            from bot.admin_panel import register_admin_command
        except ImportError:
            from admin_panel import register_admin_command
        register_admin_command(self)


bot = _NewBCBot(command_prefix="!", intents=intents)

# /api/calc_method_defaults のキャッシュ（キャラ毎の既定計算方式）
_calc_defaults_cache = None


@tasks.loop(seconds=30)
async def _heartbeat():
    """Admin パネル用に接続状態を定期更新する。"""
    _write_status(bot.is_ready())


@_heartbeat.before_loop
async def _heartbeat_before():
    await bot.wait_until_ready()


# ------------------------------------------------------------------
#  UID 保存（Discordユーザー毎に最後に使った原神UIDを覚える）
# ------------------------------------------------------------------
UID_STORE_PATH = os.path.join(_BOT_DIR, "uid_store.json")


def _load_uid_store() -> dict:
    try:
        with open(UID_STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_uid_store(store: dict) -> None:
    try:
        tmp = UID_STORE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
        os.replace(tmp, UID_STORE_PATH)
    except Exception as e:
        print(f"[Warn] UIDの保存に失敗しました: {e}")


def save_user_uid(discord_user_id: int, genshin_uid: str) -> None:
    """Discordユーザーに原神UIDを紐付けて保存する。"""
    store = _load_uid_store()
    store[str(discord_user_id)] = str(genshin_uid)
    _save_uid_store(store)


def get_user_uid(discord_user_id: int):
    """保存済みの原神UIDを返す（無ければ None）。"""
    return _load_uid_store().get(str(discord_user_id))


# ------------------------------------------------------------------
#  ユーザー設定（カードの見た目・計算方法。Discordユーザー毎に保存）
# ------------------------------------------------------------------
SETTINGS_STORE_PATH = os.path.join(_BOT_DIR, "settings_store.json")

# カード生成 API (/api/card_sign) に渡す設定。Web 版（build_card.html）の保存項目に対応。
SETTINGS_DEFAULTS = {
    "calc_method": "auto",   # auto = キャラ毎の推奨値に自動解決 / crit|atk|hp|def|em|charge
    "theme": "cinema",       # glass|glass_light|cinema|cinema_light|scorecard|scorecard_light
    "base_prec": "0",        # 基礎ステータス表示精度（0=整数 / 2=小数点2桁。Web と同じ選択肢）
    "substat_dots": "1",     # 伸び値ドット（1=ON / 0=OFF）
    "show_uid": "0",         # カードへの UID 表示（1=ON / 0=OFF）
}

_CALC_SELECT_CHOICES = [
    ("auto", "自動（キャラ毎の推奨）"),
    ("crit", "会心のみ"),
    ("atk", "攻撃力%"),
    ("hp", "HP%"),
    ("def", "防御%"),
    ("em", "元素熟知"),
    ("charge", "チャージ効率"),
]
_THEME_SELECT_CHOICES = [
    ("glass", "ガラス（定番）"),
    ("glass_light", "ガラス（白）"),
    ("cinema", "シネマ（黒）"),
    ("cinema_light", "シネマ（白）"),
    ("scorecard", "スコア（黒）"),
    ("scorecard_light", "スコア（白）"),
]
_CALC_METHOD_LABELS_JA = dict(_CALC_SELECT_CHOICES)
_THEME_LABELS_JA = dict(_THEME_SELECT_CHOICES)
_CALC_SOURCE_LABELS_JA = {"option": "今回のみ", "char": "キャラ別", "common": "共通"}

# トグルボタンの OFF 値 / ON 値 と表示ラベル（base_prec は 0=整数 / 2=小数点2桁）
_TOGGLE_VALUES = {
    "substat_dots": ("0", "1"),
    "show_uid": ("0", "1"),
    "base_prec": ("0", "2"),
}
_TOGGLE_LABELS = {
    "substat_dots": ("ON", "OFF"),
    "show_uid": ("ON", "OFF"),
    "base_prec": ("小数点2桁", "整数"),
}

_VALID_CALC_METHODS = ("crit", "atk", "hp", "def", "em", "charge")


def _load_settings_store() -> dict:
    try:
        with open(SETTINGS_STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_settings_store(store: dict) -> None:
    try:
        tmp = SETTINGS_STORE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_STORE_PATH)
    except Exception as e:
        print(f"[Warn] 設定の保存に失敗しました: {e}")


def load_user_settings(discord_user_id: int) -> dict:
    """ユーザー設定を返す（未設定キーはデフォルトで補完。last_* 等のメタも含む）。"""
    saved = _load_settings_store().get(str(discord_user_id)) or {}
    settings = dict(SETTINGS_DEFAULTS)
    for k, v in saved.items():
        if v is not None:
            settings[k] = v
    return settings


def save_user_settings(discord_user_id: int, patch: dict) -> None:
    """ユーザー設定を部分更新する。"""
    store = _load_settings_store()
    entry = store.get(str(discord_user_id)) or {}
    entry.update(patch)
    store[str(discord_user_id)] = entry
    _save_settings_store(store)


def settings_summary(settings: dict) -> str:
    """設定の1行サマリ（生成embed・設定パネル共用）。"""
    calc = _CALC_METHOD_LABELS_JA.get(settings.get("calc_method"), str(settings.get("calc_method")))
    theme = _THEME_LABELS_JA.get(settings.get("theme"), str(settings.get("theme")))
    per_char = per_char_calc_methods(settings)
    calc_text = f"{calc}（キャラ別 {len(per_char)} 件）" if per_char else calc
    return (
        f"計算: {calc_text} / テーマ: {theme} / "
        f"ドット: {'ON' if str(settings.get('substat_dots')) == '1' else 'OFF'} / "
        f"UID表示: {'ON' if str(settings.get('show_uid')) == '1' else 'OFF'}"
    )


# ------------------------------------------------------------------
#  キャラ別スコア計算方法（avatar のベースID毎の上書き設定）
# ------------------------------------------------------------------
def _char_base_id(avatar_id) -> str:
    return str(avatar_id).split("-")[0]


def per_char_calc_methods(settings: dict) -> dict:
    """キャラ別計算方法の上書き辞書（{base_id: method}）。未設定時は空。"""
    cm = settings.get("calc_methods")
    return cm if isinstance(cm, dict) else {}


def effective_calc_method(settings: dict, avatar_id: str, option_used: bool = False):
    """今回の生成で適用される計算方法と出所を返す。

    戻り値: (method, source)  source: "option" | "char" | "common"
    method が "auto" の場合、generate_card_image 側でキャラ毎の推奨値に解決される。
    優先順位: スラッシュオプション（今回のみ） > キャラ別設定 > 共通設定
    """
    if option_used:
        return str(settings.get("calc_method") or "auto"), "option"
    per = per_char_calc_methods(settings).get(_char_base_id(avatar_id))
    if per in _VALID_CALC_METHODS:
        return per, "char"
    g = settings.get("calc_method")
    if g in _VALID_CALC_METHODS:
        return g, "common"
    return "auto", "common"


def set_per_char_calc_method(discord_user_id: int, avatar_base_id: str, method: str) -> None:
    """キャラ別の計算方法を保存する。"auto"（自動）なら上書きを解除する。"""
    base = _char_base_id(avatar_base_id)
    overrides = dict(per_char_calc_methods(load_user_settings(discord_user_id)))
    if method in _VALID_CALC_METHODS:
        # 再挿入で更新順を維持し、上限を超えたら最古から削除
        overrides.pop(base, None)
        overrides[base] = method
        while len(overrides) > 60:
            overrides.pop(next(iter(overrides)))
    else:
        overrides.pop(base, None)
    save_user_settings(discord_user_id, {"calc_methods": overrides})


# ------------------------------------------------------------------
#  API ヘルパー
# ------------------------------------------------------------------
async def _error_detail(resp: aiohttp.ClientResponse) -> str:
    """HTTP エラー応答から detail メッセージを取り出す。"""
    try:
        data = await resp.json()
        if isinstance(data, dict) and data.get("detail"):
            return str(data["detail"])
    except Exception:
        pass
    return f"status {resp.status}"


async def check_enka_cooldown(sess: aiohttp.ClientSession, uid: str):
    """Enka API のクールタイム(CT)残り秒数を問い合わせる。

    戻り値: (in_cooldown: bool, remaining_sec: float)
    問い合わせに失敗した場合は CT なしとみなし (False, 0.0) を返す
    （後続の /refresh_uid がサーバー権威で CT 判定するため安全側に倒れる）。
    """
    try:
        async with sess.get(f"{API_BASE}/api/enka_cooldown", params={"uid": uid}) as resp:
            if resp.status == 200:
                data = await resp.json()
                remaining = float(data.get("cooldown") or 0.0)
                return remaining > 0, remaining
    except Exception:
        # CT 確認に失敗しても致命傷ではない: 後続の /refresh_uid が
        # サーバー権威で CT 判定するため、ここでは CT なし扱いにして進める
        pass
    return False, 0.0


async def _fetch_char_list_from_cache(sess: aiohttp.ClientSession, uid: str):
    """キャッシュ済みのショーケースからキャラ一覧を取得する（Enka 非アクセス）。

    戻り値: (char_list, error_message or None)
    """
    try:
        async with sess.get(f"{API_BASE}/api/char_list/{uid}") as resp:
            if resp.status == 200:
                char_list = (await resp.json()).get("char_list") or []
                if char_list:
                    return char_list, None
                return [], "キャッシュにキャラクターデータがありません。"
            if resp.status == 404:
                return [], "UID のキャッシュデータが見つかりませんでした。"
            return [], f"キャッシュの取得に失敗しました（{await _error_detail(resp)}）。"
    except (aiohttp.ClientError, TimeoutError) as e:
        return [], f"API への接続に失敗しました: {e or type(e).__name__}"


async def _diagnose_empty_showcase(sess: aiohttp.ClientSession, uid: str) -> str:
    """キャラ一覧が空だったとき /api/showcase_status で原因を切り分け、具体的なエラー文言を返す。"""
    fallback = "ショーケースが空か、ゲーム内プロフィールで『キャラクター詳細を公開』がオフになっています。"
    try:
        async with sess.get(f"{API_BASE}/api/showcase_status/{uid}") as resp:
            if resp.status != 200:
                return fallback
            d = await resp.json()
    except Exception:
        return fallback

    if not d.get("cached"):
        return f"UID {uid} のデータが見つかりませんでした。UID が存在しないか、まだ一度も取得されていません。"

    nickname = (d.get("nickname") or "").strip()
    who = f"UID {uid}" + (f"（プレイヤー: {nickname}）" if nickname else "")
    showcase_count = int(d.get("showcase_count") or 0)
    detail_count = int(d.get("detail_count") or 0)

    if detail_count == 0 and showcase_count > 0:
        return (
            f"{who}: ショーケースに {showcase_count} 人いますが、**『キャラクター詳細』が非公開**のためカードを生成できません。\n"
            "**対処方法**\n"
            "1. ゲーム内: プロフィール → 設定 → 『キャラクター詳細を公開』を **ON**\n"
            "2. ゲーム内の【キャラクターショーケース】画面を一度開く（データに反映させるため）\n"
            "3. 少し（約1分）待ってから、もう一度 `/buildcard` を実行"
        )
    if showcase_count == 0:
        return f"{who}: ショーケースが空です。ゲーム内のショーケースに表示したいキャラクターを編成してください。"
    return f"{who}: カード生成できるキャラクターが見つかりませんでした。"


async def fetch_char_list(uid: str):
    """キャラ一覧を取得する。フロー:

        1) CT 確認  /api/enka_cooldown
        2) CT 中    → キャッシュ /api/char_list を使用（Enka へはアクセスしない）
        3) CT 中でない → /refresh_uid で Enka から新規取得
                          失敗時はキャッシュへフォールバック

    戻り値: (char_list, meta, error_message or None)
        meta = {"source": "cache" | "fresh", "cooldown": float}
    """
    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as sess:
        # 1) CT 確認
        in_cooldown, remaining = await check_enka_cooldown(sess, uid)

        # 2) CT 中 → キャッシュを使用して画像生成へ（Enka には触らない）
        if in_cooldown:
            char_list, err = await _fetch_char_list_from_cache(sess, uid)
            if err:
                diag = await _diagnose_empty_showcase(sess, uid)
                return [], {"source": "cache", "cooldown": remaining}, (
                    f"Enka API のクールタイム中です（あと {int(remaining)} 秒）。\n{diag}"
                )
            return char_list, {"source": "cache", "cooldown": remaining}, None

        # 3) CT でない → Enka から新規取得
        try:
            async with sess.post(f"{API_BASE}/refresh_uid/{uid}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    char_list = data.get("char_list") or []
                    # レースで他リクエストが先に取得し CT に入った場合はキャッシュ一覧が返る
                    if data.get("cooldown"):
                        if char_list:
                            return char_list, {"source": "cache", "cooldown": float(data["cooldown"])}, None
                        return [], {"source": "cache", "cooldown": float(data["cooldown"])}, (
                            f"Enka API のクールタイム中です（あと {int(data['cooldown'])} 秒）。"
                            "キャッシュもないため、しばらく待ってから再試行してください。"
                        )
                    if char_list:
                        return char_list, {"source": "fresh", "cooldown": 0.0}, None
                    msg = await _diagnose_empty_showcase(sess, uid)
                    return [], {"source": "fresh", "cooldown": 0.0}, msg
                # 502 等はキャッシュへフォールバック
        except (aiohttp.ClientError, TimeoutError) as e:
            return [], {"source": "fresh", "cooldown": 0.0}, f"API への接続に失敗しました: {e or type(e).__name__}"

        # 新規取得が失敗したときのキャッシュフォールバック
        char_list, err = await _fetch_char_list_from_cache(sess, uid)
        if char_list:
            return char_list, {"source": "cache", "cooldown": 0.0}, None
        msg = await _diagnose_empty_showcase(sess, uid)
        return [], {"source": "fresh", "cooldown": 0.0}, msg


async def _get_calc_defaults(sess: aiohttp.ClientSession):
    """キャラ毎の既定計算方式を取得（キャッシュ）。"""
    global _calc_defaults_cache
    if _calc_defaults_cache is None:
        _calc_defaults_cache = {}
        try:
            async with sess.get(f"{API_BASE}/api/calc_method_defaults") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    _calc_defaults_cache = data.get("defaults") or {}
        except Exception:
            _calc_defaults_cache = {}
    return _calc_defaults_cache


def _resolve_calc_method(avatar_id: str, defaults: dict) -> str:
    base = str(avatar_id).split("-")[0]
    m = (defaults or {}).get(base)
    return m if m in VALID_CALC_METHODS else DEFAULT_CALC_METHOD


async def generate_card_image(uid: str, avatar_id: str, settings: dict = None):
    """署名付きURLを発行してビルドカード画像を取得する。

    settings: load_user_settings() の内容（スラッシュオプションによる上書き込み）。
    calc_method が auto の場合はキャラ毎の推奨値に自動解決する。
    戻り値: (image_bytes, error_message or None)
    """
    s = dict(SETTINGS_DEFAULTS)
    if settings:
        s.update({k: v for k, v in settings.items() if k in s})

    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as sess:
        if str(s.get("calc_method") or "auto") == "auto":
            defaults = await _get_calc_defaults(sess)
            calc_method = _resolve_calc_method(avatar_id, defaults)
        else:
            calc_method = str(s["calc_method"])

        params = {
            "uid": uid,
            "avatar_id": avatar_id,
            "calc_method": calc_method,
            "img_format": "png",
            "base_prec": "2" if str(s.get("base_prec")) == "2" else "0",
            "substat_dots": "1" if str(s.get("substat_dots")) == "1" else "0",
            "show_uid": "true" if str(s.get("show_uid")) == "1" else "false",
        }
        if s.get("theme") in ("cinema", "scorecard", "glass_light", "cinema_light", "scorecard_light"):
            # glass は theme 未指定時と同じ従来描画のため送らない（*_light は白背景版）
            base_theme, light_sep, _ = str(s["theme"]).rpartition("_light")
            if light_sep:
                params["theme"] = base_theme
                params["light"] = "true"
            else:
                params["theme"] = s["theme"]

        try:
            async with sess.get(f"{API_BASE}/api/card_sign", params=params) as resp:
                if resp.status != 200:
                    return None, f"画像URLの署名取得に失敗しました（{await _error_detail(resp)}）。"
                url = (await resp.json()).get("url")
                if not url:
                    return None, "画像URLの署名取得に失敗しました。"
        except (aiohttp.ClientError, TimeoutError) as e:
            return None, f"API への接続に失敗しました: {e or type(e).__name__}"

        try:
            # cache=server でサーバー側ディスクキャッシュを利用（同一パラメータなら再生成せず高速）。
            # cache は署名対象パラメータに含まれないため、署名済みURLに後付けしても検証を通過する。
            sep = "&" if "?" in url else "?"
            async with sess.get(f"{API_BASE}{url}{sep}cache=server") as resp:
                if resp.status != 200:
                    return None, f"ビルドカード画像の生成に失敗しました（{await _error_detail(resp)}）。"
                return await resp.read(), None
        except (aiohttp.ClientError, TimeoutError) as e:
            return None, f"画像の取得に失敗しました: {e or type(e).__name__}"


# ------------------------------------------------------------------
#  選択メニュー UI
# ------------------------------------------------------------------
class CharSelect(discord.ui.Select):
    def __init__(self, uid: str, options: list, element_map: dict, author_id: int, settings: dict = None):
        super().__init__(
            placeholder="キャラクターを選択…",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.uid = uid
        self.element_map = element_map or {}
        self.author_id = author_id
        self.settings = settings or {}

    async def callback(self, interaction: discord.Interaction):
        # コマンドを実行した本人以外は操作できない
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=error_embed("このメニューはコマンドを実行した本人のみ操作できます。"),
                ephemeral=True,
            )
            return

        avatar_id = self.values[0]
        label = next((o.label for o in self.options if o.value == avatar_id), avatar_id)
        color = element_color(self.element_map.get(avatar_id, ""))

        # 3秒以内に確認を返し、以降はもとのメッセージ（選択embed）を編集して進捗/結果を表示する
        await interaction.response.defer()

        # 生成中は選択メニューを無効化して重複生成を防ぐ
        self.disabled = True
        gen_embed = discord.Embed(
            title="ビルドカードを生成中…",
            description=f"**{label}** のビルドカードを生成しています。",
            color=color,
        )
        # 前回のカード画像が残らないよう添付をクリアする
        await interaction.edit_original_response(embed=gen_embed, view=self.view, attachments=[])

        # 優先順位: 今回のみのオプション > キャラ別設定 > 共通設定（auto は後段で推奨値に解決）
        eff_calc, calc_src = effective_calc_method(
            self.settings or {}, avatar_id, bool((self.settings or {}).get("_calc_opt"))
        )
        gen_settings = dict(self.settings or {})
        gen_settings["calc_method"] = eff_calc

        try:
            img_bytes, err = await generate_card_image(self.uid, avatar_id, gen_settings)
        finally:
            # 成否に関わらず選択を再開する
            self.disabled = False

        if err:
            await interaction.edit_original_response(embed=error_embed(err), view=self.view, attachments=[])
            return

        if len(img_bytes) > MAX_UPLOAD_BYTES:
            await interaction.edit_original_response(
                embed=error_embed(
                    f"画像が大きすぎて送信できません（{len(img_bytes)/1024/1024:.1f}MB / 上限 {MAX_UPLOAD_BYTES/1024/1024:.0f}MB）。"
                ),
                view=self.view,
                attachments=[],
            )
            return

        # 最後に生成したカードを覚えておく（/setting の「再生成」用）
        save_user_settings(self.author_id, {
            "last_uid": self.uid,
            "last_avatar": avatar_id,
            "last_label": label,
            "last_element": self.element_map.get(avatar_id, "") or "",
        })

        filename = f"buildcard_{self.uid}_{avatar_id}.png"
        embed = discord.Embed(
            title=f"{label} のビルドカード",
            color=color,
        )
        embed.set_image(url=f"attachment://{filename}")
        calc_text = _CALC_METHOD_LABELS_JA.get(eff_calc, eff_calc)
        if eff_calc == "auto":
            calc_text = "自動（キャラ毎の推奨）"
        embed.set_footer(text=f"UID: {self.uid} / 計算: {calc_text}（{_CALC_SOURCE_LABELS_JA[calc_src]}）")
        file = discord.File(io.BytesIO(img_bytes), filename=filename)
        # 生成したキャラの計算方法をその場で変えられるようにボタンを出す
        self.view.set_calc_button(avatar_id, label, self.element_map.get(avatar_id, ""))
        # attachments=[File] で新規ファイルをアップロード（edit には file パラメータが無いため）
        await interaction.edit_original_response(embed=embed, attachments=[file], view=self.view)


class BuildCardView(discord.ui.View):
    def __init__(self, uid: str, options: list, element_map: dict, author_id: int, settings: dict = None):
        super().__init__(timeout=600)
        self.message = None
        self.uid = uid
        self.author_id = author_id
        self.calc_button = None
        self.add_item(CharSelect(uid, options, element_map, author_id, settings))

    def set_calc_button(self, avatar_id: str, label: str, element: str):
        """生成したキャラに合わせて「計算方法を変更」ボタンを差し替える。"""
        if self.calc_button is not None:
            self.remove_item(self.calc_button)
        self.calc_button = _CalcMethodChangeButton(self.uid, avatar_id, label, element, self.author_id, card_view=self)
        self.add_item(self.calc_button)

    async def on_timeout(self):
        for item in self.children:
            if isinstance(item, discord.ui.Select):
                item.disabled = True
                item.placeholder = "選択受付を終了しました（/buildcard で再度実行できます）"
            else:
                item.disabled = True
        try:
            if self.message is not None:
                await self.message.edit(view=self)
        except Exception:
            pass


class _CalcMethodChangeButton(discord.ui.Button):
    """カード生成後の「計算方法を変更」ボタン。押すと本人にのみ見える
    計算方法選択パネル（ephemeral）を送る。"""

    def __init__(self, uid: str, avatar_id: str, label: str, element: str, author_id: int,
                 card_view: "BuildCardView" = None, row: int = None):
        super().__init__(label="計算方法を変更", style=discord.ButtonStyle.secondary, row=row)
        self.uid = uid
        self.avatar_id = avatar_id
        # 注意: self.label は discord.py のボタンラベルなのでキャラ名は別名で持つ
        self.char_label = label
        self.element = element
        self.author_id = author_id
        self.card_view = card_view

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                embed=error_embed("このボタンはカードを生成した本人のみ操作できます。"),
                ephemeral=True,
            )
            return
        view = CalcMethodChangeView(self.uid, self.avatar_id, self.char_label, self.element,
                                    self.author_id, card_view=self.card_view)
        await interaction.response.send_message(
            embed=view._embed(),
            view=view,
            ephemeral=True,  # 本人にのみ見える
        )
        try:
            view.message = await interaction.original_response()
        except Exception:
            pass


class CalcMethodChangeView(discord.ui.View):
    """生成したキャラの計算方法をその場で変更するためのパネル（ephemeral）。

    選択するとキャラ別設定として保存し、その設定でカードを再生成する。
    再生成に成功したら元のカード表示メッセージの画像を差し替え、
    この ephemeral パネルは削除する（元メッセージが編集できない場合のみパネル内に表示）。"""
    TIMEOUT_SEC = 300

    def __init__(self, uid: str, avatar_id: str, label: str, element: str, author_id: int,
                 card_view: "BuildCardView" = None):
        super().__init__(timeout=self.TIMEOUT_SEC)
        self.uid = uid
        self.avatar_id = avatar_id
        self.label = label
        self.element = element or ""
        self.author_id = author_id
        self.card_view = card_view  # 元のカード表示（BuildCardView。message 経由で差し替える）
        self.message = None
        settings = load_user_settings(author_id)
        cur, cur_src = effective_calc_method(settings, avatar_id)
        options = [
            discord.SelectOption(label=lab, value=val, default=(str(val) == str(cur)))
            for val, lab in _CALC_SELECT_CHOICES
        ]
        self.select = CalcMethodSelect(self, options)
        self.add_item(self.select)

    def _embed(self, note: str = "") -> discord.Embed:
        embed = discord.Embed(
            title=f"{self.label} の計算方法",
            description=(
                "選択した計算方法でカードを再生成します。\n"
                "「自動（キャラ毎の推奨）」を選ぶとこのキャラの個別設定は解除されます。\n"
                "※このメッセージはあなたにのみ見えます。"
            ),
            color=element_color(self.element),
        )
        if note:
            embed.add_field(name="状態", value=note, inline=False)
        return embed

    async def on_timeout(self):
        self.select.disabled = True
        self.select.placeholder = "選択受付を終了しました（再度「計算方法を変更」を押してください）"
        try:
            if self.message is not None:
                await self.message.edit(view=self)
        except Exception:
            pass


class CalcMethodSelect(discord.ui.Select):
    def __init__(self, panel: "CalcMethodChangeView", options: list):
        super().__init__(placeholder="計算方法を選択…", options=options, min_values=1, max_values=1)
        self.panel = panel

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.panel.author_id:
            await interaction.response.send_message(
                embed=error_embed("このメニューはカードを生成した本人のみ操作できます。"),
                ephemeral=True,
            )
            return
        method = self.values[0]
        # キャラ別設定として保存（"auto" は上書き解除扱い）
        set_per_char_calc_method(self.panel.author_id, self.panel.avatar_id, method)
        await interaction.response.defer(ephemeral=True)

        # 再生成中は選択を無効化して重複操作を防ぐ
        self.disabled = True
        panel_embed = self.panel._embed(note="再生成中です…")
        await interaction.edit_original_response(embed=panel_embed, view=self.view, attachments=[])

        settings = load_user_settings(self.panel.author_id)
        # 選択直後なのでキャラ別設定が効くはず（option 指定は今回パネル内では使わない）
        eff_calc, calc_src = effective_calc_method(settings, self.panel.avatar_id)
        gen_settings = dict(settings)
        gen_settings["calc_method"] = eff_calc

        img_bytes, err = await generate_card_image(self.panel.uid, self.panel.avatar_id, gen_settings)
        if err:
            self.disabled = False
            panel_embed = self.panel._embed(note=f"再生成に失敗しました: {err}")
            await interaction.edit_original_response(embed=panel_embed, view=self.view, attachments=[])
            return
        if len(img_bytes) > MAX_UPLOAD_BYTES:
            self.disabled = False
            panel_embed = self.panel._embed(
                note=f"画像が大きすぎて送信できません（{len(img_bytes)/1024/1024:.1f}MB / 上限 {MAX_UPLOAD_BYTES/1024/1024:.0f}MB）。"
            )
            await interaction.edit_original_response(embed=panel_embed, view=self.view, attachments=[])
            return

        # 最後に生成したカードを覚えておく
        save_user_settings(self.panel.author_id, {
            "last_uid": self.panel.uid,
            "last_avatar": self.panel.avatar_id,
            "last_label": self.panel.label,
            "last_element": self.panel.element or "",
        })

        filename = f"buildcard_{self.panel.uid}_{self.panel.avatar_id}.png"
        calc_text = _CALC_METHOD_LABELS_JA.get(eff_calc, eff_calc)
        if eff_calc == "auto":
            calc_text = "自動（キャラ毎の推奨）"

        # ---- 元のカード表示メッセージの画像を差し替える ----
        card_view = self.panel.card_view
        swapped = False
        if card_view is not None and card_view.message is not None:
            try:
                new_embed = discord.Embed(
                    title=f"{self.panel.label} のビルドカード",
                    color=element_color(self.panel.element),
                )
                new_embed.set_image(url=f"attachment://{filename}")
                new_embed.set_footer(text=f"UID: {self.panel.uid} / 計算: {calc_text}（{_CALC_SOURCE_LABELS_JA[calc_src]}）")
                file = discord.File(io.BytesIO(img_bytes), filename=filename)
                await card_view.message.edit(embed=new_embed, attachments=[file], view=card_view)
                swapped = True
            except discord.DiscordException as e:
                print(f"[Warn] カード差し替えに失敗: {e}")

        if swapped:
            # 成功したらこの ephemeral パネルは削除
            try:
                await interaction.delete_original_response()
            except Exception:
                pass
            return

        # 元メッセージが編集できない（期限切れ等）場合はパネル内に表示して選択を継続
        for opt in self.options:
            opt.default = (str(opt.value) == str(eff_calc))
        self.disabled = False
        panel_embed = self.panel._embed(note="元のメッセージが編集できなかったためここに表示します。")
        panel_embed.title = f"{self.panel.label} のビルドカード"
        panel_embed.set_image(url=f"attachment://{filename}")
        panel_embed.set_footer(text=f"UID: {self.panel.uid} / 計算: {calc_text}（{_CALC_SOURCE_LABELS_JA[calc_src]}）")
        file = discord.File(io.BytesIO(img_bytes), filename=filename)
        await interaction.edit_original_response(embed=panel_embed, attachments=[file], view=self.view)


# ------------------------------------------------------------------
#  /setting 設定パネル（ephemeral: 本人にのみ見える）
# ------------------------------------------------------------------
class _SettingsSelect(discord.ui.Select):
    def __init__(self, panel: "SettingsPanelView", key: str, placeholder: str, options: list, row: int = None):
        super().__init__(placeholder=placeholder, options=options, row=row)
        self.panel = panel
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        if not self.panel._guard(interaction):
            await interaction.response.send_message(
                embed=error_embed("このパネルは実行者のみ操作できます。"), ephemeral=True
            )
            return
        save_user_settings(self.panel.author_id, {self.key: self.values[0]})
        await self.panel.rerender(interaction)


class _SettingsToggle(discord.ui.Button):
    def __init__(self, panel: "SettingsPanelView", key: str, label: str, on: bool,
                 on_text: str = "ON", off_text: str = "OFF", row: int = None):
        super().__init__(
            label=f"{label}: {on_text if on else off_text}",
            style=discord.ButtonStyle.success if on else discord.ButtonStyle.secondary,
            row=row,
        )
        self.panel = panel
        self.key = key

    async def callback(self, interaction: discord.Interaction):
        if not self.panel._guard(interaction):
            await interaction.response.send_message(
                embed=error_embed("このパネルは実行者のみ操作できます。"), ephemeral=True
            )
            return
        settings = load_user_settings(self.panel.author_id)
        off_v, on_v = _TOGGLE_VALUES[self.key]
        save_user_settings(self.panel.author_id, {self.key: off_v if str(settings.get(self.key)) == on_v else on_v})
        await self.panel.rerender(interaction)


class _CharTargetSelect(discord.ui.Select):
    """キャラ別の計算方法: 対象キャラ選択（/buildcard で取得した一覧から）。"""

    def __init__(self, panel: "SettingsPanelView", row: int = None):
        settings = load_user_settings(panel.author_id)
        overrides = per_char_calc_methods(settings)
        self.panel = panel
        chars = settings.get("_char_list") or []
        if not chars:
            super().__init__(
                placeholder="（先に /buildcard を実行してください）",
                options=[discord.SelectOption(label="キャラ一覧なし", value="none")],
                disabled=True,
                row=row,
            )
            return
        sel = str(settings.get("_char_sel") or "")
        options = []
        for ch in chars[:25]:
            cid = str(ch.get("id"))
            name = (ch.get("name") or "").strip() or cid
            cur = overrides.get(cid)
            label = f"{name}｜{_CALC_METHOD_LABELS_JA.get(cur, cur)}" if cur else name
            options.append(discord.SelectOption(label=label[:100], value=cid, default=(cid == sel)))
        super().__init__(placeholder="キャラ別の計算方法を変更（対象を選択）…", options=options, row=row)

    async def callback(self, interaction: discord.Interaction):
        if not self.panel._guard(interaction):
            await interaction.response.send_message(
                embed=error_embed("このパネルは実行者のみ操作できます。"), ephemeral=True
            )
            return
        save_user_settings(self.panel.author_id, {"_char_sel": self.values[0]})
        await self.panel.rerender(interaction)


class _PerCharCalcSelect(discord.ui.Select):
    """選択中キャラの計算方法。「自動（キャラ毎の推奨）」でキャラ別上書きを解除。"""

    def __init__(self, panel: "SettingsPanelView", row: int = None):
        settings = load_user_settings(panel.author_id)
        self.panel = panel
        target = str(settings.get("_char_sel") or "")
        names = {str(c.get("id")): (c.get("name") or "").strip() for c in (settings.get("_char_list") or [])}
        name = names.get(target) or target
        overrides = per_char_calc_methods(settings)
        cur = overrides.get(target) or settings.get("calc_method") or "auto"
        options = [
            discord.SelectOption(label=lab, value=val, default=(str(val) == str(cur)))
            for val, lab in _CALC_SELECT_CHOICES
        ]
        super().__init__(
            placeholder=f"{name[:40]} の計算方法…" if target else "キャラ別の計算方法（対象未選択）",
            options=options,
            disabled=not target,
            row=row,
        )
        self.target = target

    async def callback(self, interaction: discord.Interaction):
        if not self.panel._guard(interaction):
            await interaction.response.send_message(
                embed=error_embed("このパネルは実行者のみ操作できます。"), ephemeral=True
            )
            return
        if not self.target:
            await interaction.response.defer()
            return
        set_per_char_calc_method(self.panel.author_id, self.target, self.values[0])
        await self.panel.rerender(interaction)


class SettingsPanelView(discord.ui.View):
    """/setting の設定パネル。現在値を Select の default / ボタンラベルに反映し、
    操作のたびに即保存してパネルを再描画する。"""

    def __init__(self, author_id: int):
        super().__init__(timeout=600)
        self.author_id = author_id
        self.message = None
        settings = load_user_settings(author_id)

        def options_for(choices, current):
            return [
                discord.SelectOption(label=lab, value=val, default=(str(val) == str(current)))
                for val, lab in choices
            ]

        # 共通の計算方法は常に「自動（キャラ毎の推奨）」。
        # 計算方法の変更はキャラ別設定のみ（/setting のキャラ別セレクト or カード生成後の「計算方法を変更」）。
        self.add_item(_SettingsSelect(self, "theme", "カードのテーマ…",
                                      options_for(_THEME_SELECT_CHOICES, settings.get("theme")), row=0))
        self.add_item(_CharTargetSelect(self, row=1))
        self.add_item(_PerCharCalcSelect(self, row=2))
        # 行は最大5行。トグル×3 は同じ行に収める
        for key, label in (("substat_dots", "伸び値ドット"), ("show_uid", "UID表示"), ("base_prec", "表示精度")):
            off_v, on_v = _TOGGLE_VALUES[key]
            on = str(settings.get(key)) == on_v
            on_text, off_text = _TOGGLE_LABELS[key]
            self.add_item(_SettingsToggle(self, key, label, on, on_text, off_text, row=3))

    def _guard(self, interaction: discord.Interaction) -> bool:
        # ephemeral なので通常本人しか操作できないが、念のため本人限定にする
        return interaction.user.id == self.author_id

    def _panel_embed(self, settings: dict) -> discord.Embed:
        embed = discord.Embed(
            title="ビルドカード設定",
            description=(
                "選択・ボタンは押した瞬間に保存されます（保存ボタンはありません）。\n"
                "設定はあなたごとに保存され、次回以降の `/buildcard` に適用されます。\n"
                "スコア計算はキャラ別に設定します（共通は常に「自動」。\n"
                "キャラ別は3段目のセレクト、またはカード生成後の「計算方法を変更」ボタンから設定）。"
            ),
            color=SELECT_COLOR,
        )
        theme = _THEME_LABELS_JA.get(settings.get("theme"), str(settings.get("theme")))
        embed.add_field(name="カードのテーマ", value=theme, inline=True)
        embed.add_field(
            name="表示の精度（基礎ステータス）",
            value="小数点2桁" if str(settings.get("base_prec")) == "2" else "整数",
            inline=True,
        )
        # キャラ別の上書き一覧（名前は /buildcard で取得した一覧から解決）
        overrides = per_char_calc_methods(settings)
        if overrides:
            names = {str(c.get("id")): ((c.get("name") or "").strip() or str(c.get("id")))
                     for c in (settings.get("_char_list") or [])}
            lines = [
                f"{names.get(cid, cid)}: {_CALC_METHOD_LABELS_JA.get(m, m)}"
                for cid, m in list(overrides.items())[:8]
            ]
            if len(overrides) > 8:
                lines.append(f"…他 {len(overrides) - 8} 件")
            embed.add_field(name="キャラ別の計算方法", value="\n".join(lines), inline=False)
        embed.add_field(
            name="UID（/buildcard 用の保存済みUID）",
            value=get_user_uid(self.author_id) or "未設定（/buildcard で指定）",
            inline=True,
        )
        return embed

    async def rerender(self, interaction: discord.Interaction):
        settings = load_user_settings(self.author_id)
        new_view = SettingsPanelView(self.author_id)
        new_view.message = self.message
        await interaction.response.edit_message(embed=self._panel_embed(settings), view=new_view)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            if self.message is not None:
                await self.message.edit(view=self)
        except Exception:
            pass


@bot.tree.command(name="setting", description="ビルドカードの設定（テーマ・表示など。計算方法はカード生成後に変更）")
async def setting_command(interaction: discord.Interaction):
    view = SettingsPanelView(interaction.user.id)
    await interaction.response.send_message(
        embed=view._panel_embed(load_user_settings(interaction.user.id)),
        view=view,
        ephemeral=True,  # 本人にのみ見える
    )
    try:
        view.message = await interaction.original_response()
    except Exception:
        pass


# ------------------------------------------------------------------
#  コマンド
# ------------------------------------------------------------------
@bot.tree.command(name="buildcard", description="UID のショーケースからキャラを選んでビルドカード画像を生成します")
@app_commands.describe(
    uid="ゲーム内プロフィールの UID（数字）。省略すると保存済みのUIDを使用します",
    calc_method="スコア計算方法（省略時は /setting の設定値）",
    theme="カードのテーマ（省略時は /setting の設定値）",
)
@app_commands.choices(
    calc_method=[app_commands.Choice(name=lab, value=val) for val, lab in _CALC_SELECT_CHOICES if val != "auto"],
    theme=[app_commands.Choice(name=lab, value=val) for val, lab in _THEME_SELECT_CHOICES],
)
async def buildcard(interaction: discord.Interaction, uid: str = None,
                    calc_method: app_commands.Choice[str] = None,
                    theme: app_commands.Choice[str] = None):
    user_id = interaction.user.id
    uid = str(uid or "").strip()

    if not uid:
        # UID 未指定 → このユーザーの保存済みUIDを使う
        saved = get_user_uid(user_id)
        if saved:
            uid = str(saved).strip()
        else:
            await interaction.response.send_message(
                embed=error_embed("UID が指定されておらず、保存済みのUIDもありません。\n`/buildcard uid:あなたのUID` のように指定してください。"),
                ephemeral=True,
            )
            return

    if not uid.isdigit() or len(uid) > 20:
        await interaction.response.send_message(embed=error_embed("UID は数字のみで入力してください。"), ephemeral=True)
        return

    # このユーザーのUIDとして保存（次回は uid 省略で使える）
    save_user_uid(user_id, uid)

    # /setting の保存設定 + 今回だけの上書きオプション
    user_settings = load_user_settings(user_id)
    if calc_method is not None:
        user_settings["calc_method"] = calc_method.value
        user_settings["_calc_opt"] = True  # 今回のみ有効（キャラ別設定より優先）
    if theme is not None:
        user_settings["theme"] = theme.value

    await interaction.response.defer(thinking=True)

    char_list, meta, err = await fetch_char_list(uid)
    if err:
        await interaction.followup.send(embed=error_embed(err))
        return
    if not char_list:
        await interaction.followup.send(embed=error_embed("キャラクターが見つかりませんでした。"))
        return

    # /setting のキャラ別計算方法の対象選択用に、取得したキャラ一覧を覚えておく（先頭 25 件）
    save_user_settings(user_id, {"_char_list": [
        {
            "id": str(ch.get("id")),
            "name": (ch.get("name") or "").strip() or str(ch.get("id")),
            "element": ch.get("element") or "",
        }
        for ch in char_list[:25]
    ]})

    # データソース表示（CT 中でキャッシュを使ったか / 新規取得したか）
    source = (meta or {}).get("source", "")
    cooldown_left = (meta or {}).get("cooldown") or 0.0
    if source == "cache":
        if cooldown_left > 0:
            source_text = f"キャッシュ（Enka CT 中・あと {int(cooldown_left)} 秒）"
        else:
            source_text = "キャッシュ"
    else:
        source_text = "新規取得"

    options = []
    element_map = {}
    for ch in char_list[:25]:  # Discord の選択メニュー上限は 25 件
        cid = str(ch.get("id"))
        name = (ch.get("name") or "").strip() or cid
        element_map[cid] = ch.get("element") or ""
        options.append(
            discord.SelectOption(
                label=name[:100],
                value=cid[:100],
            )
        )

    embed = discord.Embed(
        title="ビルドカード生成",
        description="下のメニューからキャラクターを選んでください。",
        color=SELECT_COLOR,
    )
    embed.add_field(name="UID", value=uid, inline=True)
    embed.add_field(name="キャラクター数", value=str(len(char_list)), inline=True)
    embed.add_field(name="データソース", value=source_text, inline=True)
    settings_text = settings_summary(user_settings) + "\n`/setting` で変更できます"
    if calc_method is not None or theme is not None:
        settings_text += "\n※オプション指定は今回のみ有効"
    embed.add_field(name="設定", value=settings_text, inline=False)
    if len(char_list) > 25:
        embed.set_footer(text=f"キャラ数が多いため先頭 25 件を表示しています（全 {len(char_list)} 件）")

    view = BuildCardView(uid, options, element_map, user_id, user_settings)
    msg = await interaction.followup.send(embed=embed, view=view)
    view.message = msg


@bot.tree.error
async def _on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """スラッシュコマンドの未処理エラーをログとステータスに記録する。"""
    _record_error("app_command", error)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=error_embed(f"内部エラーが発生しました: {error}"), ephemeral=True)
        else:
            await interaction.response.send_message(embed=error_embed(f"内部エラーが発生しました: {error}"), ephemeral=True)
    except Exception:
        pass


# ------------------------------------------------------------------
#  起動
# ------------------------------------------------------------------
# /admin コマンドは setup_hook 内で登録される（admin_panel.register_admin_command）


@bot.event
async def on_ready():
    global _synced, _LAST_READY_AT
    try:
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
        else:
            await bot.tree.sync()
        _synced = True
    except Exception as e:
        _synced = False
        print(f"[Warn] コマンド同期に失敗: {e}")
    _LAST_READY_AT = datetime.now().isoformat(timespec="seconds")
    print(f"[Ready] Logged in as {bot.user} (id={bot.user.id}) | API={API_BASE}")
    _write_status(True)
    if not _heartbeat.is_running():
        _heartbeat.start()


def main():
    global bot
    if not BOT_TOKEN:
        raise SystemExit(
            "NEWBC_BOT_TOKEN が設定されていません。プロジェクト直下の .env に Discord bot トークンを設定してください。"
        )
    # POSIX の SIGTERM で gracfully に終了する（Admin 停止ボタン時）
    if sys.platform != "win32":
        def _sigterm(signum, frame):
            raise SystemExit(0)
        signal.signal(signal.SIGTERM, _sigterm)
    print(f"[Bot] starting (pid={os.getpid()}) API={API_BASE} guild_sync={'guild:' + GUILD_ID if GUILD_ID else 'global'}")
    _write_status(False)
    try:
        try:
            bot.run(BOT_TOKEN)
        except discord.PrivilegedIntentsRequired:
            # Developer Portal で MESSAGE CONTENT INTENT が未有効の場合。
            # jishaku（!jsk）だけ諦めて、それ以外の機能で起動する。
            print("[Warn] MESSAGE CONTENT INTENT が無効のため !jsk は使えません"
                  "（Developer Portal → Bot → Privileged Gateway Intents で有効化すると使えます）")
            intents.message_content = False
            bot = _NewBCBot(command_prefix="!", intents=intents)
            bot.run(BOT_TOKEN)
    except discord.LoginFailure as e:
        _record_error("login", e)
        raise
    finally:
        _write_status(False)


if __name__ == "__main__":
    main()
