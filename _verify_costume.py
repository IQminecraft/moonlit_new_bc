# -*- coding: utf-8 -*-
"""Task E verification: costume JSON transform / asset download / splash resolution."""
import io
import json
import os
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "moonlit-verify"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


print("=== 1. transform: costume (id/icon/quality) ===")
from app.convert.characters.from_nanoka import transform_character
for cid in ["10000005-2", "10000123", "10000107"]:
    raw = get_json(f"https://static.nanoka.cc/gi/6.7/ja/character/{cid}.json")
    out = transform_character(raw)
    print(f"{cid}: costume={json.dumps(out.get('costume'), ensure_ascii=False)}")

print()
print("=== 2. local JSON 更新 (10000123 / 10000107 = showcase costumeId 対象) ===")
for cid in ["10000123", "10000107"]:
    raw = get_json(f"https://static.nanoka.cc/gi/6.7/ja/character/{cid}.json")
    out = transform_character(raw)
    with open(f"static/data/characters/{cid}.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"saved static/data/characters/{cid}.json costume_entries={len(out.get('costume') or [])}")

print()
print("=== 3. download_character_images (live) — コスチューム画像 ===")
from app.paths import BASE_DIR
from app.admin_data import DataManager
dm = DataManager(BASE_DIR)
for cid in ["10000123", "10000107"]:
    r = dm.download_character_images(cid, "live")
    print(f"{cid}: {json.dumps(r, ensure_ascii=False)}")

print()
print("=== 4. card_data splash (costumeId あり=10000123 / なし=10000116) ===")
from app.card.data import _get_card_data_sync
for cid in ["10000123", "10000116"]:
    d = _get_card_data_sync("1812256644", cid, "crit")
    print(f"{cid}: splash={d['splash']}")

print()
print("=== 5. 画像生成 (costumeId あり=10000123 / なし=10000116) ===")
from app.card.image import _generate_card_image_sync
for cid in ["10000123", "10000116"]:
    png = _generate_card_image_sync("1812256644", cid, "crit", None, None, "false", None, "png", None, None)
    print(f"{cid}: png bytes={len(png)}")

print()
print("=== 6. コスチューム画像ファイル確認 ===")
for icon in ["UI_AvatarIcon_DurinCostumeWic", "UI_AvatarIcon_CitlaliCostumeXia"]:
    p = f"static/assets/characters/{icon}.webp"
    print(f"{p}: exists={os.path.exists(p)} size={os.path.getsize(p) if os.path.exists(p) else 0}")
