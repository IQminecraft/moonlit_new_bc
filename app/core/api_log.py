# -*- coding: utf-8 -*-
"""API リクエスト / レスポンスのログミドルウェア。

応答ログの種類（タグ）:
  [API][2xx]   正常応答（info）— 高頻度ポーリング系は除外
  [API][SLOW]  API_LOG_SLOW_SEC（既定 2 秒）を超えた正常応答（warn）
  [API][4xx]   クライアントエラー（warn）
  [API][5xx]   サーバーエラー（error）
  [API][EXC]   ルート処理で未捕捉例外が送出（error。raise を再送出する）

除外対象（_API_LOG_SKIP_PATHS）は UI が常時ポーリングする軽量エンドポイント。
これらは通常応答をログに出さないが、SLOW / 4xx / 5xx / EXC は必ず出す。
"""
import os
import time

from starlette.requests import Request

_API_LOG_SLOW_SEC = float(os.environ.get("API_LOG_SLOW_SEC", "2.0"))

# ログ対象のパス（API と、カード生成などの重い公開エンドポイント）
_LOG_PREFIXES = ("/api/", "/generate_card_image/", "/generate_team_image/", "/refresh_uid/", "/uid/")
_LOG_EXACT = ("/fetch_uid", "/api/contact")

# 高頻度ポーリング系（正常応答はログ省略。エラー時のみ出力）
_API_LOG_SKIP_PATHS = {
    "/api/server_stats",
    "/api/enka_cooldown",
    "/api/data_versions",
    "/api/leyline_versions",
    "/api/calc_method_defaults",
}


def _is_logged_path(path: str) -> bool:
    if path in _LOG_EXACT:
        return True
    return any(path.startswith(p) for p in _LOG_PREFIXES)


async def api_log_middleware(request: Request, call_next):
    path = request.url.path
    if not _is_logged_path(path):
        return await call_next(request)

    method = request.method
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as e:
        dur_ms = (time.perf_counter() - start) * 1000
        print(
            f"[API][EXC] {method} {path} {dur_ms:.0f}ms "
            f"{type(e).__name__}: {e}",
            flush=True,
        )
        raise
    dur_ms = (time.perf_counter() - start) * 1000
    status = response.status_code

    if status >= 500:
        print(f"[API][5xx] {method} {path} {dur_ms:.0f}ms status={status}", flush=True)
    elif status >= 400:
        print(f"[API][4xx] {method} {path} {dur_ms:.0f}ms status={status}", flush=True)
    elif dur_ms >= _API_LOG_SLOW_SEC * 1000:
        print(f"[API][SLOW] {method} {path} {dur_ms:.0f}ms status={status}", flush=True)
    elif path not in _API_LOG_SKIP_PATHS:
        print(f"[API][2xx] {method} {path} {dur_ms:.0f}ms status={status}", flush=True)
    return response
