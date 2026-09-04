# -*- coding: utf-8 -*-
"""FastAPI エントリポイント: app 生成・静的マウント・ルーター登録・起動イベント・uvicorn 起動のみ。"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
import uvicorn
import os
import sys
import asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from app.paths import STATIC_DIR
# 起動時プリントの順序（Discord → Cache → Card-gen → Admin）を維持するため import 順を固定
from app.core import notify as notify_discord  # noqa: F401
from app.card import cache as img_cache  # noqa: F401
from app.card import pool as card_pool  # noqa: F401
from app.card.bg import _prebuild_backgrounds
from app.routes.admin import admin_router, _admin_security_middleware
from app.routes.api import api_router
from app.routes.bot_admin import bot_admin_router
from app.core.bot_manager import bot_manager

app = FastAPI()


@app.on_event("startup")
async def startup_event():
    await run_in_threadpool(_prebuild_backgrounds)
    # Discord bot 自動起動（NEWBC_BOT_AUTOSTART=0 で無効。トークン未設定時はスキップされる）
    if os.environ.get("NEWBC_BOT_AUTOSTART", "1").lower() in ("1", "true", "yes"):
        result = await run_in_threadpool(bot_manager.start)
        if result.get("ok"):
            print(f"[OK] Discord bot autostart (pid={result.get('pid')})")
        else:
            print(f"[INFO] Discord bot autostart skipped: {result.get('error')}")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    """Service Worker をルートスコープで配信する（/static/ 配下だと制御対象が /static/ に限られる）。"""
    from fastapi.responses import FileResponse
    return FileResponse(
        os.path.join(STATIC_DIR, "sw.js"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )
app.middleware("http")(_admin_security_middleware)

# 静的アセットのクライアントキャッシュ（画像は長めにキャッシュ。JSONは都度取得）
_STATIC_IMAGE_CACHE_MAX_AGE = os.environ.get("STATIC_IMAGE_CACHE_MAX_AGE", "604800")


@app.middleware("http")
async def _static_cache_middleware(request, call_next):
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/"):
        low = path.lower()
        if low.endswith((".webp", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico")):
            response.headers["Cache-Control"] = f"public, max-age={_STATIC_IMAGE_CACHE_MAX_AGE}"
        elif low.endswith(".json"):
            response.headers["Cache-Control"] = "no-cache"
        else:
            response.headers["Cache-Control"] = "public, max-age=3600"
    elif path == "/" or path == "/contact" or path.startswith("/fetch_uid"):
        # HTMLページは常に最新を返す（古いキャッシュで初期設定画面等が出なくなるのを防ぐ）
        response.headers["Cache-Control"] = "no-store"
    return response


# API 応答ログ（最後に登録 → 最外側で全リクエストを捕捉）
from app.core.api_log import api_log_middleware  # noqa: E402

app.middleware("http")(api_log_middleware)


app.include_router(admin_router)
app.include_router(api_router)
app.include_router(bot_admin_router)


if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        reload=os.environ.get("UVICORN_RELOAD", "0").lower() in ("1", "true", "yes"),
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("UVICORN_FORWARDED_ALLOW_IPS", "127.0.0.1"),
    )
