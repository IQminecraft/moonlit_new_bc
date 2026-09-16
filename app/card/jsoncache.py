# -*- coding: utf-8 -*-
"""JSON データのメモリキャッシュ（mtime 無効化 + 件数上限 LRU）。

ショーケース / キャラ / 武器 / 聖遺物リスト等の JSON はカード生成のたびに
読み直されていたが、内容はファイル更新時まで不変。
(path, mtime, size) をキーにパース済み dict を共有することで、
重複するディスク I/O と json.load の CPU を削減する。

メモリ圧迫を防ぐため件数上限の LRU で管理する。
パース済み dict は複数スレッドで共有されるため、呼び出し側は変更しないこと。
"""
import json
import os
import threading
from collections import OrderedDict
# 環境変数を import 時に読むため、.env（dotenv は app.paths の import 副作用で
# 読み込まれる）がどの import 順でも先に載るように app.paths を参照しておく。

# 件数上限（環境変数で調整可）。1エントリは数百KB〜数MBのパース済みdict。
_JSON_CACHE_MAX_ENTRIES = max(16, int(os.environ.get("JSON_CACHE_MAX_ENTRIES", "192")))

_JSON_CACHE: "OrderedDict" = OrderedDict()  # path -> (mtime, size, data)
_JSON_LOCK = threading.Lock()
_JSON_CACHE_HITS = 0
_JSON_CACHE_MISSES = 0


def _read_json_auto(path: str):
    """UTF-8 → cp932 の順で JSON を読み込む（キャッシュしない生の読み込み）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (UnicodeDecodeError, json.JSONDecodeError):
        with open(path, "r", encoding="cp932") as f:
            return json.load(f)


def load_json_cached(path: str):
    """JSON をパース済みでキャッシュから返す。ファイルが無ければ FileNotFoundError。

    mtime/size が変わっていれば自動的に再読み込みされる。
    戻り値はキャッシュ共有の dict のため変更禁止（読み取り専用で使うこと）。
    """
    global _JSON_CACHE_HITS, _JSON_CACHE_MISSES
    st = os.stat(path)
    key_sig = (st.st_mtime_ns, st.st_size)
    with _JSON_LOCK:
        ent = _JSON_CACHE.get(path)
        if ent is not None and (ent[0], ent[1]) == key_sig:
            _JSON_CACHE.move_to_end(path)
            _JSON_CACHE_HITS += 1
            return ent[2]
    data = _read_json_auto(path)
    with _JSON_LOCK:
        _JSON_CACHE_MISSES += 1
        _JSON_CACHE[path] = (st.st_mtime_ns, st.st_size, data)
        _JSON_CACHE.move_to_end(path)
        while len(_JSON_CACHE) > _JSON_CACHE_MAX_ENTRIES:
            _JSON_CACHE.popitem(last=False)
    return data


def invalidate_json_cache(path: str = None) -> None:
    """特定パス（または全て）のキャッシュを破棄する。ファイル更新後に使用。"""
    with _JSON_LOCK:
        if path is None:
            _JSON_CACHE.clear()
        else:
            _JSON_CACHE.pop(path, None)


def json_cache_stats() -> dict:
    with _JSON_LOCK:
        return {
            "entries": len(_JSON_CACHE),
            "entries_max": _JSON_CACHE_MAX_ENTRIES,
            "hits": _JSON_CACHE_HITS,
            "misses": _JSON_CACHE_MISSES,
        }
