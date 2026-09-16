import os
import time
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any
from fastapi import HTTPException
from app.card.cache import _maybe_reset_image_caches, _CARD_CACHE_TTL_SEC, _CARD_CACHE_LAST_RESET

# ==========================================================
#  カード生成専用スレッドプール（同時実行上限 + 待ち行列）
#  デフォルトの run_in_threadpool を埋めないため、サイト全体の
#  フリーズを防ぐ。超過分はキューで順番待ち。
#
#  環境変数:
#    CARD_GEN_MAX_WORKERS  同時生成数 (default: 2)
#    CARD_GEN_MAX_QUEUE    待ち行列の上限 (default: 20)
#    CARD_GEN_TIMEOUT_SEC  1枚のタイムアウト秒 (default: 8)
#    CARD_CACHE_TTL_HOURS  画像キャッシュ全クリア間隔時間 (default: 1)
#    CACHE_SWEEP_SEC       バックグラウンド掃除間隔秒 (default: 300)
# ==========================================================
_CARD_GEN_MAX_WORKERS = max(1, int(os.environ.get("CARD_GEN_MAX_WORKERS", "2")))
_CARD_GEN_MAX_QUEUE = max(0, int(os.environ.get("CARD_GEN_MAX_QUEUE", "20")))
_CARD_GEN_TIMEOUT_SEC = max(1.0, float(os.environ.get("CARD_GEN_TIMEOUT_SEC", "8")))

_CARD_GEN_EXECUTOR = ThreadPoolExecutor(
    max_workers=_CARD_GEN_MAX_WORKERS,
    thread_name_prefix="card-gen",
)
_CARD_GEN_PENDING = 0  # 実行中 + キュー待ちの合計
_CARD_GEN_RUNNING = 0
_CARD_GEN_LOCK = threading.Lock()


def _card_gen_stats() -> Dict[str, Any]:
    with _CARD_GEN_LOCK:
        pending = _CARD_GEN_PENDING
        running = _CARD_GEN_RUNNING
    return {
        "max_workers": _CARD_GEN_MAX_WORKERS,
        "max_queue": _CARD_GEN_MAX_QUEUE,
        "running": running,
        "pending": pending,  # running + waiting
        "waiting": max(0, pending - running),
        "slots_left": max(0, _CARD_GEN_MAX_WORKERS + _CARD_GEN_MAX_QUEUE - pending),
        "cache_ttl_sec": round(_CARD_CACHE_TTL_SEC, 1),
        "cache_last_reset_ago_sec": round(time.time() - _CARD_CACHE_LAST_RESET, 1),
    }


async def _run_in_card_gen_pool(fn, *args):
    """カード生成を専用プールで実行。満杯なら 503。それ以外は列で待つ。"""
    global _CARD_GEN_PENDING

    with _CARD_GEN_LOCK:
        limit = _CARD_GEN_MAX_WORKERS + _CARD_GEN_MAX_QUEUE
        if _CARD_GEN_PENDING >= limit:
            print(
                f"[CardGen] queue full pending={_CARD_GEN_PENDING} "
                f"limit={limit} → 503"
            )
            raise HTTPException(
                status_code=503,
                detail="カード生成が混雑しています。しばらくしてから再試行してください。",
            )
        _CARD_GEN_PENDING += 1
        entered = True

    try:
        def _wrapped():
            global _CARD_GEN_RUNNING
            with _CARD_GEN_LOCK:
                _CARD_GEN_RUNNING += 1
            try:
                _maybe_reset_image_caches()
                return fn(*args)
            finally:
                with _CARD_GEN_LOCK:
                    _CARD_GEN_RUNNING -= 1

        loop = asyncio.get_running_loop()
        fut = loop.run_in_executor(_CARD_GEN_EXECUTOR, _wrapped)
        try:
            return await asyncio.wait_for(fut, timeout=_CARD_GEN_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            print(
                f"[CardGen] TIMEOUT after {_CARD_GEN_TIMEOUT_SEC}s "
                f"(worker may still be stuck — restart if slots stay full)",
                flush=True,
            )
            raise HTTPException(
                status_code=504,
                detail=f"カード生成がタイムアウトしました（{_CARD_GEN_TIMEOUT_SEC:.0f}秒）。時間をおいて再試行してください。",
            )
    finally:
        if entered:
            with _CARD_GEN_LOCK:
                _CARD_GEN_PENDING -= 1


print(
    f"[OK] Card-gen pool: workers={_CARD_GEN_MAX_WORKERS} "
    f"queue={_CARD_GEN_MAX_QUEUE} timeout={_CARD_GEN_TIMEOUT_SEC:.0f}s "
    f"cache_ttl={_CARD_CACHE_TTL_SEC / 3600:.1f}h"
)
