# -*- coding: utf-8 -*-
"""育成モード用のパネルデータ計算。

カード全体を等方縮小して右側に追加するパネルに表示する内容を組み立てる:
- 装備中の聖遺物セット効果（2セット / 4セット desc_ja）
- スコア計算方式が攻撃力/HP/防御力のとき: そのステータスの1%値（基礎ステ基準）
- 同方式のとき: 指定ステータス（calc_method）のサブステ伸び平均を固定の基準値で表示
  （% と実数の両方、% の隣には実数換算値を括弧で付与）
crit / em / charge 方式のときは下2項目は None（描画側で行を非表示）。
card_data API と画像生成（image.py）の両方から使い回す。
"""
import os
import json

from app.paths import BASE_DIR
from app.card.special import resolve_list_path
from app.card.set_buffs import set_buff_label as _set_buff_label


def _load_artifact_list(beta: str) -> dict:
    """live/beta の artifacts list（set2_desc_ja / set4_desc_ja を含む）を返す。"""
    possible = [
        "static/data/lists/artifacts.json",
        os.path.join(BASE_DIR, "static", "data", "lists", "artifacts.json"),
    ]
    for raw_path in possible:
        path = resolve_list_path(raw_path, beta)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (UnicodeDecodeError, json.JSONDecodeError):
                try:
                    with open(path, "r", encoding="cp932") as f:
                        return json.load(f)
                except Exception:
                    pass
    return {}


# calc_method → 表示名
_METHOD_STAT_LABEL = {
    "atk": "攻撃力", "hp": "HP", "def": "防御力",
    "em": "元素熟知", "charge": "チャージ効率", "crit": "会心",
}

_FIXED_SUBAVG = {
    "hp": {"flat": 253.94, "pct": 4.96},
    "atk": {"flat": 16.54, "pct": 4.96},
    "def": {"flat": 19.68, "pct": 6.2},
}


def _get_set_desc_ja(set_id: str, beta: str) -> tuple:
    """指定セットIDの set2_desc_ja / set4_desc_ja を返す（無ければ空文字）。"""
    entry = _load_artifact_list(beta).get(str(set_id)) or {}
    return str(entry.get("set2_desc_ja") or ""), str(entry.get("set4_desc_ja") or "")


def build_growth_panel(calc_method: str, base_hp=0.0, base_atk=0.0, base_def=0.0,
                       weapon_affix=None, weapon_refinement=None, raw_artifacts=None,
                       set_bonuses=None, beta: str = "false") -> dict:
    """育成モードパネル用のデータを組み立てる。

    - base_hp / base_atk / base_def: 基礎ステ（atk は武器基礎攻撃を含めた値）。
    - weapon_refinement: weapons/{id}.json の refinement（{rank: {name, desc}}）。
    - raw_artifacts: equipList の聖遺物 items（サブステ平均用）。
    - set_bonuses: 既に計算済みの set_bonuses リスト [{id, name, count, ...}]。
    """
    calc_method = calc_method or "crit"
    label = _METHOD_STAT_LABEL.get(calc_method, "")

    # 1) 武器の凸
    affix = weapon_affix or 1
    affix = max(1, min(5, int(affix)))
    refine_name = ""
    refine_desc = ""
    if isinstance(weapon_refinement, dict):
        rank = weapon_refinement.get(str(affix)) or {}
        refine_name = str(rank.get("name") or "")
        refine_desc = str(rank.get("desc") or "")

    # 2) 装備中セットのセット効果
    sets = []
    for sb in set_bonuses or []:
        sid = str(sb.get("id", ""))
        if not sid or sid == "0":
            continue
        s2, s4 = _get_set_desc_ja(sid, beta)
        if not s2 and not s4:
            continue
        sets.append({
            "id": sid,
            "name": str(sb.get("name") or f"セット {sid}"),
            "count": sb.get("count", 2),
            "set2": s2,
            "set4": s4,
            # 2セット効果がステータス反映（apply_2set_buffs）されているか
            "buff_applied": bool(sb.get("buff") or _set_buff_label(sid)),
        })

    # 3) スコア方式が攻撃/HP/防御のときだけ 1%値 を表示。
    #      crit / em / charge 方式では None（描画側で行を非表示）。
    stat1pct = None
    if calc_method in ("atk", "hp", "def") and label:
        base_val = {"atk": base_atk, "hp": base_hp, "def": base_def}.get(calc_method, 0.0)
        one_pct = base_val * 0.01
        stat1pct = {
            "label": label,
            "value": round(one_pct, 1),
            "unit": "",
        }

    # 4) サブステ伸び平均（固定の基準値）。
    #      % 行には対象キャラの基礎ステから「実数換算」値を括弧で付与する。
    #      表示するのは指定ステータス（calc_method）の1件のみ。
    subavg = None
    if calc_method in _FIXED_SUBAVG and label:
        fixed = _FIXED_SUBAVG[calc_method]
        base_val = {"atk": base_atk, "hp": base_hp, "def": base_def}.get(calc_method, 0.0)
        subavg = {
            "key": calc_method,
            "label": label,
            "flat_avg": fixed["flat"],
            "pct_avg": fixed["pct"],
            "flat_equiv": round(base_val * 0.01 * fixed["pct"], 1),
            "flat_count": None,
            "pct_count": None,
        }
    subavg_all = [subavg] if subavg else []

    return {
        "affix": affix,
        "refine_name": refine_name,
        "refine_desc": refine_desc,
        "sets": sets,
        "stat1pct": stat1pct,
        "subavg": subavg,
        "subavg_all": subavg_all,
    }


def build_growth_from_fake(calc_method, base_hp, base_atk, base_def,
                           weapon_affix, weapon_jsondata, raw_artifacts,
                           set_bonuses, beta="false") -> dict:
    """fake分岐（base値が既に計算済み）用のラッパー。

    atk の1%値は base_atk（キャラ基礎攻撃）+ weapon_base_atk を足した値の1%とする。
    weapon_base_atk は weapons json の stats_modifier.atk。
    """
    weapon_base_atk = 0.0
    if isinstance(weapon_jsondata, dict):
        try:
            weapon_base_atk = float((weapon_jsondata.get("stats_modifier") or {}).get("atk", 0.0))
        except (TypeError, ValueError):
            weapon_base_atk = 0.0
    return build_growth_panel(
        calc_method,
        base_hp=base_hp,
        base_atk=(base_atk or 0.0) + weapon_base_atk,
        base_def=base_def,
        weapon_affix=weapon_affix,
        weapon_refinement=(weapon_jsondata or {}).get("refinement") if isinstance(weapon_jsondata, dict) else None,
        raw_artifacts=raw_artifacts,
        set_bonuses=set_bonuses,
        beta=beta,
    )