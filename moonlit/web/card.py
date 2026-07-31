from __future__ import annotations

import io

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from moonlit.card.build import build_card_model, build_card_image_model
from moonlit.card.image_render import render_card_image

router = APIRouter()


@router.get("/api/card_data/{uid}/{avatar_id}")
async def get_card_data(uid: str, avatar_id: str, calc_method: str = "crit",
                        fake_char: str = None, fake_weapon: str = None, beta: str = "false"):
    return await run_in_threadpool(
        build_card_model, uid, avatar_id, calc_method, fake_char, fake_weapon, beta
    )


@router.get("/generate_card_image/{uid}/{avatar_id}/{calc_method}")
async def generate_card_image(uid: str, avatar_id: str, calc_method: str,
                              fake_char: str = None, fake_weapon: str = None,
                              beta: str = "false", bg_color: str = None):
    png_bytes = await run_in_threadpool(
        _generate_sync, uid, avatar_id, calc_method, fake_char, fake_weapon, beta, bg_color
    )
    return StreamingResponse(io.BytesIO(png_bytes), media_type="image/png")


def _generate_sync(uid: str, avatar_id: str, calc_method: str,
                   fake_char=None, fake_weapon=None, beta="false", bg_color=None):
    model = build_card_image_model(uid, avatar_id, calc_method, fake_char, fake_weapon, beta, bg_color)
    return render_card_image(model)