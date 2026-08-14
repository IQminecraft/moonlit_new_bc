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

app = FastAPI()


@app.on_event("startup")
async def startup_event():
    await run_in_threadpool(_prebuild_backgrounds)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
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
    return response


app.include_router(admin_router)
app.include_router(api_router)


if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        reload=os.environ.get("UVICORN_RELOAD", "1").lower() in ("1", "true", "yes"),
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("UVICORN_FORWARDED_ALLOW_IPS", "127.0.0.1"),
    )
