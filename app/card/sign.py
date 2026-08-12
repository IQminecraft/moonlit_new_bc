import os
import time
from collections import deque
from typing import Dict, Any
from fastapi import Request
import hashlib as _hashlib
import hmac as _hmac

# ==========================================================
#  カード画像URL署名（エンドポイント秘匿化 / DDoS 対策）
#  /generate_card_image を直接叩かれても、HMAC署名が無い・
#  期限切れ・パラメータ不一致のリクエストは生成前に安価に拒否。
#  クライアントは /api/card_sign で署名付きURLを取得する
#  （署名取得エンドポイントはIP単位でレート制限）。
#
#  環境変数:
#    CARD_URL_SECRET              署名用シークレット（未設定 = 署名無効）
#    CARD_SIGN_VALIDITY_HOURS     署名の有効時間 (default: 12)
#    CARD_SIGN_RATE_LIMIT_PER_MIN 署名取得のIP毎レート上限 (default: 60, 0=無効)
#    CARD_GEN_RATE_LIMIT_PER_MIN  画像生成のIP毎レート上限 (default: 0=無効)
# ==========================================================
_CARD_URL_SECRET = (os.environ.get("CARD_URL_SECRET") or "").strip()
_CARD_SIGN_VALIDITY_SEC = max(60, float(os.environ.get("CARD_SIGN_VALIDITY_HOURS", "12")) * 3600.0)
_CARD_SIGN_MAX_AGE_SEC = 12 * 3600.0  # 発行済み署名の受付上限（12h を超える exp は拒否）
_CARD_SIGN_RATE_LIMIT_PER_MIN = max(0, int(os.environ.get("CARD_SIGN_RATE_LIMIT_PER_MIN", "60")))
_CARD_GEN_RATE_LIMIT_PER_MIN = max(0, int(os.environ.get("CARD_GEN_RATE_LIMIT_PER_MIN", "0")))
_CARD_SIGN_PARAM_ORDER = ["uid", "avatar_id", "calc_method", "fake_char", "fake_weapon", "beta", "bg_color", "img_format", "bg_mode", "bg_region"]
_TEAM_SIGN_PARAM_ORDER = ["uid", "char_ids", "configs", "boss", "beta", "img_format"]
_rate_buckets = {}  # key -> deque(monotonic秒) スライディングウィンドウ


def _rate_limited(key: str, limit_per_min: int) -> bool:
    """IP毎レート制限。超過していれば True（イベントループ単一スレッドで完結）。"""
    if limit_per_min <= 0:
        return False
    now = time.monotonic()
    dq = _rate_buckets.setdefault(key, deque())
    while dq and now - dq[0] > 60.0:
        dq.popleft()
    if len(dq) >= limit_per_min:
        return True
    dq.append(now)
    if len(_rate_buckets) > 10000:
        for k in [k for k, v in _rate_buckets.items() if not v]:
            _rate_buckets.pop(k, None)
    return False


def _client_ip(request: Request) -> str:
    """uvicorn proxy_headers=True 時、信頼プロキシ（forwarded_allow_ips）経由なら
    client.host は実クライアントIP。Cloudflare直結の場合はCFエッジIPになる。"""
    return request.client.host if request and request.client else "unknown"


def _card_sign_canonical(params: Dict[str, Any]) -> str:
    """署名対象の正規化文字列。パラメータ順を固定して欠落は空文字扱い。"""
    return "|".join(str(params.get(k) or "") for k in _CARD_SIGN_PARAM_ORDER)


def _card_signature(exp: int, params: Dict[str, Any]) -> str:
    msg = f"{_card_sign_canonical(params)}|{exp}"
    return _hmac.new(_CARD_URL_SECRET.encode(), msg.encode(), _hashlib.sha256).hexdigest()


def _verify_card_sign(card_exp, card_sig, params: Dict[str, Any]) -> bool:
    """署名検証。不正・期限切れ・パラメータ不一致は False（生成前に安価に拒否）。"""
    if not _CARD_URL_SECRET or not card_exp or not card_sig:
        return False
    try:
        exp = int(card_exp)
    except (TypeError, ValueError):
        return False
    now = time.time()
    if exp < now or exp > now + _CARD_SIGN_MAX_AGE_SEC:
        return False
    expected = _card_signature(exp, params)
    return _hmac.compare_digest(expected, str(card_sig).lower())


def _team_sign_canonical(params: Dict[str, Any]) -> str:
    """編成カード用の署名対象正規化文字列。"""
    return "|".join(str(params.get(k) or "") for k in _TEAM_SIGN_PARAM_ORDER)


def _team_signature(exp: int, params: Dict[str, Any]) -> str:
    msg = f"{_team_sign_canonical(params)}|{exp}"
    return _hmac.new(_CARD_URL_SECRET.encode(), msg.encode(), _hashlib.sha256).hexdigest()


def _verify_team_sign(card_exp, card_sig, params: Dict[str, Any]) -> bool:
    """編成カードの署名検証。"""
    if not _CARD_URL_SECRET or not card_exp or not card_sig:
        return False
    try:
        exp = int(card_exp)
    except (TypeError, ValueError):
        return False
    now = time.time()
    if exp < now or exp > now + _CARD_SIGN_MAX_AGE_SEC:
        return False
    expected = _team_signature(exp, params)
    return _hmac.compare_digest(expected, str(card_sig).lower())
