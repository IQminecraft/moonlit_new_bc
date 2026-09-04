# -*- coding: utf-8 -*-
"""Admin Bot 管理 API（/admin/api/bot/*）。

bot/bot.py を子プロセスとして起動/停止/再起動し、状態とログを返す。
認証は admin.py の _is_admin（cookie セッショントークン）で行う。
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.routes.admin import _get_data_manager, _is_admin
from app.core.bot_manager import bot_manager

bot_admin_router = APIRouter()


def _unauthorized() -> JSONResponse:
    return JSONResponse({"detail": "Unauthorized"}, status_code=401)


@bot_admin_router.get("/admin/api/bot/status")
async def bot_status(request: Request):
    if not _is_admin(request):
        return _unauthorized()
    return JSONResponse(bot_manager.status())


@bot_admin_router.get("/admin/api/bot/logs")
async def bot_logs(request: Request, lines: int = 200):
    if not _is_admin(request):
        return _unauthorized()
    return JSONResponse(bot_manager.logs(lines))


@bot_admin_router.post("/admin/api/bot/clear_logs")
async def bot_clear_logs(request: Request):
    if not _is_admin(request):
        return _unauthorized()
    result = await run_in_threadpool(bot_manager.clear_logs)
    try:
        _get_data_manager().append_log("bot_clear_logs", bool(result.get("ok")), str(result.get("message") or result.get("error") or ""))
    except Exception:
        pass
    return JSONResponse(result)


@bot_admin_router.post("/admin/api/bot/action")
async def bot_action(request: Request):
    if not _is_admin(request):
        return _unauthorized()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid JSON"}, status_code=400)
    action = (body or {}).get("action")

    if action == "start":
        result = await run_in_threadpool(bot_manager.start)
    elif action == "stop":
        result = await run_in_threadpool(bot_manager.stop)
    elif action == "restart":
        result = await run_in_threadpool(bot_manager.restart)
    else:
        return JSONResponse({"ok": False, "error": f"unknown action: {action}"}, status_code=400)

    try:
        _get_data_manager().append_log(
            f"bot_{action}",
            bool(result.get("ok")),
            str(result.get("message") or result.get("error") or ""),
        )
    except Exception:
        pass
    return JSONResponse(result)
