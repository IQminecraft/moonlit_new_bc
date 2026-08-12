# -*- coding: utf-8 -*-
"""レイライン（leyline）変換モジュール。

nanoka の leyline 一覧 + 各レイライン詳細から、
表示に必要なデータ（名前・期間・敵のアイコンID/名前/画像URL）だけを抽出する。
"""
import json
import sys


def from_nanoka(raw_list, details=None):
    """レイライン一覧と詳細から変換する。

    raw_list: {id: {ja, en, begin, end, live_begin, live_end}}
    details:  {id: {monsters: [{name, icon, img}]}}  ← _fetch_leyline_detail の形式
    """
    details = details or {}
    result = {}
    for lid, entry in (raw_list or {}).items():
        lid = str(lid)
        monsters = (details.get(lid) or {}).get("monsters") or []
        enemies = [
            {
                "icon": m.get("icon") or "",
                "name": m.get("name") or "",
                "img": m.get("img") or "",
            }
            for m in monsters
            if isinstance(m, dict)
        ]
        result[lid] = {
            "id": int(lid) if lid.isdigit() else lid,
            "name": (entry or {}).get("ja") or (entry or {}).get("en") or "",
            "en": (entry or {}).get("en") or "",
            "begin": (entry or {}).get("begin") or "",
            "end": (entry or {}).get("end") or "",
            "live_begin": (entry or {}).get("live_begin") or "",
            "live_end": (entry or {}).get("live_end") or "",
            "enemies": enemies,
        }
    return result


def main():
    """CLI: python -m app.convert.leyline list.json details.json out.json"""
    if len(sys.argv) >= 3:
        with open(sys.argv[1], "r", encoding="utf-8") as f:
            raw_list = json.load(f)
        with open(sys.argv[2], "r", encoding="utf-8") as f:
            details = json.load(f)
    else:
        raw_list = json.load(sys.stdin)
        details = {}
    out = from_nanoka(raw_list, details)
    if len(sys.argv) >= 4:
        with open(sys.argv[3], "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
    else:
        json.dump(out, sys.stdout, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
