---
name: jma-weather
description: 気象庁の公開 API + 防災情報 XML から **天気予報・警報注意報・台風・地上天気図・地方海上予報** を取り込んで 1 つに統合する schema-driven collection。LLM 不要の Python (`data/jma-weather/fetch.py` + `fetch_chart.py` + `fetch_marine.py`) が直接 JMA → JSON → コレクションファイルを書き出す。`/collections/jma-weather` のカスタムビュー「天気予報」で 5 モード切替: 指定地域カード (警報バー付き) / 全国一覧 / 🌀 台風 (活動中のみ) / 🗺️ 天気図 (実況・24h・48h 予想) / 🌊 海上 (37 海域の波・風予報、今日/明日)。レコードは `source` で識別 (today/tomorrow/weekly/past = 天気予報、warning = 警報注意報、typhoon = 台風、chart = 天気図、marine = 海上予報)。ユーザーが「今日の天気」「全国の天気」「週間予報」「気象庁から取り込んで」「警報出てる?」「土砂災害」「台風どこ?」「天気図見せて」「等圧線」「前線」「波高」「海上予報」「瀬戸内海」などと言ったら使う。レコードは `data/jma-weather/items/<office>-<date>.json` (天気) / `<office>-warning.json` (警報) / `typhoon-<eventId>.json` (台風) / `<chartType>-<targetDateHour>.json` (天気図) / `marine-<areaCode>.json` (海上予報) で 1 ファイル 1 レコード。
---

# 気象庁 天気予報 (schema-driven collection)

気象庁の公開 JSON API から全国主要 11 都市の天気予報を取得し、**1 都市 1 日 1 レコード** で
保存するコレクション。`/collections/jma-weather` を開くと標準のテーブル/カレンダー表示に
加えて **「天気予報」カスタムビュー** が表示され、指定地域カード / 全国一覧を
タブで切り替えられる。

定期取得は **`data/jma-weather/fetch.py`** が単独で完結する（LLM 不要の Python）。
コレクションの `ingest.kind: "agent"` から **毎時** 起動され、template (`templates/refresh.md`)
が `python3 data/jma-weather/fetch.py` を呼ぶだけ。コレクションヘッダの **Refresh** ボタンからも
同じ経路で手動実行できる。

## 初期セットアップ（対話）

import 直後、ユーザーと**対話しながら「指定地域（既定で開く都市）」を決める**。Claude が以下を順に行う。

### A. スクリプト配置（importer 初回のみ・host が自動配置しないファイル）

host は `schema.json` / `SKILL.md` / `views/` / `seed/items/` を自動配置するが、`scripts/*.py` は規約外なので
配置しない。次の Python 群を `data/jma-weather/` に取得する（stdlib のみ、pip 依存なし）:

```bash
mkdir -p data/jma-weather
BASE="https://raw.githubusercontent.com/receptron/mulmoclaude-collections/main/collections/michiof/jma-weather/scripts"
for f in jma_http.py fetch.py fetch_chart.py fetch_marine.py resolve_city.py; do
  curl -fsSL "$BASE/$f" -o "data/jma-weather/$f"
done
```

`jma_http.py` は他の3スクリプトが import する**共通モジュール（必須）**。`resolve_city.py` は地域コード解決に
`certifi` があれば使う（無くても Docker サンドボックス内なら通る）。

### B. 指定地域を対話で選ぶ

ユーザーに「**どの地域を既定の指定地域にしますか？**（例: 横浜 / 熊本 / 大阪）」と尋ね、答えを `resolve_city.py` に渡す:

```bash
python3 data/jma-weather/resolve_city.py <地域名> --write --set-default
```

- これは **JMA 公式コード体系（area.json）から office / class10 / class20(警報) / 週間予報の気温地点 / AMeDAS地点を決定論的に解決**し、`data/jma-weather/items/config.json`（`source:"config"` レコード）の `cities[]` に upsert + `defaultCity` を設定する。
- **曖昧なとき**（例「横浜」→ 青森の横浜町 vs 神奈川の横浜市）は候補を一覧表示して停止する。Claude はその候補をユーザーに見せ、選ばれた class20 コードで `--pick <code> --write --set-default` を再実行する。
- 既定の11都市のどれかを選んだ場合も同じコマンドでよい（コードを再解決し既定に設定、地図座標は保持）。

> **全国マップの主要都市は触らない**: 全国マップのピンは既定11都市のまま。新しい都市をマップのピンに足すには地図座標（`map.pref` 等）が要るが、本セットアップでは扱わない（指定地域カードは map 座標なしで動く）。

### C. 初回取得

```bash
python3 data/jma-weather/fetch.py            # 全ロスター取得
# まず1都市だけ試すなら:
python3 data/jma-weather/fetch.py --only <office>
```

以後は schema の `ingest`（毎時 agent）が自動で回す。

> **ユーザー設定はコードから隔離されている**: 「どの都市・既定都市」は `config.json`（コレクションのレコード）に住み、`fetch.py` とカスタムビューの両方がこれを読む。共有コード（`fetch.py` の `_BUILTIN_CITIES` / view の `CITY_ORDER`）は **`config.json` が無いときのフォールバック**。bundle 更新で再 import してもユーザー設定は消えない。

## 取得対象 (既定 11 都市)

**ロスターと既定都市は `data/jma-weather/items/config.json`（`source:"config"` レコード）が正本**。
`fetch.py` と カスタムビューの両方がこれを読む。`config.json` が無い場合のフォールバックが
`fetch.py` の `_BUILTIN_CITIES`（下表）/ view の `CITY_ORDER`。
地域の追加・変更は手書きせず **`resolve_city.py <地域> --write [--set-default]`**（→ 初期セットアップ B）で行う。

既定ロスター（`_BUILTIN_CITIES`）:

| order | office | name | region | temp_code (週間予報用) | amedas_code (観測用) |
|---|---|---|---|---|---|
| 1 | 016000 | 札幌   | 北海道 | 47412 | 14163 |
| 2 | 040000 | 仙台   | 宮城県 | 47590 | 34392 |
| 3 | 150000 | 新潟   | 新潟県 | 47604 | 54232 |
| 4 | 130000 | 東京   | 東京都 | 44132 | 44132 |
| 5 | 140000 | 横浜   | 神奈川県 ← **既定の「指定地域」** | 46106 | 46106 |
| 6 | 230000 | 名古屋 | 愛知県 | 47636 | 51106 |
| 7 | 270000 | 大阪   | 大阪府 | 47772 | 62078 |
| 8 | 320000 | 松江   | 島根県 | 47741 | 68132 |
| 9 | 370000 | 高松   | 香川県 | 47891 | 72086 |
| 10 | 400000 | 福岡  | 福岡県 | 47807 | 82182 |
| 11 | 471000 | 那覇  | 沖縄本島 | 47936 | 91197 |

**`temp_code` と `amedas_code` は別体系**:

- `temp_code` (47xxx 系) は気象官署コードで、JMA 週間予報の `timeSeries[1].areas.code` と一致する。気温予報 (tempsMin/Max) の地点指定に使う
- `amedas_code` は AMeDAS の観測所 id。今日の **currentTemp（現在気温）と precip1h/3h/24h（降水量）** を引くのに使う。`amedastable.json` の `kjName` 一致で引いた値
- 東京・横浜だけ偶然両方が同じ値。他都市は別の値なので**両フィールド必須**

`--only <office>` で単一都市だけ取得することも可能。

> **AMeDAS は全観測所一括 (map) で 1 回取得**: `fetch_amedas_map()` が `bosai/amedas/data/map/<時刻>.json` を 1 本だけ取り（全1286観測所）、`amedas_stats_from_map()` が各都市の `temp` / `precipitation1h` / `3h` / `24h` を抜く。旧実装の「都市ごと地点別8ブロック ≒ 99 req」を **2 req** に置換。降水窓は **1h/3h/24h**（6h は AMeDAS にネイティブで無い）。

## 気温の扱い（JMA 仕様メモ）

**min / max は予報値**（テレビ天気予報的に「今日の最高 28℃」を朝から見せるため）。AMeDAS 観測値は別フィールド `currentTemp` (現在気温) に入る。

**今日の min / max は予報値の保持ロジックで安定化**: JMA 短期予報の今日分 `temps` 配列は発表時刻に依存して挙動が不安定（5 時発表のみ含まれる、17 時発表では翌日のみ等）。これを `fetch_city()` の **既存値保持ロジック** で吸収する: 既存レコードを Read し、新値が空欄なら既存値を維持する。これにより朝 5 時発表で取った今日の min / max が、17 時発表後も保持される。

**明日以降の min / max** は週間予報 (forecast[1]) の `tempsMin` / `tempsMax` をそのまま使う。週間予報の今日 index 0 は空欄で返るが、今日分は短期予報 + 保持ロジックでカバーされる。

`fetch.py` の短期予報 parser は `temps` 配列の **値そのものを比較して min / max** を取る（`min == max` のときは max だけ保持）。

**AMeDAS の役割は「現在気温」と「降水量」だけに限定**: `amedas_stats_from_map()` は `currentTemp` と `precip1h` / `precip3h` / `precip24h` / `precipAsOf` を返す。min / max は AMeDAS から取らない（予報値を使う）。

## 1 回の取得で書き出されるもの

各都市について **4 つの JMA エンドポイント** を取得し、日付ごとに 1 レコード化:

- 短期予報: `bosai/forecast/data/forecast/<office>.json`（1 日 1 個の天気コード + 6h 降水確率バンド + 1 日 2 点気温）
- 概要文: `bosai/forecast/data/overview_forecast/<office>.json`（気象庁の概況テキスト）
- **地域時系列予報 (wdist VPFD)**: `bosai/jmatile/data/wdist/VPFD/<area_code>.json`（**3 時間ごとの天気・気温・風**）
- **AMeDAS 観測** (today レコード専用): 全観測所一括 `bosai/amedas/data/map/<時刻>.json` を **全都市で1回** 取得（`fetch_amedas_map()`、別途 `latest_time.txt` 1回）
  - 今日の `currentTemp` (現在気温)、`precip1h` / `precip3h` / `precip24h` (mm)、`precipAsOf` を埋める（`amedas_stats_from_map()` が各都市の観測所値を抜く）
  - 失敗したら warn を出して該当フィールドは空のまま (ブロッカーにしない)
  - **min / max は AMeDAS から取らない**: 予報値の方が「今日これから何度になるか」が分かって実用的

`hourly` フィールドに 3 時間ごとの値が入る:
```json
{
  "hourly": [
    { "time": "12:00", "datetime": "2026-06-21T12:00:00+09:00",
      "weather": "晴れ", "weatherIcon": "☀️", "temp": "25",
      "windDir": "南西", "windSpeed": 4 },
    ...
  ]
}
```

ビューの hero カードに 3 時間ごとストリップ（時刻 + アイコン + 気温 + 風向）として表示される。

- **今日** (`source: "today"`) — 短期予報の最初の日。weather/min/max/wind/wave/popsByBand + `overviewText` (気象庁の概要文)
- **明日** (`source: "tomorrow"`) — 短期予報 2 日目。時間帯別降水確率まで
- **3〜9 日後** (`source: "weekly"`) — 週間予報。weather/min/max/pop/reliability

短期と週間で日付が被ったら **短期予報が勝つ**。週間にしか無い `reliability` は温存。

**過去日 today/tomorrow の自動降格**: `classify_source()` は fetch 時の `today_iso` を元に source を付けるため、日付が進むと過去日のレコードに古い `today` / `tomorrow` ラベルが残る。ビューは `source === "today"` で find するので、複数の today レコードがあると **古い (空欄混じりの) 方** を拾ってしまう (日付昇順ソートのため)。これを防ぐため、`fetch_city()` の冒頭で `demote_past_today_records()` が走り、過去日になった `today` / `tomorrow` レコードの `source` を `past` に書き換え、`dayLabel` を「N日前」に更新する。weather / min / max / precip* などの値自体は消さず、観測値の履歴として残す。

**保持期間**: 取得の最後に `cleanup_old_records()` が走り、今日から **90 日より古い** `items/` ファイルを削除する。天気予報の履歴は実用上見返さないため、ストレージ/表示速度対策で自動掃除する。保持期間を変えるときは `fetch.py` の `keep_days=90` を編集。

1 回の取得で 11 都市 × 約 8-9 日 = **約 88-99 レコード** が更新される。

## レコード形状 (`data/jma-weather/items/<office>-<YYYY-MM-DD>.json`)

- `id` — 主キー。`<office>-<date>` (例: `140000-2026-06-21`)
- `office` — JMA office code
- `cityName`, `regionName`, `cityOrder` — 都市の並べ替え用
- `date`, `weekday` (月..日), `dayLabel` (今日/明日/N日後)
- `source` — `today` / `tomorrow` / `weekly`
- `summary` — 1 行サマリ (displayField)
- `weather`, `weatherCode`, `weatherIcon` (絵文字)
- `min`, `max` (**予報値**、今日分は短期予報＋既存値保持で 5 時発表値を 24h 維持)
- `pop` (単一値), `popsByBand` (今日・明日のみ)
- `currentTemp` (現在気温、**今日のみ**、AMeDAS 観測値)
- `precip1h`, `precip3h`, `precip24h` (mm、**今日のみ**、AMeDAS 観測値)
- `precipAsOf` (currentTemp / precip* の基準観測時刻、ISO)
- `wind`, `wave` (今日・明日のみ)
- `reliability` (週間のみ)
- `overviewText` (今日のみ)
- `areaCode`, `areaName`, `publishingOffice`, `reportDatetime`, `updatedAt`

## カスタムビュー「天気予報」

`views/news-weather.html` — ダーク背景の TV ニュース風レイアウト。

- 右上トグル「指定地域 / 全国」
- **指定地域モード**: ドロップダウンで都市選択 → 今日・明日の大カード + 7 日間ストリップ + 気象庁概要文
- **全国モード**: さらに 2 タブ
  - **地図** (既定): Geolonia の日本地図に **CITY_ORDER の全都市** のテレビ天気風カードを各都道府県の上に直置き。カードクリックで指定地域モードへ
  - **リスト**: 同じ都市セットのカードグリッド
  - 今日 / 明日の切り替えタブで両モード共通に表示日を変える
  - 重複都市を national から外したい場合は CITY_ORDER の各エントリに `excludeFromNational: true` を手で付ける（自動付与はしない）

`window.__MC_VIEW.dataUrl` から全レコードを 1 回 GET、クライアント側で都市・日付フィルタ。

### 地図モードの実装メモ

- 日本地図 SVG は [Geolonia japanese-prefectures](https://github.com/geolonia/japanese-prefectures) を採用。viewBox = 1000 × 1000、47 都道府県を `<path>` 単位で含み、沖縄は `transform="translate(52, 193)"` で左下に inset 化済み
- 取得元 SVG: `data/skills/jma-weather/views/japan-map.svg`
  - pinned commit: `90c5b4b8260de058d3db61b3cb8bfb6f67a81f9a` (2025-10-30)
  - sha256: `3b4b9aef5c6282675dc8f04fc1002af13c09e2feec7c1d130cb4038856c64497`
  - **GFDL ライセンス**（Wikipedia の日本地図ベース）。出典は view 右下に "Map: Geolonia / Wikipedia (GFDL)" として表示
  - 取り込み時に `grep` で `<script>` / `on*=` / `xlink:href` を検査済み、スクリプト要素ゼロ
- SVG は HTML view 内に **inline 展開** (build 時に `<!--__GEOLONIA_SVG__-->` プレースホルダを Python で置換)。runtime で CDN を叩かないので外部依存ゼロ
- CSS は Geolonia の inline `fill="#EEEEEE"` を `.prefecture { fill: var(--land); }` で上書き。NATIONAL_CITIES に含まれる都市の所属都道府県は JS から `.highlight` クラスを足して少し明るく塗る
- ピン位置は CITY_ORDER の `pref` (都道府県 class 名) + `xPct` / `yPct` で「prefecture の bbox 内の何%地点」として指定。`getBoundingClientRect()` で実 px 換算 → map-wrap に対する % で配置
- カードは **dot 廃止、台座型**: 各都市に `placement: N/S/E/W/NE/NW/SE/SW or center` を持たせ、CSS で都道府県中心から海側に 70px オフセットする（被り回避）。center は従来通り都道府県の上。引き出し線は試したが視覚的に不自然だったので **付けない**
- 各都市の placement: 札幌=center / 仙台=E / 新潟=N / 東京=E / 横浜=center / 名古屋=S / 大阪=N / 松江=N / 高松=S（瀬戸内海から南の海へ）/ 福岡=center（W だと地図外にはみ出すため）/ 那覇=center
- `NATIONAL_CITIES = CITY_ORDER.filter(c => !c.excludeFromNational)` で `excludeFromNational: true` が付いた都市だけ national から外す。デフォルトはどの都市にも付いていない
- **沖縄 inset 維持・boundary-line 削除**: 沖縄は元 Geolonia 通り `translate(52, 193)` で左上に inset 化する。ただし、元 SVG に含まれる白い斜めの境界線（`<g class="boundary-line">` の `<line>` 2 本）は那覇カードと衝突するため**削除済み**。**SVG 再 pin 時は boundary-line 削除を再適用すること**（沖縄 transform はオリジナルのまま）
- SVG 更新時は Geolonia repo の新 commit を再 pin して上記 grep + sha256 を取り直すこと

## 取得効率（条件付きGET + 変更時のみ書込）

HTTP 取得と書き込みは共通モジュール **`data/jma-weather/jma_http.py`** に集約され、3スクリプトが import する。

- **条件付きGET**: `fetch_json` / `fetch_bytes` は `If-None-Match` / `If-Modified-Since` を付けて取得する。JMA bosai は `ETag` / `Last-Modified` + `Cache-Control: max-age=60` を返し、未更新には **`304 Not Modified`** を返す。304 のときは `const/http_cache/` のキャッシュ本文を返してダウンロードを省略する（呼び出し側は無変更）。
- **変更時のみ書込**: `write_record_if_changed()` は `updatedAt` 以外が既存ファイルと完全一致ならレコードを書かない。毎時の無駄な書き込み churn を防ぐ（今日レコードは AMeDAS で毎回変わるので書かれる）。
- **効果**: リクエスト「数」は不変だが、未更新時は本文0バイト + 書込スキップで帯域・処理を激減。**鮮度リスクはゼロ**（変更があれば 200 で即反映 → 警報・訂正報の取りこぼし無し）。発表時刻ベースの時刻ゲートは取りこぼしリスクのため**不採用**。
- `const/http_cache/` は **runtime キャッシュ**。seed / bundle には含めない。
- `jma_http` は `certifi` フォールバックを内蔵し、ホスト（macOS の Python.framework 等、CA 未設定）でも全スクリプトが動く。

## 運用

- 定期取得: schema.json の `ingest` ブロック (`kind: "agent"`, `schedule: "hourly"`, `role: "general"`, `template: "templates/refresh.md"`) でホストが毎時 hidden agent を起動する。`config/scheduler/tasks.json` には登録しない (2026-06-27 移行)
- 手動取得: `/collections/jma-weather` ヘッダの **Refresh** ボタンか、`python3 data/jma-weather/fetch.py` を直接実行
- 同一 id のレコードは**上書き**される
- 取得失敗時はスクリプトが exit 1 → ingest agent も非ゼロ終了 → ホストが「Collection refresh failed: jma-weather」bell を立て、次回成功でクリアする。1 都市でも成功すれば exit 0
- cadence 変更履歴: 2026-06-21 〜 2026-06-26 はスケジューラの 3 時間間隔 / 2026-06-27〜 ingest agent の hourly (8 倍 → 24 倍/日)。JMA の bosai/forecast は公開エンドポイントで負荷影響は軽微

## CRUD

- 取得は **fetch.py に任せる**。人が編集する場面は基本ない
- 表示: `/collections/jma-weather` (テーブル / カレンダー / 天気予報ビュー)

## テスト

```bash
python3 /tmp/test_jma_fetch.py
```

## JMA API の参考

- 短期予報: `https://www.jma.go.jp/bosai/forecast/data/forecast/<office>.json`
- 概要文: `https://www.jma.go.jp/bosai/forecast/data/overview_forecast/<office>.json`
- 各地の office code: `https://www.jma.go.jp/bosai/common/const/area.json`
- 防災情報 XML 高頻度・随時フィード: `https://www.data.jma.go.jp/developer/xml/feed/extra.xml` (VPTW = 台風解析・予報情報。**活動中の台風がある期間にしか entry が出ない**)

## 警報・注意報データ (source="warning")

**判別**: 必ずある (毎日定常配信)。1 都市 1 ファイル、毎回上書き (履歴なし)。

**エンドポイント**: `https://www.jma.go.jp/bosai/warning/data/r8/<office>.json`
- **旧 `bosai/warning/data/warning/<office>.json` は 2026-05 以降凍結**しているので使わない (CloudFront Last-Modified が 5/22 で固定、それ以降更新されない)。フロントエンド (`bosai/warning/index.html`) は r8 を読んでいる。
- **重要: r8 はトップが list で、各要素は「単発の発表イベント」**。時系列順とは限らない。先頭 1 件だけ見ると別 code の active を取り逃がす。code ごとに reportDatetime 最大の status を採用して集約する。

**構造**:
```json
[
  { "reportDatetime": "...", "publishingOffice": "...", "headlineText": "...",
    "warning": {
      "class10Items": [{ "areaCode": "140010", "kinds": [{"code":"07","status":"発表","additions":["うねり"]}] }],
      "class20Items": [{ "areaCode": "1410011", "kinds": [...] }]
    } },
  ...
]
```

**集約対象 (city.warning_areas)**: 市域単位の **class20 のみ**。class10 (神奈川県東部など) は広域なので含めない。市民視点の警報を表示するため。class10 は文脈解決のためだけに参照する (後述)。各都市の class20 コードは `https://www.jma.go.jp/bosai/common/const/area.json` の `class20s` から拾う。
- 札幌 `0110000` / 仙台 `0410001 + 0410002` (東部/西部) / 新潟 `1510000` / 東京 `TOKYO_23WARDS` (23 区全部) / 横浜 `1410011 + 1410012` (北部/南部) / 名古屋 `2310000` / 大阪 `2710000` / 松江 `3220100` / 高松 `3720100` / 福岡 `4013000` / 那覇 `4720100`

**code → 名称マッピング (`WARNING_CODE_MAP`)**: r8 のコードは XML 仕様書の番号と微妙にズレている。**実データの headline と additions から裏取りした**もの:
- 03=大雨警報、07=波浪警報、14=雷注意報、15=強風注意報、16=波浪注意報、20=濃霧注意報、29=土砂災害 (重大度別)
- ハマりやすい誤訳: 16 を「大雪注意報」/ 20 を「洪水注意報」とすると間違い。波浪 (うねり) や濃霧の headline と矛盾する。

**2026-05-29 改革による呼称変更**: JMA は河川氾濫・大雨・土砂災害・高潮に関する情報を「警戒レベル」と整合させた新呼称に切り替えた。
- 例: 大雨警報 → **レベル3大雨警報**、土砂災害警戒情報 → **レベル4土砂災害危険警報**、大雨注意報 → **レベル2大雨注意報**
- レベル4相当として **危険警報** 階層 (level="danger") が新設された。`WARNING_CODE_MAP` と `SEDIMENT_LOCALS_MAP` はこの新呼称で実装。
- 一次情報: https://www.jma.go.jp/jma/kishou/know/bosai/keiho-update2026/
- 風雪・雷・乾燥・濃霧等は警戒レベル相当情報の対象外で、旧名のまま。

**status の解釈 (`resolve_warning(code, status, properties)`)**:
- 通常 status: `発表` / `継続` = active、`解除` / `発表警報・注意報はなし` = 除外
- 降格 status: `警報から注意報` / `危険警報から注意報` など — これは「以前は上位レベル、今は下位レベル」を意味する **履歴つきの現在状態**
  - 通常 code (16 = 波浪注意報など): code 自体が既に注意報級なので名前/レベルは変えず、status_label に降格事実を残す
  - **code 29 (土砂災害)** は特殊で、1 つの code 内で重大度が変動する。**`properties.significancyPart.locals[].code` で重大度を解決する** (SEDIMENT_LOCALS_MAP):
    - locals.code `21` → レベル2土砂災害注意報 (advisory)
    - locals.code `22` → レベル3土砂災害警報 (warning)
    - locals.code `23` → レベル4土砂災害危険警報 (danger) ← 旧「土砂災害警戒情報」
    - locals.code `24` → レベル5土砂災害特別警報 (special)
    - properties が無い古い entry のフォールバック: status (`危険警報から注意報` / `危険警報から警報`) から推定

**class10 と class20 の status 連関ルール** (横浜事例から):
- 横浜の警報 JSON で、同一 entry の class10 (140010 = 神奈川県東部) が status="危険警報から注意報"、class20 (1410011/1410012 = 横浜市) が status="継続" となるケースがある。
- 素直に読むと「class10 は緩和、class20 は警戒情報継続」と矛盾するが、JMA の意図は **「class10 で起きたレベル変化を class20 でも継続する」** = 横浜市も注意報レベルに緩和された状態。
- **`fetch_warnings` のロジック**: 同一 entry で親 class10 (= `city["area_code"]`) の status が降格系 (`X から Y` で始まる) のとき、class20 の `"継続"` を **class10 の status で上書き** して文脈解決する。これがないと横浜の現状を「警戒情報継続中」と誤判定する。

**取得経路**:
```
https://www.jma.go.jp/bosai/warning/data/r8/<office>.json (11 都市)
  └ 全 entry を走査
       ↓ city.warning_areas に一致する class10Items / class20Items の kinds を集める
       ↓ 同一 entry の親 class10 status を文脈として記憶
       ↓ class20 の "継続" は class10 の降格 status で上書き
       ↓ code ごとに reportDatetime 最大の status を採用
       ↓ resolve_warning(code, status) で (name, level, status_label) に変換
       ↓ data/jma-weather/items/<office>-warning.json
```

**レコード形状** (`<office>-warning.json`):
- `id` — `<office>-warning` (例: `140000-warning`)
- `source` — `"warning"`
- `office`, `cityName`, `regionName`, `cityOrder`
- `publishingOffice`, `reportDatetime`, `headlineText` (UI 非表示だが記録)
- `warnings` — `[{ code, name, level, status, additions? }]` の配列。`level` は `special` / `warning` / `advisory`、`status` は降格時は「警戒情報→注意報」のような短縮表記

**ビュー統合**: `views/news-weather.html` の指定地域モード (city mode) で hero の上に **警報バー**:
- ピル状で警報名 + 気象台 + 発表時刻
- レベルで色分け: 特別警報=紫グラデ + グロー / 警報=赤 / 注意報=黄
- アクティブ警報が 0 件のときバー自体を非表示
- ワイド画面 (≥ 900px) で overview がある時は警報バーが grid 最上段 (`warnings warnings`) に span

**運用**: 天気・台風・天気図・海上と同じ ingest agent に相乗り (hourly)。専用タスクは作らない。11 都市 × 1 リクエスト = 軽量。

## 台風データ (source="typhoon")

**判別**: extra.xml + extra_l.xml から `VPTW` を含む atom entry を抜くだけで「活動中の台風があるか」が判る。JMA は活動台風がない期間 VPTW を発番しない。

**JMA の VPTW 発番パターン (一次情報)**:

[気象庁 配信資料に関する仕様 No.11902 「台風解析・予報情報」 (令和4年3月30日)](https://www.data.jma.go.jp/suishin/shiyou/pdf/no11902) (イ) 内容表より:

| 観測時刻 | 内容 | `max_forecast_h` |
|---|---|---|
| 03/09/15/21 時 | 実況 + 推定1時間後 + 12,24,48,72,96,**120 時間先予報** (5日予報) | 120 (省略時 24) |
| 00/06/12/18 時 | 実況 + 推定1時間後 + 12,24 時間先 + 3時間前解析に基づく 45,69,93,117 時間先 | 24 |
| 毎時 (合間) | 実況 + 推定1時間後 (日本に大きな影響を及ぼす台風等が接近しているときのみ) | 0 |
| **温帯低気圧化または熱帯低気圧化のとき** | **実況のみ** | 0 |

つまり同じ EventID 内に「完全版 (予報あり)」「位置のみ版」「終息報 (実況のみ + cls=ETC/TD)」の 3 種類が時系列で混在する。`forecastSerial` は全種共通で連番なので Serial を時系列順に使えるとは限らない → **`reportDatetime` を主キーにする**。

**取得経路 (2026-06-28 再設計)**:
```
extra.xml + extra_l.xml (高頻度 + 約24時間履歴)
  └ entry id に VPTW を含むもの全部 fetch
       ↓ EventID ごとに候補リスト化 (実況のみ版・完全版を区別しない)
       ↓ 各 EventID で 2 つのソースを選ぶ:
         ・観測ソース  = 最新 reportDatetime の VPTW (max_forecast_h は問わない)
         ・予報ソース  = 最新 reportDatetime の VPTW で max_forecast_h > 0 のもの
       ↓ build_merged_typhoon_record(): 
         ・実況/推定 points は観測ソースから
         ・予報 points は予報ソースから (観測の 実況 dt より後のものだけ)
       ↓ 観測ソース rdt が ACTIVE_WINDOW_HOURS (=12h) 内 → active
       ↓ data/jma-weather/items/typhoon-<EventID>.json
```

**なぜ 2 ソース合成か**: 旧設計は「予報が 24h 以上ある VPTW の中で最新」を 1 つ選んでいたため、温帯低気圧化後など完全版の発番が遅れているとき **実況時刻が 3〜10 時間古い VPTW を採用**してしまい、ビューに「実況 18:00」と古い時刻が表示されていた。新設計は実況時刻と予報内容を別ソースから合成して、JMA が最後に位置確認した時刻 (=21:00) を実況として表示する。

`fetch.py` の末尾 (--only 指定時はスキップ) で extra.xml + extra_l.xml → 各 VPTW を fetch。台風なしの時は extra.xml + extra_l.xml の 2 リクエストだけで終わる軽量な追跡。

**終息判定 (一次情報ベース)**:

仕様 No.11902 (イ) 内容表の 4 列目「温帯低気圧化または熱帯低気圧化のとき: 実況」が JMA の最終報シグナル。**観測ソースの MeteorologicalInfo が 1 つだけ (= 実況のみ、推定/予報なし) で、その熱帯擾乱種類 (cls) が温帯低気圧または熱帯低気圧** のとき、`fetch.py` の `_detect_dissipation()` がそれを最終報と判定し、レコードに以下を立てる:

- `dissipated: true`
- `dissipationKind: "温帯低気圧化" | "熱帯低気圧化"`
- `dissipationCls: "温帯低気圧(LOW)" | "熱帯低気圧(TD)"` 等
- `dissipationAnnouncedAt: ISO` (= 最終報の reportDatetime)

実例: TC2608 メーカラー 2026-06-27 21:50 JST、Serial 109、実況のみ、cls=温帯低気圧(LOW) → 温帯低気圧化の最終報と判定。

**ライフサイクル**:

| 状態 | active window | フィールド | UI |
|---|---|---|---|
| 通常 (発番継続中) | 12h (`ACTIVE_WINDOW_HOURS`) | `archived: false`, `dissipated: false` | 通常表示 |
| 終息直後 (closing announcement 後 0〜6h) | 6h grace (`DISSIPATED_GRACE_HOURS`) | `archived: false`, `dissipated: true` | 「終息（温帯低気圧化）」バッジ、通常表示 |
| 終息 (announcement 後 6h+) | — | `archived: true`, `dissipated: true` | 「終息（温帯低気圧化）」バッジ、半透明 |
| 鮮度切れ (実況時刻が 12h 超過、かつ終息報を受け取らずに発番が止まった) | — | `archived: true`, `dissipated: false` | 「終息」バッジのみ、半透明 |

JMA 仕様上、温帯/熱帯低気圧化のとき VPTW 発番は止まるので、`dissipated: true` を検知したら 12h 待たずに 6h で `archived: true` にする。**「劣化ガード (skip overwrite)」は廃止** — マージ設計では新レコードが古いソースより悪くなることがないため不要。

**レコード形状** (`typhoon-<EventID>.json`):
- `id` — `typhoon-<EventID>` (例: `typhoon-TC2608`)
- `source` — `"typhoon"`
- `summary` — displayField (例: `台風7号 メーカラー 996hPa 中心36.0N/143.0E → 予報　２４時間後`)
- `eventId`, `typhoonNumber` (yyNN, 例 2607)、`typhoonName` (MEKKHALA)、`typhoonNameJa` (メーカラー)、`typhoonLabel` (台風7号)
- `forecastSerial` — 観測ソースの Serial (= 最新の VPTW 番号)
- `forecastPoints` — 実況 + 予報の合成配列。各点に lat/lng/pressure_hpa/wind_max_ms/wind_gust_ms/cls/moveDir/moveKmh/forecast_radius_km/strong_radius_km/storm_radius_km/storm_warning_radius_km (km)
- `reportDatetime` — 観測ソースの発表時刻 (= JMA が最後に位置確認した時刻)
- `forecastIssuedAt` (任意) — 予報ソースの発表時刻。観測ソースと別 VPTW のときだけ付く
- `forecastIssuedSerial` (任意) — 予報ソースの Serial
- `archived` — boolean
- `dissipated` (任意) — boolean。仕様 No.11902 の最終報を検知したら true
- `dissipationKind` (任意) — `"温帯低気圧化"` | `"熱帯低気圧化"`
- `dissipationCls` (任意) — 終息時の熱帯擾乱種類文字列
- `dissipationAnnouncedAt` (任意) — 最終報の reportDatetime
- `updatedAt`

**ビュー統合**: `views/news-weather.html` の右上スイッチに **🌀 台風** ボタンを **常時表示**。`activeTyphoons().length === 0` のときは `opacity: 0.55` で薄く表示し、クリックすると `#typhoon-empty` overlay (「台風はありません」) が地図上にかぶる。これで「タブが消える」現象を避け、ユーザーが「台風モードはあるが今は無いだけ」と確認できる。台風モードは Leaflet + OpenStreetMap で:
- 中心マーカー (実況=塗り、予報=白抜き)
- 中心ライン (破線)
- 実況の強風域 (黄)・暴風域 (赤)
- 予報円 (台風色、70% 確率)
- 暴風警戒域 (赤の破線リング)
- 各点ラベル (dtType + 中心気圧)
- 右ペインに台風別の素データ表 (時刻/中心/気圧&風/半径)
- `archived: true` の台風は半透明 (opacity 0.35) で残る
- `dissipated: true` のとき H3 タイトル横に「終息（温帯低気圧化）」/「終息（熱帯低気圧化）」バッジ:
  - `archived: false`: 青系バッジ (`.dissipating-tag`)、本体はフル表示 (closing announcement から 6h 以内)
  - `archived: true`: 橙系バッジ (`.archived-tag`)、本体は薄く (closing announcement から 6h+ 経過、または鮮度切れ)

**運用**: 天気と同じ 3 時間ごとの scheduler に相乗り。台風専用タスクは作らない。台風期に手動で `python3 data/jma-weather/fetch.py` を流して即時更新も可能。

## 天気図データ (source="chart")

**判別**: 必ずある (毎日定常配信されるため条件不要)。

**方式 (2026-06-25 ベクトル → PNG 切替)**:
- 旧: 防災情報 XML (VZSA50/VZSF50/VZSF51) を parse して Leaflet 上に等圧線・H/L・前線を自前描画
- **新: JMA 公式の天気図 PNG を直リンクで表示する**
  - 理由: ベクトル描画は等圧線数百本のレンダリングでブラウザが重かった。PNG は 1 枚 ~60 KB で即表示
  - 失ったもの: zoom/pan で拡大荒れ、他レイヤとの重ねができない、個別クリック不可
  - 必要になったら git log の "PNG 切替" 以前のコミットから戻せる

**取得経路**:
```
https://www.jma.go.jp/bosai/weather_map/data/list.json (カタログ)
  └ near.now  → spas (実況図, 3 時間ごと)
  └ near.ft24 → fsas24 (24h 予想, 12 時間ごと)
  └ near.ft48 → fsas48 (48h 予想, 12 時間ごと)
       ↓ カラー版 (JRcolor) の最新ファイル名を選ぶ
       ↓ PNG を https://www.jma.go.jp/bosai/weather_map/data/png/<filename> から
         ローカルに DL: data/jma-weather/charts/<filename>.png
       ↓ JSON レコード: data/jma-weather/items/<chartType>-<targetDateHour>.json
```
`fetch.py` の末尾で `from fetch_chart import fetch_weather_charts` をして実行。

**ファイル名パターン**:
`20260624190531_0_Z__C_010000_<対象時刻 14桁 UTC>_MET_CHT_JCI<spas|fsas24|fsas48>_..._JR<color|monochrome>_..._image.png`

**TargetDateTime と validDateTime**:
- 実況図: targetDateTime = 解析時刻、validDateTime = 同じ
- 予想図: targetDateTime = 基準解析時刻、validDateTime = 基準 + 24h or 48h (予想が有効な時刻)
- ビュー表示は **validDateTime** を「対象時刻」として出す

**ライフサイクル**: 同じ chartType でより新しいレコードが出たら旧レコードに `archived: true`。validDateTime が 7 日より前の chart レコードは JSON も対応 PNG も自動削除 (天気図は鮮度が命)。`items/*.json` が参照しなくなった PNG (orphan) も同タイミングで掃除。

**レコード形状** (`<chartType>-<targetDateHour>.json`):
- `id` — `surface-2026-06-25T06` 等 (validDateTime の YYYY-MM-DDTHH)
- `source` — `"chart"`
- `chartType` — `surface` / `fcst24` / `fcst48`
- `chartLabel` — 「地上実況図」等
- `targetDateTime` / `validDateTime` — 上記
- `summary` — displayField
- `pngFilename` — JMA のファイル名そのまま (例: `20260624230030_0_Z__C_010000_..._image.png`)
- `pngUrl` — `https://www.jma.go.jp/bosai/weather_map/data/png/<filename>` (ビューはこれを `<img src>` に使う)
- `pngPath` — `data/jma-weather/charts/<filename>` (ローカルキャッシュ、Finder で開ける)
- `archived` — boolean

**ビュー統合**: `views/news-weather.html` の右上スイッチに **🗺️ 天気図** ボタン。
- 上部にサブタブ [実況図 | 24h予想 | 48h予想]
- 中身は `<img src={pngUrl}>` の 1 タグだけ (Leaflet を使わない)
- `body.mode-chart` で max-width を 1400px に広げる
- 出典リンクで JMA PNG を別タブで開ける

**運用**: 天気・台風と同じ 3 時間ごとの scheduler に相乗り (`jma-weather-fetch` が weather + typhoon + chart を一括実行)。専用タスクは作らない。1 回あたり 3 PNG × ~60 KB = 180 KB を DL してキャッシュ。

## 地方海上予報データ (source="marine")

**判別**: 必ずある (毎日定常配信されるため条件不要)。1 海域 1 レコード、毎回上書き (履歴なし)。

**位置付け**: 旧 wave モードは沿岸府県の `wave` フィールドを流用していたが、JMA bosai/seawarning に **「地方海上予報」の正式 JSON エンドポイント** があると判明 (2026-06-25)。波高だけでなく **風 (ノット + m/s 併記)・天気・視程・概況** が同じ予報区・同じ時間軸で配信されているので、これに一本化した。マリナー視点での Marine Forecast が直接読める形になった。

**取得経路**:
```
https://www.jma.go.jp/bosai/seawarning/data/forecast/<office>.json (12 オフィス)
  └ 札幌・函館・仙台・東京・新潟・名古屋・舞鶴・神戸・福岡・長崎・鹿児島・沖縄
  └ 各 JSON に "今日 / 明日" 2 期間 × 複数海域の wind/wave/weather
       ↓ areaCode (4桁) で 1 海域 1 レコードに展開
https://www.jma.go.jp/bosai/common/const/label_pos/marines.json
  └ 37 海域の重心 lon/lat (Leaflet マーカー配置用)
https://www.jma.go.jp/bosai/common/const/geojson/marines.json (~178KB)
  └ 37 海域の境界ポリゴン (MultiPolygon)。レコードに geometry として埋め込み
```
`fetch.py` 末尾で `fetch_marine.main()` を呼ぶ (chart と同じ相乗りパターン)。

**全 37 海域カバレッジ** (オフィス → 担当海域):
- 札幌 (016000): サハリン東/西方・網走沖・宗谷海峡・北海道西方
- 函館 (017000): 北海道東方・釧路沖・日高沖・津軽海峡・檜山津軽沖
- 仙台 (040000): 三陸沖 東部/西部
- 東京 (130000): 関東海域 北部/南部
- 新潟 (150000): 沿海州南部沖・秋田沖・佐渡沖・能登沖
- 名古屋 (230000): 東海海域 東部/西部/南部
- 舞鶴 (260020): 日本海北西部・山陰沖 東/西
- **神戸 (280000): 瀬戸内海 (4010) / 四国沖 北部/南部**
- 福岡 (400000): 対馬海峡
- 長崎 (420000): 済州島西・長崎西・女島南西
- 鹿児島 (460100): 日向灘・鹿児島海域・奄美海域
- 沖縄 (471000): 東シナ海南部・沖縄東方/南方

**レコード形状** (`marine-<areaCode>.json`):
- `id` — `marine-<areaCode>` (例: `marine-4010` = 瀬戸内海)
- `source` — `"marine"`
- `areaCode` (4 桁) / `areaName`
- `office` (6 桁) / `officeLabel` (発表気象台)
- `title` — 「神戸海上気象」など電文タイトル
- `publishingOffice` — 実際の発表元 (代理発表時は管区が変わる)
- `reportDatetime` — 発表時刻 (ISO)
- `centroid` — `{ lon, lat }` 海域重心 (marines.json 由来。地図ピン配置に使用)
- `synopses` — 概況テキスト配列 (低気圧位置・前線形状・台風中心など。dedupe してビュー上部に表示)
- `periods` — `[{ name, wind[], wave[], weather[], vis[], windPrimaryDirection, waveMaxM, hasSwell }]`
  - `name` は JMA 電文の期間名 (「今日」「明日」)
  - `wind`/`wave`/`weather`/`vis` は **原文配列のまま保持**。風は「東 30ノット（15メートル）」のようにノット + m/s 併記
  - `windPrimaryDirection` = 最初に登場する方位 (16 方位の日本語)。ピン表示用に view 側で英字 (NE/SSW 等) に変換
  - `waveMaxM` = 文中から抜いた最大波高 (m)。ピン色分けに使用
  - `hasSwell` = 「うねり」を含むか

**風速の単位換算は行わない**: JMA 原文に「30ノット（15メートル）」のようにノット・m/s が併記されているため、Python 側は **両方を含む原文をそのまま保持** する (`compactWind` で view 側が短縮表示するだけ)。

**ビュー統合**: wave モード (`#mode-wave`、ボタンは「🌊 波」)。**Leaflet + 国土地理院 淡色地図 + JMA 海域ポリゴン** で描画 (Geolonia 都道府県 SVG ベースは 2026-06-25 廃止。理由: (a) 沖縄が左上 inset で地理座標と一致しない (b) 海岸線・海域境界がない (c) 線形近似で精度が出ない、いずれも marine forecast には不向きだったため)。
- 日付タブ: **「今日」「明日」** (JMA 電文の period 名そのまま、`waveDay` 変数も `今日`/`明日`)
- 中央 **Leaflet 地図** (`#wave-leaflet-map`、高さ 70vh):
  - 基盤: CartoDB Positron (opacity 0.6、太平洋・東シナ海・日本海の大域カバー)
  - 主層: 国土地理院 淡色 (`xyz/pale`、日本付近の精度高め)
  - 各海域: JMA GeoJSON ポリゴン (`rec.geometry`) を波高で色塗り (青/橙/赤の 3 段階、`waveStyle()`)
  - ホバーで強調、クリックで詳細 popup (波・風・天気の原文 + 発表元)
  - 海域重心に永続 `divIcon` ラベル (海域名 + 🌊 波高 + 💨 方位 + ノット/m/s)
- 下部 **概況バー** (`.wave-synopsis`): 全オフィスの `synopses` を dedupe して箇条書き
- さらに下 **サイドリスト** (`.wave-side`): 波高降順、画面幅に応じて 1-3 列の grid。波 原文 + 風 原文を併記
- **MARINE_DROP_CODES**: 既定は空 = **37 海域全部を表示**。サハリン東/西、沿海州、済州島西、東シナ海南部 など外国近海を含めて Marine Forecast 全域をカバー。地図表示が重い場合や特定海域を隠したいときだけ areaCode を追加する

**運用**: 天気・台風・天気図と同じ 3 時間ごとの scheduler に相乗り (`jma-weather-fetch`)。専用タスクは作らない。地方海上予報は気象庁が 1 日 4 回 (主に 04/10/16/22 JST) 発表するが、3 時間ごと fetch でも `reportDatetime` が同じなら上書きで実害なし。1 回あたり 12 オフィス × ~1.5 KB + marines.json (label) ~1.2 KB + marines.json (GeoJSON ポリゴン) ~178 KB ≒ 200 KB の fetch。GeoJSON は items に embed されるので 1 レコード ~6-14 KB に膨らむが、地図描画はクライアントで完結 (追加 round-trip ゼロ)。
