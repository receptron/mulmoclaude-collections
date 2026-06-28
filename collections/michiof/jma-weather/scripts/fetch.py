#!/usr/bin/env python3
"""
気象庁の公開 JSON API から全国主要都市の天気予報を取得し、
data/jma-weather/items/<office>-<YYYY-MM-DD>.json として **1 都市 1 日 1 レコード** で保存する。

LLM を使わずに動作する純粋なスクリプト。
スケジューラからは `python3 data/jma-weather/fetch.py` で起動する想定。

毎回の取得で 1 都市あたり ~9 レコード × 11 都市 = 70-99 レコードを書き出す:
  - 今日 (source=today): 短期予報 + overview + 時間帯別降水確率 + 風 + 波
  - 明日 (source=tomorrow): 短期予報 + 時間帯別降水確率 + 風 + 波
  - 3 日後〜9 日後 (source=weekly): 週間予報

短期予報と週間予報で日付が被ったときは **短期予報が勝つ** (情報が多いため)。
過去日付のレコードは削除されない (履歴として残る)。

引数 (環境変数で上書き可):
  --only <office>  単一都市だけ取得 (default: 全 11 都市)
  --out-dir / --raw-dir

終了コード: 0 = 成功, 1 = 全都市失敗 (1 都市でも成功すれば 0)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
WEEKDAY_JA = ["月", "火", "水", "木", "金", "土", "日"]

# 全国主要 11 都市 (10 候補 + 松山)。テレビ天気予報の標準的なラインアップ + ユーザー追加都市。
# - office: 気象庁の予報区 (5 〜 6 桁の数字、先頭ゼロあり)
# - area_code: 短期予報の細分区域 (主要市を含む区)
# - temp_code: 週間予報用の気温観測地点 (5 桁、気象官署コード=47xxx 系)
# - amedas_code: AMeDAS 観測点コード (今日の min/max/降水量に使用、気象官署コードと別体系)
#   amedastable.json から各都市の "kjName" 一致で引いた値。東京・横浜は temp_code と同じ。
#   warning_areas: 警報・注意報の集約対象エリア (class20 = 市町村単位のみ)
#     方針: その市の住民視点で警報を見せたいので、class10 (神奈川県東部 / 東京地方 等) は使わず、
#     class20 (市単位) だけを集約する。広域 class10 で発表された警報は JMA が下位 class20 に
#     継承して発表するので、市域の class20 さえ見ていれば取りこぼさない。
#     (例: 東部全体に波浪警報が出ていても、横浜市は波浪注意報レベルになっているケースは、
#      class20 で正しく "横浜市の波浪注意報" として出るので、それを採用するのが市民視点で正しい)
#     fetch_warnings は class20Items のみ走査し、ここに列挙された areaCode のすべての警報を
#     code ごとに最新 status で集約する。
# 東京 23 区の class20 コード (千代田 1310100 〜 江戸川 1312300、parent=130011/130012)
TOKYO_23WARDS = [
    "1310100", "1310200", "1310300", "1310400", "1310500",  # 千代田・中央・港・新宿・文京
    "1310600", "1310700", "1310800",                          # 台東・墨田・江東
    "1310900", "1311000", "1311100", "1311200", "1311300",    # 品川・目黒・大田・世田谷・渋谷
    "1311400", "1311500", "1311600", "1311700",               # 中野・杉並・豊島・北
    "1311800", "1311900", "1312000",                          # 荒川・板橋・練馬
    "1312100", "1312200", "1312300",                          # 足立・葛飾・江戸川
]
# フォールバック用の組込みロスター。実行時に items/config.json があればそちらで上書きする
# (CITIES_CONFIG / DEFAULT_CITY_OFFICE は main() で load_cities_config() の結果に置き換わる)。
_BUILTIN_CITIES = [
    {"office": "016000", "name": "札幌",   "region": "北海道",   "area_code": "016010", "warning_areas": ["0110000"], "temp_code": "47412", "amedas_code": "14163", "order": 1},
    {"office": "040000", "name": "仙台",   "region": "宮城県",   "area_code": "040010", "warning_areas": ["0410001", "0410002"], "temp_code": "47590", "amedas_code": "34392", "order": 2},
    {"office": "150000", "name": "新潟",   "region": "新潟県",   "area_code": "150010", "warning_areas": ["1510000"], "temp_code": "47604", "amedas_code": "54232", "order": 3},
    # 東京は 23 区まとめての class20 が無いので 23 区全部を列挙
    {"office": "130000", "name": "東京",   "region": "東京都",   "area_code": "130010", "warning_areas": TOKYO_23WARDS, "temp_code": "44132", "amedas_code": "44132", "order": 4},
    {"office": "140000", "name": "横浜",   "region": "神奈川県", "area_code": "140010", "warning_areas": ["1410011", "1410012"], "temp_code": "46106", "amedas_code": "46106", "order": 5},
    {"office": "230000", "name": "名古屋", "region": "愛知県",   "area_code": "230010", "warning_areas": ["2310000"], "temp_code": "47636", "amedas_code": "51106", "order": 6},
    {"office": "270000", "name": "大阪",   "region": "大阪府",   "area_code": "270000", "warning_areas": ["2710000"], "temp_code": "47772", "amedas_code": "62078", "order": 7},
    {"office": "320000", "name": "松江",   "region": "島根県",   "area_code": "320010", "warning_areas": ["3220100"], "temp_code": "47741", "amedas_code": "68132", "order": 8},
    {"office": "370000", "name": "高松",   "region": "香川県",   "area_code": "370000", "warning_areas": ["3720100"], "temp_code": "47891", "amedas_code": "72086", "order": 9},
    {"office": "400000", "name": "福岡",   "region": "福岡県",   "area_code": "400010", "warning_areas": ["4013000"], "temp_code": "47807", "amedas_code": "82182", "order": 10},
    {"office": "471000", "name": "那覇",   "region": "沖縄本島", "area_code": "471010", "warning_areas": ["4720100"], "temp_code": "47936", "amedas_code": "91197", "order": 11},
]
_BUILTIN_DEFAULT_CITY_OFFICE = "140000"

# 実行時に main() で config.json から上書きされるグローバル (無ければ組込みを使う)。
CITIES_CONFIG = _BUILTIN_CITIES
DEFAULT_CITY_OFFICE = _BUILTIN_DEFAULT_CITY_OFFICE


def load_cities_config(out_dir: Path) -> "tuple[list, str]":
    """items/config.json (source=config レコード) を読み、(CITIES_CONFIG 相当, 既定office) を返す。

    ユーザー設定 (どの都市・既定都市) を共有コードから隔離するための config 層。
    config.json が無い / 壊れている場合は組込みロスターにフォールバックする。

    config の city 形:
      { office, name, region, order, codes:{area, temp, amedas, warning:[...]}, map?:{...} }
    を fetch.py 内部形 {office, name, region, area_code, warning_areas, temp_code, amedas_code, order}
    に変換する。
    """
    path = out_dir / "config.json"
    if not path.exists():
        return _BUILTIN_CITIES, _BUILTIN_DEFAULT_CITY_OFFICE
    try:
        with open(path, encoding="utf-8") as f:
            cfg = json.load(f)
        cities = []
        for c in cfg.get("cities", []):
            codes = c.get("codes", {})
            cities.append({
                "office": c["office"],
                "name": c.get("name", ""),
                "region": c.get("region", ""),
                "area_code": codes.get("area", ""),
                "warning_areas": codes.get("warning", []),
                "temp_code": codes.get("temp", ""),
                "amedas_code": codes.get("amedas", ""),
                "order": c.get("order", 99),
            })
        if not cities:
            return _BUILTIN_CITIES, _BUILTIN_DEFAULT_CITY_OFFICE
        cities.sort(key=lambda x: x.get("order", 99))
        default_office = cfg.get("defaultCity") or cities[0]["office"]
        return cities, default_office
    except Exception as e:
        print(f"[warn] config.json 読み込み失敗、組込みロスターを使用: {e}", file=sys.stderr)
        return _BUILTIN_CITIES, _BUILTIN_DEFAULT_CITY_OFFICE

# JMA 天気コード → 短い和文
WEATHER_CODE_MAP = {
    "100": "晴れ", "101": "晴れ時々曇り", "102": "晴れ一時雨", "103": "晴れ時々雨",
    "104": "晴れ一時雪", "105": "晴れ時々雪", "106": "晴れ一時雨か雪", "107": "晴れ時々雨か雪",
    "108": "晴れ一時雨か雷雨", "110": "晴れ後時々曇り", "111": "晴れ後曇り", "112": "晴れ後一時雨",
    "113": "晴れ後時々雨", "114": "晴れ後雨", "115": "晴れ後一時雪", "116": "晴れ後時々雪",
    "117": "晴れ後雪", "118": "晴れ後雨か雪", "119": "晴れ後雨か雷雨", "120": "晴れ朝夕一時雨",
    "121": "晴れ朝の内一時雨", "122": "晴れ夕方一時雨", "123": "晴れ山沿い雷雨", "124": "晴れ山沿い雪",
    "125": "晴れ昼頃から雷雨", "126": "晴れ昼頃から雨", "127": "晴れ夕方から雨", "128": "晴れ夜は雨",
    "130": "朝の内霧後晴れ", "131": "晴れ明け方霧", "132": "晴れ朝夕曇り",
    "140": "晴れ時々雨で雷を伴う", "160": "晴れ一時雪か雨", "170": "晴れ時々雪か雨", "181": "晴れ後雪か雨",
    "200": "曇り", "201": "曇り時々晴れ", "202": "曇り一時雨", "203": "曇り時々雨",
    "204": "曇り一時雪", "205": "曇り時々雪", "206": "曇り一時雨か雪", "207": "曇り時々雨か雪",
    "208": "曇り一時雨か雷雨", "209": "霧", "210": "曇り後時々晴れ", "211": "曇り後晴れ",
    "212": "曇り後一時雨", "213": "曇り後時々雨", "214": "曇り後雨", "215": "曇り後一時雪",
    "216": "曇り後時々雪", "217": "曇り後雪", "218": "曇り後雨か雪", "219": "曇り後雨か雷雨",
    "220": "曇り朝夕一時雨", "221": "曇り朝の内一時雨", "222": "曇り夕方一時雨", "223": "曇り日中時々晴れ",
    "224": "曇り昼頃から雨", "225": "曇り夕方から雨", "226": "曇り夜は雨", "228": "曇り昼頃から雪",
    "229": "曇り夕方から雪", "230": "曇り夜は雪", "231": "曇り海上海岸は霧か霧雨",
    "240": "曇り時々雨で雷を伴う", "250": "曇り時々雪で雷を伴う", "260": "曇り一時雪か雨",
    "270": "曇り時々雪か雨", "281": "曇り後雪か雨",
    "300": "雨", "301": "雨時々晴れ", "302": "雨時々止む", "303": "雨時々雪", "304": "雨か雪",
    "306": "大雨", "308": "雨で暴風を伴う", "309": "雨一時雪", "311": "雨後晴れ", "313": "雨後曇り",
    "314": "雨後時々雪", "315": "雨後雪", "316": "雨か雪後晴れ", "317": "雨か雪後曇り",
    "320": "朝の内雨後晴れ", "321": "朝の内雨後曇り", "322": "雨朝晩一時雪", "323": "雨昼頃から晴れ",
    "324": "雨夕方から晴れ", "325": "雨夜は晴れ", "326": "雨夕方から雪", "327": "雨夜は雪",
    "328": "雨一時強く降る", "329": "雨一時みぞれ", "340": "雪か雨", "350": "雨で雷を伴う",
    "361": "雪か雨後晴れ", "371": "雪か雨後曇り",
    "400": "雪", "401": "雪時々晴れ", "402": "雪時々止む", "403": "雪時々雨", "405": "大雪",
    "406": "風雪強い", "407": "暴風雪", "409": "雪一時雨", "411": "雪後晴れ", "413": "雪後曇り",
    "414": "雪後雨", "420": "朝の内雪後晴れ", "421": "朝の内雪後曇り", "422": "雪昼頃から雨",
    "423": "雪夕方から雨", "425": "雪一時強く降る", "426": "雪後みぞれ", "427": "雪一時みぞれ",
    "450": "雪で雷を伴う",
}


# JMA 警報・注意報コード → (和文名, レベル)
# レベル: "special" (特別警報) / "warning" (警報) / "advisory" (注意報)
# コード対応は bosai/warning/data/r8/ の実データから裏取り。03/07/14/15/16/20/29 は
# headline と additions で確認済み、他は出現実例が少なく JMA 標準仕様からの推定。
# code 29 (土砂災害警戒情報) は同一 code 内で status により警戒度が変動するので、
# 実際の (name, level) 解決は resolve_warning() で行う。ここの level は発表/継続時のデフォルト。
WARNING_CODE_MAP: dict[str, tuple[str, str]] = {
    "02": ("暴風警報",     "warning"),
    "03": ("大雨警報",     "warning"),
    "04": ("洪水警報",     "warning"),
    "05": ("暴風雪警報",   "warning"),
    "06": ("大雪警報",     "warning"),
    "07": ("波浪警報",     "warning"),
    "08": ("高潮警報",     "warning"),
    "10": ("大雨注意報",   "advisory"),
    "12": ("大雨特別警報", "special"),
    "14": ("雷注意報",     "advisory"),
    "15": ("強風注意報",   "advisory"),
    "16": ("波浪注意報",   "advisory"),
    "17": ("大雪注意報",   "advisory"),
    "18": ("着雪注意報",   "advisory"),
    "19": ("融雪注意報",   "advisory"),
    "20": ("濃霧注意報",   "advisory"),
    "21": ("高潮注意報",   "advisory"),
    "22": ("低温注意報",   "advisory"),
    "23": ("霜注意報",     "advisory"),
    "24": ("乾燥注意報",   "advisory"),
    "25": ("洪水注意報",   "advisory"),
    "26": ("なだれ注意報", "advisory"),
    "27": ("その他の注意報","advisory"),
    "29": ("土砂災害警戒情報", "warning"),
    "32": ("暴風雪特別警報","special"),
    "33": ("大雨特別警報",  "special"),
    "35": ("大雪特別警報",  "special"),
    "36": ("暴風特別警報",  "special"),
    "37": ("波浪特別警報",  "special"),
    "38": ("高潮特別警報",  "special"),
}


# 警報の現在状態を表す可能性のある status と意味:
#   "発表"                — 新規発表
#   "継続"                — 継続発表中
#   "解除"                — 解除済み (active 集合から除外)
#   "発表警報・注意報はなし"  — 現在の発表は無し (active 集合から除外)
#   "警報から注意報"        — 警報レベルから注意報レベルに緩和した結果、現在は注意報レベル
#                              code 側が既に "注意報" 系の番号 (例: code 16 = 波浪注意報) なので
#                              現在状態の表示名は code 通りで OK。降格の事実だけ補助表示。
#   "危険警報から注意報"     — 土砂災害警戒情報 (code 29) が注意報レベルに緩和した状態。
#                              code 29 は同一 code 内で警戒度が変動するので、resolve_warning で
#                              "土砂災害(注意レベル)/advisory" として扱う。
#   "危険警報から警報"      — 土砂災害警戒情報 (code 29) が警報レベルに緩和した状態。
#                              "大雨警報(土砂災害相当)/warning" として扱う。

# active から除外する status (= 現在発表中ではない / 該当無し)
INACTIVE_STATUSES = ("解除", "発表警報・注意報はなし", "")


def resolve_warning(code: str, status: str) -> tuple[str, str, str]:
    """code + status の組み合わせから (表示名, レベル, 補助ラベル) を返す。

    通常の code は WARNING_CODE_MAP の (name, level) をそのまま返し、status は補助ラベルへ。
    code 29 (土砂災害警戒情報) は同一 code 内で警戒度レベルが変動するので status を見て分岐:
      - 発表/継続 → 土砂災害警戒情報 (warning)
      - 危険警報から注意報 → 土砂災害(注意レベル) (advisory)
      - 危険警報から警報 → 大雨警報(土砂災害相当) (warning)

    返り値の補助ラベル (status_label) は UI 側で「直前まで警報レベルだった」等の補足表示用。
    INACTIVE な status (解除など) が来た場合は ("", "", "") を返す (active 集合に含めない合図)。
    """
    if status in INACTIVE_STATUSES:
        return ("", "", "")

    # 土砂災害警戒情報 (キキクル) の特殊扱い
    if code == "29":
        if status in ("発表", "継続"):
            return ("土砂災害警戒情報", "warning", status)
        if status == "危険警報から注意報":
            # 警戒情報レベル (レベル4) から注意報レベル (レベル2) に緩和
            return ("土砂災害(注意レベル)", "advisory", "警戒情報→注意報")
        if status == "危険警報から警報":
            # 警戒情報レベルから警報レベル (レベル3) に緩和
            return ("大雨警報(土砂災害相当)", "warning", "警戒情報→警報")
        # 想定外の status はとりあえず警戒情報のままで補助ラベルに status を出す
        return ("土砂災害警戒情報", "warning", status)

    # 通常 code: WARNING_CODE_MAP の (name, level) をそのまま返す
    name, level = WARNING_CODE_MAP.get(code, (f"コード:{code}", "advisory"))
    # "警報から注意報" のような降格 status はそのまま補助ラベルに残す (UI 側で区別表示可能)
    return (name, level, status)


def weather_code_to_text(code: str) -> str:
    return WEATHER_CODE_MAP.get(code, f"コード:{code}") if code else ""


def weather_text_to_icon(text: str) -> str:
    """日本語天気テキストから絵文字を返す共通関数。

    forecast (`weatherCode` 経由) と wdist (`weather` 直接) の両方で使う。
    判定優先順位:
      1. 雷 → ⛈️
      2. 先頭文字 (晴/曇/雨/雪) で主タイプ判定 → 補助天気で派生
      3. 先頭判定が効かない短いテキスト (wdist の "雨または雪" 等) は部分一致で fallback
    """
    if not text:
        return ""
    if "雷" in text:
        return "⛈️"
    has_rain = "雨" in text
    has_snow = "雪" in text
    has_cloud = "曇" in text or "くもり" in text

    if text.startswith("晴"):
        if has_rain or has_snow:
            return "🌦️"
        if has_cloud:
            return "⛅"
        return "☀️"
    if text.startswith("曇") or text.startswith("くもり"):
        if has_rain:
            return "🌧️"
        if has_snow:
            return "🌨️"
        return "☁️"
    if text.startswith("雨"):
        return "🌨️" if has_snow else "🌧️"
    if text.startswith("雪") or text.startswith("みぞれ"):
        return "❄️"

    # fallback: 短いテキスト (wdist の "雨または雪" 等) で先頭判定が効かないケース
    if has_rain and has_snow:
        return "🌨️"
    if has_rain:
        return "🌧️"
    if has_snow:
        return "❄️"
    if "晴" in text and has_cloud:
        return "⛅"
    if "晴" in text:
        return "☀️"
    if has_cloud:
        return "☁️"
    return ""


def weather_code_to_icon(code: str) -> str:
    """JMA 天気コードから絵文字を返す (weather_text_to_icon の薄いラッパー)。"""
    if not code:
        return ""
    return weather_text_to_icon(WEATHER_CODE_MAP.get(code, ""))


# HTTP 取得 + 書き込みは共通モジュール jma_http に集約 (L3 効率化: 条件付きGET + 変更時のみ書込)。
# fetch_json/fetch_bytes は条件付きGET (304 でキャッシュ本文を返す)、
# write_record_if_changed は updatedAt 以外が同一なら書かない。
from jma_http import http_get, fetch_json, fetch_bytes, write_record_if_changed  # noqa: E402


def fetch_overview_text(office: str) -> str:
    try:
        data = fetch_json(f"https://www.jma.go.jp/bosai/forecast/data/overview_forecast/{office}.json")
        if isinstance(data, dict):
            return str(data.get("text", "")).strip()
    except Exception as e:
        print(f"[warn] overview fetch failed for {office}: {e}", file=sys.stderr)
    return ""


def find_area(timeseries_areas: list, prefer_area_code: str | None) -> dict:
    if prefer_area_code:
        for a in timeseries_areas:
            if a.get("area", {}).get("code") == prefer_area_code:
                return a
    return timeseries_areas[0]


def day_label(date_iso: str, today_iso: str) -> str:
    try:
        d = datetime.strptime(date_iso, "%Y-%m-%d").date()
        t = datetime.strptime(today_iso, "%Y-%m-%d").date()
        diff = (d - t).days
        if diff == 0:
            return "今日"
        if diff == 1:
            return "明日"
        if diff == 2:
            return "明後日"
        if diff > 0:
            return f"{diff}日後"
        if diff == -1:
            return "昨日"
        return f"{abs(diff)}日前"
    except Exception:
        return ""


def weekday_label(date_iso: str) -> str:
    try:
        d = datetime.strptime(date_iso, "%Y-%m-%d").date()
        return WEEKDAY_JA[d.weekday()]
    except Exception:
        return ""


def parse_short_term(short_term: dict, prefer_area_code: str | None) -> dict[str, dict]:
    days: dict[str, dict] = {}
    if not short_term.get("timeSeries"):
        return days

    ts0 = short_term["timeSeries"][0]
    a0 = find_area(ts0["areas"], prefer_area_code)
    codes = a0.get("weatherCodes", [])
    weathers = a0.get("weathers", [])
    winds = a0.get("winds", [])
    waves = a0.get("waves", [])
    for i, t in enumerate(ts0["timeDefines"]):
        d = t[:10]
        rec = days.setdefault(d, {})
        code = codes[i] if i < len(codes) else ""
        text = (weathers[i] if i < len(weathers) else "") or weather_code_to_text(code)
        rec["weatherCode"] = code
        rec["weather"] = text.replace("　", " ").strip()
        rec["weatherIcon"] = weather_code_to_icon(code)
        if i < len(winds):
            rec["wind"] = winds[i].replace("　", " ").strip()
        if i < len(waves):
            rec["wave"] = waves[i].replace("　", " ").strip()

    if len(short_term["timeSeries"]) > 1:
        ts1 = short_term["timeSeries"][1]
        a1 = find_area(ts1["areas"], prefer_area_code)
        pops = a1.get("pops", [])
        bands_by_date: dict[str, list[str]] = {}
        for t, p in zip(ts1["timeDefines"], pops):
            dt = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(JST)
            band = f"{dt.hour:02d}-{(dt.hour + 6) % 24:02d}: {p}%"
            d = dt.strftime("%Y-%m-%d")
            bands_by_date.setdefault(d, []).append(band)
        for d, bands in bands_by_date.items():
            rec = days.setdefault(d, {})
            rec["popsByBand"] = " / ".join(bands)
            try:
                vals = [int(b.rsplit(": ", 1)[1].rstrip("%")) for b in bands]
                if vals:
                    rec["pop"] = str(max(vals))
            except Exception:
                pass

    if len(short_term["timeSeries"]) > 2:
        ts2 = short_term["timeSeries"][2]
        a2 = find_area(ts2["areas"], prefer_area_code)
        temps = a2.get("temps", [])
        # JMA は同日の temps を [09:00, 00:00, ...] の順で返すことがあり (17時発表で今日分が観測値の場合)、
        # 配列順では min/max を判定できない。値そのものを比較して min/max を取る。
        by_date: dict[str, list[str]] = {}
        for t, v in zip(ts2["timeDefines"], temps):
            if v not in (None, ""):
                by_date.setdefault(t[:10], []).append(v)
        for d, vals in by_date.items():
            rec = days.setdefault(d, {})
            try:
                nums = sorted(float(v) for v in vals)
                if len(nums) >= 2:
                    # 整数なら ".0" を落として元の文字列形式に近づける
                    rec["min"] = str(int(nums[0])) if nums[0].is_integer() else str(nums[0])
                    rec["max"] = str(int(nums[-1])) if nums[-1].is_integer() else str(nums[-1])
                elif len(nums) == 1:
                    rec["max"] = str(int(nums[0])) if nums[0].is_integer() else str(nums[0])
            except (ValueError, TypeError):
                # 数値変換できないときは元の値の最初をそのまま使う
                if len(vals) >= 1:
                    rec["max"] = vals[-1]

    return days


def parse_weekly(weekly: dict, prefer_area_code: str | None, prefer_temp_area_code: str | None) -> dict[str, dict]:
    days: dict[str, dict] = {}
    if not weekly.get("timeSeries"):
        return days

    ts0 = weekly["timeSeries"][0]
    a0 = find_area(ts0["areas"], prefer_area_code)
    codes = a0.get("weatherCodes", [])
    pops = a0.get("pops", [])
    reliabilities = a0.get("reliabilities", [])
    for i, t in enumerate(ts0["timeDefines"]):
        d = t[:10]
        rec = days.setdefault(d, {})
        code = codes[i] if i < len(codes) else ""
        rec["weatherCode"] = code
        rec["weather"] = weather_code_to_text(code)
        rec["weatherIcon"] = weather_code_to_icon(code)
        if i < len(pops):
            rec["pop"] = pops[i]
        if i < len(reliabilities) and reliabilities[i]:
            rec["reliability"] = reliabilities[i]

    if len(weekly["timeSeries"]) > 1:
        ts1 = weekly["timeSeries"][1]
        a1 = find_area(ts1["areas"], prefer_temp_area_code)
        times = ts1["timeDefines"]
        tmin = a1.get("tempsMin", [])
        tmax = a1.get("tempsMax", [])
        for i, t in enumerate(times):
            d = t[:10]
            rec = days.setdefault(d, {})
            if i < len(tmin) and tmin[i]:
                rec["min"] = tmin[i]
            if i < len(tmax) and tmax[i]:
                rec["max"] = tmax[i]

    return days


def merge_records(short_term_days: dict[str, dict], weekly_days: dict[str, dict]) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for d, w in weekly_days.items():
        merged[d] = dict(w)
    for d, s in short_term_days.items():
        if d in merged:
            base = dict(merged[d])
            base.update({k: v for k, v in s.items() if v not in (None, "")})
            merged[d] = base
        else:
            merged[d] = dict(s)
    return merged


def classify_source(date_iso: str, today_iso: str, in_short_term: bool) -> str:
    if in_short_term:
        try:
            d = datetime.strptime(date_iso, "%Y-%m-%d").date()
            t = datetime.strptime(today_iso, "%Y-%m-%d").date()
            diff = (d - t).days
            if diff == 0:
                return "today"
            if diff == 1:
                return "tomorrow"
        except Exception:
            pass
    return "weekly"


def fetch_wdist(area_code: str) -> dict[str, list[dict]]:
    """地域時系列予報 (3 時間ごと) を取得して {date: [hourly entries]} を返す。

    エンドポイント: https://www.jma.go.jp/bosai/jmatile/data/wdist/VPFD/<area_code>.json
    返却値: 日付 (YYYY-MM-DD) を key とし、その日の 3 時間ごとエントリの list。
    各エントリ: { time, datetime, weather, weatherIcon, temp, windDir, windSpeed }
    """
    by_date: dict[str, list[dict]] = {}
    try:
        data = fetch_json(f"https://www.jma.go.jp/bosai/jmatile/data/wdist/VPFD/{area_code}.json")
    except Exception as e:
        print(f"[warn] wdist fetch failed for {area_code}: {e}", file=sys.stderr)
        return by_date

    ats = data.get("areaTimeSeries", {})
    pts = data.get("pointTimeSeries", {})
    times = [t["dateTime"] for t in ats.get("timeDefines", [])]
    weathers = ats.get("weather", [])
    winds = ats.get("wind", [])
    temps = pts.get("temperature", [])

    for i, t in enumerate(times):
        try:
            dt = datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(JST)
        except Exception:
            continue
        d = dt.strftime("%Y-%m-%d")
        wtext = weathers[i] if i < len(weathers) else ""
        w = winds[i] if i < len(winds) else {}
        temp_val = temps[i] if i < len(temps) else ""
        entry = {
            "time": dt.strftime("%H:%M"),
            "datetime": t,
            "weather": wtext,
            "weatherIcon": weather_text_to_icon(wtext),
            "temp": str(temp_val) if temp_val != "" and temp_val is not None else "",
            "windDir": w.get("direction", "") if isinstance(w, dict) else "",
            "windSpeed": w.get("speed", "") if isinstance(w, dict) else "",
        }
        by_date.setdefault(d, []).append(entry)
    return by_date


def fetch_amedas_map() -> "tuple[dict, str | None]":
    """全 AMeDAS 観測所の最新観測を 1 リクエストで取得する。

    旧実装は都市ごとに latest_time + point×8 ブロック (= 全11都市で約99 req) を引いていたが、
    実況ボックスが使うのは現在気温と直近降水量だけなので、全観測所を 1 本で返す
    map エンドポイントに置き換える (latest_time×1 + map×1 = 2 req)。

    返り値: (観測所コード -> 観測 dict, 最新観測時刻 ISO)。失敗時は ({}, None)。
    エンドポイント:
      - 最新観測時刻: https://www.jma.go.jp/bosai/amedas/data/latest_time.txt
      - 全観測所一括: https://www.jma.go.jp/bosai/amedas/data/map/<YYYYMMDDHHMMSS>.json
    """
    try:
        # jma_http 経由 (certifi フォールバック付き) で取得。ホストでも SSL 検証が通る。
        latest_str = http_get("https://www.jma.go.jp/bosai/amedas/data/latest_time.txt", 10).decode().strip()
        latest_dt = datetime.fromisoformat(latest_str)
    except Exception as e:
        print(f"[warn] amedas latest_time fetch failed: {e}", file=sys.stderr)
        return {}, None
    stamp = latest_dt.strftime("%Y%m%d%H%M%S")  # JST。map のファイル名と一致
    try:
        data = fetch_json(f"https://www.jma.go.jp/bosai/amedas/data/map/{stamp}.json")
    except Exception as e:
        print(f"[warn] amedas map fetch failed: {e}", file=sys.stderr)
        return {}, None
    if not isinstance(data, dict):
        return {}, None
    return data, latest_dt.isoformat()


def amedas_stats_from_map(amedas_map: dict, latest_iso: "str | None", amedas_code: str) -> dict:
    """map スナップショットから 1 地点の現在気温と直近降水量 (1h/3h/24h, mm) を取り出す。

    map の各観測所値は [value, qcFlag] 形式。precipitation6h は AMeDAS のネイティブ
    フィールドに無いため 1h/3h/24h を採用する (6h は旧実装が時系列から合成していた)。

    フィールド: currentTemp / precip1h / precip3h / precip24h / precipAsOf。
    注: min/max (今日の最低/最高) は AMeDAS では返さない。予報側の予想値を使う。
    """
    result: dict = {}
    if not amedas_map or not amedas_code:
        return result
    point = amedas_map.get(amedas_code)
    if not isinstance(point, dict):
        return result

    def val(key):
        v = point.get(key)
        if isinstance(v, list) and v and v[0] is not None:
            return v[0]
        return None

    t = val("temp")
    if t is not None:
        try:
            f = float(t)
            result["currentTemp"] = str(int(f)) if f.is_integer() else f"{f:.1f}"
        except (ValueError, TypeError):
            pass
    for field, key in (("precip1h", "precipitation1h"),
                       ("precip3h", "precipitation3h"),
                       ("precip24h", "precipitation24h")):
        p = val(key)
        if p is not None:
            try:
                result[field] = f"{float(p):.1f}"
            except (ValueError, TypeError):
                pass
    if latest_iso:
        result["precipAsOf"] = latest_iso
    return result


def build_summary(rec: dict) -> str:
    parts = []
    parts.append(f"{rec.get('cityName', '')} {rec.get('dayLabel', '')} ({rec.get('weekday', '')})".strip())
    if rec.get("weatherIcon") or rec.get("weather"):
        parts.append(f"{rec.get('weatherIcon', '')} {rec.get('weather', '')}".strip())
    if rec.get("min") or rec.get("max"):
        parts.append(f"{rec.get('min', '?')}/{rec.get('max', '?')}℃")
    if rec.get("pop"):
        parts.append(f"降水 {rec['pop']}%")
    return " | ".join(parts)


def demote_past_today_records(out_dir: Path, office: str, today_iso: str, now_iso: str) -> int:
    """fetch 開始時に呼ぶ。items/ 内の過去日 (date < today_iso) かつ
    source が `today` / `tomorrow` のレコードを `past` に書き換える。

    `classify_source()` は fetch 時の today_iso を元に source を付けるため、
    日付が進むと過去日のレコードに古い `today` / `tomorrow` ラベルが残ってしまう。
    ビュー側は `source === "today"` で find するので、複数の today レコードが
    あると **古い (空欄混じりの) 方** を拾ってしまう (日付昇順ソートのため)。
    これを防ぐため、過去日になった瞬間にラベルだけ rewrite する。

    weather / min / max / precip* などのデータ自体は消さない (観測値の履歴)。
    """
    rewritten = 0
    for fp in out_dir.glob(f"{office}-*.json"):
        try:
            rec = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        rec_date = rec.get("date")
        if not rec_date or rec_date >= today_iso:
            continue
        if rec.get("source") not in ("today", "tomorrow"):
            continue
        rec["source"] = "past"
        rec["dayLabel"] = day_label(rec_date, today_iso)
        rec["updatedAt"] = now_iso
        write_record_if_changed(fp, rec)
        rewritten += 1
    return rewritten


def fetch_city(city: dict, out_dir: Path, now_iso: str,
               amedas_map: dict | None = None, amedas_iso: str | None = None) -> tuple[int, str | None, str]:
    """1 都市分を取得して書き出す。

    amedas_map / amedas_iso: 全観測所の最新観測 (fetch_amedas_map の結果) を都市間で使い回す。

    Returns: (written_count, today_iso, today_summary)
    """
    try:
        forecast = fetch_json(f"https://www.jma.go.jp/bosai/forecast/data/forecast/{city['office']}.json")
    except Exception as e:
        print(f"[error] forecast fetch failed for {city['name']} ({city['office']}): {e}", file=sys.stderr)
        return (0, None, "")
    if not isinstance(forecast, list) or len(forecast) < 2:
        print(f"[error] unexpected forecast shape for {city['name']}", file=sys.stderr)
        return (0, None, "")

    overview = fetch_overview_text(city["office"])
    short_section, weekly_section = forecast[0], forecast[1]

    short_days = parse_short_term(short_section, city["area_code"])
    weekly_days = parse_weekly(weekly_section, city["area_code"], city["temp_code"])
    merged = merge_records(short_days, weekly_days)

    # 3 時間ごと予報 (地域時系列予報)
    hourly_by_date = fetch_wdist(city["area_code"])

    short_dates = sorted(short_days.keys())
    today_iso = short_dates[0] if short_dates else datetime.now(JST).strftime("%Y-%m-%d")

    # 過去日になった today/tomorrow レコードのラベルを past に書き換える
    # (ビューが複数の today レコードから古い空欄版を拾うのを防ぐ)
    demote_past_today_records(out_dir, city["office"], today_iso, now_iso)

    # 今日の AMeDAS 観測値 (currentTemp/precip1h/3h/24h/precipAsOf) を map スナップショットから抽出
    # AMeDAS の point id は気象官署コード (temp_code) と別体系なので amedas_code を使う
    amedas_today: dict = {}
    if city.get("amedas_code"):
        amedas_today = amedas_stats_from_map(amedas_map or {}, amedas_iso, city["amedas_code"])

    today_summary = ""
    for d in sorted(merged.keys()):
        body = merged[d]
        rec_id = f"{city['office']}-{d}"
        out_path = out_dir / f"{rec_id}.json"

        # 既存レコードを読んでフォールバック用に保持
        # (5 時発表で取れた今日の min/max を 17 時発表後も保持するため)
        existing: dict = {}
        if out_path.exists():
            try:
                existing = json.loads(out_path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}

        # 新値が空欄なら既存値を保持するヘルパ
        def keep(field: str, new_val):
            if new_val not in (None, ""):
                return new_val
            return existing.get(field, "")

        rec = {
            "id": rec_id,
            "office": city["office"],
            "cityName": city["name"],
            "regionName": city["region"],
            "cityOrder": city["order"],
            "date": d,
            "weekday": weekday_label(d),
            "dayLabel": day_label(d, today_iso),
            "source": classify_source(d, today_iso, d in short_days),
            "areaCode": city["area_code"],
            "areaName": f"{city['region']} {city['name']}",
            "publishingOffice": short_section.get("publishingOffice", ""),
            "reportDatetime": short_section.get("reportDatetime", ""),
            "updatedAt": now_iso,
            "weather": keep("weather", body.get("weather", "")),
            "weatherCode": keep("weatherCode", body.get("weatherCode", "")),
            "weatherIcon": keep("weatherIcon", body.get("weatherIcon", "")),
            "min": keep("min", body.get("min", "")),
            "max": keep("max", body.get("max", "")),
            "pop": keep("pop", body.get("pop", "")),
            "popsByBand": keep("popsByBand", body.get("popsByBand", "")),
            "wind": keep("wind", body.get("wind", "")),
            "wave": keep("wave", body.get("wave", "")),
            "reliability": keep("reliability", body.get("reliability", "")),
            "overviewText": overview if d == today_iso else "",
            "hourly": hourly_by_date.get(d, []) or existing.get("hourly", []),
            "currentTemp": "",
            "precip1h": "",
            "precip6h": "",
            "precip24h": "",
            "precipAsOf": "",
        }
        # today レコードのみ AMeDAS 観測値を埋め込み
        # - currentTemp: 最新観測時刻の気温 (現在気温の観測値)
        # - precip1h/3h/24h: map スナップショットの累積降水量。明日以降は仕様上空欄
        # - min/max は AMeDAS から取らない: 予報値を見せる方が「今日これから何度になるか」が
        #   分かって実用的 (テレビ天気予報的)
        if d == today_iso and amedas_today:
            for k in ("currentTemp", "precip1h", "precip3h", "precip24h", "precipAsOf"):
                v = amedas_today.get(k)
                if v not in (None, ""):
                    rec[k] = v
        rec["summary"] = build_summary(rec)
        write_record_if_changed(out_path, rec)
        if d == today_iso:
            today_summary = rec["summary"]

    return (len(merged), today_iso, today_summary)


def cleanup_old_records(out_dir: Path, today_iso: str, keep_days: int) -> int:
    """today から keep_days より古い items/ レコードを削除する。

    ファイル名は `<office>-<YYYY-MM-DD>.json` 形式。末尾の YYYY-MM-DD を抽出して判定する
    (office に "-" は含まれないが、念のため末尾 3 セグメントで取る)。

    返り値: 削除件数
    """
    try:
        cutoff = datetime.strptime(today_iso, "%Y-%m-%d").date() - timedelta(days=keep_days)
    except ValueError:
        return 0
    if not out_dir.exists():
        return 0

    deleted = 0
    for f in out_dir.glob("*.json"):
        parts = f.stem.rsplit("-", 3)
        if len(parts) < 4:
            continue
        try:
            file_date = datetime.strptime("-".join(parts[-3:]), "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_date < cutoff:
            try:
                f.unlink()
                deleted += 1
            except Exception as e:
                print(f"[warn] failed to delete {f}: {e}", file=sys.stderr)
    return deleted


# ─────────────────────────────────────────────────────────────────────
# 台風データ取り込み (VPTW)
#
# JMA 防災情報 XML の高頻度・随時フィード extra.xml を polling し、
# 「台風解析・予報情報（５日予報）」(VPTW) エントリがあれば各台風の
# 最新 XML をフェッチして source="typhoon" レコードとして書き出す。
#
# 活動中の台風がない期間は extra.xml に VPTW が現れず、その間は
# typhoon-* レコードを新規生成しない。既存の typhoon-* レコードは
# extra.xml から消えた時点で archived=true を付ける（削除はしない）。
# ─────────────────────────────────────────────────────────────────────

JMA_EXTRA_FEED = "https://www.data.jma.go.jp/developer/xml/feed/extra.xml"
JMA_EXTRA_L_FEED = "https://www.data.jma.go.jp/developer/xml/feed/extra_l.xml"  # 履歴フィード (約24時間分)
ATOM_NS = "{http://www.w3.org/2005/Atom}"
VPTW_NS = {
    "j": "http://xml.kishou.go.jp/jmaxml1/body/meteorology1/",
    "eb": "http://xml.kishou.go.jp/jmaxml1/elementBasis1/",
    "h": "http://xml.kishou.go.jp/jmaxml1/informationBasis1/",
}
import re as _re
import xml.etree.ElementTree as _ET


def _parse_coord_str(s: str) -> tuple[float, float] | None:
    """+15.8+140.1/ → (15.8, 140.1)。+/- 付き 10 進緯度経度のみ扱う。"""
    if not s:
        return None
    m = _re.match(r"([+-][\d.]+)([+-][\d.]+)", s)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


def _get_int(el) -> int | None:
    if el is None or el.text is None:
        return None
    try:
        return int(float(el.text))
    except (TypeError, ValueError):
        return None


def _max_forecast_hours(xroot) -> int:
    """VPTW XML から MeteorologicalInfo/DateTime[@type] の最大予報時間 (h) を返す。

    JMA の VPTW60 は同じ系列番号の中で複数フォーマット (位置のみ / 短時間予報 /
    5日予報) が混在する。Serial の大小では判別できないので「DateTime[@type]
    が "予報　XX時間後" のうち XX の最大」を品質スコアとして使う。

    返り値の目安:
      0  …… 実況 + 推定1時間後 のみ (位置情報)
      9  …… 短時間予報 (3/6/9h)
      96 / 120 …… 5日予報
    """
    mx = 0
    for mi in xroot.findall(".//j:MeteorologicalInfo", VPTW_NS):
        dt_el = mi.find("j:DateTime", VPTW_NS)
        if dt_el is None:
            continue
        t = dt_el.get("type") or ""
        if "予報" not in t:
            continue
        m = _re.search(r"(\d+)", t)
        if m:
            try:
                mx = max(mx, int(m.group(1)))
            except ValueError:
                pass
    return mx


def _collect_vptw_urls(feed_url: str, timeout: int) -> list[dict]:
    """1つの ATOM フィードから VPTW エントリ {entry_id, url, updated} を抜く。"""
    try:
        raw = fetch_bytes(feed_url, timeout=timeout)
    except Exception as e:
        print(f"[typhoon] failed to fetch {feed_url}: {e}", file=sys.stderr)
        return []
    try:
        root = _ET.fromstring(raw)
    except _ET.ParseError as e:
        print(f"[typhoon] {feed_url} parse error: {e}", file=sys.stderr)
        return []

    out: list[dict] = []
    for entry in root.findall(f"{ATOM_NS}entry"):
        eid_el = entry.find(f"{ATOM_NS}id")
        eid = eid_el.text if eid_el is not None else ""
        # VPTW のエントリ id は ".../<timestamp>_<n>_VPTW<2 digits>_010000.xml" 形式
        if not eid or "VPTW" not in eid:
            continue
        link = entry.find(f"{ATOM_NS}link")
        href = link.get("href") if link is not None else None
        upd_el = entry.find(f"{ATOM_NS}updated")
        updated = upd_el.text if upd_el is not None else ""
        if not href:
            continue
        out.append({"entry_id": eid, "url": href, "updated": updated})
    return out


def _parse_vptw_meta(xroot) -> dict | None:
    """VPTW XML root から Head + TyphoonNamePart を抜き、meta dict を返す。
    無効なら None。
    """
    head = xroot.find(f".//{{http://xml.kishou.go.jp/jmaxml1/informationBasis1/}}Head")
    if head is None:
        return None
    event_id = head.findtext("h:EventID", namespaces=VPTW_NS) or ""
    serial = head.findtext("h:Serial", namespaces=VPTW_NS) or "0"
    report_dt = head.findtext("h:ReportDateTime", namespaces=VPTW_NS) or ""
    info_kind = head.findtext("h:InfoKind", namespaces=VPTW_NS) or ""
    if not event_id:
        return None

    name = xroot.find(".//j:TyphoonNamePart/j:Name", VPTW_NS)
    name_kana = xroot.find(".//j:TyphoonNamePart/j:NameKana", VPTW_NS)
    number = xroot.find(".//j:TyphoonNamePart/j:Number", VPTW_NS)
    name_text = name.text if name is not None else None
    name_kana_text = name_kana.text if name_kana is not None else None
    number_text = number.text if number is not None else None
    # number は yyNN 形式 (例 2607 = 2026 年 7 号)
    label = None
    if number_text and len(number_text) >= 4:
        try:
            n = int(number_text[2:])
            label = f"台風{n}号"
        except ValueError:
            pass

    try:
        serial_n = int(serial)
    except ValueError:
        serial_n = None

    return {
        "event_id": event_id,
        "serial": serial_n,
        "report_dt": report_dt,
        "info_kind": info_kind,
        "name": name_text,
        "name_kana": name_kana_text,
        "number": number_text,
        "label": label,
    }


def _parse_vptw_points(xroot) -> list[dict]:
    """VPTW XML root から MeteorologicalInfo を全部抜く (実況 / 推定 / 予報 混在)。"""
    points: list[dict] = []
    for mi in xroot.findall(".//j:MeteorologicalInfo", VPTW_NS):
        dt_el = mi.find("j:DateTime", VPTW_NS)
        dt_type = dt_el.get("type") if dt_el is not None else ""
        dt_text = dt_el.text if dt_el is not None else ""

        center = mi.find(".//j:CenterPart", VPTW_NS)
        if center is None:
            continue

        # 中心位置 (実況は eb:Coordinate、予報は ProbabilityCircle/eb:BasePoint)
        c = center.find('eb:Coordinate[@type="中心位置（度）"]', VPTW_NS)
        if c is None:
            c = center.find('.//eb:BasePoint[@type="中心位置（度）"]', VPTW_NS)
        coord = _parse_coord_str(c.text) if (c is not None and c.text) else None
        if coord is None:
            continue
        lat, lng = coord

        pressure = _get_int(center.find('eb:Pressure[@type="中心気圧"]', VPTW_NS))
        dir_el = center.find('eb:Direction[@type="移動方向"]', VPTW_NS)
        spd_el = center.find('eb:Speed[@type="移動速度"][@unit="km/h"]', VPTW_NS)

        # 予報円半径 (予報のみ、km)
        fc_r = None
        pc = center.find('j:ProbabilityCircle[@type="予報円"]', VPTW_NS)
        if pc is not None:
            r_el = pc.find('.//eb:Radius[@unit="km"]', VPTW_NS)
            fc_r = _get_int(r_el)

        wind_max = _get_int(mi.find('.//j:WindPart/eb:WindSpeed[@type="最大風速"][@unit="m/s"]', VPTW_NS))
        wind_gust = _get_int(mi.find('.//j:WindPart/eb:WindSpeed[@type="最大瞬間風速"][@unit="m/s"]', VPTW_NS))

        # 強風域 / 暴風域 (実況) / 暴風警戒域 (予報) の半径 km
        strong_km = storm_km = warn_km = None
        for wa in mi.findall(".//j:WarningAreaPart", VPTW_NS):
            t = wa.get("type", "")
            r_el = wa.find('.//eb:Radius[@unit="km"]', VPTW_NS)
            v = _get_int(r_el)
            if t == "強風域":
                strong_km = v
            elif t == "暴風域":
                storm_km = v
            elif t == "暴風警戒域":
                warn_km = v

        cls_el = mi.find('.//eb:TyphoonClass[@type="熱帯擾乱種類"]', VPTW_NS)
        cls_text = cls_el.text if cls_el is not None else None

        points.append({
            "dtType": dt_type,
            "dt": dt_text,
            "lat": lat,
            "lng": lng,
            "pressure_hpa": pressure,
            "cls": cls_text,
            "moveDir": dir_el.text if dir_el is not None else None,
            "moveKmh": _get_int(spd_el),
            "wind_max_ms": wind_max,
            "wind_gust_ms": wind_gust,
            "forecast_radius_km": fc_r,
            "strong_radius_km": strong_km,
            "storm_radius_km": storm_km,
            "storm_warning_radius_km": warn_km,
        })
    return points


def _assemble_typhoon_record(
    meta: dict,
    points: list[dict],
    forecast_meta: dict | None = None,
) -> dict | None:
    """meta + points から typhoon record を組み立てる。points が空なら None。"""
    if not points:
        return None
    now_pt = next((p for p in points if p["dtType"] == "実況"), points[0])
    last_pt = points[-1]
    summary_parts = [
        meta["label"] or f"台風 {meta['number']}",
        meta["name_kana"] or meta["name"] or "",
        f"{now_pt['pressure_hpa']}hPa" if now_pt.get("pressure_hpa") else "",
        f"中心{now_pt['lat']:.1f}N/{now_pt['lng']:.1f}E",
        f"→ {last_pt['dtType']}",
    ]
    summary = " ".join(s for s in summary_parts if s)

    rec = {
        "id": f"typhoon-{meta['event_id']}",
        "source": "typhoon",
        "summary": summary,
        "eventId": meta["event_id"],
        "typhoonNumber": meta["number"],
        "typhoonName": meta["name"],
        "typhoonNameJa": meta["name_kana"],
        "typhoonLabel": meta["label"],
        "forecastSerial": meta["serial"],
        "forecastPoints": points,
        "reportDatetime": meta["report_dt"],
        "publishingOffice": "気象庁",
        "areaName": meta["info_kind"],
        "archived": False,
    }
    # 予報のソース VPTW が観測ソースと別なら出典を記録 (デバッグ・透明性のため)
    if forecast_meta is not None:
        rec["forecastIssuedAt"] = forecast_meta.get("report_dt")
        rec["forecastIssuedSerial"] = forecast_meta.get("serial")
    return rec


def _detect_dissipation(obs_points_all: list[dict]) -> dict | None:
    """JMA 仕様 No.11902 に基づく終息判定 (一次情報の最終報シグナル)。

    https://www.data.jma.go.jp/suishin/shiyou/pdf/no11902 (令和4年3月30日 気象庁大気海洋部)

    仕様の (イ) 内容表 4 列目より:
      | 温帯低気圧化または熱帯低気圧化のとき | 実況 |
    つまり VPTW が「実況のみ (推定/予報なし)」かつ「実況点の熱帯擾乱種類が
    温帯低気圧または熱帯低気圧」であれば、それは JMA の最終報 (closing announcement)。
    この EventID については以後 VPTW は発番されない。

    返り値: {"kind": "温帯低気圧化" | "熱帯低気圧化", "cls": str} または None (まだ活動中)。

    注: 「実況のみ」だが cls が依然として 台風 (TS/STS/TY) の場合は本仕様の closing
    announcement ではないので None を返す (前提条件が崩れたら判定しない)。
    """
    if not obs_points_all:
        return None
    # 実況以外の点 (推定1時間後 / 予報) が含まれていたら最終報ではない
    if any(p.get("dtType") != "実況" for p in obs_points_all):
        return None
    obs = obs_points_all[0]
    cls = obs.get("cls") or ""
    if "温帯低気圧" in cls:
        return {"kind": "温帯低気圧化", "cls": cls}
    if "熱帯低気圧" in cls:
        return {"kind": "熱帯低気圧化", "cls": cls}
    return None


def parse_vptw(xml_bytes: bytes) -> dict | None:
    """単一 VPTW XML を 1 つ parse して typhoon record を返す (単一ソース版)。

    新しい設計では fetch_typhoons() は build_merged_typhoon_record() を使い、
    観測と予報を別 XML からマージする。この関数は単発の取り込み用に残してある。
    """
    try:
        xroot = _ET.fromstring(xml_bytes)
    except _ET.ParseError as e:
        print(f"[typhoon] vptw parse error: {e}", file=sys.stderr)
        return None
    meta = _parse_vptw_meta(xroot)
    if meta is None:
        return None
    points = _parse_vptw_points(xroot)
    return _assemble_typhoon_record(meta, points)


def collect_vptw_candidates_by_event(timeout: int = 15) -> dict[str, list[dict]]:
    """extra.xml + extra_l.xml から全 VPTW を取り、EventID ごとにメタ情報リストを返す。

    返り値: EventID → [{url, entry_id, report_dt, serial, max_forecast_h, xml_bytes}, ...]
    時系列順は保証しない。呼び元 (select_observation_and_forecast) でソートして選ぶ。

    JMA は 1 台風につき以下 2 種類の VPTW を混在発行する:
      - 完全版 (3 時間おき): 実況 + 予報 (24h or 5日)、max_forecast_h ≥ 24
      - 位置のみ版 (毎時の合間): 実況のみ、max_forecast_h = 0
    どちらも EventID は同じ。新しい設計では両方とも候補に残し、観測ソースと予報ソースを
    別々に選ぶことで実況時刻の鮮度を確保する。
    """
    # extra.xml + extra_l.xml の union
    candidate_urls = _collect_vptw_urls(JMA_EXTRA_FEED, timeout)
    seen_urls = {c["url"] for c in candidate_urls}
    for c in _collect_vptw_urls(JMA_EXTRA_L_FEED, timeout):
        if c["url"] in seen_urls:
            continue
        seen_urls.add(c["url"])
        candidate_urls.append(c)

    per_event: dict[str, list[dict]] = {}
    for c in candidate_urls:
        try:
            xml_bytes = fetch_bytes(c["url"], timeout=timeout)
            xroot = _ET.fromstring(xml_bytes)
        except Exception as e:
            print(f"[typhoon] failed to fetch/parse {c['url']}: {e}", file=sys.stderr)
            continue
        meta = _parse_vptw_meta(xroot)
        if meta is None:
            continue
        per_event.setdefault(meta["event_id"], []).append({
            "url": c["url"],
            "entry_id": c["entry_id"],
            "report_dt": meta["report_dt"],
            "serial": meta["serial"] or 0,
            "max_forecast_h": _max_forecast_hours(xroot),
            "xml_bytes": xml_bytes,
        })
    return per_event


def select_observation_and_forecast(candidates: list[dict]) -> tuple[dict, dict | None]:
    """1 EventID の候補リストから (観測ソース, 予報ソース) を選ぶ。

    - 観測ソース: report_dt が最新の VPTW (max_forecast_h は問わない)
    - 予報ソース: report_dt が最新の VPTW で max_forecast_h > 0 のもの (なければ None)

    観測ソースと予報ソースが同一 VPTW のこともある (完全版が最新のとき)。
    別の VPTW のときは observation が「最新の位置 (e.g. 21:00 JST)」、forecast が
    「3 時間前に発表された予報 (e.g. 18:00 JST 発表)」のように分かれる。
    """
    by_rdt = sorted(candidates, key=lambda c: (c["report_dt"], c["serial"]), reverse=True)
    observation = by_rdt[0]
    forecast = next((c for c in by_rdt if c["max_forecast_h"] > 0), None)
    return observation, forecast


def build_merged_typhoon_record(observation: dict, forecast: dict | None) -> dict | None:
    """観測ソースと予報ソースを 1 つの typhoon record にマージする。

    - meta (event_id, serial, reportDatetime 等) は観測ソースのもの
    - 実況 / 推定 points は観測ソースから (= JMA が最後に確認した位置)
    - 予報 points は予報ソースから、ただし観測ソースの 実況 dt より後のものだけ
      (実況より前の予報は意味がないので捨てる)
    - 予報ソースが観測ソースと違う VPTW なら、forecastIssuedAt / forecastIssuedSerial で出典を残す
    """
    try:
        obs_root = _ET.fromstring(observation["xml_bytes"])
    except _ET.ParseError as e:
        print(f"[typhoon] obs parse error: {e}", file=sys.stderr)
        return None
    obs_meta = _parse_vptw_meta(obs_root)
    if obs_meta is None:
        return None
    obs_points_all = _parse_vptw_points(obs_root)
    # 観測ソースからは 実況 / 推定 だけ採用 (古い 予報 は使わない)
    obs_points = [p for p in obs_points_all if "予報" not in p["dtType"]]

    # 観測ソースの 実況 dt を境にして、forecast points をフィルタする
    obs_now_dt = next((p["dt"] for p in obs_points if p["dtType"] == "実況"), "")

    forecast_points: list[dict] = []
    forecast_meta_payload: dict | None = None
    if forecast is not None:
        if forecast["url"] == observation["url"]:
            # 同一 VPTW: 既に parse 済みの points を流用
            forecast_points = [
                p for p in obs_points_all
                if "予報" in p["dtType"] and p["dt"] > obs_now_dt
            ]
            # 出典は観測と同じなので forecastIssuedAt は付けない
        else:
            try:
                fc_root = _ET.fromstring(forecast["xml_bytes"])
            except _ET.ParseError as e:
                print(f"[typhoon] forecast parse error: {e}", file=sys.stderr)
                fc_root = None
            if fc_root is not None:
                fc_meta = _parse_vptw_meta(fc_root)
                if fc_meta is not None:
                    fc_points = _parse_vptw_points(fc_root)
                    forecast_points = [
                        p for p in fc_points
                        if "予報" in p["dtType"] and p["dt"] > obs_now_dt
                    ]
                    forecast_meta_payload = {
                        "report_dt": fc_meta["report_dt"],
                        "serial": fc_meta["serial"],
                    }

    points = obs_points + forecast_points
    rec = _assemble_typhoon_record(obs_meta, points, forecast_meta_payload)
    if rec is None:
        return None

    # JMA 仕様 No.11902 に基づく終息判定 (一次情報)。観測ソースの points 全体を見て、
    # 「実況のみ + cls が温帯低気圧/熱帯低気圧」なら最終報。これが立っていると
    # この EventID については以後 VPTW は発番されない。
    dissipation = _detect_dissipation(obs_points_all)
    if dissipation is not None:
        rec["dissipated"] = True
        rec["dissipationKind"] = dissipation["kind"]
        rec["dissipationCls"] = dissipation["cls"]
        # 終息報自体の発表時刻 = reportDatetime と同じ (観測ソース = 最終報)
        rec["dissipationAnnouncedAt"] = obs_meta["report_dt"]
    return rec


def fetch_typhoons(out_dir: Path, now_iso: str) -> tuple[int, int, int]:
    """extra.xml + extra_l.xml から VPTW を引いて typhoon-* レコードを書く。

    新設計:
      - 各 EventID について観測ソースと予報ソースを別々に選び、1 レコードにマージ
      - 観測ソース (= 最新の VPTW) の reportDatetime が active window 以内のときだけ active
        - 通常: ACTIVE_WINDOW_HOURS (12h) — JMA が hourly 発番を続けている前提
        - 終息報を検知済み (dissipated=True): DISSIPATED_GRACE_HOURS (6h) —
          仕様 No.11902 では温帯/熱帯低気圧化のとき VPTW 発番が終わるため、
          12h 待たずに 6h で archived 化する (それ以後は鮮度ゼロのまま居座らせない)
      - active window から外れた既存 typhoon-* は archived=true

    返り値: (active 台風数, 書き出した件数, archived 化した件数)
    """
    try:
        per_event = collect_vptw_candidates_by_event()
    except Exception as e:
        print(f"[typhoon] unexpected error in candidate collection: {e}", file=sys.stderr)
        return (0, 0, 0)

    ACTIVE_WINDOW_HOURS = 12
    DISSIPATED_GRACE_HOURS = 6
    now_utc = datetime.now(timezone.utc)
    active_cutoff = now_utc - timedelta(hours=ACTIVE_WINDOW_HOURS)
    dissipated_cutoff = now_utc - timedelta(hours=DISSIPATED_GRACE_HOURS)

    written = 0
    active_event_ids: set[str] = set()
    for event_id, candidates in per_event.items():
        if not candidates:
            continue
        observation, forecast = select_observation_and_forecast(candidates)

        # 観測ソースの report_dt をまず parse (粗い 12h pre-filter のため)
        try:
            obs_rdt = datetime.fromisoformat(observation["report_dt"].replace("Z", "+00:00"))
            if obs_rdt.tzinfo is None:
                obs_rdt = obs_rdt.replace(tzinfo=timezone.utc)
        except (ValueError, AttributeError):
            print(f"[typhoon] {event_id}: bad report_dt {observation['report_dt']!r}", file=sys.stderr)
            continue
        if obs_rdt < active_cutoff:
            # 12h を超えて古いフィード履歴に残っているだけ。新規 active としては扱わない
            # (既存ファイルは下の archive ループで archived=true 化される)
            continue

        rec = build_merged_typhoon_record(observation, forecast)
        if rec is None:
            print(f"[typhoon] failed to build record for {event_id}", file=sys.stderr)
            continue

        # 終息報を検知済みかつ 6h 超過なら、archived=true で書き出す。
        # 仕様 No.11902 上、温帯/熱帯低気圧化のときの実況のみ電文を最後に VPTW の発番は止まる。
        # 12h 待たずに 6h で archived 化することで、鮮度ゼロの終息報が active 扱いで
        # 居座るのを防ぐ。fresh data (dissipationKind 等) は記録に残す。
        dissipated_stale = rec.get("dissipated") and obs_rdt < dissipated_cutoff
        if dissipated_stale:
            rec["archived"] = True

        rec["updatedAt"] = now_iso
        # active_event_ids は archive loop の対象外指定 (= この run でレコードを触った)。
        # archived=true で書いた場合も含めて、loop に二重処理させないよう add する。
        active_event_ids.add(event_id)
        path = out_dir / f"{rec['id']}.json"
        try:
            write_record_if_changed(path, rec, newline=False)
            written += 1
            fc_note = ""
            if forecast and forecast["url"] != observation["url"]:
                fc_note = (
                    f", forecast from serial={forecast['serial']} rdt={forecast['report_dt']}"
                )
            elif forecast is None:
                fc_note = ", no forecast (observation-only)"
            diss_note = ""
            if rec.get("dissipated"):
                diss_note = f", dissipated={rec.get('dissipationKind')}"
                if dissipated_stale:
                    diss_note += " (archived: >6h since closing announcement)"
            print(
                f"[typhoon] wrote {rec['id']} "
                f"(obs serial={observation['serial']} rdt={observation['report_dt']}{fc_note}{diss_note})",
                file=sys.stderr,
            )
        except OSError as e:
            print(f"[typhoon] failed to write {path}: {e}", file=sys.stderr)

    # active でなくなった既存 typhoon-* は archived=true
    archived = 0
    if out_dir.exists():
        for f in out_dir.glob("typhoon-*.json"):
            event_id = f.stem.split("typhoon-", 1)[-1]
            if event_id in active_event_ids:
                continue  # まだ活動中
            try:
                cur = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            if cur.get("archived") is True:
                continue  # 既に archived
            cur["archived"] = True
            cur["updatedAt"] = now_iso
            try:
                write_record_if_changed(f, cur, newline=False)
                archived += 1
                print(f"[typhoon] archived {f.stem} (no fresh observation in active window)", file=sys.stderr)
            except OSError as e:
                print(f"[typhoon] failed to archive {f}: {e}", file=sys.stderr)

    return (len(active_event_ids), written, archived)


def fetch_warnings(out_dir: Path, now_iso: str) -> tuple[int, int]:
    """各都市の警報・注意報を取得し、source="warning" のレコードを 1 都市 1 ファイルで書き出す。

    エンドポイント: https://www.jma.go.jp/bosai/warning/data/r8/<office>.json
      レスポンスはトップが **list で「単発の発表イベント」の集合**。時系列順とは限らない。
      現在のアクティブ警報は code ごとに reportDatetime 最大の status を採用して集約する
      (先頭 1 件だけ見ると別 code の active を取り逃がす)。
    構造:
      [{ reportDatetime, publishingOffice, headlineText,
         warning: { class10Items: [{ areaCode, kinds: [{code, status, additions?}] }],
                    class20Items: [...] } }, ...]
    集約対象は city.warning_areas (class20 = 市町村単位)。class10 は status の文脈解決にのみ参照。

    返り値: (書いた都市数, スキップした都市数)
    """
    written = 0
    skipped = 0
    for city in CITIES_CONFIG:
        office = city["office"]
        # 警報集約対象エリア (class10 + class20)。後方互換で warning_areas が無ければ area_code 単独。
        target_areas = set(city.get("warning_areas") or [city["area_code"]])
        out_path = out_dir / f"{office}-warning.json"
        try:
            data = fetch_json(f"https://www.jma.go.jp/bosai/warning/data/r8/{office}.json")
        except Exception as e:
            print(f"[warning] {office} {city['name']} fetch failed: {e}", file=sys.stderr)
            skipped += 1
            continue

        if not isinstance(data, list) or not data:
            print(f"[warning] {office} {city['name']} unexpected payload (not a non-empty list)", file=sys.stderr)
            skipped += 1
            continue

        # ── code ごとに最新の status を集約 ──
        # code_state[code] = {"status", "reportDatetime", "additions", "areaCode" (どこで出ているか)}
        # class10Items (一次細分区域) と class20Items (市町村単位) の両方を走査し、target_areas
        # に含まれる areaCode の全 kinds を集約する。
        #
        # ※重要な解釈ルール (JMA r8 の慣習):
        #   同一 entry 内で class10 の status が "X から Y" 系の降格表現になっている場合
        #   (例: "危険警報から注意報" / "警報から注意報" / "特別警報から警報" 等)、
        #   その変化はその class10 配下の全 class20 にも適用される。
        #   ところが class20 側の status は単に "継続" としか書かれないため、
        #   class20 の "継続" を素直に「警戒情報レベルが継続」と読むと誤解する。
        #   そのため、同一 entry の class10 が降格 status のとき、class20 の "継続" を
        #   class10 の status で上書き解釈する。
        # 親 class10 (= city['area_code']) も対象に含める。warning_areas が class20 のみで
        # 構成されていても、class10 の status は文脈解決のためだけに参照する。
        parent_class10 = city.get("area_code")

        def _is_downgrade(s: str) -> bool:
            return any(s.startswith(p) for p in ("危険警報から", "警報から", "特別警報から", "注意報から"))

        code_state: dict[str, dict] = {}
        for entry in data:
            rt = entry.get("reportDatetime", "") or ""
            warning_obj = entry.get("warning", {}) or {}

            # 先にこの entry の親 class10 (county / 県 単位) の status を集める (code -> status)
            # class20 の "継続" を文脈解決するために使う。
            class10_ctx: dict[str, str] = {}
            for it in warning_obj.get("class10Items", []) or []:
                if it.get("areaCode") != parent_class10:
                    continue
                for k in it.get("kinds", []) or []:
                    c = k.get("code") or ""
                    if c:
                        class10_ctx[c] = k.get("status") or ""

            for bucket_key in ("class10Items", "class20Items"):
                for it in warning_obj.get(bucket_key, []) or []:
                    area_code = it.get("areaCode")
                    if area_code not in target_areas:
                        continue
                    for k in it.get("kinds", []) or []:
                        code = k.get("code") or ""
                        if not code:
                            continue
                        status = k.get("status") or ""
                        # class20 の "継続" は、同 entry の親 class10 status が降格系
                        # ("X から Y") なら、その status で文脈解釈する。
                        if (
                            bucket_key == "class20Items"
                            and status == "継続"
                            and _is_downgrade(class10_ctx.get(code, ""))
                        ):
                            status = class10_ctx[code]
                        prev = code_state.get(code)
                        if prev is None or rt > prev["reportDatetime"]:
                            code_state[code] = {
                                "status": status,
                                "reportDatetime": rt,
                                "additions": k.get("additions") or [],
                                "areaCode": area_code,
                            }

        # アクティブ判定 — resolve_warning で code+status から (name, level, status_label) を取る。
        # 空文字 name のものは INACTIVE (解除等) のサイン。
        active: list[dict] = []
        for code, st in code_state.items():
            status = st["status"]
            name, level, status_label = resolve_warning(code, status)
            if not name:
                continue
            item = {
                "code": code,
                "name": name,
                "level": level,
                "status": status_label,
            }
            if st["additions"]:
                item["additions"] = st["additions"]
            active.append(item)

        # headline / reportDatetime / publishingOffice は全体で最も新しいエントリから取る
        # (時刻バラバラなのでソートが必要)
        sorted_data = sorted(data, key=lambda e: e.get("reportDatetime", "") or "", reverse=True)
        latest = sorted_data[0]

        rec = {
            "id": f"{office}-warning",
            "office": office,
            "cityName": city["name"],
            "regionName": city["region"],
            "cityOrder": city["order"],
            "source": "warning",
            "publishingOffice": latest.get("publishingOffice", ""),
            "reportDatetime": latest.get("reportDatetime", ""),
            "headlineText": latest.get("headlineText", "") or "",
            "warnings": active,
            "updatedAt": now_iso,
        }
        # ソート: special → warning → advisory、各レベル内はコード昇順
        level_order = {"special": 0, "warning": 1, "advisory": 2}
        rec["warnings"].sort(key=lambda w: (level_order.get(w["level"], 9), w["code"]))

        try:
            write_record_if_changed(out_path, rec)
            written += 1
        except OSError as e:
            print(f"[warning] {office} write failed: {e}", file=sys.stderr)
            skipped += 1

    return written, skipped


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--only", default=os.environ.get("JMA_ONLY"), help="単一都市の office code (default: 全 11 都市)")
    p.add_argument("--out-dir", default=os.environ.get("JMA_OUT_DIR", "data/jma-weather/items"))
    p.add_argument("--sleep", type=float, default=float(os.environ.get("JMA_SLEEP", "0.3")), help="都市間 sleep 秒 (rate-limit 配慮)")
    args = p.parse_args()

    workspace = Path(__file__).resolve().parents[2]
    out_dir = Path(args.out_dir) if Path(args.out_dir).is_absolute() else workspace / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # ユーザー設定 (ロスター・既定都市) を config.json から読み、グローバルを上書きする。
    # fetch_warnings() 等が module global CITIES_CONFIG を参照するため global で差し替える。
    global CITIES_CONFIG, DEFAULT_CITY_OFFICE
    CITIES_CONFIG, DEFAULT_CITY_OFFICE = load_cities_config(out_dir)

    targets = CITIES_CONFIG
    if args.only:
        targets = [c for c in CITIES_CONFIG if c["office"] == args.only]
        if not targets:
            print(f"[error] unknown office code: {args.only}", file=sys.stderr)
            return 1

    now_iso = datetime.now(JST).isoformat()
    # 全観測所の最新観測を 1 回だけ取得し、全都市で使い回す (旧: 都市ごと約9 req → 計2 req)
    amedas_map, amedas_iso = fetch_amedas_map()
    total_records = 0
    success_count = 0
    default_summary = ""
    for i, city in enumerate(targets):
        n, today, summary = fetch_city(city, out_dir, now_iso, amedas_map, amedas_iso)
        total_records += n
        if n > 0:
            success_count += 1
        if city["office"] == DEFAULT_CITY_OFFICE and summary:
            default_summary = summary
        if i < len(targets) - 1 and args.sleep > 0:
            time.sleep(args.sleep)

    if success_count == 0:
        print(f"[error] all {len(targets)} cities failed", file=sys.stderr)
        return 1

    # 古いレコードの自動削除 (3 日より古い items/ を削除)
    # 12 都市 × 3 日 = 36 件以下に保ち、コレクション全体を unselective limit (200) 未満に抑える。
    # 過去日のレコードはチャットでは review しないので、3 日あれば直近の振り返り用途に足りる。
    today_for_cleanup = datetime.now(JST).strftime("%Y-%m-%d")
    di = cleanup_old_records(out_dir, today_for_cleanup, keep_days=3)
    if di:
        print(f"[cleanup] deleted {di} items (>3d old)", file=sys.stderr)

    # 台風データ取り込み (extra.xml に VPTW があるときだけ書き出す。
    #   --only 指定時は天気の単一都市デバッグ目的なのでスキップ)
    if not args.only:
        try:
            n_active, n_written, n_archived = fetch_typhoons(out_dir, now_iso)
            if n_active or n_archived:
                print(f"[typhoon] {n_active} active / {n_written} written / {n_archived} archived", file=sys.stderr)
            else:
                print("[typhoon] no active typhoons", file=sys.stderr)
        except Exception as e:
            print(f"[typhoon] failed: {e}", file=sys.stderr)

    # 天気図取り込み (VZSA50/VZSF50/VZSF51 から source="chart" レコードを書く)
    if not args.only:
        try:
            from fetch_chart import fetch_weather_charts
            n_chart, n_carch, n_cdel, n_corph = fetch_weather_charts(out_dir, now_iso)
            print(
                f"[chart] {n_chart} written / {n_carch} archived / {n_cdel} JSON deleted / {n_corph} orphan PNG deleted",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"[chart] failed: {e}", file=sys.stderr)

    # 警報・注意報 (bosai/warning) から source="warning" レコードを書く
    # 1 都市 1 ファイル (上書き)。アクティブな警報のみ warnings[] に積む。
    if not args.only:
        try:
            n_warn, n_warn_skip = fetch_warnings(out_dir, now_iso)
            print(f"[warning] {n_warn} cities written / {n_warn_skip} skipped", file=sys.stderr)
        except Exception as e:
            print(f"[warning] failed: {e}", file=sys.stderr)

    # 地方海上予報 (bosai/seawarning) から source="marine" レコードを書く
    # 12 オフィス → 37 海域、各海域 1 レコード (上書き、履歴は残さない)
    if not args.only:
        try:
            from fetch_marine import main as fetch_marine_main
            # 既存 argparse と衝突しないよう sys.argv を一時退避
            saved_argv = sys.argv
            sys.argv = ["fetch_marine.py", "--out-dir", str(out_dir)]
            try:
                rc = fetch_marine_main()
            finally:
                sys.argv = saved_argv
            print(f"[marine] fetch_marine exit={rc}", file=sys.stderr)
        except Exception as e:
            print(f"[marine] failed: {e}", file=sys.stderr)

    # 最終行: 既定都市のサマリ (なければ汎用集計)
    if default_summary:
        print(default_summary)
    else:
        print(f"wrote {total_records} records across {success_count}/{len(targets)} cities")
    return 0


if __name__ == "__main__":
    sys.exit(main())
