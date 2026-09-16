# -*- coding: utf-8 -*-
"""ローカル share_data/ の共有データを R2 へ一括移行する運用スクリプト。

使い方:
  1) .env に SHARE_BACKEND=r2 と R2_* の4項目を設定
  2) python tools/share_migrate_to_r2.py           (確認のみ: 差分一覧)
     python tools/share_migrate_to_r2.py --apply    (アップロード実行)
     python tools/share_migrate_to_r2.py --apply --delete  (成功分をローカルから削除)

ローカルにある同じ key が R2 にもある場合はスキップ（上書きしない）。
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE, ".env"))
except ImportError:
    pass

from app import share_store  # noqa: E402
from app.paths import SHARE_DATA_DIR  # noqa: E402

def main():
    apply = "--apply" in sys.argv
    delete = "--delete" in sys.argv and apply
    if share_store.mode() != "r2":
        print("SHARE_BACKEND=r2 と R2 の設定（.env）が必要です。今は local のままです。")
        return 1
    keys = []
    for root, _, files in os.walk(SHARE_DATA_DIR):
        for f in files:
            if not f.endswith(".json"):
                continue
            p = os.path.join(root, f)
            key = os.path.relpath(p, SHARE_DATA_DIR).replace("\\", "/")
            keys.append((key, p))
    todo = []
    for key, p in keys:
        if share_store.get(key) is None:
            todo.append((key, p))
    print(f"local: {len(keys)} 件 / R2 に無い: {len(todo)} 件")
    if not apply:
        for key, _ in todo[:20]:
            print("  would upload:", key)
        if len(todo) > 20:
            print(f"  ... 他 {len(todo) - 20} 件")
        print("(実行は --apply を付けないとアップロードされません)")
        return 0
    done = 0
    for key, p in todo:
        with open(p, "rb") as f:
            data = f.read()
        try:
            share_store.put(key, data)
            if share_store.get(key) is not None:
                done += 1
                if delete:
                    os.remove(p)
            else:
                print("  verify failed:", key)
        except Exception as e:
            print("  upload failed:", key, e)
    print(f"uploaded: {done}/{len(todo)}" + (" (local deleted)" if delete else ""))
    return 0

if __name__ == "__main__":
    sys.exit(main())
