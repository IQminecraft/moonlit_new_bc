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

# 編成共有関連のランタイムデータ置き場（プロジェクト直下・共有リンク/スナップショット等のJSON）
# static/cache の生成キャッシュ（cards/ showcase_*.json）とは分離して管理する
SHARE_DATA_DIR = os.path.join(BASE_DIR, "share_data")
if not os.path.exists(SHARE_DATA_DIR):
    os.makedirs(SHARE_DATA_DIR)

templates = Jinja2Templates(directory=TEMPLATES_DIR)

# staticアセットの差し替えをブラウザが即反映できるよう mtime を ?v= に付与する
def _asset_ver(rel: str) -> str:
    try:
        return str(int(os.path.getmtime(os.path.join(STATIC_DIR, rel))))
    except OSError:
        return "0"

templates.env.globals["asset_ver"] = _asset_ver

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

# サイト（アプリ）自体のバージョン。ヘッダーのタイトル横に表示する。
SITE_VERSION = "2.2"
