#!/usr/bin/env python3
"""
気象庁 天気図 PNG を取り込む (jma-weather コレクションに同居)。

旧版は VZSA50/VZSF50/VZSF51 XML を parse してベクトル描画していたが、
ブラウザ負荷が重いので **JMA 公式 PNG を直リンクで使う** 方式に切替 (2026-06-25)。

- カタログ: https://www.jma.go.jp/bosai/weather_map/data/list.json
- PNG: https://www.jma.go.jp/bosai/weather_map/data/png/<filename>
- 日本付近のカラー版だけ取り込む (near.now / near.ft24 / near.ft48)

各 chartType の最新ファイル名を取得 → 同名 PNG を data/jma-weather/charts/ にダウンロード
→ data/jma-weather/items/<chartType>-<targetDateHour>.json (source="chart") を書き出す。

ファイル名パターン:
  20260624190531_0_Z__C_010000_20260624120000_MET_CHT_JCIfsas24_Rjp_JCP600x581_JRcolor_Tjmahp_image.png
  └── 生成時刻       └── 対象時刻 (UTC、秒付き)         └── JCI<spas|fsas24|fsas48> = 図種
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))
UTC = timezone.utc

CATALOG_URL = "https://www.jma.go.jp/bosai/weather_map/data/list.json"
PNG_BASE = "https://www.jma.go.jp/bosai/weather_map/data/png/"

# カタログの (region, time_key) → (chartType, chartLabel, offset_hours)
TARGETS = [
    ("near", "now",  "surface", "地上実況図",      0),
    ("near", "ft24", "fcst24",  "地上24時間予想図", 24),
    ("near", "ft48", "fcst48",  "地上48時間予想図", 48),
]


# HTTP 取得 + 書き込みは共通モジュール jma_http に集約 (L3 効率化: 条件付きGET + 変更時のみ書込)
from jma_http import fetch_bytes as _fetch_bytes, write_record_if_changed  # noqa: E402


def _parse_filename(filename: str) -> dict | None:
    """JMA PNG ファイル名から対象時刻 (UTC) を抽出する。

    `20260624190531_0_Z__C_010000_20260624120000_MET_CHT_JCIfsas24_..._image.png`
                                  └── 14 桁 = 対象時刻 (YYYYMMDDHHMMSS, UTC)
    """
    m = re.search(r"_0_Z__C_010000_(\d{14})_MET_CHT_JCI(\w+?)_", filename)
    if not m:
        return None
    target_str = m.group(1)
    jci = m.group(2)  # spas / fsas24 / fsas48
    try:
        dt_utc = datetime.strptime(target_str, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None
    return {"target_dt_utc": dt_utc, "jci": jci}


def _pick_color_latest(files: list[str]) -> str | None:
    """ファイル名リストからカラー版の最新を取る (mtime ではなく対象時刻順)。"""
    color = [f for f in files if "JRcolor" in f]
    if not color:
        return None
    # 対象時刻の降順
    color.sort(key=lambda f: (_parse_filename(f) or {}).get("target_dt_utc", datetime.min.replace(tzinfo=UTC)), reverse=True)
    return color[0]


def build_record(region: str, chart_type: str, chart_label: str, offset_h: int, filename: str, now_iso: str, charts_dir_rel: str) -> dict | None:
    meta = _parse_filename(filename)
    if not meta:
        return None
    target_jst = meta["target_dt_utc"].astimezone(JST)
    valid_jst = target_jst + timedelta(hours=offset_h)
    target_iso = target_jst.isoformat()
    valid_iso = valid_jst.isoformat()
    target_short = valid_jst.strftime("%Y-%m-%dT%H")

    png_url = PNG_BASE + filename
    png_path = f"{charts_dir_rel}/{filename}"

    summary = f"{chart_label} {valid_jst.strftime('%m/%d %H:%M')} (JMA PNG)"

    return {
        "id": f"{chart_type}-{target_short}",
        "source": "chart",
        "chartType": chart_type,
        "chartLabel": chart_label,
        "targetDateTime": target_iso,
        "validDateTime": valid_iso,
        "pngFilename": filename,
        "pngUrl": png_url,
        "pngPath": png_path,
        "summary": summary,
        "archived": False,
        "updatedAt": now_iso,
    }


def cleanup_old_charts(items_dir: Path, charts_dir: Path, keep_days: int = 7) -> int:
    """validDateTime が今日 - keep_days より前の chart JSON + 対応 PNG を削除。"""
    cutoff = (datetime.now(JST) - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    deleted = 0
    if not items_dir.exists():
        return 0
    for f in items_dir.glob("*.json"):
        if not f.name.startswith(("surface-", "fcst24-", "fcst48-")):
            continue
        try:
            cur = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if cur.get("source") != "chart":
            continue
        target = cur.get("validDateTime") or cur.get("targetDateTime") or ""
        if target[:10] and target[:10] < cutoff:
            # 対応 PNG も削除
            png_name = cur.get("pngFilename")
            if png_name:
                png_file = charts_dir / png_name
                if png_file.exists():
                    try: png_file.unlink()
                    except OSError: pass
            try:
                f.unlink()
                deleted += 1
            except OSError:
                pass
    return deleted


def archive_obsolete_charts(items_dir: Path, current_ids: set[str]) -> int:
    """今回書いていない (= 古い世代の) chart レコードに archived: true を付ける。"""
    if not items_dir.exists():
        return 0
    archived = 0
    for f in items_dir.glob("*.json"):
        if not f.name.startswith(("surface-", "fcst24-", "fcst48-")):
            continue
        if f.stem in current_ids:
            continue
        try:
            cur = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if cur.get("source") != "chart":
            continue
        if cur.get("archived") is True:
            continue
        cur["archived"] = True
        try:
            write_record_if_changed(f, cur, newline=False)
            archived += 1
        except OSError:
            pass
    return archived


def cleanup_orphan_pngs(charts_dir: Path, referenced: set[str]) -> int:
    """items の JSON が参照しなくなった PNG を削除 (容量節約)。"""
    if not charts_dir.exists():
        return 0
    deleted = 0
    for f in charts_dir.glob("*.png"):
        if f.name in referenced:
            continue
        try:
            f.unlink()
            deleted += 1
        except OSError:
            pass
    return deleted


def fetch_weather_charts(out_dir: Path, now_iso: str) -> tuple[int, int, int, int]:
    """3 chartTypes の最新 PNG をダウンロードして JSON レコードを書く。

    返り値: (取得成功数, archived, deleted JSON, deleted orphan PNG)
    """
    workspace = out_dir.parents[1] if out_dir.name == "items" else out_dir.parent
    charts_dir = workspace / "data" / "jma-weather" / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)
    charts_dir_rel = "data/jma-weather/charts"

    try:
        cat = json.loads(_fetch_bytes(CATALOG_URL))
    except Exception as e:
        print(f"[chart] failed to fetch list.json: {e}", file=sys.stderr)
        return (0, 0, 0, 0)

    written_ids: set[str] = set()
    referenced_pngs: set[str] = set()
    success = 0

    for region, time_key, chart_type, chart_label, offset_h in TARGETS:
        files = cat.get(region, {}).get(time_key) or []
        filename = _pick_color_latest(files)
        if not filename:
            print(f"[chart] {chart_type}: no color PNG in catalog", file=sys.stderr)
            continue

        # PNG をローカルにダウンロード (既にあればスキップ)
        png_local = charts_dir / filename
        if not png_local.exists():
            try:
                png_bytes = _fetch_bytes(PNG_BASE + filename)
                png_local.write_bytes(png_bytes)
                print(f"[chart] downloaded {filename} ({len(png_bytes)/1024:.1f} KB)", file=sys.stderr)
            except Exception as e:
                print(f"[chart] download failed for {filename}: {e}", file=sys.stderr)
                continue
        referenced_pngs.add(filename)

        rec = build_record(region, chart_type, chart_label, offset_h, filename, now_iso, charts_dir_rel)
        if rec is None:
            print(f"[chart] failed to parse filename {filename}", file=sys.stderr)
            continue

        path = out_dir / f"{rec['id']}.json"
        try:
            write_record_if_changed(path, rec, newline=False)
            written_ids.add(rec["id"])
            success += 1
            print(f"[chart] wrote {rec['id']} ({rec['summary']})", file=sys.stderr)
        except OSError as e:
            print(f"[chart] write {path}: {e}", file=sys.stderr)

    archived = archive_obsolete_charts(out_dir, written_ids)
    # cleanup 後にどの PNG が参照されているかを再集計
    if out_dir.exists():
        for f in out_dir.glob("*.json"):
            if not f.name.startswith(("surface-", "fcst24-", "fcst48-")):
                continue
            try:
                cur = json.loads(f.read_text(encoding="utf-8"))
                if cur.get("source") == "chart" and cur.get("pngFilename"):
                    referenced_pngs.add(cur["pngFilename"])
            except Exception:
                pass
    # 過去 2 日より古い天気図 JSON + 対応 PNG を削除。コレクション件数を 200 件未満に抑えるため。
    # 直近 2 日 × 3 chartType + α (前世代 archived) ≒ 6-16 件程度に収まる。
    deleted = cleanup_old_charts(out_dir, charts_dir, keep_days=2)
    orphan = cleanup_orphan_pngs(charts_dir, referenced_pngs)
    return (success, archived, deleted, orphan)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default=os.environ.get("JMA_OUT_DIR", "data/jma-weather/items"))
    args = p.parse_args()

    workspace = Path(__file__).resolve().parents[2]
    out_dir = Path(args.out_dir) if Path(args.out_dir).is_absolute() else workspace / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    now_iso = datetime.now(JST).isoformat()

    success, archived, deleted, orphan = fetch_weather_charts(out_dir, now_iso)
    if success == 0:
        return 1
    print(f"chart fetch ok: {success} written / {archived} archived / {deleted} JSON deleted / {orphan} orphan PNG deleted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
