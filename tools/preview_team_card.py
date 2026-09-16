# -*- coding: utf-8 -*-
"""編成カードのモックプレビュー。

実データ（Enka API）なしで team_image の描画パイプラインを呼び、
レイアウト確認用の PNG を出力する。

    python tools/preview_team_card.py [出力先.png]

_get_card_data_sync を差し替えているので、team_image の描画コード本体は
一切変更せずに現状の描画結果をそのまま確認できる。
"""
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

# ----------------------------------------------------------------
# モックデータ
# ----------------------------------------------------------------
W = "static/assets/weapons"
A = "static/assets/artifacts"
P = "static/assets/props"
S = "static/assets/splash"

STATS_HUTAO = [
    {"label": "HP", "val": "33,524", "base": "15,545", "add": "+17,979", "icon": f"{P}/hp.png"},
    {"label": "攻撃力", "val": "2,845", "base": "715", "add": "+2,130", "icon": f"{P}/atk.png"},
    {"label": "防御力", "val": "743", "base": "493", "add": "+250", "icon": f"{P}/def.png"},
    {"label": "元素熟知", "val": "105", "icon": f"{P}/em.png"},
    {"label": "会心率", "val": "71.5%", "icon": f"{P}/rate.webp"},
    {"label": "会心ダメージ", "val": "234.2%", "icon": f"{P}/dmg.webp"},
    {"label": "チャージ効率", "val": "104.6%", "icon": f"{P}/er.png"},
    {"label": "炎ダメバフ", "val": "94.4%", "icon": f"{P}/pyro.png"},
]
STATS_NEUV = [
    {"label": "HP", "val": "54,381", "base": "18,620", "add": "+35,761", "icon": f"{P}/hp.png"},
    {"label": "攻撃力", "val": "1,102", "base": "995", "add": "+107", "icon": f"{P}/atk.png"},
    {"label": "防御力", "val": "797", "base": "611", "add": "+186", "icon": f"{P}/def.png"},
    {"label": "元素熟知", "val": "0", "icon": f"{P}/em.png"},
    {"label": "会心率", "val": "44.1%", "icon": f"{P}/rate.webp"},
    {"label": "会心ダメージ", "val": "178.4%", "icon": f"{P}/dmg.webp"},
    {"label": "チャージ効率", "val": "115.8%", "icon": f"{P}/er.png"},
    {"label": "水ダメバフ", "val": "138.8%", "icon": f"{P}/hydro.png"},
]
STATS_KAZUHA = [
    {"label": "HP", "val": "14,987", "base": "13,388", "add": "+1,599", "icon": f"{P}/hp.png"},
    {"label": "攻撃力", "val": "1,284", "base": "297", "add": "+987", "icon": f"{P}/atk.png"},
    {"label": "防御力", "val": "928", "base": "771", "add": "+157", "icon": f"{P}/def.png"},
    {"label": "元素熟知", "val": "982", "icon": f"{P}/em.png"},
    {"label": "会心率", "val": "26.0%", "icon": f"{P}/rate.webp"},
    {"label": "会心ダメージ", "val": "94.0%", "icon": f"{P}/dmg.webp"},
    {"label": "チャージ効率", "val": "172.6%", "icon": f"{P}/er.png"},
    {"label": "風ダメバフ", "val": "5.0%", "icon": f"{P}/anemo.png"},
]
STATS_FURINA = [
    {"label": "HP", "val": "47,662", "base": "17,320", "add": "+30,342", "icon": f"{P}/hp.png"},
    {"label": "攻撃力", "val": "1,214", "base": "1,082", "add": "+132", "icon": f"{P}/atk.png"},
    {"label": "防御力", "val": "714", "base": "644", "add": "+70", "icon": f"{P}/def.png"},
    {"label": "元素熟知", "val": "68", "icon": f"{P}/em.png"},
    {"label": "会心率", "val": "62.2%", "icon": f"{P}/rate.webp"},
    {"label": "会心ダメージ", "val": "202.4%", "icon": f"{P}/dmg.webp"},
    {"label": "チャージ効率", "val": "148.2%", "icon": f"{P}/er.png"},
    {"label": "水ダメバフ", "val": "114.2%", "icon": f"{P}/hydro.png"},
]


def art(slot, set_id, piece, upgrade, main_name, main_val, score, tier, subs):
    return {
        "slot": slot,
        "set": str(set_id),
        "name": "-",
        "upgrade": upgrade,
        "icon": f"{A}/UI_RelicIcon_{set_id}_{piece}.webp",
        "main": {"name": main_name, "value": main_val},
        "substats": subs,
        "score": score,
        "tier": tier,
    }


def sub(icon, name, val, rolls=None):
    # rolls 未指定時は値から決定的に擬似ロール品質(0..3)を生成してドットを検証可能に
    if rolls is None:
        h = sum(ord(c) for c in str(name) + str(val))
        n = 2 + (h % 4)  # 2..5 ロール
        rolls = [(h // (i + 1)) % 4 for i in range(n)]
    return {"name": name, "value": val, "icon": f"{P}/{icon}", "rolls": rolls}


ARTS_HUTAO = [
    art("生の花", 15010, 1, 4, "HP", "4,780", 0.0, "B", [sub("hp_per.png", "HP%", "5.8%"), sub("atk_per.png", "攻撃%", "14.0%"), sub("rate.webp", "会心率", "10.5%"), sub("dmg.webp", "会心ダメ", "27.2%")]),
    art("死の羽", 15010, 2, 4, "攻撃力", "311", 52.3, "SS", [sub("rate.webp", "会心率", "11.6%"), sub("dmg.webp", "会心ダメ", "32.1%"), sub("em.png", "元素熟知", "23"), sub("atk_per.png", "攻撃%", "5.8%")]),
    art("時の砂", 15010, 5, 4, "攻撃%", "46.6%", 44.6, "S", [sub("rate.webp", "会心率", "9.7%"), sub("dmg.webp", "会心ダメ", "24.9%"), sub("hp_per.png", "HP%", "4.1%"), sub("em.png", "元素熟知", "21")]),
    art("空の杯", 15030, 4, 4, "炎元素ダメージ", "92.2%", 47.2, "S", [sub("rate.webp", "会心率", "10.1%"), sub("dmg.webp", "会心ダメ", "28.8%"), sub("atk.png", "攻撃力", "18"), sub("atk_per.png", "攻撃%", "5.3%")]),
    art("理の冠", 15030, 1, 4, "会心ダメージ", "62.2%", 45.9, "S", [sub("rate.webp", "会心率", "11.1%"), sub("dmg.webp", "会心ダメ", "14.8%"), sub("atk_per.png", "攻撃%", "13.9%"), sub("em.png", "元素熟知", "19")]),
]
ARTS_NEUV = [
    art("生の花", 15020, 1, 4, "HP", "4,780", 0.0, "B", [sub("hp_per.png", "HP%", "14.6%"), sub("em.png", "元素熟知", "42"), sub("er.png", "チャージ", "5.8%"), sub("rate.webp", "会心率", "6.2%")]),
    art("死の羽", 15020, 2, 4, "攻撃力", "311", 38.4, "A", [sub("hp_per.png", "HP%", "16.3%"), sub("dmg.webp", "会心ダメ", "19.4%"), sub("rate.webp", "会心率", "8.9%"), sub("er.png", "チャージ", "6.5%")]),
    art("時の砂", 15020, 5, 4, "HP%", "46.6%", 49.8, "S", [sub("rate.webp", "会心率", "9.4%"), sub("dmg.webp", "会心ダメ", "26.2%"), sub("hp.png", "HP", "299"), sub("em.png", "元素熟知", "35")]),
    art("空の杯", 15020, 4, 4, "水元素ダメージ", "92.2%", 51.2, "SS", [sub("rate.webp", "会心率", "10.9%"), sub("dmg.webp", "会心ダメ", "29.8%"), sub("hp_per.png", "HP%", "5.8%"), sub("er.png", "チャージ", "11.7%")]),
    art("理の冠", 15020, 1, 4, "会心率", "38.4%", 43.1, "S", [sub("dmg.webp", "会心ダメ", "34.2%"), sub("hp_per.png", "HP%", "13.9%"), sub("er.png", "チャージ", "8.1%"), sub("hp.png", "HP", "269")]),
]
ARTS_KAZUHA = [
    art("生の花", 15005, 1, 4, "HP", "4,780", 0.0, "B", [sub("em.png", "元素熟知", "37"), sub("er.png", "チャージ", "12.4%"), sub("atk_per.png", "攻撃%", "4.7%"), sub("hp.png", "HP", "209")]),
    art("死の羽", 15005, 2, 4, "攻撃力", "311", 0.0, "B", [sub("em.png", "元素熟知", "40"), sub("er.png", "チャージ", "10.2%"), sub("rate.webp", "会心率", "7.8%"), sub("atk.png", "攻撃力", "19")]),
    art("時の砂", 15005, 5, 4, "元素熟知", "186", 0.0, "B", [sub("er.png", "チャージ", "15.2%"), sub("em.png", "元素熟知", "31"), sub("rate.webp", "会心率", "6.8%"), sub("dmg.webp", "会心ダメ", "12.4%")]),
    art("空の杯", 15005, 4, 4, "元素熟知", "85", 0.0, "B", [sub("em.png", "元素熟知", "28"), sub("er.png", "チャージ", "9.4%"), sub("rate.webp", "会心率", "8.4%"), sub("hp_per.png", "HP%", "4.1%")]),
    art("理の冠", 15005, 1, 4, "元素熟知", "46", 0.0, "B", [sub("em.png", "元素熟知", "21"), sub("er.png", "チャージ", "8.2%"), sub("dmg.webp", "会心ダメ", "18.7%"), sub("atk.png", "攻撃力", "14")]),
]
ARTS_FURINA = [
    art("生の花", 15025, 1, 4, "HP", "4,780", 0.0, "B", [sub("hp_per.png", "HP%", "10.2%"), sub("rate.webp", "会心率", "8.9%"), sub("dmg.webp", "会心ダメ", "21.4%"), sub("er.png", "チャージ", "6.2%")]),
    art("死の羽", 15025, 2, 4, "攻撃力", "311", 42.6, "A", [sub("rate.webp", "会心率", "10.4%"), sub("dmg.webp", "会心ダメ", "24.8%"), sub("hp_per.png", "HP%", "9.8%"), sub("em.png", "元素熟知", "24")]),
    art("時の砂", 15025, 5, 4, "HP%", "46.6%", 47.9, "S", [sub("rate.webp", "会心率", "9.9%"), sub("dmg.webp", "会心ダメ", "28.4%"), sub("hp_per.png", "HP%", "15.2%"), sub("er.png", "チャージ", "5.4%")]),
    art("空の杯", 15025, 4, 4, "HP%", "46.6%", 49.4, "S", [sub("dmg.webp", "会心ダメ", "31.2%"), sub("rate.webp", "会心率", "10.2%"), sub("hp_per.png", "HP%", "8.4%"), sub("em.png", "元素熟知", "18")]),
    art("理の冠", 15025, 1, 4, "会心ダメージ", "62.2%", 51.8, "SS", [sub("rate.webp", "会心率", "12.2%"), sub("dmg.webp", "会心ダメ", "16.4%"), sub("hp_per.png", "HP%", "11.4%"), sub("atk_per.png", "攻撃%", "4.9%")]),
]

MOCK_CHARS = [
    {
        "displayName": "アルレッキーノ", "element": "Pyro", "level": 90, "friendship": 10,
        "constellation": 1, "splash": f"{S}/UI_Gacha_AvatarImg_Arlecchino.webp", "costumeId": None,
        "weaponName": "赤月の形", "weaponIcon": f"{W}/UI_EquipIcon_Polearm_Crimson.webp",
        "weaponLevel": 90, "weaponAffix": 1,
        "mainStats": STATS_HUTAO, "artifacts": ARTS_HUTAO,
        "setBonuses": [
            {"name": "黒曜の秘典", "count": 4, "icon": f"{A}/UI_RelicIcon_15010_4.webp", "id": "15010", "buff": "FIGHT_PROP_NORMAL_ATTACK_ADD_HURT"},
            {"name": "炎の砂", "count": 2, "icon": f"{A}/UI_RelicIcon_15030_5.webp", "id": "15030", "buff": ""},
        ],
        "scoreSum": 240.0, "tierSum": "SS", "calcMethodLabel": "会心のみ", "id": "10000105",
    },
    {
        "displayName": "ヌヴィレット", "element": "Hydro", "level": 90, "friendship": 10,
        "constellation": 0, "splash": f"{S}/UI_Gacha_AvatarImg_Neuvillette.webp", "costumeId": None,
        "weaponName": "碧落の 琉璃", "weaponIcon": f"{W}/UI_EquipIcon_Catalyst_Suzaku.webp",
        "weaponLevel": 90, "weaponAffix": 1,
        "mainStats": STATS_NEUV, "artifacts": ARTS_NEUV,
        "setBonuses": [
            {"name": "沈眠の黄金", "count": 4, "icon": f"{A}/UI_RelicIcon_15020_4.webp", "id": "15020", "buff": "FIGHT_PROP_HEAL_ADD"},
        ],
        "scoreSum": 224.9, "tierSum": "SS", "calcMethodLabel": "HP%", "id": "10000089",
    },
    {
        "displayName": "楓原万葉", "element": "Anemo", "level": 90, "friendship": 8,
        "constellation": 2, "splash": f"{S}/UI_Gacha_AvatarImg_Kazuha.webp", "costumeId": None,
        "weaponName": "鉄蜂の刺殻", "weaponIcon": f"{W}/UI_EquipIcon_Sword_Teppei.webp",
        "weaponLevel": 90, "weaponAffix": 5,
        "mainStats": STATS_KAZUHA, "artifacts": ARTS_KAZUHA,
        "setBonuses": [
            {"name": "翠緑の影", "count": 4, "icon": f"{A}/UI_RelicIcon_15005_4.webp", "id": "15005", "buff": "FIGHT_PROP_SUB_HURT"},
        ],
        "scoreSum": 0.0, "tierSum": "B", "calcMethodLabel": "元素熟知", "id": "10000047",
    },
    {
        "displayName": "フリーナ", "element": "Hydro", "level": 90, "friendship": 10,
        "constellation": 3, "splash": f"{S}/UI_Gacha_AvatarImg_Furina.webp", "costumeId": None,
        "weaponName": "静水流転の輝", "weaponIcon": f"{W}/UI_EquipIcon_Sword_Suisyo.webp",
        "weaponLevel": 90, "weaponAffix": 1,
        "mainStats": STATS_FURINA, "artifacts": ARTS_FURINA,
        "setBonuses": [
            {"name": "黄金劇場の夢", "count": 4, "icon": f"{A}/UI_RelicIcon_15025_4.webp", "id": "15025", "buff": "FIGHT_PROP_CHARGE_EFFICIENCY"},
        ],
        "scoreSum": 243.1, "tierSum": "SS", "calcMethodLabel": "HP%", "id": "10000102",
    },
]


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BASE_DIR, "logs", "preview_team_card.png")
    import app.card.team_image as ti

    real_fetch = ti._get_card_data_sync

    def fake_fetch(uid, cid, **kwargs):
        for d in MOCK_CHARS:
            if str(d["id"]) == str(cid):
                return dict(d)
        return dict(real_fetch(uid, cid, **kwargs)) if False else dict(MOCK_CHARS[0])

    ti._get_card_data_sync = fake_fetch

    boss = {
        "version": "5.7",
        "name": "「水の幻形」テオカリスライン",
        "img": "static/assets/leyline/5269001/UI_MonsterIcon_DragonCollar.webp",
    }
    payload = ti._generate_team_image_sync(
        "181225664",
        [str(c["id"]) for c in MOCK_CHARS],
        configs=[{} for _ in MOCK_CHARS],
        boss=boss,
        beta="false",
        lang="ja",
    )
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "wb") as f:
        f.write(payload)
    print(f"saved: {out} ({len(payload)} bytes)")


if __name__ == "__main__":
    main()
