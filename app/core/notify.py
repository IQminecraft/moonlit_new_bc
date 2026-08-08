import os
import json
import time
from typing import Optional, Dict, Any

# Discord Webhook（エラー通知）.env の DISCORD_WEBHOOK_URL
# 未設定なら送信しない。リクエストをブロックしないよう別スレッドで POST。
_DISCORD_WEBHOOK_URL = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
_DISCORD_WEBHOOK_USERNAME = (os.environ.get("DISCORD_WEBHOOK_USERNAME") or "Artifacter Error").strip()
_DISCORD_MIN_INTERVAL_SEC = float(os.environ.get("DISCORD_MIN_INTERVAL_SEC", "2"))
_DISCORD_LAST_SENT = 0.0
_DISCORD_LOCK = __import__("threading").Lock()


def _now_millis() -> int:
    """System.currentTimeMillis() 相当（UNIX epoch ミリ秒）。"""
    return int(time.time() * 1000)


def _discord_send_sync(payload: dict) -> None:
    """同期 POST。失敗しても握りつぶす（監視自体で落とさない）。"""
    if not _DISCORD_WEBHOOK_URL:
        return
    global _DISCORD_LAST_SENT
    try:
        import urllib.request
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            _DISCORD_WEBHOOK_URL,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "ArtifacterServer/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp.read()
        with _DISCORD_LOCK:
            _DISCORD_LAST_SENT = time.time()
    except Exception as e:
        print(f"[WARN] Discord webhook failed: {e}")


def report_error_to_discord(
    title: str,
    message: str,
    *,
    path: str = "",
    extra: Optional[Dict[str, Any]] = None,
    traceback_text: str = "",
    level: str = "error",
) -> None:
    """エラーをコンソールに出し、Discord Webhook へ非同期送信する。

    埋め込みに currentTimeMillis 相当の millis を必ず含める。
    """
    millis = _now_millis()
    ts_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(millis / 1000.0))
    line = f"[{millis}] {title}: {message}"
    if path:
        line += f" path={path}"
    print(f"[Error] {line}")

    if not _DISCORD_WEBHOOK_URL:
        return

    # 連打防止（最低間隔）
    with _DISCORD_LOCK:
        if time.time() - _DISCORD_LAST_SENT < _DISCORD_MIN_INTERVAL_SEC:
            # コンソールには出したので Webhook だけスキップ
            print(f"[Error] Discord rate-limit skip (min interval {_DISCORD_MIN_INTERVAL_SEC}s)")
            return

    color = 0xE74C3C if level == "error" else 0xF39C12  # red / orange
    fields = [
        {"name": "millis", "value": f"`{millis}`", "inline": True},
        {"name": "time", "value": ts_iso, "inline": True},
        {"name": "level", "value": level, "inline": True},
    ]
    if path:
        fields.append({"name": "path", "value": f"`{path[:200]}`", "inline": False})
    if extra:
        for k, v in list(extra.items())[:8]:
            fields.append({
                "name": str(k)[:64],
                "value": f"`{str(v)[:200]}`",
                "inline": True,
            })
    desc = (message or "")[:1800]
    if traceback_text:
        tb = traceback_text.strip()
        if len(tb) > 1500:
            tb = "…\n" + tb[-1500:]
        desc = (desc + "\n```\n" + tb + "\n```")[:3900]

    payload = {
        "username": _DISCORD_WEBHOOK_USERNAME,
        "embeds": [{
            "title": (title or "Error")[:200],
            "description": desc or "(no message)",
            "color": color,
            "fields": fields,
            "footer": {"text": f"millis={millis}"},
        }],
    }

    try:
        t = __import__("threading").Thread(
            target=_discord_send_sync,
            args=(payload,),
            name="discord-webhook",
            daemon=True,
        )
        t.start()
    except Exception as e:
        print(f"[WARN] Discord thread start failed: {e}")


if _DISCORD_WEBHOOK_URL:
    print("[OK] Discord error webhook configured (DISCORD_WEBHOOK_URL)")
else:
    print("[WARN] DISCORD_WEBHOOK_URL 未設定のためエラーの Discord 通知は無効です")
