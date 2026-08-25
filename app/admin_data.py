"""
ゲームデータ取得・beta→live 昇格を担当するモジュール。
get_data_server.py のロジックを FastAPI 向けに移植し、
パスは server.py と同じ BASE_DIR 基準に統一している。

使い方:
  from app.admin_data import DataManager
  dm = DataManager(base_dir)
  dm.fetch_beta_nanoka()
  dm.promote_beta_to_live()
"""

from __future__ import annotations

import json
import os
import shutil
import traceback
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

# convert / gachabase_changelog は app パッケージ配下（app/convert, app/gachabase_changelog）
characters = weapons = characters_list = weapons_list = artifacts_list = None  # type: ignore
gachabase_changelog = None  # type: ignore

def _try_import_convert():
    global characters, weapons, characters_list, weapons_list, artifacts_list
    try:
        from app.convert import characters as _c
        characters = _c
    except Exception as e:
        print(f"[admin_data] convert.characters import failed: {e}")
    try:
        from app.convert import weapons as _w
        weapons = _w
    except Exception as e:
        print(f"[admin_data] convert.weapons import failed: {e}")
    try:
        from app.convert import characters_list as _cl
        characters_list = _cl
    except Exception as e:
        print(f"[admin_data] convert.characters_list import failed: {e}")
    try:
        from app.convert import weapons_list as _wl
        weapons_list = _wl
    except Exception as e:
        print(f"[admin_data] convert.weapons_list import failed: {e}")
    try:
        from app.convert import artifacts as _a
        artifacts_list = _a
    except Exception:
        try:
            from app.convert import artifacts_list as _a  # type: ignore
            artifacts_list = _a
        except Exception as e:
            print(f"[admin_data] convert.artifacts import failed: {e}")

def _try_import_gachabase():
    global gachabase_changelog
    try:
        from app import gachabase_changelog as _g
        gachabase_changelog = _g
    except Exception as e:
        print(f"[admin_data] gachabase_changelog import failed: {e}")

_try_import_convert()
_try_import_gachabase()


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def _safe_json_dump(path: str, data: Any) -> None:
    _ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _safe_json_load(path: str, default: Any = None) -> Any:
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _copy_file(src: str, dst: str) -> bool:
    if not os.path.exists(src):
        return False
    _ensure_dir(os.path.dirname(dst))
    shutil.copy2(src, dst)
    return True


def _copy_tree_files(src_dir: str, dst_dir: str, names: List[str], ext: str = ".json") -> List[str]:
    """names に含まれるファイルを src→dst にコピー。成功した id を返す。"""
    done = []
    for name in names:
        src = os.path.join(src_dir, f"{name}{ext}")
        dst = os.path.join(dst_dir, f"{name}{ext}")
        if _copy_file(src, dst):
            done.append(name)
    return done


class DataManager:
    """static/data (live) と static/beta/data を管理する。"""

    STATE_REL = os.path.join("static", "admin", "version_state.json")
    LOG_REL = os.path.join("static", "admin", "operation_log.json")

    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.state_path = os.path.join(base_dir, self.STATE_REL)
        self.log_path = os.path.join(base_dir, self.LOG_REL)
        _ensure_dir(os.path.join(base_dir, "static", "admin"))
        self._ensure_state()

    # ------------------------------------------------------------------ paths
    def live_dir(self, *parts: str) -> str:
        return os.path.join(self.base_dir, "static", "data", *parts)

    def beta_dir(self, *parts: str) -> str:
        return os.path.join(self.base_dir, "static", "beta", "data", *parts)

    def live_assets(self, *parts: str) -> str:
        return os.path.join(self.base_dir, "static", "assets", *parts)

    def beta_assets(self, *parts: str) -> str:
        return os.path.join(self.base_dir, "static", "beta", "assets", *parts)

    # ------------------------------------------------------------------ state
    def _default_state(self) -> Dict[str, Any]:
        return {
            "live_version": None,
            "beta_version": None,
            "source": None,  # "nanoka" | "lunaris" | "gachabase"
            "pending": {
                "characters": [],
                "weapons": [],
                "artifacts": [],
            },
            "last_beta_fetch": None,
            "last_live_fetch": None,
            "last_promote": None,
            "history": [],  # [{at, action, detail}]
        }

    def _ensure_state(self) -> None:
        if not os.path.exists(self.state_path):
            _safe_json_dump(self.state_path, self._default_state())

    def get_state(self) -> Dict[str, Any]:
        state = _safe_json_load(self.state_path, self._default_state())
        # 欠損キー補完
        base = self._default_state()
        for k, v in base.items():
            if k not in state:
                state[k] = v
        if "pending" not in state or not isinstance(state["pending"], dict):
            state["pending"] = base["pending"]
        for key in ("characters", "weapons", "artifacts"):
            state["pending"].setdefault(key, [])
        return state

    def save_state(self, state: Dict[str, Any]) -> None:
        _safe_json_dump(self.state_path, state)

    def _append_history(self, state: Dict[str, Any], action: str, detail: Any) -> None:
        hist = state.setdefault("history", [])
        hist.append({"at": _now_iso(), "action": action, "detail": detail})
        # 直近 50 件だけ保持
        state["history"] = hist[-50:]

    def append_log(self, action: str, ok: bool, message: str, extra: Any = None) -> None:
        logs = _safe_json_load(self.log_path, [])
        if not isinstance(logs, list):
            logs = []
        logs.append({
            "at": _now_iso(),
            "action": action,
            "ok": ok,
            "message": message,
            "extra": extra,
        })
        _safe_json_dump(self.log_path, logs[-100:])

    def get_logs(self, limit: int = 30) -> List[Dict[str, Any]]:
        logs = _safe_json_load(self.log_path, [])
        if not isinstance(logs, list):
            return []
        return list(reversed(logs[-limit:]))

    # ------------------------------------------------------------------ helpers
    def _require_convert(self) -> None:
        if characters is None or weapons is None:
            raise RuntimeError(
                "convert モジュールが見つかりません。"
                "server.py と同じ階層、または PYTHONPATH に get_data_flask/convert を置いてください。"
            )

    def _download_webp(self, icon_name: str, dest_dir: str) -> bool:
        if not icon_name:
            return False
        _ensure_dir(dest_dir)
        path = os.path.join(dest_dir, f"{icon_name}.webp")
        if os.path.exists(path):
            return True
        url = f"https://static.nanoka.cc/assets/gi/{icon_name}.webp"
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            with open(path, "wb") as f:
                f.write(r.content)
            return True
        except Exception as e:
            print(f"[admin_data] download failed {icon_name}: {e}")
            return False

    def download_character_images(self, char_id: str, mode: str = "beta") -> Dict[str, Any]:
        """キャラ JSON から icon / skills / passives / constellations の画像を取得。"""
        if mode == "live":
            json_path = self.live_dir("characters", f"{char_id}.json")
            img_dir = self.live_assets("skills")
            char_img_dir = self.live_assets("characters")
            splash_dir = self.live_assets("splash")
        else:
            json_path = self.beta_dir("characters", f"{char_id}.json")
            img_dir = self.beta_assets("skills")
            char_img_dir = self.beta_assets("characters")
            splash_dir = self.beta_assets("splash")

        data = _safe_json_load(json_path)
        if not data:
            return {"ok": False, "error": f"JSON not found: {json_path}"}

        icons = []
        if data.get("icon"):
            icons.append(data["icon"])
        for key in ("skills", "passives", "constellations"):
            for item in data.get(key) or []:
                if isinstance(item, dict) and item.get("icon"):
                    icons.append(item["icon"])
        icons = list(set(icons))

        saved = 0
        for icon in icons:
            if self._download_webp(icon, img_dir):
                saved += 1

        # キャラアバター / スプラッシュ
        avatar = data.get("icon")
        if avatar:
            self._download_webp(avatar, char_img_dir)
            splash = str(avatar).replace("AvatarIcon", "Gacha_AvatarImg")
            self._download_webp(splash, splash_dir)

        # コスチューム画像（icon が空でないもののみ assets/characters へ）
        costume_icons = list({
            c.get("icon") for c in (data.get("costume") or [])
            if isinstance(c, dict) and c.get("icon")
        })
        costume_saved = 0
        for icon in costume_icons:
            if self._download_webp(icon, char_img_dir):
                costume_saved += 1

        # コスチュームスプラッシュ（AvatarIcon→Costume 置換、assets/splash へ）
        costume_splash_saved = 0
        for icon in costume_icons:
            splash_name = str(icon).replace("AvatarIcon", "Costume")
            if splash_name != icon and self._download_webp(splash_name, splash_dir):
                costume_splash_saved += 1

        return {
            "ok": True,
            "icons": len(icons),
            "saved": saved,
            "costume_icons": len(costume_icons),
            "costume_saved": costume_saved,
            "costume_splashes": len(costume_icons),
            "costume_splash_saved": costume_splash_saved,
            "name": data.get("name", char_id),
        }


    # ------------------------------------------------------------------ nanoka helpers
    def _nanoka_manifest(self):
        manifest = requests.get("https://static.nanoka.cc/manifest.json", timeout=15)
        manifest.raise_for_status()
        data = manifest.json()
        return data["gi"]["live"], data["gi"]["latest"]

    def _nanoka_keys(self, version: str, kind: str) -> List[str]:
        # kind: character | weapon | artifact
        r = requests.get(f"https://static.nanoka.cc/gi/{version}/{kind}.json", timeout=20)
        r.raise_for_status()
        return list(r.json().keys())

    def _nanoka_char_en_names(self, version: str) -> Dict[str, str]:
        """キャラ一覧 JSON から {char_id: 英語名} を取得（1リクエスト）。失敗時は空 dict。"""
        try:
            r = requests.get(f"https://static.nanoka.cc/gi/{version}/character.json", timeout=20)
            r.raise_for_status()
            raw = r.json() or {}
            return {
                str(k): str(v.get("en"))
                for k, v in raw.items()
                if isinstance(v, dict) and v.get("en")
            }
        except Exception as e:
            print(f"[nanoka] en name list {version}: {e}")
            return {}

    def _save_nanoka_lists(self, version: str, list_dir: str, scope: str = "all") -> List[str]:
        saved = []
        mapping = [
            ("characters.json", "character", characters_list),
            ("weapons.json", "weapon", weapons_list),
            ("artifacts.json", "artifact", artifacts_list),
        ]
        scope_kind = {"characters": "character", "weapons": "weapon", "artifacts": "artifact"}
        _ensure_dir(list_dir)
        for filename, kind, module in mapping:
            if scope != "all" and scope_kind.get(scope) != kind:
                continue
            try:
                url = f"https://static.nanoka.cc/gi/{version}/{kind}.json"
                raw = requests.get(url, timeout=20).json()
                converted = module.from_nanoka(raw) if module is not None else raw
                _safe_json_dump(os.path.join(list_dir, filename), converted)
                saved.append(filename)
            except Exception as e:
                print(f"[nanoka] list {filename}: {e}")
        return saved

    def _download_weapon_assets(self, weapon_ids: List[str], mode: str) -> int:
        n = 0
        weapon_dir = self.live_dir("weapons") if mode == "live" else self.beta_dir("weapons")
        dest = self.live_assets("weapons") if mode == "live" else self.beta_assets("weapons")
        for wid in weapon_ids:
            wdata = _safe_json_load(os.path.join(weapon_dir, f"{wid}.json"))
            if wdata and wdata.get("icon") and self._download_webp(wdata["icon"], dest):
                n += 1
        return n

    def _download_artifact_assets(self, art_ids: List[str], mode: str) -> int:
        n = 0
        list_path = self.live_dir("lists", "artifacts.json") if mode == "live" else self.beta_dir("lists", "artifacts.json")
        dest = self.live_assets("artifacts") if mode == "live" else self.beta_assets("artifacts")
        art_list = _safe_json_load(list_path, {}) or {}
        for art_id in art_ids:
            entry = art_list.get(art_id) or art_list.get(str(art_id)) or {}
            icon = entry.get("icon")
            if not icon:
                continue
            for i in range(5):
                icon_i = str(icon).replace("_4", f"_{i + 1}")
                if self._download_webp(icon_i, dest):
                    n += 1
        return n

    def _list_json_ids(self, directory: str) -> List[str]:
        if not os.path.isdir(directory):
            return []
        return sorted(f[:-5] for f in os.listdir(directory) if f.endswith(".json"))

    # ------------------------------------------------------------------ BETA JSON (nanoka 差分)
    def fetch_beta_nanoka_json(self, scope: str = "all") -> Dict[str, Any]:
        """beta と live の差分 JSON のみ static/beta/data に保存（scope: characters/weapons/artifacts/all）。"""
        self._require_convert()
        kind = "beta_json" if scope == "all" else f"beta_json_{scope}"
        result: Dict[str, Any] = {"source": "nanoka", "ok": False, "kind": kind}
        try:
            live, beta = self._nanoka_manifest()
            result["live_version"] = live
            result["beta_version"] = beta

            added_chars = sorted(set(self._nanoka_keys(beta, "character")) - set(self._nanoka_keys(live, "character")))
            added_weapons = sorted(set(self._nanoka_keys(beta, "weapon")) - set(self._nanoka_keys(live, "weapon")))
            added_arts = sorted(set(self._nanoka_keys(beta, "artifact")) - set(self._nanoka_keys(live, "artifact")))

            char_dir = self.beta_dir("characters")
            weapon_dir = self.beta_dir("weapons")
            _ensure_dir(char_dir)
            _ensure_dir(weapon_dir)

            ok_chars, ok_weapons = [], []
            if scope in ("characters", "all"):
                en_names = self._nanoka_char_en_names(beta)
                for char_id in added_chars:
                    url = f"https://static.nanoka.cc/gi/{beta}/ja/character/{char_id}.json"
                    try:
                        response = requests.get(url, timeout=15)
                        response.raise_for_status()
                        raw = response.json()
                        en_name = en_names.get(str(char_id))
                        if en_name:
                            raw["en_name"] = en_name
                        converted = characters.from_nanoka(raw)
                        _safe_json_dump(os.path.join(char_dir, f"{char_id}.json"), converted)
                        ok_chars.append(char_id)
                    except Exception as e:
                        print(f"[nanoka beta json] char {char_id}: {e}")

            if scope in ("weapons", "all"):
                for weapon_id in added_weapons:
                    url = f"https://static.nanoka.cc/gi/{beta}/ja/weapon/{weapon_id}.json"
                    try:
                        response = requests.get(url, timeout=15)
                        response.raise_for_status()
                        converted = weapons.from_nanoka(response.json())
                        _safe_json_dump(os.path.join(weapon_dir, f"{weapon_id}.json"), converted)
                        ok_weapons.append(weapon_id)
                    except Exception as e:
                        print(f"[nanoka beta json] weapon {weapon_id}: {e}")

            lists_saved = self._save_nanoka_lists(beta, self.beta_dir("lists"), scope)

            state = self.get_state()
            state["live_version"] = live
            state["beta_version"] = beta
            state["source"] = "nanoka"
            pending = dict(state.get("pending") or {})
            if scope in ("characters", "all"):
                pending["characters"] = ok_chars
            if scope in ("weapons", "all"):
                pending["weapons"] = ok_weapons
            if scope in ("artifacts", "all"):
                pending["artifacts"] = added_arts
            state["pending"] = pending
            state["last_beta_fetch"] = _now_iso()
            hist_key = "fetch_beta_nanoka_json" if scope == "all" else f"fetch_beta_nanoka_json_{scope}"
            self._append_history(state, hist_key, {
                "live": live, "beta": beta, "scope": scope,
                "characters": ok_chars, "weapons": ok_weapons, "artifacts": added_arts,
            })
            self.save_state(state)

            result.update({
                "ok": True,
                "scope": scope,
                "added_characters": ok_chars,
                "added_weapons": ok_weapons,
                "added_artifacts": added_arts,
                "lists": lists_saved,
            })
            self.append_log(hist_key, True, f"beta={beta} scope={scope}", result)
            return result
        except Exception as e:
            result["error"] = str(e)
            result["traceback"] = traceback.format_exc()
            self.append_log("fetch_beta_nanoka_json", False, str(e))
            return result

    # ------------------------------------------------------------------ BETA ASSETS
    def fetch_beta_nanoka_assets(self) -> Dict[str, Any]:
        """pending（または beta ディレクトリ上の JSON）向けアセットのみ取得。"""
        result: Dict[str, Any] = {"source": "nanoka", "ok": False, "kind": "beta_assets"}
        try:
            state = self.get_state()
            pending = state.get("pending") or {}
            chars = list(pending.get("characters") or []) or self._list_json_ids(self.beta_dir("characters"))
            weapons_ids = list(pending.get("weapons") or []) or self._list_json_ids(self.beta_dir("weapons"))
            arts = list(pending.get("artifacts") or [])

            char_ok = 0
            for cid in chars:
                try:
                    r = self.download_character_images(cid, "beta")
                    if r.get("ok"):
                        char_ok += 1
                except Exception as e:
                    print(f"[nanoka beta assets] char {cid}: {e}")

            weapon_n = self._download_weapon_assets(weapons_ids, "beta")
            art_n = self._download_artifact_assets(arts, "beta") if arts else 0

            # 聖遺物 ID が pending 空でもリストから差分推定はしない（明示 ID 優先）
            result.update({
                "ok": True,
                "characters": char_ok,
                "character_ids": chars,
                "weapons": weapon_n,
                "weapon_ids": weapons_ids,
                "artifacts": art_n,
                "artifact_ids": arts,
            })
            state["last_beta_assets_fetch"] = _now_iso()
            self._append_history(state, "fetch_beta_nanoka_assets", result)
            self.save_state(state)
            self.append_log("fetch_beta_nanoka_assets", True, f"chars={char_ok} weapons={weapon_n}", result)
            return result
        except Exception as e:
            result["error"] = str(e)
            result["traceback"] = traceback.format_exc()
            self.append_log("fetch_beta_nanoka_assets", False, str(e))
            return result

    def fetch_beta_nanoka(self, download_images: bool = True) -> Dict[str, Any]:
        """beta JSON + assets 同時取得。"""
        json_res = self.fetch_beta_nanoka_json()
        if not json_res.get("ok"):
            return json_res
        if not download_images:
            return json_res
        assets_res = self.fetch_beta_nanoka_assets()
        return {
            "ok": assets_res.get("ok", False),
            "kind": "beta_both",
            "json": json_res,
            "assets": assets_res,
            "live_version": json_res.get("live_version"),
            "beta_version": json_res.get("beta_version"),
        }

    # ------------------------------------------------------------------ LIVE JSON
    def fetch_live_nanoka_json(self, scope: str = "all") -> Dict[str, Any]:
        """live バージョンの JSON を static/data に保存（scope: characters/weapons/artifacts/all）。"""
        self._require_convert()
        kind = "live_json" if scope == "all" else f"live_json_{scope}"
        result: Dict[str, Any] = {"source": "nanoka", "ok": False, "kind": kind}
        try:
            live, beta = self._nanoka_manifest()
            result["live_version"] = live
            result["beta_version"] = beta

            char_dir = self.live_dir("characters")
            weapon_dir = self.live_dir("weapons")
            _ensure_dir(char_dir)
            _ensure_dir(weapon_dir)

            ok_chars = 0
            if scope in ("characters", "all"):
                en_names = self._nanoka_char_en_names(live)
                for m in self._nanoka_keys(live, "character"):
                    url = f"https://static.nanoka.cc/gi/{live}/ja/character/{m}.json"
                    try:
                        r = requests.get(url, timeout=15)
                        r.raise_for_status()
                        raw = r.json()
                        en_name = en_names.get(str(m))
                        if en_name:
                            raw["en_name"] = en_name
                        converted = characters.from_nanoka(raw)
                        _safe_json_dump(os.path.join(char_dir, f"{m}.json"), converted)
                        ok_chars += 1
                    except Exception as e:
                        print(f"[nanoka live json] char {m}: {e}")

            ok_weapons = 0
            if scope in ("weapons", "all"):
                for m in self._nanoka_keys(live, "weapon"):
                    url = f"https://static.nanoka.cc/gi/{live}/ja/weapon/{m}.json"
                    try:
                        r = requests.get(url, timeout=15)
                        r.raise_for_status()
                        converted = weapons.from_nanoka(r.json())
                        _safe_json_dump(os.path.join(weapon_dir, f"{m}.json"), converted)
                        ok_weapons += 1
                    except Exception as e:
                        print(f"[nanoka live json] weapon {m}: {e}")

            lists_saved = self._save_nanoka_lists(live, self.live_dir("lists"), scope)
            live_arts = self._nanoka_keys(live, "artifact") if scope in ("artifacts", "all") else []

            state = self.get_state()
            state["live_version"] = live
            state["beta_version"] = beta
            state["source"] = "nanoka"
            state["last_live_fetch"] = _now_iso()
            # 全件取得時のみ pending をクリア
            if scope == "all":
                state["pending"] = {"characters": [], "weapons": [], "artifacts": []}
            hist_key = "fetch_live_nanoka_json" if scope == "all" else f"fetch_live_nanoka_json_{scope}"
            self._append_history(state, hist_key, {
                "live": live, "scope": scope,
                "characters": ok_chars,
                "weapons": ok_weapons,
                "lists": lists_saved,
            })
            self.save_state(state)

            result.update({
                "ok": True,
                "scope": scope,
                "character_count": ok_chars,
                "weapon_count": ok_weapons,
                "artifact_list_count": len(live_arts),
                "lists": lists_saved,
            })
            self.append_log(hist_key, True, f"live={live} scope={scope}", result)
            return result
        except Exception as e:
            result["error"] = str(e)
            result["traceback"] = traceback.format_exc()
            self.append_log("fetch_live_nanoka_json", False, str(e))
            return result

    # ------------------------------------------------------------------ LIVE ASSETS
    def fetch_live_nanoka_assets(self) -> Dict[str, Any]:
        """live 領域の JSON を元に全アセットを取得。"""
        result: Dict[str, Any] = {"source": "nanoka", "ok": False, "kind": "live_assets"}
        try:
            chars = self._list_json_ids(self.live_dir("characters"))
            weapons_ids = self._list_json_ids(self.live_dir("weapons"))
            art_list = _safe_json_load(self.live_dir("lists", "artifacts.json"), {}) or {}
            arts = list(art_list.keys()) if isinstance(art_list, dict) else []

            char_ok = 0
            for cid in chars:
                try:
                    r = self.download_character_images(cid, "live")
                    if r.get("ok"):
                        char_ok += 1
                except Exception as e:
                    print(f"[nanoka live assets] char {cid}: {e}")

            weapon_n = self._download_weapon_assets(weapons_ids, "live")
            art_n = self._download_artifact_assets(arts, "live")

            state = self.get_state()
            state["last_live_assets_fetch"] = _now_iso()
            self._append_history(state, "fetch_live_nanoka_assets", {
                "characters": char_ok, "weapons": weapon_n, "artifacts": art_n,
            })
            self.save_state(state)

            result.update({
                "ok": True,
                "characters": char_ok,
                "weapons": weapon_n,
                "artifacts": art_n,
                "character_total": len(chars),
                "weapon_total": len(weapons_ids),
                "artifact_total": len(arts),
            })
            self.append_log("fetch_live_nanoka_assets", True, f"chars={char_ok}/{len(chars)}", result)
            return result
        except Exception as e:
            result["error"] = str(e)
            result["traceback"] = traceback.format_exc()
            self.append_log("fetch_live_nanoka_assets", False, str(e))
            return result

    def fetch_live_nanoka(self, characters_only: bool = False) -> Dict[str, Any]:
        """live JSON + assets 同時（characters_only は互換用・無視してフル）。"""
        json_res = self.fetch_live_nanoka_json()
        if not json_res.get("ok"):
            return json_res
        assets_res = self.fetch_live_nanoka_assets()
        return {
            "ok": assets_res.get("ok", False),
            "kind": "live_both",
            "json": json_res,
            "assets": assets_res,
            "live_version": json_res.get("live_version"),
        }

    # ------------------------------------------------------------------ COSTUME ASSETS
    def _costume_icons_from_json(self, char_ids: List[str], mode: str) -> List[str]:
        """キャラJSONの costume 配列から icon が空でないものを収集（live/beta 共通）。"""
        base = self.live_dir("characters") if mode == "live" else self.beta_dir("characters")
        icons = set()
        for cid in char_ids:
            data = _safe_json_load(os.path.join(base, f"{cid}.json"))
            if not data:
                continue
            for c in data.get("costume") or []:
                if isinstance(c, dict) and c.get("icon"):
                    icons.add(c["icon"])
        return sorted(icons)

    def _download_costume_assets(self, char_ids: List[str], mode: str, missing_only: bool = False) -> Dict[str, Any]:
        """コスチューム画像のみ取得（アイコン→assets/characters、スプラッシュ→assets/splash）。
        missing_only=True なら存在しない画像のみ取得する。"""
        char_dest = self.live_assets("characters") if mode == "live" else self.beta_assets("characters")
        splash_dest = self.live_assets("splash") if mode == "live" else self.beta_assets("splash")
        icons = self._costume_icons_from_json(char_ids, mode)
        missing, saved, failed = 0, 0, 0
        for icon in icons:
            targets = [(icon, char_dest)]
            splash_name = str(icon).replace("AvatarIcon", "Costume")
            if splash_name != icon:
                targets.append((splash_name, splash_dest))
            for name, dest in targets:
                if os.path.exists(os.path.join(dest, f"{name}.webp")):
                    continue  # 既存スキップ
                missing += 1
                if self._download_webp(name, dest):
                    saved += 1
                else:
                    failed += 1
        return {"icons": len(icons), "missing": missing, "saved": saved, "failed": failed}

    def fetch_beta_nanoka_costumes(self) -> Dict[str, Any]:
        """beta の全キャラJSONからコスチューム画像のみ取得。"""
        result: Dict[str, Any] = {"source": "nanoka", "ok": False, "kind": "beta_costume_assets"}
        try:
            chars = self._list_json_ids(self.beta_dir("characters"))
            r = self._download_costume_assets(chars, "beta")
            result.update({"ok": True, "characters": len(chars), **r})
            state = self.get_state()
            state["last_beta_costume_fetch"] = _now_iso()
            self._append_history(state, "fetch_beta_nanoka_costumes", result)
            self.save_state(state)
            self.append_log("fetch_beta_nanoka_costumes", True, f"costumes={r['saved']}/{r['icons']}", result)
            return result
        except Exception as e:
            result["error"] = str(e)
            result["traceback"] = traceback.format_exc()
            self.append_log("fetch_beta_nanoka_costumes", False, str(e))
            return result

    def fetch_live_nanoka_costumes(self) -> Dict[str, Any]:
        """live の全キャラJSONからコスチューム画像のみ取得。"""
        result: Dict[str, Any] = {"source": "nanoka", "ok": False, "kind": "live_costume_assets"}
        try:
            chars = self._list_json_ids(self.live_dir("characters"))
            r = self._download_costume_assets(chars, "live")
            result.update({"ok": True, "characters": len(chars), **r})
            state = self.get_state()
            state["last_live_costume_fetch"] = _now_iso()
            self._append_history(state, "fetch_live_nanoka_costumes", result)
            self.save_state(state)
            self.append_log("fetch_live_nanoka_costumes", True, f"costumes={r['saved']}/{r['icons']}", result)
            return result
        except Exception as e:
            result["error"] = str(e)
            result["traceback"] = traceback.format_exc()
            self.append_log("fetch_live_nanoka_costumes", False, str(e))
            return result

    def fetch_beta_nanoka_costumes_missing(self) -> Dict[str, Any]:
        """beta の全キャラJSONから、画像が存在しないコスチュームのみ取得。"""
        result: Dict[str, Any] = {"source": "nanoka", "ok": False, "kind": "beta_costume_missing"}
        try:
            chars = self._list_json_ids(self.beta_dir("characters"))
            r = self._download_costume_assets(chars, "beta", missing_only=True)
            result.update({"ok": True, "characters": len(chars), **r})
            state = self.get_state()
            state["last_beta_costume_missing_fetch"] = _now_iso()
            self._append_history(state, "fetch_beta_nanoka_costumes_missing", result)
            self.save_state(state)
            self.append_log("fetch_beta_nanoka_costumes_missing", True, f"missing={r['missing']} saved={r['saved']}", result)
            return result
        except Exception as e:
            result["error"] = str(e)
            result["traceback"] = traceback.format_exc()
            self.append_log("fetch_beta_nanoka_costumes_missing", False, str(e))
            return result

    def fetch_live_nanoka_costumes_missing(self) -> Dict[str, Any]:
        """live の全キャラJSONから、画像が存在しないコスチュームのみ取得。"""
        result: Dict[str, Any] = {"source": "nanoka", "ok": False, "kind": "live_costume_missing"}
        try:
            chars = self._list_json_ids(self.live_dir("characters"))
            r = self._download_costume_assets(chars, "live", missing_only=True)
            result.update({"ok": True, "characters": len(chars), **r})
            state = self.get_state()
            state["last_live_costume_missing_fetch"] = _now_iso()
            self._append_history(state, "fetch_live_nanoka_costumes_missing", result)
            self.save_state(state)
            self.append_log("fetch_live_nanoka_costumes_missing", True, f"missing={r['missing']} saved={r['saved']}", result)
            return result
        except Exception as e:
            result["error"] = str(e)
            result["traceback"] = traceback.format_exc()
            self.append_log("fetch_live_nanoka_costumes_missing", False, str(e))
            return result

    def version_upgrade_live(self) -> Dict[str, Any]:
        """
        バージョンアップ時: beta コピーではなく nanoka live から
        JSON（キャラ/武器/リスト）は全件再取得する。
        アセットは「新規追加された ID」のみ取得する（既存分は基本的に
        変更されないため、毎回の再ダウンロードを省く）。
        """
        # 取得前の既存 ID を控えておき、取得後に新規分を差分判定する
        old_chars = set(self._list_json_ids(self.live_dir("characters")))
        old_weapons = set(self._list_json_ids(self.live_dir("weapons")))
        old_arts = set(_safe_json_load(self.live_dir("lists", "artifacts.json"), {}) or {})

        json_res = self.fetch_live_nanoka_json()
        assets_res = None
        new_chars = new_weapons = new_arts = []
        if json_res.get("ok"):
            new_chars = sorted(set(self._list_json_ids(self.live_dir("characters"))) - old_chars)
            new_weapons = sorted(set(self._list_json_ids(self.live_dir("weapons"))) - old_weapons)
            art_list = _safe_json_load(self.live_dir("lists", "artifacts.json"), {}) or {}
            new_arts = sorted(set(art_list) - old_arts)
            assets_res = self._fetch_new_live_assets(new_chars, new_weapons, new_arts)

        state = self.get_state()
        state["last_promote"] = _now_iso()
        state["pending"] = {"characters": [], "weapons": [], "artifacts": []}
        self._append_history(state, "version_upgrade_live", {
            "live_version": json_res.get("live_version"),
            "json_ok": json_res.get("ok"),
            "assets": assets_res,
        })
        self.save_state(state)

        result = {**json_res, "kind": "version_upgrade_live"}
        if assets_res is not None:
            result["assets"] = assets_res
        self.append_log(
            "version_upgrade_live", bool(json_res.get("ok")),
            f"live={json_res.get('live_version')} new_chars={len(new_chars)} "
            f"new_weapons={len(new_weapons)} new_arts={len(new_arts)}",
            result,
        )
        return result

    def _fetch_new_live_assets(
        self, char_ids: List[str], weapon_ids: List[str], art_ids: List[str]
    ) -> Dict[str, Any]:
        """新規追加された ID のみ live アセットを取得する（差分取得用）。"""
        char_ok = 0
        for cid in char_ids:
            try:
                r = self.download_character_images(cid, "live")
                if r.get("ok"):
                    char_ok += 1
            except Exception as e:
                print(f"[nanoka live assets diff] char {cid}: {e}")
        weapon_n = self._download_weapon_assets(weapon_ids, "live")
        art_n = self._download_artifact_assets(art_ids, "live") if art_ids else 0
        return {
            "ok": True,
            "kind": "live_assets_new",
            "new_characters": char_ids,
            "new_weapons": weapon_ids,
            "new_artifacts": art_ids,
            "characters": char_ok,
            "weapons": weapon_n,
            "artifacts": art_n,
        }

    # 後方互換（使わないが残す）
    def promote_beta_to_live(self, **kwargs) -> Dict[str, Any]:
        return self.version_upgrade_live()

    def fetch_beta_lunaris(self) -> Dict[str, Any]:
        return {"ok": False, "error": "Lunaris is disabled", "disabled": True}

    def fetch_gachabase_changelog(self) -> Dict[str, Any]:
        return {"ok": False, "error": "Gachabase is disabled", "disabled": True}


    # ------------------------------------------------------------------ status snapshot for UI
    def status_snapshot(self) -> Dict[str, Any]:
        state = self.get_state()

        def _count_files(d: str) -> int:
            if not os.path.isdir(d):
                return 0
            return len([f for f in os.listdir(d) if f.endswith(".json")])

        return {
            "state": state,
            "counts": {
                "live_characters": _count_files(self.live_dir("characters")),
                "live_weapons": _count_files(self.live_dir("weapons")),
                "beta_characters": _count_files(self.beta_dir("characters")),
                "beta_weapons": _count_files(self.beta_dir("weapons")),
            },
            "logs": self.get_logs(20),
            "convert_available": characters is not None,
            "gachabase_available": gachabase_changelog is not None,
        }
