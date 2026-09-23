# -*- coding: utf-8 -*-
import re

from fastapi import HTTPException

from app.card.calc_method import VALID_CALC_METHODS
from app.card.resonance import parse_resonance_param

_UID_RE = re.compile(r"^\d{1,20}$")
_AVATAR_ID_RE = re.compile(r"^\d{1,20}(?:-\d{1,3})?$")
_FAKE_CHAR_RE = re.compile(r"^[0-9A-Za-z_-]{1,64}$")
_FAKE_WEAPON_RE = re.compile(r"^\d{1,20}$")
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
_HEX_COLOR_RE = re.compile(r"^(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off", ""}


def clean_uid(uid, field: str = "UID") -> str:
    s = str(uid or "").strip()
    if not _UID_RE.match(s):
        raise HTTPException(status_code=400, detail=f"{field}は数字のみで入力してください。")
    return s


def clean_avatar_id(avatar_id) -> str:
    s = str(avatar_id or "").strip()
    if not _AVATAR_ID_RE.match(s):
        raise HTTPException(status_code=400, detail="キャラクターIDが不正です。")
    return s


def clean_calc_method_strict(calc_method) -> str:
    s = str(calc_method or "").strip().lower()
    if s not in VALID_CALC_METHODS:
        raise HTTPException(status_code=400, detail="計算方式が不正です。")
    return s


def clean_bool_str(value, default: bool = False) -> str:
    if value is None:
        return "true" if default else "false"
    s = str(value).strip().lower()
    if s in _TRUE_VALUES:
        return "true"
    if s in _FALSE_VALUES:
        return "false"
    return "true" if default else "false"


def clean_base_prec(value) -> str:
    s = str(value or "0").strip()
    return s if s in ("0", "2", "4") else "0"


def clean_substat_dots(value) -> str:
    s = str(value or "0").strip().lower()
    return "1" if s in _TRUE_VALUES else "0"


def clean_resonance(value):
    keys = parse_resonance_param(value)
    return ",".join(keys) if keys else None


def clean_traveler_buffs(value):
    if value is None:
        return None
    from app.card.traveler_buffs import parse_traveler_buffs
    keys = parse_traveler_buffs(value)
    return ",".join(sorted(keys))


def clean_bg_color(value):
    if value is None:
        return None
    s = str(value).strip().lstrip("#").lower()
    if not s:
        return None
    if not _HEX_COLOR_RE.match(s):
        raise HTTPException(status_code=400, detail="背景色が不正です。16進数で指定してください。")
    return s


def clean_token(value, field: str, allow_empty: bool = True):
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None if allow_empty else ""
    if not _TOKEN_RE.match(s):
        raise HTTPException(status_code=400, detail=f"{field}が不正です。")
    return s


def clean_fake_char(value):
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if not _FAKE_CHAR_RE.match(s):
        raise HTTPException(status_code=400, detail="差し替えキャラIDが不正です。")
    return s


def clean_fake_weapon(value):
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if not _FAKE_WEAPON_RE.match(s):
        raise HTTPException(status_code=400, detail="差し替え武器IDが不正です。")
    return s


def clean_char_ids(char_ids) -> list:
    ids = [s.strip() for s in str(char_ids or "").split(",") if s.strip()]
    if len(ids) != 4:
        raise HTTPException(status_code=400, detail="キャラは4体選択してください。")
    for cid in ids:
        if not _AVATAR_ID_RE.match(cid):
            raise HTTPException(status_code=400, detail="キャラクターIDが不正です。")
    return ids
