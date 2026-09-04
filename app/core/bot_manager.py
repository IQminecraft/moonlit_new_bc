# -*- coding: utf-8 -*-
"""Discord bot（bot/bot.py）の子プロセス管理。

Admin パネルから起動/停止/再起動できるよう、bot を server の子プロセスとして
spawn し、stdout/stderr を logs/discord_bot.{out,err}.log に記録する。
bot 自体の接続状態は bot/bot_status.json（bot 側が定期書き込み）を経由して取得する。
"""
import atexit
import json
import os
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Optional

from app.paths import BASE_DIR

BOT_DIR = os.path.join(BASE_DIR, "bot")
BOT_SCRIPT = os.path.join(BOT_DIR, "bot.py")
BOT_STATUS_PATH = os.path.join(BOT_DIR, "bot_status.json")
LOG_DIR = os.path.join(BASE_DIR, "logs")
OUT_LOG_PATH = os.path.join(LOG_DIR, "discord_bot.out.log")
ERR_LOG_PATH = os.path.join(LOG_DIR, "discord_bot.err.log")

_STOP_WAIT_SEC = 10.0
_TAIL_MAX_BYTES = 256 * 1024


def _token_configured() -> bool:
    """bot トークンが環境変数（.env 由来を含む）に設定済みか。"""
    return bool((os.environ.get("NEWBC_BOT_TOKEN") or os.environ.get("DISCORD_BOT_TOKEN") or "").strip())


def _pid_alive(pid: Optional[int]) -> bool:
    """pid のプロセスが生存しているか（Windows は ctypes、POSIX は kill(0)）。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            if kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
                return code.value == STILL_ACTIVE
            return True
        finally:
            kernel32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _read_status_file() -> Optional[Dict[str, Any]]:
    """bot 側が書き込んだ bot_status.json を読む。"""
    try:
        with open(BOT_STATUS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _read_tail_text(path: str, max_bytes: int = _TAIL_MAX_BYTES) -> str:
    """ログファイルの末尾（最大 max_bytes）を読み、UTF-8 文字列として返す。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            raw = f.read()
        text = raw.decode("utf-8", errors="replace")
        if size > max_bytes:
            # 途中から読んだ場合の先頭不完全行を捨てる
            nl = text.find("\n")
            if nl >= 0:
                text = text[nl + 1:]
        return text
    except FileNotFoundError:
        return ""
    except Exception as e:
        return f"(ログ読み込み失敗: {e})"


def _tail_lines(path: str, n: int) -> str:
    text = _read_tail_text(path)
    if not text:
        return ""
    return "\n".join(text.rstrip("\n").split("\n")[-n:])


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


class BotManager:
    """bot サブプロセスのライフサイクルとログを管理するシングルトン。"""

    def __init__(self):
        self._proc: Optional[subprocess.Popen] = None
        self._out_fh = None
        self._err_fh = None
        self._started_at: Optional[str] = None
        self._stop_requested = False
        self._lock = threading.Lock()
        atexit.register(self._atexit)

    # ------------------------------------------------------------ internal
    def _orphan_pid(self) -> Optional[int]:
        """管理下にない（前回の server 終了時に取り残された）bot プロセスの pid。"""
        proc = self._proc
        if proc is not None and proc.poll() is None:
            return None
        data = _read_status_file()
        if not data:
            return None
        pid = data.get("pid")
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return None
        if pid > 0 and _pid_alive(pid):
            return pid
        return None

    def _kill_pid(self, pid: int, wait_sec: float = _STOP_WAIT_SEC) -> None:
        if sys.platform == "win32":
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=15)
            except Exception as e:
                print(f"[WARN] bot taskkill failed (pid={pid}): {e}")
            return
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            return
        deadline = time.time() + wait_sec
        while time.time() < deadline and _pid_alive(pid):
            time.sleep(0.2)
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def _close_log_handles(self) -> None:
        for attr in ("_out_fh", "_err_fh"):
            fh = getattr(self, attr)
            if fh:
                try:
                    fh.close()
                except Exception:
                    pass
                setattr(self, attr, None)

    # ------------------------------------------------------------ public
    def start(self) -> Dict[str, Any]:
        with self._lock:
            return self._start_locked()

    def _start_locked(self) -> Dict[str, Any]:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            return {"ok": False, "error": "bot はすでに起動しています", "pid": proc.pid}
        if not os.path.exists(BOT_SCRIPT):
            return {"ok": False, "error": f"bot スクリプトが見つかりません: {BOT_SCRIPT}"}
        if not _token_configured():
            return {"ok": False, "error": "NEWBC_BOT_TOKEN が未設定のため起動できません（.env に設定してください）"}

        # 孤立プロセスが残っていれば二重起動防止のため先に停止する
        orphan = self._orphan_pid()
        if orphan:
            print(f"[Bot] stopping orphan bot process (pid={orphan})")
            self._kill_pid(orphan)

        os.makedirs(LOG_DIR, exist_ok=True)
        self._close_log_handles()
        try:
            self._out_fh = open(OUT_LOG_PATH, "ab")
            self._err_fh = open(ERR_LOG_PATH, "ab")
        except OSError as e:
            self._close_log_handles()
            return {"ok": False, "error": f"ログファイルを開けませんでした: {e}"}

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        kwargs: Dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            self._proc = subprocess.Popen(
                [sys.executable, "-u", BOT_SCRIPT],
                cwd=BASE_DIR,
                env=env,
                stdout=self._out_fh,
                stderr=self._err_fh,
                stdin=subprocess.DEVNULL,
                **kwargs,
            )
        except Exception as e:
            self._close_log_handles()
            self._proc = None
            return {"ok": False, "error": f"プロセスの起動に失敗しました: {e}"}
        self._started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        self._stop_requested = False
        print(f"[OK] Discord bot started (pid={self._proc.pid})")
        return {"ok": True, "pid": self._proc.pid, "message": "bot を起動しました"}

    def stop(self) -> Dict[str, Any]:
        with self._lock:
            return self._stop_locked()

    def _stop_locked(self) -> Dict[str, Any]:
        self._stop_requested = True
        proc = self._proc
        if proc is None or proc.poll() is not None:
            orphan = self._orphan_pid()
            if orphan:
                self._kill_pid(orphan)
                return {"ok": True, "message": f"孤立プロセス (pid={orphan}) を停止しました"}
            return {"ok": False, "error": "bot は起動していません"}
        pid = proc.pid
        try:
            # Windows では TerminateProcess（強制終了）、POSIX では SIGTERM
            proc.terminate()
        except Exception as e:
            print(f"[WARN] bot terminate failed: {e}")
        try:
            proc.wait(timeout=_STOP_WAIT_SEC)
        except subprocess.TimeoutExpired:
            print(f"[WARN] bot did not exit within {_STOP_WAIT_SEC}s, killing")
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        code = proc.poll()
        self._close_log_handles()
        self._proc = None
        print(f"[OK] Discord bot stopped (pid={pid}, exit={code})")
        return {"ok": True, "message": "bot を停止しました", "exit_code": code}

    def restart(self) -> Dict[str, Any]:
        with self._lock:
            stop_res = self._stop_locked()
            start_res = self._start_locked()
            if not start_res.get("ok"):
                return {
                    "ok": False,
                    "error": start_res.get("error") or "再起動に失敗しました",
                    "stop": stop_res,
                }
            return {"ok": True, "message": "bot を再起動しました", "pid": start_res.get("pid")}

    def status(self) -> Dict[str, Any]:
        proc = self._proc
        exit_code = None
        managed = False
        if proc is not None and proc.poll() is None:
            managed = True
        elif proc is not None:
            exit_code = proc.poll()

        if managed:
            state = "running"
        else:
            orphan = self._orphan_pid()
            state = "orphan" if orphan else "stopped"

        pid = proc.pid if managed else (self._orphan_pid() if state == "orphan" else None)

        # bot 側ステータス（停止中は前回実行の残滅なので送らない）
        data = _read_status_file() if state in ("running", "orphan") else None
        bot_info = None
        if data:
            bot_info = {
                "connected": bool(data.get("connected")),
                "user": data.get("user"),
                "user_id": data.get("user_id"),
                "latency_ms": data.get("latency_ms"),
                "guild_count": data.get("guild_count"),
                "command_count": data.get("command_count"),
                "discord_py": data.get("discord_py"),
                "api_base": data.get("api_base"),
                "guild_id_set": bool(data.get("guild_id_set")),
                "started_at": data.get("started_at"),
                "last_ready_at": data.get("last_ready_at"),
                "synced": data.get("synced"),
                "last_error": data.get("last_error"),
            }

        return {
            "ok": True,
            "state": state,
            "managed": managed,
            "pid": pid,
            "exit_code": exit_code,
            "started_at": self._started_at if managed else None,
            "stop_requested": self._stop_requested,
            "token_configured": _token_configured(),
            "autostart": os.environ.get("NEWBC_BOT_AUTOSTART", "1").lower() in ("1", "true", "yes"),
            "bot": bot_info,
            "logs": {"out_size": _file_size(OUT_LOG_PATH), "err_size": _file_size(ERR_LOG_PATH)},
        }

    def logs(self, lines: int = 200) -> Dict[str, Any]:
        try:
            lines = max(10, min(1000, int(lines or 200)))
        except (TypeError, ValueError):
            lines = 200
        return {
            "ok": True,
            "out": _tail_lines(OUT_LOG_PATH, lines),
            "err": _tail_lines(ERR_LOG_PATH, lines),
        }

    def clear_logs(self) -> Dict[str, Any]:
        with self._lock:
            try:
                os.makedirs(LOG_DIR, exist_ok=True)
                for path in (OUT_LOG_PATH, ERR_LOG_PATH):
                    with open(path, "wb"):
                        pass
                return {"ok": True, "message": "bot のログをクリアしました"}
            except Exception as e:
                return {"ok": False, "error": str(e)}

    def _atexit(self) -> None:
        """server 終了時に bot 子プロセスを取り残さない。"""
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass


bot_manager = BotManager()
