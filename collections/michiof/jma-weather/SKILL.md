---
name: jma-weather
description: 気象庁の公開 API から **天気予報・警報注意報・台風・地上天気図・地方海上予報** を取り込んで 1 つに統合する schema-driven collection。`/collections/jma-weather` のカスタムビュー「天気予報」で 5 モード切替: 指定地域カード (警報バー付き) / 全国一覧 / 🌀 台風 / 🗺️ 天気図 / 🌊 海上。レコードは `source` で識別 (today/tomorrow/weekly/past = 天気予報、warning = 警報注意報、typhoon = 台風、chart = 天気図、marine = 海上予報)。ユーザーが「今日の天気」「全国の天気」「週間予報」「気象庁から取り込んで」「警報出てる?」「台風どこ?」「天気図見せて」「海上予報」などと言ったら使う。
---

# 気象庁 天気予報 (schema-driven collection)

気象庁の公開 JSON API から全国主要 11 都市の天気予報、警報・注意報、台風、地上天気図、地方海上予報を取り込み、**1 都市 1 日 1 レコード** で保存するコレクション。`/collections/jma-weather` を開くと標準のテーブル / カレンダー表示に加えて **「天気予報」カスタムビュー**（テレビニュース風ダーク UI）が使える。

## このバンドルに含まれているもの

- `schema.json` — フィールド定義 + カスタムビュー宣言。`source` enum でレコード種別を分岐
- `views/news-weather.html` — 5 モードの統合ビュー（指定地域 / 全国地図 / 台風 / 天気図 / 海上）
- `views/japan-map.svg` — Geolonia japanese-prefectures（GFDL）の source-of-truth コピー。HTML 側に既に inline 展開済みなので runtime 依存はないが、再 pin 時の元ファイルとして同梱
- `seed/items/*.json` — スキーマを確認するための **完全合成のダミーレコード**（後述）

## live JMA データで動かすセットアップ（importer 必読）

import 直後は **schema + view + 合成 seed 5 件**だけが入った状態で動きます。実際の気象庁から自動取得するには **Python スクリプトと ingest ブロックを手動で追加する必要があります**（MulmoClaude の host は import 時に `views/` `templates/` `seed/items/` 配下しか自動配置しないため）。

### 前提条件

- **Python 3.8+**（標準ライブラリのみ使用、`pip install` 不要 — `urllib.request` / `json` / `xml.etree.ElementTree` などの stdlib だけで動きます）
- ネットワーク接続（JMA API はすべて公開・**認証不要**）

### セットアップ手順（5〜10 分）

**1. スクリプトを workspace にコピー**

このバンドルには取得スクリプト 3 本が同梱されています（`scripts/fetch.py` / `fetch_chart.py` / `fetch_marine.py`）。MulmoClaude の workspace で次の場所にコピーしてください:

```
data/jma-weather/
├── fetch.py         ← scripts/fetch.py をここに
├── fetch_chart.py   ← scripts/fetch_chart.py をここに
├── fetch_marine.py  ← scripts/fetch_marine.py をここに
└── items/           ← 既存（schema の dataPath、レコードが入る）
```

GitHub からの直接ダウンロード例（workspace ルートで）:

```bash
mkdir -p data/jma-weather
BASE="https://raw.githubusercontent.com/receptron/mulmoclaude-collections/main/collections/michiof/jma-weather/scripts"
curl -fsSL "$BASE/fetch.py"        -o data/jma-weather/fetch.py
curl -fsSL "$BASE/fetch_chart.py"  -o data/jma-weather/fetch_chart.py
curl -fsSL "$BASE/fetch_marine.py" -o data/jma-weather/fetch_marine.py
```

**2. 動作確認（手動実行）**

```bash
python3 data/jma-weather/fetch.py --only 140000   # 横浜だけ取得してテスト
# 成功すれば data/jma-weather/items/140000-*.json が複数生成される
python3 data/jma-weather/fetch.py                 # 11 都市分を一括取得
```

**3.（任意）スケジュール起動を有効化**

毎時 自動取得したい場合は、Claude に「manageCollection で jma-weather の schema に ingest ブロックを足して」と頼むか、`schema.json` の末尾に手書きで追記してください:

```json
"ingest": {
  "kind": "agent",
  "schedule": "hourly",
  "role": "general",
  "template": "templates/refresh.md"
}
```

そして `data/skills/jma-weather/templates/refresh.md` に以下を作成:

```markdown
気象庁の天気予報・台風・天気図・地方海上予報を取得します。確認は不要、すぐに実行してください。

## 手順

1. Bash で `python3 data/jma-weather/fetch.py` を実行する。
2. 成功時 (exit 0): 何も出力しない。チャット返信なし、要約なし、解説なし。そのまま終了する。
3. 失敗時 (exit != 0): stderr の最後 200 文字程度を含めたエラーメッセージを返し、非ゼロ終了する。

他の処理はしない。誰もこの canvas を見ていない。
```

これで `/collections/jma-weather` ヘッダの **Refresh** ボタンと毎時自動 ingest が有効になります。

### トラブルシューティング

- `python3: command not found` → macOS なら `brew install python3`、Ubuntu なら `apt install python3`
- `urllib.error.HTTPError` が出る → JMA API が一時的に応答していない。5 分待って再実行
- レコードが 1 件も書き出されない → `python3 data/jma-weather/fetch.py --only 140000` で 1 都市だけ走らせて stderr を読む
- ビューに地図が出ない → views/news-weather.html の SVG inline が壊れている可能性。バンドルからもう一度コピーする

## レコード形状（共通）

- `id` — 主キー。`<office>-<YYYY-MM-DD>`（天気）/ `<office>-warning`（警報）/ `typhoon-<EventID>`（台風）/ `<chartType>-<targetDateHour>`（天気図）/ `marine-<areaCode>`（海上）
- `source` — `today` / `tomorrow` / `weekly` / `past` / `warning` / `typhoon` / `chart` / `marine`
- `summary` — 1 行サマリ（displayField）
- 天気系: `weather` / `weatherIcon` / `min` / `max` / `pop` / `wind` / `wave` / `overviewText` …
- 警報: `warnings: [{ code, name, level: special/warning/advisory, status }]`
- 台風: `eventId`, `typhoonNumber`, `forecastPoints[]` (dtType, lat, lng, pressure_hpa, wind_max_ms, …)
- 天気図: `chartType` (surface/fcst24/fcst48), `pngUrl`, `validDateTime`
- 海上: `areaCode`, `periods[]` (今日/明日 × wind/wave/weather/vis)

詳細はフィールド一覧（schema.json の `fields`）を参照。

## カスタムビュー「天気予報」

`views/news-weather.html` — ダーク背景の TV ニュース風レイアウト。`window.__MC_VIEW.dataUrl` から全レコードを 1 回 GET し、クライアント側で都市・日付・モードフィルタする。

- 右上トグル「指定地域 / 全国 / 🌀 台風 / 🗺️ 天気図 / 🌊 海上」
- **指定地域モード**: ドロップダウンで都市選択 → 今日・明日の大カード + 7 日間ストリップ + 気象庁概要文 + 警報バー
- **全国モード**: Geolonia の日本地図（SVG inline）に各都市のテレビ天気風カードを配置 / リスト表示も切替可
- **🌀 台風モード**: Leaflet + OSM で中心位置・実況強風域 / 暴風域・予報円・暴風警戒域を描画
- **🗺️ 天気図モード**: JMA 公式 PNG を実況 / 24h 予想 / 48h 予想で切替
- **🌊 海上モード**: Leaflet + 国土地理院淡色 + JMA 海域 GeoJSON ポリゴンを波高で色分け

外部 `fetch()` は使用していない（CSP / sandbox lint クリア）。Leaflet / CartoDB / OSM タイルなどの CDN 読み込みは MulmoClaude カスタムビュー sandbox のホワイトリスト経由。

## CRUD

- import 直後は seed の 5 レコードだけ入っている。実運用には fetch.py 系の導入が必要（前述）
- 表示: `/collections/jma-weather`（テーブル / カレンダー / 天気予報ビュー）
- 同一 id のレコードは**上書き**

## JMA API リファレンス

- 短期予報: `https://www.jma.go.jp/bosai/forecast/data/forecast/<office>.json`
- 概要文: `https://www.jma.go.jp/bosai/forecast/data/overview_forecast/<office>.json`
- 警報・注意報: `https://www.jma.go.jp/bosai/warning/data/r8/<office>.json`
- 各地の office code: `https://www.jma.go.jp/bosai/common/const/area.json`
- 防災情報 XML feed: `https://www.data.jma.go.jp/developer/xml/feed/extra.xml`（VPTW = 台風解析・予報情報）
- 天気図カタログ: `https://www.jma.go.jp/bosai/weather_map/data/list.json`
- 地方海上予報: `https://www.jma.go.jp/bosai/seawarning/data/forecast/<office>.json`
- 海域 GeoJSON: `https://www.jma.go.jp/bosai/common/const/geojson/marines.json`

## ライセンス

スキーマ・ビュー・SKILL.md: **MIT**。`views/japan-map.svg` は Geolonia / Wikipedia 由来で **GFDL**（出典は view 右下に表示）。
