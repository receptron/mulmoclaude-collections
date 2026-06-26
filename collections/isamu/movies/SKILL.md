---
name: movies
description: 個人の映画リストコレクション。ユーザーが「この映画を追加して」「観たい映画を登録」「この映画を観た、★4」「未鑑賞の映画は?」「SFで評価の高い映画は?」「今年観た映画は?」などと言ったら使う。記録は dataPath（取り込み時に正規化される）に1件1ファイルの JSON で保存し、ユーザーは /collections/<slug> で schema.json からレンダリングされた表・入力フォームを見る。CRUD は manageCollection（または JSON ファイルへの Read / Write / Edit）で行う。
---

# 映画リスト（スキーマ駆動コレクション）

スキーマ駆動のデータアプリ。`schema.json` がデータモデルと UI を宣言し、ホストが
`/collections/<slug>` に表・カレンダー・入力フォーム・カスタムビューを描画する。

## レコードの形

- `id` — kebab-case slug、主キー（ファイル名、拡張子なし）
- `title` — 文字列、必須
- `originalTitle` / `year` / `genre`(enum) / `theme`(enum) / `director` / `country` /
  `runtime` / `platform`(enum) — 任意
- `watched` — boolean。`rating`(1〜5) と `watchDate` は `watched` のときだけ表示
- `poster` — image（https URL もワークスペース相対パスも可）
- `notes` — テキスト

## カスタムビュー「シネマ」

`schema.json` の `views[]` に `cinema`（ラベル「シネマ」）を登録済み。テーマ別の横
スクロール棚＋検索の Netflix/Amazon Prime 風レイアウト。各カードのサムネは `poster`
フィールドを使う。

## やること

- **追加 / 更新** — `manageCollection` putItems（`mode: "create"` で追加、`mode: "merge"`
  で部分更新）。`poster` は実在ポスター URL（Wikipedia インフォボックス先頭画像など）を埋める。
- **一覧 / 参照** — `manageCollection` getItems。
- **削除** — レコードファイルを削除。

> このコレクションは `receptron/mulmoclaude-collections` レジストリのサンプルです。
> 取り込むと `dataPath` は `data/collections/<slug>/items` に正規化され、同梱 seed が
> （dataPath が空のとき）投入されます。
