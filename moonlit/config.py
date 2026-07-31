from __future__ import annotations

import os
import secrets

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
FONTS_DIR = os.path.join(BASE_DIR, "fonts")
CACHE_DIR = os.path.join(STATIC_DIR, "cache")
EXTERNAL_DIR = os.path.join(BASE_DIR, "external")

FONT_PATH = os.path.join(FONTS_DIR, "font_fixed.ttf")
FONT_LIGHT_PATH = os.path.join(FONTS_DIR, "font_light.ttf")
TEXT_MAP_PATH = os.path.join(EXTERNAL_DIR, "enka_py", "assets", "text_map.json")

ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "aikyu")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "iqmc1104")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET") or secrets.token_hex(32)
ADMIN_COOKIE = "admin_session"
ADMIN_MAX_AGE = 60 * 60 * 12


def showcase_cache_path(uid) -> str:
    return os.path.join(CACHE_DIR, f"showcase_{uid}.json")
