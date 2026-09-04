# -*- coding: utf-8 -*-
"""
Discord bot 用 /admin コマンド（adminパネルの機能を Discord から操作する）
==============================================================
Web の admin パネルと同じデータ（static/data, static/admin 配下の設定ファイルと
app 側の管理関数）を直接読み書きする。

実行できるユーザー:
    環境変数 ADMIN_DISCORD_USERID に書かれた Discord ユーザーIDのみ
    （カンマ区切りで複数可）。未設定なら誰も実行できない。
    ※ .env はこのモジュールからは読み書きしない（値の記入は人間が行う）。

UI:
    ホーム = カテゴリ選択ボタン（1ページ 6 個。5個目の位置が ←、8個目の位置が →）。
    カテゴリ詳細パネルでボタン操作。全メッセージは本人にのみ見える（ephemeral）。

カテゴリ（Webパネルのタブに対応）:
    data      データ管理（バージョン状態 / nanoka全取得ボタン / live昇格）
    assets    アセット取得（ネームカード/pfp 一括取得・欠落チェック/修復）
    leyline   Leyline バージョン取得
    calc      デフォルト計算方式
    flags     表示設定（UIフラグ）
    logs      操作ログ
    bot       Bot管理（状態・ログクリア・再起動・停止）
"""
import asyncio
import json
import os
import sys

import discord

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BOT_DIR = os.path.dirname(os.path.abspath(__file__))
if _BASE_DIR not in sys.path:
    sys.path.insert(0, _BASE_DIR)

ADMIN_PANEL_COLOR = 0xF1C40F

# ------------------------------------------------------------------
#  権限（環境変数の Discord userID のみ実行可能）
# ------------------------------------------------------------------
def _admin_user_ids() -> set:
    raw = (os.environ.get("ADMIN_DISCORD_USERID")
           or os.environ.get("ADMIN_DISCORD_USERIDS")
           or os.environ.get("ADMIN_DISCORD_USER_IDS") or "")
    return {s.strip() for s in raw.replace(";", ",").split(",") if s.strip()}


def is_admin_user(user_id) -> bool:
    """環境変数 ADMIN_DISCORD_USERID に列挙されたユーザーかどうか。"""
    ids = _admin_user_ids()
    return bool(ids) and str(user_id) in ids


# ------------------------------------------------------------------
#  app 側モジュールへの遅延アクセス（bot 起動を轻く保つ）
# ------------------------------------------------------------------
def _dm():
    from app.admin_data import DataManager
    return DataManager(_BASE_DIR)


def _adm():
    import app.routes.admin as m
    return m


def _jsonio():
    from app.core.jsonio import write_json_atomic
    return write_json_atomic


def _catalog():
    """{ベースID: 表示名} の辞書（Webパネルと同じカタログ）。"""
    try:
        out = {}
        for entry in _adm()._build_admin_character_catalog():
            eid = str(entry.get("id"))
            name = (entry.get("name") or entry.get("janame") or "").strip() or eid
            out[eid] = name
        return out
    except Exception:
        return {}


def _fmt_logs(logs, limit=10) -> str:
    lines = []
    for log in (logs or [])[:limit]:
        t = str(log.get("at") or log.get("time") or "")[:19]
        ok = "✅" if log.get("ok") else "❌"
        lines.append(f"{ok} `{t}` {log.get('action')}: {str(log.get('message') or '')[:60]}")
    return "\n".join(lines) if lines else "（記録なし）"


def _err(message: str) -> discord.Embed:
    return discord.Embed(title="エラー", description=message, color=0xE74C3C)


# ------------------------------------------------------------------
#  共通 UI
# ------------------------------------------------------------------
def _text_input(label: str, *, required: bool = True, default: str = None,
                max_len: int = 64, placeholder: str = None,
                style: discord.TextStyle = discord.TextStyle.short) -> discord.ui.TextInput:
    return discord.ui.TextInput(
        label=label[:45], required=required, default=default, max_length=max_len,
        placeholder=placeholder, style=style,
    )


class AdminModal(discord.ui.Modal):
    """汎用モーダル。fields=[(key, TextInput)] を受け取り、on_submit で
    handler(interaction, values) を呼ぶ。handler 側でパネルを再描画する。"""

    def __init__(self, title: str, fields, handler):
        super().__init__(title=str(title)[:45])
        self._fields = fields
        self._handler = handler
        for _, item in fields:
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        values = {key: str(item.value or "").strip() for key, item in self._fields}
        await self._handler(interaction, values)


# ------------------------------------------------------------------
#  共通 UI（ホームのページング / 戻るボタン）
# ------------------------------------------------------------------
CATEGORIES_PER_PAGE = 6   # 1ページに表示するカテゴリボタン数


class _PageNavButton(discord.ui.Button):
    """ホームの ← / → ページ送りボタン（5個目と8個目の位置に置く）。"""

    def __init__(self, panel: "AdminHomeView", delta: int, row: int, enabled: bool):
        super().__init__(label="← 前のページ" if delta < 0 else "次のページ →",
                         style=discord.ButtonStyle.secondary, row=row, disabled=not enabled)
        self.panel = panel
        self.delta = delta

    async def callback(self, interaction: discord.Interaction):
        if not self.panel._guard(interaction):
            await interaction.response.send_message(embed=_err("このパネルは実行者のみ操作できます。"), ephemeral=True)
            return
        self.panel.page = max(0, min(self.panel.max_page, self.panel.page + self.delta))
        await self.panel.rerender(interaction)


class _CategoryButton(discord.ui.Button):
    """ホームのカテゴリ選択ボタン。"""

    def __init__(self, panel: "AdminHomeView", value: str, label: str, row: int):
        super().__init__(label=str(label)[:80], style=discord.ButtonStyle.primary, row=row)
        self.panel = panel
        self.value = value

    async def callback(self, interaction: discord.Interaction):
        if not self.panel._guard(interaction):
            await interaction.response.send_message(embed=_err("このパネルは実行者のみ操作できます。"), ephemeral=True)
            return
        embed, view = render_category(self.value, self.panel.author_id)
        view.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=view)


class _AdminSubView(discord.ui.View):
    """カテゴリ配下の共通ビュー。rerender で自分のカテゴリを再描画する。"""

    category = None

    def __init__(self, author_id: int):
        super().__init__(timeout=600)
        self.author_id = author_id
        self.message = None

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    async def rerender(self, interaction: discord.Interaction):
        embed, view = render_category(self.category, self.author_id)
        view.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=view)

    async def rerender_home(self, interaction: discord.Interaction):
        home = AdminHomeView(self.author_id)
        home.message = interaction.message
        await interaction.response.edit_message(embed=admin_home_embed(), view=home)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            if self.message is not None:
                await self.message.edit(view=self)
        except Exception:
            pass


class _HomeButton(discord.ui.Button):
    """ホームへ戻るボタン（全カテゴリ共通で最終行に付ける）。"""

    def __init__(self):
        super().__init__(label="ホームへ戻る", style=discord.ButtonStyle.primary, row=4)

    async def callback(self, interaction: discord.Interaction):
        view = self.view
        if not view._guard(interaction):
            await interaction.response.send_message(embed=_err("このパネルは実行者のみ操作できます。"), ephemeral=True)
            return
        await view.rerender_home(interaction)


def _add_home_button(view: _AdminSubView):
    view.add_item(_HomeButton())


class _AdminActionButton(discord.ui.Button):
    """モーダルや共通コールバックを開く汎用ボタン。"""

    def __init__(self, label: str, callback, style, row: int, parent: _AdminSubView):
        super().__init__(label=label[:80], style=style, row=row)
        self._cb = callback
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        if not self.parent._guard(interaction):
            await interaction.response.send_message(embed=_err("このパネルは実行者のみ操作できます。"), ephemeral=True)
            return
        await self._cb(interaction)


# ------------------------------------------------------------------
#  ホーム / カテゴリ定義
# ------------------------------------------------------------------
CATEGORIES = [
    ("data", "データ管理"),
    ("assets", "アセット取得"),
    ("leyline", "Leyline"),
    ("calc", "デフォルト計算方式"),
    ("flags", "表示設定"),
    ("logs", "操作ログ"),
    ("bot", "Bot管理"),
]


def admin_home_embed(page: int = 0) -> discord.Embed:
    total = (len(CATEGORIES) + CATEGORIES_PER_PAGE - 1) // CATEGORIES_PER_PAGE
    desc = "ボタンからカテゴリを選択してください。\n※このメッセージはあなたにのみ見えます。"
    if total > 1:
        desc += f"\nページ {page + 1} / {total}"
    return discord.Embed(title="Admin パネル（Discord）", description=desc, color=ADMIN_PANEL_COLOR)


class AdminHomeView(discord.ui.View):
    """/admin のホーム: カテゴリ選択ボタン（ページング付き）。

    ボタン配置（4列×2行 = 8枠/ページ、カテゴリは 6 個まで）:
        [c1] [c2] [c3] [c4]
        [← ] [c5] [c6] [→ ]    ← が5個目の位置、→ が8個目の位置
    """

    def __init__(self, author_id: int, page: int = 0):
        super().__init__(timeout=600)
        self.author_id = author_id
        self.message = None
        self.max_page = max(0, (len(CATEGORIES) - 1) // CATEGORIES_PER_PAGE)
        self.page = max(0, min(self.max_page, page))
        chunk = CATEGORIES[self.page * CATEGORIES_PER_PAGE:(self.page + 1) * CATEGORIES_PER_PAGE]
        row1, row2 = chunk[:4], chunk[4:6]
        for value, label in row1:
            self.add_item(_CategoryButton(self, value, label, row=0))
        # 5個目の位置（row2 の先頭）= ← 前ページ、8個目の位置（row2 の末尾）= → 次ページ
        self.add_item(_PageNavButton(self, -1, row=1, enabled=self.page > 0))
        for value, label in row2:
            self.add_item(_CategoryButton(self, value, label, row=1))
        self.add_item(_PageNavButton(self, +1, row=1, enabled=self.page < self.max_page))

    def _guard(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.author_id

    async def rerender(self, interaction: discord.Interaction):
        view = AdminHomeView(self.author_id, self.page)
        view.message = self.message
        await interaction.response.edit_message(embed=admin_home_embed(self.page), view=view)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            if self.message is not None:
                await self.message.edit(view=self)
        except Exception:
            pass


def render_category(category: str, author_id: int):
    render = _RENDERERS.get(category)
    if render is None:
        return _err(f"不明なカテゴリ: {category}"), AdminHomeView(author_id)
    return render(author_id)


# ------------------------------------------------------------------
#  data: データ管理
# ------------------------------------------------------------------
def _snapshot_embed() -> discord.Embed:
    try:
        snap = _dm().status_snapshot()
    except Exception as e:
        return _err(f"状態の取得に失敗しました: {e}")
    state = snap.get("state") or {}
    counts = snap.get("counts") or {}
    desc = (
        f"Live: `{state.get('live') or '—'}` / Beta: `{state.get('beta') or '—'}`\n"
        f"データソース: `{state.get('source') or '—'}` / 最終beta取得: `{state.get('last_beta_fetch') or '—'}`\n"
        f"キャラJSON: live {counts.get('live_characters', 0)} / beta {counts.get('beta_characters', 0)}"
    )
    embed = discord.Embed(title="データ管理", description=desc, color=ADMIN_PANEL_COLOR)
    embed.add_field(name="操作ログ（最新）", value=_fmt_logs(snap.get("logs"), 5), inline=False)
    embed.set_footer(text="取得・昇格は完了まで時間がかかります（進捗はWebパネルのログで確認できます）")
    return embed


def render_data(author_id: int):
    view = _DataView(author_id)
    return _snapshot_embed(), view


class _DataView(_AdminSubView):
    category = "data"

    def __init__(self, author_id: int):
        super().__init__(author_id)
        # Webパネル（データ管理タブ）の全アクションボタン。5個/行 × 4行 + ホーム
        actions = [
            # --- beta（1〜2行目） ---
            ("beta:キャラJSON", "fetch_beta_nanoka_json_characters", discord.ButtonStyle.primary, 0),
            ("beta:武器JSON", "fetch_beta_nanoka_json_weapons", discord.ButtonStyle.primary, 0),
            ("beta:聖遺物JSON", "fetch_beta_nanoka_json_artifacts", discord.ButtonStyle.primary, 0),
            ("beta:JSON全部", "fetch_beta_nanoka_json", discord.ButtonStyle.primary, 0),
            ("beta:画像のみ", "fetch_beta_nanoka_assets", discord.ButtonStyle.primary, 0),
            ("beta:全取得", "fetch_beta_nanoka", discord.ButtonStyle.primary, 1),
            ("beta:コスチューム", "fetch_beta_nanoka_costumes", discord.ButtonStyle.primary, 1),
            ("beta:不足衣装", "fetch_beta_nanoka_costumes_missing", discord.ButtonStyle.primary, 1),
            # --- live（2〜3行目） ---
            ("live:キャラJSON", "fetch_live_nanoka_json_characters", discord.ButtonStyle.secondary, 1),
            ("live:武器JSON", "fetch_live_nanoka_json_weapons", discord.ButtonStyle.secondary, 1),
            ("live:聖遺物JSON", "fetch_live_nanoka_json_artifacts", discord.ButtonStyle.secondary, 2),
            ("live:JSON全部", "fetch_live_nanoka_json", discord.ButtonStyle.secondary, 2),
            ("live:画像のみ", "fetch_live_nanoka_assets", discord.ButtonStyle.secondary, 2),
            ("live:全取得", "fetch_live_nanoka", discord.ButtonStyle.secondary, 2),
            ("live:コスチューム", "fetch_live_nanoka_costumes", discord.ButtonStyle.secondary, 2),
            ("live:不足衣装", "fetch_live_nanoka_costumes_missing", discord.ButtonStyle.secondary, 3),
            # --- 昇格（3行目） ---
            ("beta→live昇格", "version_upgrade_live", discord.ButtonStyle.danger, 3),
        ]
        for label, action, style, row in actions:
            self.add_item(_DataManagerActionButton(label, action, style, row, self))
        _add_home_button(self)


class _DataManagerActionButton(discord.ui.Button):
    def __init__(self, label: str, action: str, style, row: int, parent: "_DataView"):
        super().__init__(label=label, style=style, row=row)
        self.action = action
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        if not self.parent._guard(interaction):
            await interaction.response.send_message(embed=_err("このパネルは実行者のみ操作できます。"), ephemeral=True)
            return
        await interaction.response.defer()
        try:
            result = await asyncio.to_thread(getattr(_dm(), self.action))
        except Exception as e:
            result = {"ok": False, "error": str(e)}
        ok = bool(result.get("ok"))
        msg = str(result.get("error") or result.get("message") or "")
        detail = result.get("detail")
        if not msg and detail is not None:
            msg = str(detail)[:120]
        _dm().append_log(f"discord_{self.action}", ok, msg[:200])
        snap = _dm().status_snapshot()
        state = snap.get("state") or {}
        counts = snap.get("counts") or {}
        embed = discord.Embed(
            title="データ管理",
            description=(
                f"Live: `{state.get('live') or '—'}` / Beta: `{state.get('beta') or '—'}`\n"
                f"キャラJSON: live {counts.get('live_characters', 0)} / beta {counts.get('beta_characters', 0)}"
            ),
            color=ADMIN_PANEL_COLOR if ok else 0xE74C3C,
        )
        embed.add_field(name=f"結果: {self.action}", value=("✅ 成功" if ok else f"❌ {msg or '失敗'}")[:1000], inline=False)
        embed.add_field(name="操作ログ（最新）", value=_fmt_logs(snap.get("logs"), 5), inline=False)
        embed.set_footer(text="※バージョン状態の詳細は「ホームへ戻る」→ 再度カテゴリ選択で更新されます")
        view = _DataView(self.parent.author_id)
        view.message = interaction.message
        await interaction.edit_original_response(embed=embed, view=view)


# ------------------------------------------------------------------
#  assets: アセット取得（ネームカード/pfp・欠落チェック）
# ------------------------------------------------------------------
def _asset_status_text() -> str:
    try:
        a = _adm()
        nc_map = a._load_enka_asset_map("namecards.json")
        pfp_map = a._load_enka_asset_map("pfps.json")
        nc_local = len(os.listdir(a._NAMECARD_DIR)) if os.path.isdir(a._NAMECARD_DIR) else 0
        pfp_local = len(os.listdir(a._PFP_DIR)) if os.path.isdir(a._PFP_DIR) else 0
        return (f"ネームカード {nc_local} / {max(len(nc_map), nc_local)} ・ "
                f"プロフ {pfp_local} / {max(len(pfp_map), pfp_local)}")
    except Exception as e:
        return f"（状態取得失敗: {e}）"


def render_assets(author_id: int):
    embed = discord.Embed(
        title="アセット取得",
        description=(
            f"{_asset_status_text()}\n\n"
            "モードを選んで「欠落チェック / 欠落修復」を実行してください。\n"
            "一括取得ボタンは既存ファイルをスキップし、不足分だけ取得します。"
        ),
        color=ADMIN_PANEL_COLOR,
    )
    view = _AssetsView(author_id)
    return embed, view


class _AssetsModeSelect(discord.ui.Select):
    def __init__(self, parent: "_AssetsView"):
        super().__init__(placeholder="欠落チェックの対象…", options=[
            discord.SelectOption(label="live", value="live", default=parent.mode == "live"),
            discord.SelectOption(label="beta", value="beta", default=parent.mode == "beta"),
        ], row=0)
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        if not self.parent._guard(interaction):
            await interaction.response.send_message(embed=_err("このパネルは実行者のみ操作できます。"), ephemeral=True)
            return
        self.parent.mode = self.values[0]
        await self.parent.rerender(interaction)


class _AssetsView(_AdminSubView):
    category = "assets"

    def __init__(self, author_id: int):
        super().__init__(author_id)
        self.mode = "live"
        self.add_item(_AssetsModeSelect(self))
        buttons = [
            ("欠落チェック", _AssetsView._run_check, discord.ButtonStyle.secondary, 1),
            ("欠落修復", _AssetsView._run_fix, discord.ButtonStyle.primary, 1),
            ("nanoka取得", _AssetsView._run_nanoka, discord.ButtonStyle.primary, 2),
            ("lunaris取得", _AssetsView._run_lunaris, discord.ButtonStyle.secondary, 2),
            ("enkaのみ取得", _AssetsView._run_enka, discord.ButtonStyle.secondary, 2),
        ]
        for label, cb, style, row in buttons:
            self.add_item(_AssetsActionButton(label, cb, style, row, self))
        _add_home_button(self)

    async def _run_check(self, interaction: discord.Interaction):
        await interaction.response.defer()
        result = await asyncio.to_thread(_missing_check_run, self.mode)
        await _assets_view_render_result(self, interaction, f"欠落チェック（{self.mode}）", result)

    async def _run_fix(self, interaction: discord.Interaction):
        await interaction.response.defer()
        result = await asyncio.to_thread(_missing_fix_run, self.mode)
        await _assets_view_render_result(self, interaction, f"欠落修復（{self.mode}）", result)

    async def _run_bulk(self, interaction: discord.Interaction, kind: str):
        await interaction.response.defer()
        a = _adm()
        if kind == "nanoka":
            result = await asyncio.to_thread(a._bulk_fetch_namecards_pfps_nanoka)
        elif kind == "lunaris":
            result = await asyncio.to_thread(a._bulk_fetch_namecards_pfps_lunaris)
        else:
            result = await asyncio.to_thread(a._bulk_fetch_namecards_pfps)
        ok = bool(result.get("ok"))
        nc = result.get("namecards") or {}
        pfp = result.get("pfps") or {}
        src = (result.get("sources") or {})
        desc = (f"ネームカード: 新規 {nc.get('new', 0)} / 失敗 {nc.get('failed', 0)}（一覧: {src.get('namecard_list') or '—'}）\n"
                f"プロフ: 新規 {pfp.get('new', 0)} / 失敗 {pfp.get('failed', 0)}（{json.dumps(src.get('pfp_list') or {}, ensure_ascii=False)}）")
        embed = discord.Embed(
            title=f"一括取得（{kind}）: {'✅ 成功' if ok else '❌ 失敗'}",
            description=desc, color=ADMIN_PANEL_COLOR if ok else 0xE74C3C,
        )
        embed.add_field(name="一括取得の状態", value=_asset_status_text(), inline=False)
        new_view = _AssetsView(self.author_id)
        new_view.mode = self.mode
        new_view.message = interaction.message
        await interaction.edit_original_response(embed=embed, view=new_view)

    async def _run_nanoka(self, interaction: discord.Interaction):
        await self._run_bulk(interaction, "nanoka")

    async def _run_lunaris(self, interaction: discord.Interaction):
        await self._run_bulk(interaction, "lunaris")

    async def _run_enka(self, interaction: discord.Interaction):
        await self._run_bulk(interaction, "enka")


class _AssetsActionButton(discord.ui.Button):
    def __init__(self, label, callback, style, row, parent: "_AssetsView"):
        super().__init__(label=label, style=style, row=row)
        self._cb = callback
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        if not self.parent._guard(interaction):
            await interaction.response.send_message(embed=_err("このパネルは実行者のみ操作できます。"), ephemeral=True)
            return
        await self._cb(interaction)


def _missing_fix_run(mode: str):
    """欠落アイコンを CDN から再取得する（Webパネルの missing_fix と同じ処理）。"""
    a = _adm()
    result = a._scan_character_asset_refs(mode)
    skills_dir = result["skills_dir"]
    icon_dirs = result.get("icon_dirs") or []
    os.makedirs(skills_dir, exist_ok=True)
    for d in icon_dirs:
        os.makedirs(d, exist_ok=True)
    fixed, failed = [], []
    for m in result["missing"]:
        icon = m["icon"]
        dest_dir = icon_dirs[0] if m.get("kind") == "icon" else skills_dir
        dest = os.path.join(dest_dir, f"{icon}.webp")
        try:
            import requests
            r = requests.get(f"https://static.nanoka.cc/assets/gi/{icon}.webp", timeout=15)
            r.raise_for_status()
            with open(dest, "wb") as f:
                f.write(r.content)
            fixed.append(icon)
        except Exception as e:
            failed.append({"icon": icon, "error": str(e)})
    return {"total": len(result["refs"]), "missing_before": len(result["missing"]),
            "fixed": fixed, "failed": failed}


def _missing_check_run(mode: str):
    a = _adm()
    result = a._scan_character_asset_refs(mode)
    import requests
    checked = []
    for m in result["missing"][:40]:
        try:
            r = requests.head(f"https://static.nanoka.cc/assets/gi/{m['icon']}.webp", timeout=8)
            cdn = r.status_code == 200
        except Exception:
            cdn = None
        checked.append({**m, "cdn": cdn})
    return {"total": len(result["refs"]), "missing": result["missing"], "checked": checked}


async def _assets_view_render_result(parent: "_AssetsView", interaction: discord.Interaction,
                                     title_ok: str, result: dict):
    lines = []
    if "fixed" in result:
        lines.append(f"修復 {len(result['fixed'])} 件 / 失敗 {len(result['failed'])} 件"
                     f"（対象欠落 {result.get('missing_before', '?')} / 参照 {result.get('total', '?')}）")
        for icon in result["fixed"][:8]:
            lines.append(f"✅ {icon}")
        for item in result["failed"][:8]:
            lines.append(f"❌ {item['icon']}: {item['error'][:40]}")
    else:
        missing = result.get("missing") or []
        lines.append(f"参照 {result.get('total', '?')} 件のうち欠落 {len(missing)} 件")
        for m in (result.get("checked") or [])[:12]:
            cdn = {True: "CDN:あり", False: "CDN:なし", None: "CDN:確認失敗"}[m.get("cdn")]
            lines.append(f"⚠ {m['icon']} ({m['char']} / {m['kind']}) {cdn}")
        if not missing:
            lines.append("🎉 欠落はありません。すべてのアイコンが揃っています。")
    embed = discord.Embed(title=title_ok, description="\n".join(lines)[:4000], color=ADMIN_PANEL_COLOR)
    embed.add_field(name="一括取得の状態", value=_asset_status_text(), inline=False)
    new_view = _AssetsView(parent.author_id)
    new_view.mode = parent.mode
    new_view.message = interaction.message
    await interaction.edit_original_response(embed=embed, view=new_view)


# ------------------------------------------------------------------
#  leyline: Leyline バージョン取得
# ------------------------------------------------------------------
def render_leyline(author_id: int):
    a = _adm()
    manifest = {"live": "—", "latest": "—", "available": []}
    try:
        import requests
        r = requests.get("https://static.nanoka.cc/manifest.json", timeout=15)
        r.raise_for_status()
        gi = (r.json() or {}).get("gi") or {}
        manifest = {"live": gi.get("live"), "latest": gi.get("latest"),
                    "available": (gi.get("available") or [])[-6:]}
    except Exception as e:
        manifest["available"] = [f"（manifest取得失敗: {e}）"]
    saved = a._load_leyline_versions_config()
    embed = discord.Embed(
        title="Leyline",
        description=(
            f"nanoka live: `{manifest.get('live')}` / latest: `{manifest.get('latest')}`\n"
            f"取得済みバージョン（管理設定）: `{', '.join(saved) or 'なし'}`"
        ),
        color=ADMIN_PANEL_COLOR,
    )
    embed.add_field(name="nanoka available（末尾6件）", value=", ".join(map(str, manifest.get("available") or [])) or "—", inline=False)
    view = _LeylineView(author_id)
    return embed, view


class _LeylineView(_AdminSubView):
    category = "leyline"

    def __init__(self, author_id: int):
        super().__init__(author_id)
        self.add_item(_AdminActionButton("現行バージョンを取得", self._fetch_latest, discord.ButtonStyle.primary, 0, self))
        self.add_item(_AdminActionButton("バージョン指定で取得", self._open_fetch, discord.ButtonStyle.secondary, 0, self))
        _add_home_button(self)

    def _do_fetch(self, version: str) -> dict:
        """Webパネルの /admin/api/leyline と同じ処理（詳細+敵画像のダウンロード込み）。"""
        a = _adm()
        version = (version or "").strip()
        if not a._LEYLINE_VERSION_PATTERN.match(version):
            return {"ok": False, "error": "バージョン形式が不正です（例: 6.7.54）"}
        import requests
        from app.convert.leyline import from_nanoka
        live = a._nanoka_live_version()
        cache_path = a._leyline_list_path(version, live)
        try:
            r = requests.get(f"https://static.nanoka.cc/gi/{version}/leyline.json", timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            return {"ok": False, "error": f"leyline.json の取得に失敗しました: {e}"}
        downloaded = 0
        details = {}
        for lid in (data.keys() if isinstance(data, dict) else []):
            try:
                det = a._fetch_leyline_detail(version, str(lid), live)
                details[str(lid)] = det
                downloaded += len(det.get("monsters") or [])
            except Exception:
                continue
        converted = from_nanoka(data, details)
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            _jsonio()(cache_path, converted)
        except Exception:
            pass
        saved = a._load_leyline_versions_config()
        if version not in saved:
            saved.append(version)
            a._save_leyline_versions_config(saved)
        return {"ok": True, "count": len(details), "images": downloaded,
                "saved": a._load_leyline_versions_config()}

    async def _fetch_latest(self, interaction: discord.Interaction):
        await interaction.response.defer()
        result = await asyncio.to_thread(self._do_fetch, str(_adm()._nanoka_live_version() or ""))
        await self._after(interaction, result)

    async def _open_fetch(self, interaction: discord.Interaction):
        modal = AdminModal(
            "バージョン指定で取得",
            [("version", _text_input("バージョン（例: 6.7.54）", placeholder="6.7.54"))],
            self._fetch_version,
        )
        await interaction.response.send_modal(modal)

    async def _fetch_version(self, interaction: discord.Interaction, values: dict):
        await interaction.response.defer()
        result = await asyncio.to_thread(self._do_fetch, values.get("version", ""))
        await self._after(interaction, result)

    async def _after(self, interaction: discord.Interaction, result: dict):
        ok = bool(result.get("ok"))
        if ok:
            note = (f"✅ {result.get('count', 0)} 件のレイライン / 敵画像 {result.get('images', 0)} 枚を取得しました。\n"
                    f"管理バージョン: {', '.join(result.get('saved') or [])}")
        else:
            note = f"❌ {result.get('error') or '失敗'}"
        embed, view = render_leyline(self.author_id)
        embed.add_field(name="結果", value=note[:1000], inline=False)
        view.message = interaction.message
        await interaction.edit_original_response(embed=embed, view=view)


# ------------------------------------------------------------------
#  calc: デフォルト計算方式
# ------------------------------------------------------------------
_CALC_METHOD_OPTIONS = [("crit", "会心のみ"), ("atk", "攻撃力%"), ("hp", "HP%"),
                        ("def", "防御%"), ("em", "元素熟知"), ("charge", "チャージ効率"),
                        ("clear", "設定解除")]


def render_calc(author_id: int):
    from app.card.calc_method import load_default_calc_method_map
    chars = load_default_calc_method_map() or {}
    names = _catalog()
    lines = [f"{names.get(cid, cid)} ({cid}): {m}" for cid, m in sorted(chars.items())]
    embed = discord.Embed(
        title="デフォルト計算方式",
        description="キャラ毎の既定計算方式（ビルドカード生成時の推奨に使われます）。\n"
                    "1段目でキャラ、2段目で方式を選ぶと保存されます。",
        color=ADMIN_PANEL_COLOR,
    )
    embed.add_field(name="現在の設定", value="\n".join(lines)[:2000] if lines else "（設定なし・全キャラ自動）", inline=False)
    view = _CalcDefaultsView(author_id)
    return embed, view


class _CalcTargetSelect(discord.ui.Select):
    def __init__(self, parent: "_CalcDefaultsView"):
        chars = parent.current_map or {}
        names = parent.names
        options = [discord.SelectOption(
            label=f"{names.get(cid, cid)} ({cid}): {m}"[:100], value=cid[:100],
            default=(cid == parent.target_id),
        ) for cid, m in sorted(chars.items())[:25]]
        if not options:
            options = [discord.SelectOption(label="（設定なし・ID指定で追加）", value="none")]
        super().__init__(placeholder="対象キャラを選択（設定済みのみ）…", options=options, row=0)
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        if not self.parent._guard(interaction):
            await interaction.response.send_message(embed=_err("このパネルは実行者のみ操作できます。"), ephemeral=True)
            return
        self.parent.target_id = self.values[0]
        await interaction.response.defer()


class _CalcMethodSelect(discord.ui.Select):
    def __init__(self, parent: "_CalcDefaultsView"):
        options = [discord.SelectOption(label=lab, value=val) for val, lab in _CALC_METHOD_OPTIONS]
        super().__init__(placeholder="適用する計算方式を選択…", options=options, row=1)
        self.parent = parent

    async def callback(self, interaction: discord.Interaction):
        if not self.parent._guard(interaction):
            await interaction.response.send_message(embed=_err("このパネルは実行者のみ操作できます。"), ephemeral=True)
            return
        if not self.parent.target_id or self.parent.target_id == "none":
            await interaction.response.send_message(embed=_err("先に1段目で対象キャラを選択してください。"), ephemeral=True)
            return
        from app.card.calc_method import load_default_calc_method_map, save_default_calc_method_map
        chars = dict(load_default_calc_method_map() or {})
        method = self.values[0]
        if method == "clear":
            chars.pop(self.parent.target_id, None)
            note = f"✅ {self.parent.names.get(self.parent.target_id, self.parent.target_id)} の設定を解除しました"
        else:
            chars[self.parent.target_id] = method
            note = f"✅ {self.parent.names.get(self.parent.target_id, self.parent.target_id)} の既定を {method} に設定しました"
        await asyncio.to_thread(save_default_calc_method_map, chars)
        await self.parent.rerender_after_action(interaction, note)


class _CalcDefaultsView(_AdminSubView):
    category = "calc"

    def __init__(self, author_id: int):
        super().__init__(author_id)
        from app.card.calc_method import load_default_calc_method_map
        self.current_map = load_default_calc_method_map() or {}
        self.names = _catalog()
        self.target_id = None
        self.add_item(_CalcTargetSelect(self))
        self.add_item(_CalcMethodSelect(self))
        self.add_item(_AdminActionButton("ID指定で設定", self._open_set, discord.ButtonStyle.secondary, 2, self))
        _add_home_button(self)

    async def _open_set(self, interaction: discord.Interaction):
        modal = AdminModal(
            "デフォルト計算方式（ID指定）",
            [
                ("char_id", _text_input("キャラID", placeholder="10000124")),
                ("method", _text_input(f"方法（{', '.join(v for v, _ in _CALC_METHOD_OPTIONS)}）", placeholder="crit")),
            ],
            self._save_entry,
        )
        await interaction.response.send_modal(modal)

    async def _save_entry(self, interaction: discord.Interaction, values: dict):
        from app.card.calc_method import load_default_calc_method_map, save_default_calc_method_map
        char_id = values.get("char_id", "")
        method = values.get("method", "")
        if char_id not in self.names:
            await interaction.response.send_message(embed=_err(f"不明なキャラID: {char_id or '（空）'}"), ephemeral=True)
            return
        chars = dict(load_default_calc_method_map() or {})
        if method == "clear":
            chars.pop(char_id, None)
            note = f"✅ {self.names.get(char_id, char_id)} の設定を解除しました"
        elif method in dict(_CALC_METHOD_OPTIONS) and method != "clear":
            chars[char_id] = method
            note = f"✅ {self.names.get(char_id, char_id)} の既定を {method} に設定しました"
        else:
            await interaction.response.send_message(embed=_err(f"方法は {', '.join(v for v, _ in _CALC_METHOD_OPTIONS)} で指定してください。"), ephemeral=True)
            return
        await asyncio.to_thread(save_default_calc_method_map, chars)
        await self.rerender_after_action(interaction, note)

    async def rerender_after_action(self, interaction: discord.Interaction, note: str):
        embed, view = render_calc(self.author_id)
        embed.add_field(name="結果", value=note[:1000], inline=False)
        view.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=view)


# ------------------------------------------------------------------
#  flags: 表示設定（UIフラグ）
# ------------------------------------------------------------------
_FLAG_DEFS = [
    ("show_team_abyss_buttons", "編成/幽境ボタン"),
    ("show_status_view_setting", "ステータス表示トグル"),
    ("show_score_history", "スコア履歴グラフ"),
]


def render_flags(author_id: int):
    from app.card.ui_flags import load_ui_flags
    flags = load_ui_flags()
    lines = [f"{'✅ 表示' if flags.get(key) else '❌ 非表示'} {label}" for key, label in _FLAG_DEFS]
    embed = discord.Embed(
        title="表示設定（UIフラグ）",
        description="ビルドカード画面の要素表示。押すと即保存されます（次に開いたページから反映）。\n"
                    + "\n".join(lines),
        color=ADMIN_PANEL_COLOR,
    )
    view = _FlagsView(author_id)
    return embed, view


class _FlagToggle(discord.ui.Button):
    def __init__(self, key: str, label: str, on: bool, row: int):
        super().__init__(label=f"{label}: {'表示' if on else '非表示'}",
                         style=discord.ButtonStyle.success if on else discord.ButtonStyle.secondary, row=row)
        self.flag_key = key
        self.flag_label = label
        self.on = on

    async def callback(self, interaction: discord.Interaction):
        from app.card.ui_flags import load_ui_flags, save_ui_flags
        view = self.view
        if not view._guard(interaction):
            await interaction.response.send_message(embed=_err("このパネルは実行者のみ操作できます。"), ephemeral=True)
            return
        flags = dict(load_ui_flags())
        flags[self.flag_key] = not bool(flags.get(self.flag_key))
        await asyncio.to_thread(save_ui_flags, flags)
        await view.rerender_after_action(
            interaction,
            f"✅ {self.flag_label} を {'表示' if flags[self.flag_key] else '非表示'} にしました",
        )


class _FlagsView(_AdminSubView):
    category = "flags"

    def __init__(self, author_id: int):
        super().__init__(author_id)
        from app.card.ui_flags import load_ui_flags
        flags = load_ui_flags()
        for i, (key, label) in enumerate(_FLAG_DEFS):
            self.add_item(_FlagToggle(key, label, bool(flags.get(key)), row=0))
        _add_home_button(self)

    async def rerender_after_action(self, interaction: discord.Interaction, note: str):
        embed, view = render_flags(self.author_id)
        embed.add_field(name="結果", value=note[:1000], inline=False)
        view.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=view)


# ------------------------------------------------------------------
#  logs: 操作ログ
# ------------------------------------------------------------------
def render_logs(author_id: int):
    embed = discord.Embed(title="操作ログ", color=ADMIN_PANEL_COLOR)
    try:
        snap = _dm().status_snapshot()
        state = snap.get("state") or {}
        embed.add_field(name="バージョン状態",
                        value=f"Live: `{state.get('live') or '—'}` / Beta: `{state.get('beta') or '—'}`",
                        inline=False)
        embed.add_field(name="操作ログ（最新10件）", value=_fmt_logs(snap.get("logs"), 10)[:1000], inline=False)
    except Exception as e:
        embed.add_field(name="操作ログ", value=f"（取得失敗: {e}）", inline=False)
    view = _LogsView(author_id)
    return embed, view


class _LogsView(_AdminSubView):
    category = "logs"

    def __init__(self, author_id: int):
        super().__init__(author_id)
        self.add_item(_AdminActionButton("再読み込み", self._reload, discord.ButtonStyle.secondary, 0, self))
        _add_home_button(self)

    async def _reload(self, interaction: discord.Interaction):
        await self.rerender_after_action(interaction, "更新しました")

    async def rerender_after_action(self, interaction: discord.Interaction, note: str):
        embed, view = render_logs(self.author_id)
        if note:
            embed.add_field(name="状態", value=note[:1000], inline=False)
        view.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=view)


# ------------------------------------------------------------------
#  bot: Bot管理（状態・ログクリア・再起動・停止）
# ------------------------------------------------------------------
def render_bot(author_id: int):
    embed = discord.Embed(title="Bot管理", color=ADMIN_PANEL_COLOR)
    try:
        with open(os.path.join(_BOT_DIR, "bot_status.json"), "r", encoding="utf-8") as f:
            bs = json.load(f) or {}
        state = "🟢 接続中" if bs.get("connected") else "🔴 切断"
        embed.description = (
            f"{state} {bs.get('user') or '—'}\n"
            f"guilds: {bs.get('guild_count', 0)} / latency: {bs.get('latency_ms') or '—'}ms / "
            f"commands: {bs.get('command_count', 0)}\n"
            f"pid: `{bs.get('pid') or '—'}` / started: `{bs.get('started_at') or '—'}`\n"
            f"last_ready: `{bs.get('last_ready_at') or '—'}`\n"
            f"last_error: `{(bs.get('last_error') or {}).get('error') or 'なし'}`"
        )
    except Exception:
        embed.description = "（bot_status.json が読めません）"
    for label, name in (("bot stdout", "discord_bot.out.log"), ("bot stderr", "discord_bot.err.log")):
        path = os.path.join(_BASE_DIR, "logs", name)
        tail = ""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                tail = "".join(f.readlines()[-8:])
        except Exception:
            tail = "（ログなし）"
        embed.add_field(name=label, value=f"```\n{tail[-900:] or '（空）'}\n```", inline=False)
    view = _BotView(author_id)
    return embed, view


class _BotView(_AdminSubView):
    category = "bot"

    def __init__(self, author_id: int):
        super().__init__(author_id)
        self.add_item(_AdminActionButton("状態を更新", self._reload, discord.ButtonStyle.secondary, 0, self))
        self.add_item(_AdminActionButton("Botログをクリア", self._clear_logs, discord.ButtonStyle.secondary, 0, self))
        self.add_item(_AdminActionButton("Bot再起動", self._restart, discord.ButtonStyle.primary, 0, self))
        self.add_item(_AdminActionButton("Bot停止", self._stop, discord.ButtonStyle.danger, 0, self))
        _add_home_button(self)

    async def _reload(self, interaction: discord.Interaction):
        await self.rerender_after_action(interaction, "更新しました")

    async def _bot_action(self, interaction: discord.Interaction, action: str) -> str:
        await interaction.response.defer()
        try:
            from app.core.bot_manager import bot_manager
            fn = {
                "restart": bot_manager.restart,
                "stop": bot_manager.stop,
                "clear_logs": bot_manager.clear_logs,
            }[action]
            result = await asyncio.to_thread(fn)
        except Exception as e:
            result = {"ok": False, "error": str(e)}
        ok = bool(result.get("ok"))
        return ("✅ " if ok else "❌ ") + str(result.get("message") or result.get("error") or ("成功" if ok else "失敗"))

    async def _clear_logs(self, interaction: discord.Interaction):
        note = await self._bot_action(interaction, "clear_logs")
        try:
            _dm().append_log("bot_clear_logs", note.startswith("✅"), note[2:200])
        except Exception:
            pass
        await self.rerender_after_action(interaction, note)

    async def _restart(self, interaction: discord.Interaction):
        note = await self._bot_action(interaction, "restart")
        try:
            _dm().append_log("bot_restart_from_discord", note.startswith("✅"), note[2:200])
        except Exception:
            pass
        await self.rerender_after_action(interaction, note)

    async def _stop(self, interaction: discord.Interaction):
        note = await self._bot_action(interaction, "stop")
        try:
            _dm().append_log("bot_stop_from_discord", note.startswith("✅"), note[2:200])
        except Exception:
            pass
        await self.rerender_after_action(interaction, note)

    async def rerender_after_action(self, interaction: discord.Interaction, note: str):
        embed, view = render_bot(self.author_id)
        embed.add_field(name="結果", value=note[:1000], inline=False)
        view.message = interaction.message
        await interaction.response.edit_message(embed=embed, view=view)


# ------------------------------------------------------------------
#  レンダラ登録
# ------------------------------------------------------------------
_RENDERERS = {
    "data": render_data,
    "assets": render_assets,
    "leyline": render_leyline,
    "calc": render_calc,
    "flags": render_flags,
    "logs": render_logs,
    "bot": render_bot,
}


# ------------------------------------------------------------------
#  コマンド登録
# ------------------------------------------------------------------
def register_admin_command(bot):
    @bot.tree.command(name="admin", description="adminパネル操作（許可されたユーザーのみ）")
    async def admin_command(interaction: discord.Interaction):
        if not _admin_user_ids():
            await interaction.response.send_message(
                embed=_err("ADMIN_DISCORD_USERID が未設定です。プロジェクト直下の .env にあなたの Discord ユーザーIDを設定してください。"),
                ephemeral=True,
            )
            return
        if not is_admin_user(interaction.user.id):
            await interaction.response.send_message(
                embed=_err("このコマンドは許可されたユーザーのみ実行できます。"),
                ephemeral=True,
            )
            return
        view = AdminHomeView(interaction.user.id)
        await interaction.response.send_message(embed=admin_home_embed(), view=view, ephemeral=True)
        try:
            view.message = await interaction.original_response()
        except Exception:
            pass

    return admin_command
