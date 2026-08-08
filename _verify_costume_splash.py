# -*- coding: utf-8 -*-
"""Task F verification: costume splash (UI_Costume_*) download / resolution / admin actions."""
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)

print("=== 1. download_character_images (live) — コスチュームスプラッシュ(UI_Costume_*) ===")
from app.paths import BASE_DIR
from app.admin_data import DataManager
dm = DataManager(BASE_DIR)
for cid in ["10000123", "10000107"]:
    r = dm.download_character_images(cid, "live")
    print(f"{cid}: {json.dumps(r, ensure_ascii=False)}")

print()
print("=== 2. コスチュームスプラッシュファイル確認 ===")
for icon in ["UI_Costume_DurinCostumeWic", "UI_Costume_CitlaliCostumeXia"]:
    p = f"static/assets/splash/{icon}.webp"
    print(f"{p}: exists={os.path.exists(p)} size={os.path.getsize(p) if os.path.exists(p) else 0}")

print()
print("=== 3. card_data splash (costumeId あり=10000123 / なし=10000116) ===")
from app.card.data import _get_card_data_sync
for cid in ["10000123", "10000116"]:
    d = _get_card_data_sync("1812256644", cid, "crit")
    print(f"{cid}: splash={d['splash']}")

print()
print("=== 4. 画像生成 (costumeId あり=10000123 / なし=10000116) ===")
from app.card.image import _generate_card_image_sync
for cid in ["10000123", "10000116"]:
    png = _generate_card_image_sync("1812256644", cid, "crit", None, None, "false", None, "png", None, None)
    print(f"{cid}: png bytes={len(png)}")

print()
print("=== 5. フォールバック: UI_Costume が無い場合 → 既存コスチュームアイコン ===")
from app.card.special import resolve_costume_splash
p = "static/assets/splash/UI_Costume_DurinCostumeWic.webp"
os.rename(p, p + ".bak")
with open("static/data/characters/10000123.json", encoding="utf-8") as f:
    chardatas = json.load(f)
avatar_info = {"costumeId": 212301}
print("splash(no UI_Costume):", resolve_costume_splash(chardatas, avatar_info, "false"))
os.rename(p + ".bak", p)
print("splash(restored):", resolve_costume_splash(chardatas, avatar_info, "false"))
print("splash(no costumeId):", repr(resolve_costume_splash(chardatas, {}, "false")))

print()
print("=== 6. コスチューム全取得（既存スキップ / 不足分のみ） ===")
r_all = dm._download_costume_assets(["10000123", "10000107"], "live")
print("all:", json.dumps(r_all, ensure_ascii=False))
p2 = "static/assets/splash/UI_Costume_CitlaliCostumeXia.webp"
os.rename(p2, p2 + ".bak")
r_missing = dm._download_costume_assets(["10000123", "10000107"], "live", missing_only=True)
print("missing_only(after delete):", json.dumps(r_missing, ensure_ascii=False))
print("restored:", os.path.exists(p2))
r_missing2 = dm._download_costume_assets(["10000123", "10000107"], "live", missing_only=True)
print("missing_only(again):", json.dumps(r_missing2, ensure_ascii=False))

print()
print("=== 7. JSON 取得スコープ (artifacts のみ = 高速) ===")
r = dm.fetch_live_nanoka_json("artifacts")
print("live scope=artifacts:", json.dumps({k: r.get(k) for k in ("ok", "kind", "scope", "character_count", "weapon_count", "artifact_list_count", "lists")}, ensure_ascii=False))
r = dm.fetch_beta_nanoka_json("artifacts")
print("beta scope=artifacts:", json.dumps({k: r.get(k) for k in ("ok", "kind", "scope", "added_artifacts", "lists")}, ensure_ascii=False))
