import os
import sys
import time
import time as _time
import threading as _threading
from typing import Optional, Dict, Any

from app.card.cache import (
    _IMAGE_CACHE, _RESIZED_CACHE, _SPLASH_BLUR_CACHE, _REGION_BGS, _FONT_CACHE,
    _CACHE_MAX_IMAGES, _CACHE_MAX_RESIZED, _CACHE_MAX_SPLASH_BLUR, _CACHE_MAX_REGION_BGS,
    _CARD_CACHE_TTL_SEC, _CARD_CACHE_LAST_RESET,
)
from app.card.pool import _card_gen_stats

# ==========================================================
#  サーバー CPU / メモリ（システム全体のみ）
# ==========================================================
_CPU_LOCK = _threading.Lock()

# システム全体の CPU 使用率は、リクエスト毎の計測ではなくバックグラウンド
# スレッドの連続サンプリング（約1秒窓）で保持する。タスクマネージャーと
# 同じ粒度・途切れのない値になり、短い負荷スパイクも取りこぼさない。
_CPU_CURRENT: Optional[float] = None
_CPU_SOURCE: Optional[str] = None
_CPU_SAMPLER_STARTED = False
_CPU_SAMPLER_START_LOCK = _threading.Lock()


def _cpu_count():
    try:
        return max(1, os.cpu_count() or 1)
    except Exception:
        return 1


def _read_system_cpu_jiffies():
    """
    システム全体の CPU 時間を /proc/stat 先頭行 (cpu ...) から読む。
    戻り値: (busy, total)  ※jiffies
      busy  = user+nice+system+irq+softirq+steal
      total = busy + idle + iowait
    """
    try:
        with open("/proc/stat", "r", encoding="utf-8") as f:
            line = f.readline()
        if not (line.startswith("cpu ") or line.startswith("cpu\t")):
            return None
        parts = line.split()
        # parts[0] == 'cpu'
        vals = [int(x) for x in parts[1:]]
        if len(vals) < 4:
            return None
        user = vals[0]
        nice = vals[1]
        system = vals[2]
        idle = vals[3]
        iowait = vals[4] if len(vals) > 4 else 0
        irq = vals[5] if len(vals) > 5 else 0
        softirq = vals[6] if len(vals) > 6 else 0
        steal = vals[7] if len(vals) > 7 else 0
        busy = user + nice + system + irq + softirq + steal
        total = busy + idle + iowait
        if total <= 0:
            return None
        return busy, total
    except Exception:
        return None


def _system_cpu_percent_proc(sample_sec: float = 0.4):
    """同一リクエスト内で2回サンプリングしてシステム全体の使用率を算出。"""
    with _CPU_LOCK:
        a = _read_system_cpu_jiffies()
        if a is None:
            return None, "proc_stat_unreadable"
        _time.sleep(sample_sec)
        b = _read_system_cpu_jiffies()
        if b is None:
            return None, "proc_stat_second_failed"
        busy1, total1 = a
        busy2, total2 = b
        db = busy2 - busy1
        dt = total2 - total1
        if dt <= 0:
            return None, f"proc_stat_bad_delta dt={dt}"
        pct = max(0.0, min(100.0, (db / float(dt)) * 100.0))
        return pct, "proc_stat"


def _system_cpu_percent_psutil():
    try:
        import psutil  # type: ignore
        # システム全体（Process ではない）
        return float(psutil.cpu_percent(interval=0.4)), "psutil"
    except Exception as e:
        return None, f"psutil:{e}"


def _system_cpu_percent_loadavg():
    try:
        load1 = float(os.getloadavg()[0])
        n = _cpu_count()
        return max(0.0, min(100.0, (load1 / n) * 100.0)), "loadavg"
    except Exception as e:
        return None, f"loadavg:{e}"


def _cpu_sampler_loop():
    """1秒ごとにシステム全体の CPU 使用率を更新し続けるバックグラウンドスレッド。"""
    global _CPU_CURRENT, _CPU_SOURCE
    psutil_ok = False
    try:
        import psutil  # type: ignore
        psutil_ok = True
    except Exception:
        pass
    while True:
        try:
            if psutil_ok:
                # interval=None は「前回呼び出しからの平均」≒直近1秒の使用率
                psutil.cpu_percent(interval=0.1)  # 初回の基準点を確定させる
                _time.sleep(1.0)
                _CPU_CURRENT, _CPU_SOURCE = float(psutil.cpu_percent(interval=None)), "psutil"
                continue
        except Exception:
            pass
        if sys.platform.startswith("linux"):
            v, s = _system_cpu_percent_proc(1.0)
            if v is not None:
                _CPU_CURRENT, _CPU_SOURCE = v, s
                continue
        try:
            load1 = float(os.getloadavg()[0])
            _CPU_CURRENT, _CPU_SOURCE = max(0.0, min(100.0, (load1 / _cpu_count()) * 100.0)), "loadavg"
            continue
        except Exception:
            pass
        _time.sleep(1.0)


def _ensure_cpu_sampler():
    """初回アクセス時にサンプラースレッドを1回だけ起動する。"""
    global _CPU_SAMPLER_STARTED
    if _CPU_SAMPLER_STARTED:
        return
    with _CPU_SAMPLER_START_LOCK:
        if _CPU_SAMPLER_STARTED:
            return
        _threading.Thread(target=_cpu_sampler_loop, name="cpu-sampler", daemon=True).start()
        _CPU_SAMPLER_STARTED = True


def _cpu_sample_once():
    """スレッド起動直後の初回リクエスト用に、その場で1回だけ計測する。"""
    if sys.platform.startswith("linux"):
        v, s = _system_cpu_percent_proc(0.4)
        if v is not None:
            return v, s
    v, s = _system_cpu_percent_psutil()
    if v is not None:
        return v, s
    return _system_cpu_percent_loadavg()


def _read_meminfo():
    total_kb = avail_kb = free_kb = buffers_kb = cached_kb = None
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total_kb = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    avail_kb = int(line.split()[1])
                elif line.startswith("MemFree:"):
                    free_kb = int(line.split()[1])
                elif line.startswith("Buffers:"):
                    buffers_kb = int(line.split()[1])
                elif line.startswith("Cached:"):
                    cached_kb = int(line.split()[1])
        if avail_kb is None and free_kb is not None:
            avail_kb = free_kb + (buffers_kb or 0) + (cached_kb or 0)
    except Exception:
        pass
    return total_kb, avail_kb


def _read_process_rss_kb():
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except Exception:
        pass
    try:
        import resource
        rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        if sys.platform == "darwin":
            return rss // 1024
        return rss
    except Exception:
        return None


def _mem_from_psutil():
    try:
        import psutil  # type: ignore
        vm = psutil.virtual_memory()
        return int(vm.total / 1024), int(vm.available / 1024), float(vm.percent)
    except Exception:
        return None


def _server_stats_snapshot() -> Dict[str, Any]:
    now = _time.time()
    errors = []

    # システム全体 CPU のみ（バックグラウンドの1秒窓連続サンプリング結果を返す）
    _ensure_cpu_sampler()
    cpu_pct, cpu_source = _CPU_CURRENT, _CPU_SOURCE
    if cpu_pct is None:
        # スレッド初回更新前のリクエストのみ、その場で1回計測して埋める
        cpu_pct, cpu_source = _cpu_sample_once()
    if cpu_pct is None:
        errors.append("cpu_unavailable")

    # システム全体メモリ
    mem_pct = mem_used_mb = mem_total_mb = None
    ps_mem = _mem_from_psutil()
    if ps_mem is not None:
        total_kb, avail_kb, mem_pct = ps_mem
        used_kb = max(0, total_kb - avail_kb)
        mem_used_mb = round(used_kb / 1024.0, 1)
        mem_total_mb = round(total_kb / 1024.0, 1)
    else:
        total_kb, avail_kb = _read_meminfo()
        if total_kb and total_kb > 0 and avail_kb is not None:
            used_kb = max(0, total_kb - avail_kb)
            mem_pct = max(0.0, min(100.0, (used_kb / total_kb) * 100.0))
            mem_used_mb = round(used_kb / 1024.0, 1)
            mem_total_mb = round(total_kb / 1024.0, 1)
        else:
            errors.append("mem_unavailable")

    rss_kb = _read_process_rss_kb()
    process_mb = round(rss_kb / 1024.0, 1) if rss_kb is not None else None

    loadavg = None
    try:
        loadavg = [round(x, 2) for x in os.getloadavg()]
    except Exception:
        pass

    return {
        "ok": True,
        "cpu_percent": None if cpu_pct is None else round(float(cpu_pct), 1),
        "cpu_source": cpu_source,
        "cpu_count": _cpu_count(),
        "mem_percent": None if mem_pct is None else round(float(mem_pct), 1),
        "mem_used_mb": mem_used_mb,
        "mem_total_mb": mem_total_mb,
        "process_mb": process_mb,
        "loadavg": loadavg,
        "platform": sys.platform,
        "cache": {
            "images": len(_IMAGE_CACHE),
            "images_max": _CACHE_MAX_IMAGES,
            "resized": len(_RESIZED_CACHE),
            "resized_max": _CACHE_MAX_RESIZED,
            "splash_blur": len(_SPLASH_BLUR_CACHE),
            "splash_blur_max": _CACHE_MAX_SPLASH_BLUR,
            "fonts": len(_FONT_CACHE),
            "region_bgs": len(_REGION_BGS),
            "region_bgs_max": _CACHE_MAX_REGION_BGS,
            "ttl_sec": round(_CARD_CACHE_TTL_SEC, 1),
            "last_reset_ago_sec": round(time.time() - _CARD_CACHE_LAST_RESET, 1),
        },
        "card_gen": _card_gen_stats(),
        "errors": errors,
        "ts": now,
    }
