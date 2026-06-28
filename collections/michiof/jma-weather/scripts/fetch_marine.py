#!/usr/bin/env python3
"""
気象庁 海上警報・予報の公開 JSON から **全国 33 海域** の地方海上予報を取得し、
data/jma-weather/items/marine-<areaCode>.json として 1 海域 1 レコードで保存する。

LLM を使わずに動作する純粋なスクリプト。
スケジューラからは ``python3 data/jma-weather/fetch_marine.py`` で起動する想定。

データソース:
  - https://www.jma.go.jp/bosai/seawarning/data/forecast/<office>.json
    (12 オフィス × 「今日 / 明日」 = 33 海域 × 2 期 の構造化予報)
  - https://www.jma.go.jp/bosai/common/const/label_pos/marines.json
    (各海域の重心 lon/lat。地図描画用)

出力: 1 海域 1 ファイル。1 回の fetch でファイルは上書き (履歴は残さない)。
  data/jma-weather/items/marine-<areaCode>.json (source="marine")

風速換算 (ノット↔m/s) は気象庁の原文に両方含まれているのでこちらで行わない。
原文をそのまま raw として保持し、表示用の補助フィールドだけ最低限抽出する:
  - primaryDirection: ピンに出す方位タグ ("北東" など、最初に登場するもの)
  - waveMaxM: ピンの色分けに使う最大波高 (m)
  - hasSwell: "うねり" 文字列の有無
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))

FORECAST_URL_TPL = "https://www.jma.go.jp/bosai/seawarning/data/forecast/{office}.json"
LABEL_POS_URL = "https://www.jma.go.jp/bosai/common/const/label_pos/marines.json"
GEOJSON_URL = "https://www.jma.go.jp/bosai/common/const/geojson/marines.json"

# 12 オフィス (海上気象を発表する管区気象台 / 海洋気象台)
# (office_code, label) — label はログ表示用
OFFICES = [
    ("016000", "札幌"),
    ("017000", "函館"),
    ("040000", "仙台"),
    ("130000", "東京"),
    ("150000", "新潟"),
    ("230000", "名古屋"),
    ("260020", "舞鶴"),
    ("280000", "神戸"),
    ("400000", "福岡"),
    ("420000", "長崎"),
    ("460100", "鹿児島"),
    ("471000", "沖縄"),
]

UA = "mulmoclaude-jma-marine/1.0"

# ──────────────────────────────────────────────────────────
# JMA 原文パース
# ──────────────────────────────────────────────────────────

_FW_DIGITS = str.maketrans("０１２３４５６７８９．", "0123456789.")

# 方位は 16 方位を網羅 (より長い表記を先に試す)
_DIR_PATTERNS = [
    "北北東", "東北東", "東南東", "南南東",
    "南南西", "西南西", "西北西", "北北西",
    "北東", "南東", "南西", "北西",
    "北", "東", "南", "西",
]


def _normalize(text: str) -> str:
    """全角数字 → 半角、全角ピリオド → 半角。"""
    return text.translate(_FW_DIGITS)


def parse_wave_max_m(lines: list[str]) -> float | None:
    """波 ["１．５メートル ２５日１５時までに １メートル", ...] → 最大値 (float, m)。"""
    max_v: float | None = None
    for line in lines or []:
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*メートル", _normalize(line)):
            try:
                v = float(m.group(1))
            except ValueError:
                continue
            if max_v is None or v > max_v:
                max_v = v
    return max_v


def has_swell(lines: list[str]) -> bool:
    """波文中の "うねり" 検出。"""
    for line in lines or []:
        if "うねり" in line:
            return True
    return False


def parse_primary_direction(lines: list[str]) -> str | None:
    """風 ["北東 30ノット（15メートル） ...", "豊後水道で 南 ..."] から最初に出る方位を返す。"""
    for line in lines or []:
        for dirn in _DIR_PATTERNS:
            if dirn in line:
                return dirn
    return None


# ──────────────────────────────────────────────────────────
# Fetch
# ──────────────────────────────────────────────────────────

# HTTP 取得 + 書き込みは共通モジュール jma_http に集約 (L3 効率化: 条件付きGET + 変更時のみ書込)
from jma_http import fetch_json, write_record_if_changed  # noqa: E402


def build_records(office: str, office_label: str, src: dict, label_pos: dict, geoms: dict, now_iso: str) -> list[dict]:
    """1 オフィス JSON → 1 海域 1 レコードのリスト。

    海域コード単位で 2 期間 (今日 / 明日) を束ねる。各レコードに synopsis と発表元情報を持たせる。
    """
    publishing = src.get("publishingOffice", "")
    title = src.get("title", "")
    report_dt = src.get("reportDatetime", "")
    factors = src.get("factors", {})
    synopses = [s for s in factors.get("synopses", []) if s and s.strip()]

    # forecasts: [{name: "今日", properties: [{area, wind, wave, ...}, ...]}, ...]
    forecasts = src.get("forecasts", [])

    # area_code → {periodName → period entry}
    by_area: dict[str, dict] = {}
    for period in forecasts:
        period_name = period.get("name", "")
        for prop in period.get("properties", []):
            area = prop.get("area", {})
            code = area.get("code")
            name = area.get("name", "")
            if not code:
                continue
            slot = by_area.setdefault(code, {"areaCode": code, "areaName": name, "periods": []})
            wind_lines = prop.get("wind", []) or []
            wave_lines = prop.get("wave", []) or []
            slot["periods"].append({
                "name": period_name,
                "wind": wind_lines,
                "wave": wave_lines,
                "weather": prop.get("weather", []) or [],
                "vis": prop.get("vis", []) or [],
                "windPrimaryDirection": parse_primary_direction(wind_lines),
                "waveMaxM": parse_wave_max_m(wave_lines),
                "hasSwell": has_swell(wave_lines),
            })

    out = []
    for code, slot in by_area.items():
        centroid = label_pos.get(code, {}).get("gravity")  # [lon, lat]
        if centroid:
            lon, lat = centroid[0], centroid[1]
        else:
            lon, lat = None, None
        out.append({
            "id": f"marine-{code}",
            "source": "marine",
            "areaCode": code,
            "areaName": slot["areaName"],
            "office": office,
            "officeLabel": office_label,
            "title": title,
            "publishingOffice": publishing,
            "reportDatetime": report_dt,
            "centroid": {"lon": lon, "lat": lat} if lon is not None else None,
            "geometry": geoms.get(code),  # JMA 海域ポリゴン (Leaflet で色塗りに使用)
            "synopses": synopses,
            "periods": slot["periods"],
            "updatedAt": now_iso,
        })
    return out


def write_record(out_dir: Path, rec: dict) -> Path:
    p = out_dir / f"{rec['id']}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    write_record_if_changed(p, rec, newline=False)
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description="JMA 地方海上予報を fetch し marine items を書き出す")
    ap.add_argument("--out-dir", default=str(Path(__file__).parent / "items"))
    ap.add_argument("--only", help="特定 office だけ (例: 280000)")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) 海域重心 lon/lat
    try:
        label_pos = fetch_json(LABEL_POS_URL)
        print(f"[label_pos] fetched {len(label_pos)} marine areas", file=sys.stderr)
    except Exception as e:
        print(f"[label_pos] FAILED: {e}", file=sys.stderr)
        return 1

    # 2) 海域ポリゴン GeoJSON (Leaflet 描画用、約 178KB)
    geoms: dict[str, dict] = {}
    try:
        gj = fetch_json(GEOJSON_URL)
        for feat in gj.get("features", []):
            code = feat.get("properties", {}).get("code")
            if code:
                geoms[code] = feat.get("geometry")
        print(f"[geojson] fetched {len(geoms)} marine polygons", file=sys.stderr)
    except Exception as e:
        print(f"[geojson] FAILED: {e}", file=sys.stderr)
        # geom が無くてもレコード自体は書き続ける

    now_iso = datetime.now(JST).isoformat(timespec="seconds")
    n_ok = 0
    n_records = 0
    for office, label in OFFICES:
        if args.only and office != args.only:
            continue
        try:
            src = fetch_json(FORECAST_URL_TPL.format(office=office))
        except Exception as e:
            print(f"[{office} {label}] fetch failed: {e}", file=sys.stderr)
            continue
        records = build_records(office, label, src, label_pos, geoms, now_iso)
        for rec in records:
            write_record(out_dir, rec)
            n_records += 1
        n_ok += 1
        print(f"[{office} {label}] {len(records)} areas written", file=sys.stderr)

    print(f"DONE: {n_ok}/{len(OFFICES)} offices, {n_records} marine records", file=sys.stderr)
    return 0 if n_ok > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
