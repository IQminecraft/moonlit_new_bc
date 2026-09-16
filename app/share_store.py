# -*- coding: utf-8 -*-
"""編成共有ランタイムデータの置き場抽象レイヤー。

ローカル（プロジェクト直下 share_data/）と Cloudflare R2 を同一 API で扱う。
共有リンク（sid / 短縮ID）のデータは「実機と開発サーバーで同じものが見える」必要が
あるため、R2 に置けばどちらで生成したリンクも相互に踏める。

環境変数（.env）:
  SHARE_BACKEND=local|r2          既定 local。r2 指定時に設定/接続が不正なら
                                  起動時 warning を出して local にフォールバック
  R2_ACCOUNT_ID                   Cloudflare アカウント ID（ダッシュボードの URL にも表示）
  R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY
                                  R2 の API トークン（S3互換）
  R2_BUCKET                       バケット名（private のまま運用すること）

key はスラッシュ区相対パス（例: "snapshots/s….json", "short/xxxxxxx.json",
"abyss_drafts/<uid>_live.json"）。ローカルでは SHARE_DATA_DIR 直下の相対パスに対応。
"""
import os
import threading

from app.paths import SHARE_DATA_DIR

_MODE = None
_MODE_LOCK = threading.Lock()
_CLIENT = None
_LAST_ERROR = None


def _r2_config():
    return (
        os.environ.get("R2_ACCOUNT_ID") or "",
        os.environ.get("R2_ACCESS_KEY_ID") or "",
        os.environ.get("R2_SECRET_ACCESS_KEY") or "",
        os.environ.get("R2_BUCKET") or "",
    )


def mode() -> str:
    """'r2' または 'local'。初回に決定し、以降は据え置き（フォールバックも1回だけ判定）。"""
    global _MODE, _CLIENT, _LAST_ERROR
    with _MODE_LOCK:
        if _MODE is not None:
            return _MODE
        want = (os.environ.get("SHARE_BACKEND") or "local").strip().lower()
        if want != "r2":
            _MODE = "local"
            return _MODE
        acct, ak, sk, bucket = _r2_config()
        if not (acct and ak and sk and bucket):
            print("[ShareStore] SHARE_BACKEND=r2 だが R2_* 設定が不足しているため local にフォールバック", flush=True)
            _MODE = "local"
            return _MODE
        try:
            import boto3
            _CLIENT = boto3.client(
                "s3",
                endpoint_url=f"https://{acct}.r2.cloudflarestorage.com",
                aws_access_key_id=ak,
                aws_secret_access_key=sk,
                region_name="auto",
            )
            # HeadBucket はバケット限定トークンで 403 になり得るため、
            # 実運用で使う put/get/delete のプローブで接続確認する
            probe = "__sharestore__/probe.json"
            _CLIENT.put_object(Bucket=bucket, Key=probe, Body=b"{}")
            got = _CLIENT.get_object(Bucket=bucket, Key=probe)["Body"].read()
            _CLIENT.delete_object(Bucket=bucket, Key=probe)
            if got != b"{}":
                raise RuntimeError("probe read mismatch")
            _MODE = "r2"
            print(f"[ShareStore] Cloudflare R2 バックエンド有効 (bucket={bucket})", flush=True)
        except Exception as e:
            print(f"[ShareStore] R2 接続失敗のため local にフォールバック: {e}", flush=True)
            _MODE = "local"
            _LAST_ERROR = str(e)
        return _MODE


def _local_path(key: str) -> str:
    # key は "dir/name.json" 形式。親ディレクトリは自動作成
    p = os.path.join(SHARE_DATA_DIR, *key.split("/"))
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    return p


def put(key: str, data: bytes) -> None:
    if mode() == "r2":
        _, _, _, bucket = _r2_config()
        _CLIENT.put_object(Bucket=bucket, Key=key, Body=data)
        return
    p = _local_path(key)
    tmp = p + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, p)


def get(key: str):
    if mode() == "r2":
        _, _, _, bucket = _r2_config()
        try:
            obj = _CLIENT.get_object(Bucket=bucket, Key=key)
            return obj["Body"].read()
        except Exception:
            return None
    try:
        with open(os.path.join(SHARE_DATA_DIR, *key.split("/")), "rb") as f:
            return f.read()
    except OSError:
        return None


def delete(key: str) -> None:
    if mode() == "r2":
        _, _, _, bucket = _r2_config()
        try:
            _CLIENT.delete_object(Bucket=bucket, Key=key)
        except Exception:
            pass
        return
    try:
        os.remove(os.path.join(SHARE_DATA_DIR, *key.split("/")))
    except OSError:
        pass


def exists(key: str) -> bool:
    return get(key) is not None


def list_keys(prefix: str = ""):
    """.json を持つ key 一覧（SHARE_DATA_DIR からの相対、"/"区切り）。prune 用の走査に使う。"""
    if mode() == "r2":
        _, _, _, bucket = _r2_config()
        out = []
        token = None
        try:
            while True:
                kw = {"Bucket": bucket, "Prefix": prefix}
                if token:
                    kw["ContinuationToken"] = token
                resp = _CLIENT.list_objects_v2(**kw)
                out.extend(o["Key"] for o in resp.get("Contents", []) if o["Key"].endswith(".json"))
                token = resp.get("NextContinuationToken")
                if not token:
                    break
        except Exception:
            pass
        return out
    parts = [p for p in prefix.split("/") if p]
    start = os.path.join(SHARE_DATA_DIR, *parts) if parts else SHARE_DATA_DIR
    if os.path.isfile(start):
        return [prefix]
    out = []
    stack = [start]
    while stack:
        d = stack.pop()
        try:
            entries = os.listdir(d)
        except OSError:
            continue
        for f in entries:
            p = os.path.join(d, f)
            if os.path.isdir(p):
                stack.append(p)
            elif f.endswith(".json"):
                out.append(os.path.relpath(p, SHARE_DATA_DIR).replace("\\", "/"))
    return out


def backend_info() -> dict:
    """管理/デバッグ用（server_stats 等から参照できる程度の情報）。"""
    m = mode()
    return {"backend": m, "error": _LAST_ERROR}
