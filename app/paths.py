import os
from pathlib import Path
from fastapi.templating import Jinja2Templates

# プロジェクトルート = app/paths.py の2階層上（server.py と同居）
# str に保つのは sys.path のメンバーシップチェック（BASE_DIR not in sys.path）を
# 従来通り文字列比較にするため（Path 型だと常に不一致となり毎回 insert される）
BASE_DIR = str(Path(__file__).resolve().parents[1])
STATIC_DIR = os.path.join(BASE_DIR, "static")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
_REGION_STATES_DIR = os.path.join(STATIC_DIR, "assets", "states")

if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR)

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# .env の読み込み（存在しなければ通常の環境変数のみ使用）
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    print("[WARN] python-dotenv が未インストールのため .env は読み込まれません（pip install -r requirements.txt）")

FONT_PATH = os.path.join(BASE_DIR, "static", "fonts", "font_fixed.ttf")
FONT_LIGHT_PATH = os.path.join(BASE_DIR, "static", "fonts", "font_light.ttf")

DESIGN_W, DESIGN_H = 1741, 1159
CARD_W, CARD_H = 2400, 1620
SX = CARD_W / DESIGN_W
SY = CARD_H / DESIGN_H
