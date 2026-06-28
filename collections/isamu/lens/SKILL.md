---
name: lens
description: カメラレンズを記録するコレクション。ユーザーが「このレンズを追加して」「持ってるレンズ一覧」「この焦点距離は手持ちでカバーできてる?」「F2.8通しのズームある?」「売ったレンズはどれ?」などと言ったら使う。記録は dataPath（取り込み時に正規化される）に1件1ファイルの JSON で保存し、ユーザーは /collections/<slug> で schema.json からレンダリングされた表・入力フォーム・『図鑑』ビューを見る。CRUD は manageCollection（または JSON ファイルへの Read / Write / Edit）で行う。
---

# カメラのレンズ（スキーマ駆動コレクション）

スキーマ駆動のデータアプリ。`schema.json` がデータモデルと UI を宣言し、ホストが
`/collections/<slug>` に表・入力フォーム・カスタムビューを描画する。買ったレンズ・売った
レンズを記録し、焦点距離のカバー範囲・F値・スペックを比較するための個人用データベース。
元になったデータモデルは `isamu/lens-database` の `LensData` 型。

## レコードの形

- `id` — kebab-case のスラッグ。主キー＝ファイル名（拡張子なし）。`<maker>-<焦点距離>-<F値>` から作る（例: `sony-fe-24-70mm-f28-gm2`, `canon-rf-50mm-f18`）。
- `name` — レンズ名（必須）
- `maker` — メーカー（enum・必須）
- `mount` — マウント（enum・必須）
- `focalLengthMin` — 焦点距離の最小 mm（必須）。**単焦点はこの値だけ**入れる
- `focalLengthMax` — 焦点距離の最大 mm。**ズームのみ**。単焦点は空欄
- `fStopWide` / `fStopTele` — 開放F値。固定絞りは `fStopWide` のみ、可変絞りは両方
- `format` — 対応センサーフォーマット（enum）
- `focus` — AF / MF（enum）
- `ownership` — 欲しい / 所有中 / 売却済み（enum）。**現在の状態**。かんばんの既定グループ・図鑑の色分けに使う
- `purchaseDate` — 購入日（date）。初回購入の目安＝図鑑の「購入日」並び替え用
- `ownershipPeriods` — **所有期間**（table・array of object）。各行 = `from`(取得日) / `to`(手放し日, 空=現在も所有) / `acquirePrice`(取得額) / `releasePrice`(売却額) / `note`。**出戻り（売って買い直し）は行を足す**、**同時に複数本所有は期間が重なる行**で表現
- `netCashflow` — **通算収支**（derived・host計算＝`Σ売却額 − Σ取得額`）。**書かない**（getItems でのみ見える）
- `hasStabilizer` / `hasDustMoistureResistance` / `isInternalFocus` — 機能の有無（boolean）
- `minFocusDistance`(m) / `maxMagnification` / `bladesCount` / `elements` / `groups` — 光学スペック（number）
- `filterDiameter` / `diameter` / `length` / `weight` — 物理スペック（number, mm / g）
- `releaseDate` — 発売日。`YYYY` でも可なので string
- `discontinued` — 生産終了（boolean）。**true のときだけ** `discontinuedDate`（string）が表示される
- `hoodModel` / `caseModel` / `EANCode` — 型番・コード（string）
- `photo` — 写真。ワークスペース相対の画像パス（image, 詳細ビューのみ）
- `links` — 公式 / 価格.com / Amazon 等のリンク（markdown）
- `notes` — メモ（text）

host-computed は `netCashflow`（derived）のみ＝**書かない**。他は全フィールドが編集・保存対象。

## やること

**追加 / 更新** — `manageCollection` putItems。各行はスキーマ検証後に書き込まれる。`rejected` が出たら `problem` を見て直してから該当行だけ再送。追加は `mode: "create"`（id 衝突を拒否）、既存レコードの一部更新は `mode: "merge"` で部分行（`{ id, 変更フィールド }`）。既定の upsert はレコード全体を置換するので、省いた任意フィールドが消える点に注意。
**一覧 / 参照** — `manageCollection` getItems。件数が増えたら `ids` / `fields` を指定。
**削除** — レコードファイルを削除。
**スキーマ変更**（フィールド / ビュー / アクションの追加・改名・削除、enum 値の増減など）— `manageCollection` の schemaDocs → getSchema → putSchema。schema.json を生の Read/Write/Edit で直接いじらない（putSchema が全体を検証してから書き込む）。

## ドメインのコツ

- **「85mm は持ってる?」** → 各レコードで `focalLengthMin ≤ 85 ≤ (focalLengthMax があればそれ、無ければ focalLengthMin)` を満たすものを探す（単焦点は max が空欄＝min と同値とみなす）。「所有中だけ」なら `ownership = 所有中` でフィルタ。
- **「F2.8 通しのズームはある?」** → `fStopWide ≤ 2.8` かつ `fStopTele` が空欄（＝固定絞り）かつ `focalLengthMax` あり（＝ズーム）。
- **単焦点 vs ズーム** ＝ `focalLengthMax` の有無で判別。
- **売却したレンズ** → `ownership = 売却済み`。
- メーカーや対応フォーマットを増やしたいときは putSchema で enum の `values` に追加する。
- チャットで全件を羅列しない。追加・更新後は `presentCollection`（slug と record id）でインライン表示。単なる「見せて / 一覧」は slug だけで `presentCollection`。

## 図鑑ビュー

`views/zukan.html`（ラベル「図鑑」）= ①焦点距離バー（対数 mm 軸、単焦点はドット、マウント別の色、売却は半透明＋斜線）②リスト（小サムネ＋スペック）③画像ギャラリー の3モード切替。並び替え（焦点距離 / 購入日 / メーカー / F値 / 名前）、所有フィルタ、メーカー / マウント / シリーズの絞り込み（AND 重ねがけ）、「実焦点 / 35mm換算」トグル（フォーマット＋マウントからクロップ係数を判定）を備える。レコードのクリックでホスト標準の詳細モーダルが開く。
