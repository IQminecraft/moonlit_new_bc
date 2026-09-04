# -*- coding: utf-8 -*-
"""カード画像内の固定ラベル辞書（PIL描画用の ja/en 切替）。

画像生成モジュール（image.py / theme_cards.py / team_image.py）から使う。
キーは日本語ラベル、値に "en" を持たない項目は言語不問（例: "HP", "Lv."）。
"""
LABELS_EN = {
    "伸び値": "Growth",
    "スコア": "Score",
    "総合スコア": "Total Score",
    "計算方法": "Method",
    "未装備": "Not equipped",
    "未知の武器": "Unknown Weapon",
    "未知の聖遺物": "Unknown Artifact",
    "セット効果なし": "No Set Bonuses",
    "キャラ": "Char",
    "幽境": "Abyss",
    "立ち絵": "Character",
    "ステータス": "Stats",
    "武器": "Weapon",
    "聖遺物": "Artifacts",
    "育成メモ": "Growth Notes",
    "聖遺物セット効果": "Artifact Set Bonuses",
    "2セット:": "2-Set:",
    "4セット:": "4-Set:",
    "基礎ステータス %換算": "Base Stats (%)",
    "1%あたり": "per 1%",
    "サブステ伸び平均": "Avg Substat Rolls",
    "1回あたりの平均": "Avg per roll",
    "実数": " (flat)",
    "攻撃": "ATK",
    "防御": "DEF",
}


def img_t(text: str, lang: str = "ja") -> str:
    """固定ラベルを指定言語へ解決する（en 以外は原文のまま）。"""
    if lang != "en":
        return text
    return LABELS_EN.get(str(text), str(text))
